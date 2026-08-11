"""Planning Board — `/api/planning/*` [Blueprint: /api/planning].

PERSON-centric free-form board. Replaces the old week-centric canvas
(`sharing.canvas_get/save`, `weekly_canvas`) — there used to be one shared board per
week, and it was born empty every Monday. Now there are two kinds of boards:

  * `management`   — ONE board shared by all managers; employees can't access it
  * `user:<sub>`   — SHARED workspace per person; the manager + that employee write

AUTHORIZATION (single gate `_board_access`, SAME rule for read and write):

  actor                                  | management | own | other
  --------------------------------------|---------|-------|--------
  management                            |    ✓    |   ✓   |   ✓
  designer/content_creator/videographer |   403   |   ✓   |  403
  anonymous                             |   401   |  401  |  401
  role 'pending' etc.                   |   403   |  403  |  403

`_require_management()` isn't enough for this because the rule is asymmetric: a
manager can reach any board, an employee only their own. Authorization runs through
`current_user()` → under impersonation, a manager looking through a designer's eyes
can't see the management board (correct behavior).

CONFLICT MODEL — delta PATCH, not a blunt 409:
The old endpoint overwrote the whole card array raw (`c.tasks = data['tasks']`) → when
two people wrote at the same time, one would silently disappear. A blunt
`If-Match → 409` was also rejected: a user would move 40 cards, get a 409, and lose
their work. Instead:
  * request {base_version, upsert:[...], delete:[...]} — item granularity
  * the board row is locked with `with_for_update()` → two concurrent PATCHes on the
    same board queue up (Postgres; sqlite IGNORES this hint, can't be proven in tests)
  * the write is ALWAYS applied; if `base_version` is stale, the response returns
    `stale:true` + the full item list (reconciliation in a single round trip)
  * item-level `rev`: if the client's known rev is lower than the server's, the item
    goes into `conflicts[]`, LAST WRITER WINS, the panel shows a warning
  * two people touching DIFFERENT cards never conflict at all — this is the intended
    behavior
  * `version` isn't a gate, it's a "someone wrote" signal (the cheap /version endpoint
    polls it)

CSRF is shared via `api.csrf_protect` (same pattern as ads.py/client_tracking.py).
"""
import datetime as dt
import json
import re

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import logging

import notifications
import planning_images
from api import csrf_protect
from extensions import db
from models import AdCampaign, Client, UserRef, utcnow
from models_planning import ITEM_STATUSES, ITEM_TYPES, PlanningBoard, PlanningItem
from models_sharing import ShootTask
from sso_client import current_user

bp = Blueprint('planning', __name__)
log = logging.getLogger('agency.planning')
bp.before_request(csrf_protect)  # same CSRF as api (session token)

# Roles that have boards. 'pending' / customer roles are excluded — they have no
# boards either.
PANEL_ROLES = ('management', 'designer', 'content_creator', 'videographer')

BOARD_KEY_RE = re.compile(r'^(management|user:[A-Za-z0-9_\-.|@]{1,56})$')

# Picker lists are truncated: these feed the search box, not a full dump.
LINKABLE_LIMIT = 100
ASSIGNED_LIMIT = 200
ITEM_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

MANAGEMENT_KEY = 'management'
MAX_ITEMS_PER_BOARD = 2000      # 588 migrated cards + 19 regions + generous headroom
MAX_BATCH = 200                 # upsert/delete upper limit per request
MAX_EXTRA_BYTES = 4096
COORD_LIMIT = 100_000
SIZE_MIN, SIZE_MAX = 20, 4000
TITLE_MAX, LABEL_MAX, TEXT_MAX, LINK_MAX = 300, 80, 5000, 1024


# --- authorization ---------------------------------------------------------------

def _board_access(board_key):
    """(user, err) — SINGLE authorization gate; read and write use the same rule."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not authenticated'), 401)
    if not BOARD_KEY_RE.match(board_key or ''):
        return None, (jsonify(error='invalid board key'), 400)
    role = u.get('role')
    if role not in PANEL_ROLES:
        return None, (jsonify(error='you do not have access to this page'), 403)

    if board_key == MANAGEMENT_KEY:
        if role != 'management':
            return None, (jsonify(error='the management board is only open to management'), 403)
        return u, None

    owner_sub = board_key.split(':', 1)[1]
    if owner_sub == str(u.get('sub')):
        return u, None                      # own board — any panel role
    if role != 'management':
        return None, (jsonify(error='you can only view your own board'), 403)
    if db.session.get(UserRef, owner_sub) is None:
        return None, (jsonify(error='user not found'), 404)
    return u, None


# --- helpers ---------------------------------------------------------

def _user_names():
    """{sub: display name} — SINGLE query. Per-item lazy access is FORBIDDEN (N+1)."""
    return {u.sub: (u.name or u.email)
            for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}


def _board_title(board, names):
    if board.kind == 'management':
        return 'Management Board'
    return names.get(board.owner_sub) or f'#{board.owner_sub}'


def _get_or_create_board(board_key, user):
    """Get the board, create it if missing. In a multi-worker race, UNIQUE(board_key)
    rejects the second INSERT → rollback + re-query (client_tracking seed pattern)."""
    board = PlanningBoard.query.filter_by(board_key=board_key).first()
    if board is not None:
        return board
    kind = 'management' if board_key == MANAGEMENT_KEY else 'user'
    owner_sub = None if kind == 'management' else board_key.split(':', 1)[1]
    board = PlanningBoard(board_key=board_key, kind=kind, owner_sub=owner_sub,
                          version=1, created_by=user.get('sub'))
    db.session.add(board)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        board = PlanningBoard.query.filter_by(board_key=board_key).first()
    return board


def _link_titles(rows):
    """Display names for domain links — ONE batched query per link TYPE.

    Writing `r.client.name` would trigger a per-item lazy query and break the
    `test_board_get_sorgu_sayisi_oge_sayisindan_bagimsiz` guard test.
    If there are no links at all, we don't even issue a query for that type (avoid
    producing an empty `IN ()`)."""
    cids = {r.client_id for r in rows if r.client_id}
    sids = {r.shoot_task_id for r in rows if r.shoot_task_id}
    aids = {r.ad_campaign_id for r in rows if r.ad_campaign_id}

    clients = dict(db.session.query(Client.id, Client.name)
                   .filter(Client.id.in_(cids)).all()) if cids else {}
    shoots = dict(db.session.query(ShootTask.id, ShootTask.title)
                  .filter(ShootTask.id.in_(sids)).all()) if sids else {}
    camps = dict(db.session.query(AdCampaign.id, AdCampaign.title)
                 .filter(AdCampaign.id.in_(aids)).all()) if aids else {}
    return clients, shoots, camps


def _items_payload(board, names):
    rows = (PlanningItem.query.filter_by(board_id=board.id)
            .order_by(PlanningItem.z.asc(), PlanningItem.id.asc()).all())
    clients, shoots, camps = _link_titles(rows)
    return [r.to_dict(assignee_name=names.get(r.assignee_sub),
                      updated_by_name=names.get(r.updated_by),
                      client_name=clients.get(r.client_id),
                      shoot_title=shoots.get(r.shoot_task_id),
                      campaign_title=camps.get(r.ad_campaign_id)) for r in rows]


# --- validation (all ValueError → 400) ----------------------------------

def _parse_date(value, field):
    if value in (None, ''):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f'{field} has an invalid date (expected YYYY-MM-DD)')


def _clean_text(value, field, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'{field} must be text')
    value = value.strip()
    if not value:
        return None
    if len(value) > limit:
        raise ValueError(f'{field} can be at most {limit} characters')
    return value


def _coord(value, field):
    if value in (None, ''):
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be a number')
    n = int(round(value))
    if abs(n) > COORD_LIMIT:
        raise ValueError(f'{field} is out of range')
    return n


def _dimension(value, field):
    if value in (None, ''):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be a number')
    n = int(round(value))
    if not (SIZE_MIN <= n <= SIZE_MAX):
        raise ValueError(f'{field} must be between {SIZE_MIN} and {SIZE_MAX}')
    return n


def _clean_link(value):
    """http(s) only. The old canvas never filtered the link field → it accepted
    `javascript:`; that hole is closed here."""
    link = _clean_text(value, 'link', LINK_MAX)
    if link and not link.lower().startswith(('http://', 'https://')):
        raise ValueError('invalid link (http/https expected)')
    return link


def _apply_item(item, data, user, names, creating=False):
    """Apply + validate the body onto the item. Fields not sent are LEFT ALONE
    (partial upsert)."""
    if creating or 'type' in data:
        kind = (data.get('type') or 'card').strip().lower()
        if kind not in ITEM_TYPES:
            raise ValueError(f'invalid item type: {kind}')
        item.type = kind
    if creating or 'title' in data:
        item.title = _clean_text(data.get('title'), 'title', TITLE_MAX)
    if creating or 'text' in data:
        item.text = _clean_text(data.get('text'), 'text', TEXT_MAX)
    if creating or 'label' in data:
        item.label = _clean_text(data.get('label'), 'label', LABEL_MAX)
    if creating or 'link' in data:
        item.link = _clean_link(data.get('link'))
    if creating or 'color' in data:
        color = _clean_text(data.get('color'), 'color', 16)
        if color and not COLOR_RE.match(color):
            raise ValueError('color must be in #rrggbb format')
        item.color = color
    if creating or 'x' in data:
        item.x = _coord(data.get('x'), 'x')
    if creating or 'y' in data:
        item.y = _coord(data.get('y'), 'y')
    if creating or 'width' in data:
        item.width = _dimension(data.get('width'), 'width')
    if creating or 'height' in data:
        item.height = _dimension(data.get('height'), 'height')
    if creating or 'z' in data:
        item.z = _coord(data.get('z'), 'z')
    if creating or 'from_key' in data:
        item.from_key = _clean_text(data.get('from_key'), 'from_key', 64)
    if creating or 'to_key' in data:
        item.to_key = _clean_text(data.get('to_key'), 'to_key', 64)
    if creating or 'status' in data:
        status = (data.get('status') or 'open').strip().lower()
        if status not in ITEM_STATUSES:
            raise ValueError(f'invalid status: {status}')
        item.status = status
    if creating or 'due_date' in data:
        item.due_date = _parse_date(data.get('due_date'), 'due date')
    if creating or 'assignee_sub' in data:
        sub = _clean_text(data.get('assignee_sub'), 'assignee', 64)
        if sub and sub not in names:
            raise ValueError('assignee not found')
        item.assignee_sub = sub
    # Domain links — all three follow the same pattern: empty → NULL, if filled the
    # record MUST exist. Doing this validation here makes a dangling reference
    # impossible, unlike stashing it in the `extra` jsonb.
    for field, model, label in (('client_id', Client, 'client'),
                                ('shoot_task_id', ShootTask, 'shoot task'),
                                ('ad_campaign_id', AdCampaign, 'ad campaign')):
        if not (creating or field in data):
            continue
        val = data.get(field)
        if val in (None, ''):
            setattr(item, field, None)
            continue
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError(f'{label} id must be a number')
        if db.session.get(model, val) is None:
            raise ValueError(f'{label} not found')
        setattr(item, field, val)
    if 'extra' in data:
        extra = data.get('extra')
        if extra is None:
            item.extra = None
        else:
            if not isinstance(extra, dict):
                raise ValueError('extra must be an object')
            # SHALLOW MERGE, not a replace: a client writing a single key shouldn't
            # wipe out the others (e.g. legacy_* keys coming from the migration). To
            # actually remove a key, send its value as null.
            merged = dict(item.extra or {})
            merged.update(extra)
            merged = {k: v for k, v in merged.items() if v is not None}
            if len(json.dumps(merged)) > MAX_EXTRA_BYTES:
                raise ValueError('extra is too large')
            item.extra = merged
    if item.type == 'edge' and not (item.from_key and item.to_key):
        raise ValueError('from_key and to_key are required for a connection')
    item.updated_by = user.get('sub')
    if creating:
        item.created_by = user.get('sub')
    return item


# --- endpoints ---------------------------------------------------------------

@bp.get('/linkables')
def linkables():
    """Domain records that a card can be linked to — ONE endpoint, ONE role gate.

    Why we don't hit four separate endpoints: their authorization rules differ
    (`/api/clients` is open to every panel role, `/api/sharing/shoot-plan` is
    management+videographer only, `/api/ads` is management only), and on top of that
    `shoot-plan` **requires week_iso**, so a week-independent search isn't possible
    there. If the frontend hit four endpoints it would have to handle a 403 per role.

    A section the user has no access to returns an **empty array**, NOT a 403: the
    panel only renders non-empty sections, so the role difference resolves itself."""
    u = current_user()
    if not u:
        return jsonify(error='not authenticated'), 401
    role = u.get('role')
    if role not in PANEL_ROLES:
        return jsonify(error='you do not have access to this page'), 403

    q = (request.args.get('q') or '').strip()
    like = f'%{q}%' if q else None

    cq = Client.query.filter(Client.status == 'active')
    if like:
        cq = cq.filter(Client.name.ilike(like))
    clients = [{'id': c.id, 'name': c.name}
               for c in cq.order_by(Client.name.asc()).limit(LINKABLE_LIMIT).all()]

    # users: `/api/users` also returns the `pending` role, which has NO access to
    # any board; it's filtered to PANEL_ROLES here so a card can't be assigned to
    # someone without access.
    uq = UserRef.query.filter(UserRef.role.in_(PANEL_ROLES))
    if like:
        uq = uq.filter(UserRef.name.ilike(like))
    users = [{'sub': r.sub, 'name': r.name or r.email, 'role': r.role}
             for r in uq.order_by(UserRef.name.asc()).limit(LINKABLE_LIMIT).all()]

    shoots = []
    if role in ('management', 'videographer'):
        sq = db.session.query(ShootTask, Client.name).outerjoin(
            Client, Client.id == ShootTask.client_id)
        if like:
            sq = sq.filter(ShootTask.title.ilike(like))
        shoots = [{'id': t.id, 'title': t.title,
                   'scheduled_date': t.scheduled_date.isoformat() if t.scheduled_date else None,
                   'client_name': cname}
                  for t, cname in sq.order_by(ShootTask.scheduled_date.desc().nullslast(),
                                              ShootTask.id.desc())
                                    .limit(LINKABLE_LIMIT).all()]

    campaigns = []
    if role == 'management':   # financial info — ads.py is also management-only
        aq = AdCampaign.query.filter(AdCampaign.deleted_at.is_(None))
        if like:
            aq = aq.filter(AdCampaign.title.ilike(like))
        campaigns = [{'id': c.id, 'title': c.title, 'platform': c.platform,
                      'client_name': c.client.name if c.client else None}
                     for c in aq.order_by(AdCampaign.start_date.desc())
                                .limit(LINKABLE_LIMIT).all()]

    return jsonify(clients=clients, users=users,
                   shoot_tasks=shoots, ad_campaigns=campaigns)


@bp.get('/assigned')
def assigned_items():
    """Cards assigned to a person — CROSSES BOARD BOUNDARIES, read-only.

    DELIBERATE AUTHORIZATION GAP (2026-07-26 decision, approved by the project
    owner): `_board_access` says 'a designer can't see the management board', but
    this endpoint also returns a card from the management board to the person it's
    assigned to — title/status/due date/client leak. Rationale: otherwise a manager
    assigning a card to an employee would be completely invisible to that employee,
    i.e. the assignment feature would be dead. The leaked field set is deliberately
    narrow: card body (`text`), color, position and `extra` are NOT returned.
    There's NO write path — editing only happens from the card's own board.

    If `?assignee_sub=` isn't given, the caller themselves is assumed. An employee
    can't request someone else's assignments (403); management can query anyone."""
    u = current_user()
    if not u:
        return jsonify(error='not authenticated'), 401
    role = u.get('role')
    if role not in PANEL_ROLES:
        return jsonify(error='you do not have access to this page'), 403

    me = str(u.get('sub'))
    target = (request.args.get('assignee_sub') or me).strip()
    if target != me and role != 'management':
        return jsonify(error='you can only view your own assignments'), 403

    rows = (db.session.query(PlanningItem, PlanningBoard)
            .join(PlanningBoard, PlanningBoard.id == PlanningItem.board_id)
            .filter(PlanningItem.assignee_sub == target,
                    PlanningItem.type != 'edge'))
    status = (request.args.get('status') or '').strip()
    if status in ITEM_STATUSES:
        rows = rows.filter(PlanningItem.status == status)
    rows = rows.order_by(PlanningItem.due_date.asc().nullslast(),
                         PlanningItem.id.asc()).limit(ASSIGNED_LIMIT).all()

    names = _user_names()
    clients, shoots, camps = _link_titles([it for it, _ in rows])
    return jsonify(items=[{
        'board_key': b.board_key, 'board_title': _board_title(b, names),
        'item_key': it.item_key, 'type': it.type, 'title': it.title,
        'label': it.label, 'status': it.status,
        'due_date': it.due_date.isoformat() if it.due_date else None,
        'client_id': it.client_id, 'client_name': clients.get(it.client_id),
        'shoot_title': shoots.get(it.shoot_task_id),
        'campaign_title': camps.get(it.ad_campaign_id),
    } for it, b in rows])


@bp.get('/boards')
def boards_list():
    """Accessible boards. management → management + all panel-role users;
    employee → ONLY themselves (single element) → the panel never renders a
    selector. So hiding the dropdown is derived from the API, not just a frontend
    decision."""
    u = current_user()
    if not u:
        return jsonify(error='not authenticated'), 401
    role = u.get('role')
    if role not in PANEL_ROLES:
        return jsonify(error='you do not have access to this page'), 403

    names = _user_names()
    if role == 'management':
        wanted = [MANAGEMENT_KEY]
        people = (db.session.query(UserRef.sub, UserRef.name, UserRef.email, UserRef.role)
                  .filter(UserRef.role.in_(PANEL_ROLES)).all())
        for p in sorted(people, key=lambda p: (p.name or p.email or '').casefold()):
            wanted.append(f'user:{p.sub}')
    else:
        wanted = [f'user:{u.get("sub")}']

    rows = PlanningBoard.query.filter(PlanningBoard.board_key.in_(wanted)).all()
    by_key = {b.board_key: b for b in rows}
    counts = dict(db.session.query(PlanningItem.board_id, func.count(PlanningItem.id))
                  .group_by(PlanningItem.board_id).all())

    out = []
    for key in wanted:
        board = by_key.get(key)
        if board is None:           # never-opened board — virtual, shows empty on the page
            owner = None if key == MANAGEMENT_KEY else key.split(':', 1)[1]
            out.append({'key': key, 'kind': 'management' if owner is None else 'user',
                        'owner_sub': owner, 'version': 0, 'updated_at': None,
                        'last_modified_by': None, 'last_modified_name': None,
                        'item_count': 0,
                        'title': 'Management Board' if owner is None
                                 else (names.get(owner) or f'#{owner}')})
            continue
        out.append(board.to_dict(title=_board_title(board, names),
                                 item_count=counts.get(board.id, 0),
                                 last_modified_name=names.get(board.last_modified_by)))
    return jsonify(boards=out)


@bp.get('/boards/<string:board_key>/version')
def board_version(board_key):
    """Cheap poll (~150 bytes). The panel calls this periodically; if the version
    changed, it refetches the whole board. Polling the full board would mean a
    600-item body."""
    _, err = _board_access(board_key)
    if err:
        return err
    board = PlanningBoard.query.filter_by(board_key=board_key).first()
    if board is None:
        return jsonify(version=0, updated_at=None, last_modified_by=None,
                       last_modified_name=None, item_count=0)
    names = _user_names()
    count = (db.session.query(func.count(PlanningItem.id))
             .filter(PlanningItem.board_id == board.id).scalar() or 0)
    return jsonify(version=board.version, updated_at=board.updated_at.isoformat()
                   if board.updated_at else None,
                   last_modified_by=board.last_modified_by,
                   last_modified_name=names.get(board.last_modified_by),
                   item_count=count)


@bp.get('/boards/<string:board_key>')
def board_get(board_key):
    """Board + all its items. If the board doesn't exist it's created lazily (old
    canvas_get behavior)."""
    u, err = _board_access(board_key)
    if err:
        return err
    board = _get_or_create_board(board_key, u)
    names = _user_names()
    items = _items_payload(board, names)
    return jsonify(board=board.to_dict(title=_board_title(board, names),
                                       item_count=len(items),
                                       last_modified_name=names.get(board.last_modified_by)),
                   items=items)


@bp.patch('/boards/<string:board_key>/items')
def board_items_patch(board_key):
    """Delta write: {base_version, upsert:[...], delete:[...]}.

    Response: {board, applied:[...], conflicts:[item_key], stale:bool, items:[...]|None}
    If `stale` is true, `items` is the full list (client reconciles in one round trip)."""
    u, err = _board_access(board_key)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    upsert = data.get('upsert') or []
    delete = data.get('delete') or []
    if not isinstance(upsert, list) or not isinstance(delete, list):
        return jsonify(error='upsert and delete must be lists'), 400
    if len(upsert) > MAX_BATCH or len(delete) > MAX_BATCH:
        return jsonify(error=f'at most {MAX_BATCH} items can be processed per request'), 400

    board = _get_or_create_board(board_key, u)
    # Queues up two concurrent PATCHes on the same board (Postgres; sqlite ignores it).
    locked = (PlanningBoard.query.filter_by(id=board.id)
              .with_for_update().first()) or board

    names = _user_names()
    existing = {i.item_key: i for i in PlanningItem.query.filter_by(board_id=board.id).all()}

    base_version = data.get('base_version')
    stale = isinstance(base_version, int) and base_version < locked.version

    applied, conflicts = [], []
    try:
        for payload in delete:
            key = payload if isinstance(payload, str) else None
            if key and key in existing:
                db.session.delete(existing.pop(key))

        for payload in upsert:
            if not isinstance(payload, dict):
                raise ValueError('item must be an object')
            key = payload.get('item_key')
            if not isinstance(key, str) or not ITEM_KEY_RE.match(key):
                raise ValueError('invalid item_key')
            item = existing.get(key)
            creating = item is None
            if creating:
                if len(existing) >= MAX_ITEMS_PER_BOARD:
                    raise ValueError(f'board can hold at most {MAX_ITEMS_PER_BOARD} items')
                item = PlanningItem(board_id=board.id, item_key=key)
                db.session.add(item)
                existing[key] = item
            else:
                client_rev = payload.get('rev')
                if isinstance(client_rev, int) and client_rev < (item.rev or 1):
                    conflicts.append(key)      # last writer wins, panel warns
                item.rev = (item.rev or 1) + 1
            _apply_item(item, payload, u, names, creating=creating)
            applied.append(item)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400

    locked.version = (locked.version or 1) + 1
    locked.updated_at = utcnow()
    locked.last_modified_by = u.get('sub')
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error='item key conflict, refresh the board'), 409

    # The board owner needs to know about a change SOMEONE ELSE (management) made
    # on their board (2026-08-05). No notification for an employee on their own
    # board; the management board has no owner → skipped. Several PATCHes per
    # second can arrive during dragging → 15 min coalesce
    # (notifications.COALESCE_MINUTES).
    if locked.owner_sub and locked.owner_sub != str(u.get('sub')) and (applied or delete):
        try:
            notifications.notify_planning_changed(
                locked.owner_sub, names.get(str(u.get('sub'))) or 'A manager')
        except Exception:  # noqa: BLE001 — notification is best-effort, the write is critical
            log.exception('planlama bildirimi başarısız (board=%s)', locked.id)

    return jsonify(
        board=locked.to_dict(title=_board_title(locked, names),
                             last_modified_name=names.get(locked.last_modified_by)),
        applied=[i.to_dict(assignee_name=names.get(i.assignee_sub),
                           updated_by_name=names.get(i.updated_by)) for i in applied],
        conflicts=conflicts,
        stale=bool(stale),
        items=_items_payload(locked, names) if stale else None,
    )


# --- board images (2026-07-28) ----------------------------------------
# An image pasted/dragged onto the board is stored ON THE SERVER
# (`planning_images`), the item points to it via `type='image'` +
# `extra.image={name,w,h}`.
#
# Authorization comes for free: since files live in a directory per board, both
# endpoints go through `_board_access` — even knowing the name, it's not possible
# to read another board's image (the path is built from the board directory, not
# from the request).

@bp.post('/boards/<board_key>/images')
def image_upload(board_key):
    """Upload an image → `{name, width, height, size}`. The panel writes the item
    via PATCH.

    Upload and item write are DELIBERATELY separate: the file goes out at the
    moment of pasting, the item is created through the normal delta flow (offline
    queue, undo, conflict resolution) — merging them into one endpoint would
    bypass that machinery."""
    u, err = _board_access(board_key)
    if err:
        return err
    board = _get_or_create_board(board_key, u)
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify(error='no file provided'), 400
    try:
        meta = planning_images.store(board.id, f.read())
    except planning_images.ImageError as e:
        return jsonify(error=str(e)), 400
    return jsonify(image=meta), 201


@bp.get('/boards/<board_key>/images/<name>')
def image_serve(board_key, name):
    """Serve the image (session-authenticated). `send_file` conditional → cheap
    via 304."""
    _, err = _board_access(board_key)
    if err:
        return err
    board = PlanningBoard.query.filter_by(board_key=board_key).first()
    if board is None:
        return jsonify(error='board not found'), 404
    path = planning_images.path_of(board.id, name)
    if not path:
        return jsonify(error='image not found'), 404
    # max_age is long: the name isn't content-addressed but it is a uuid → same
    # name always means the same file.
    return send_file(path, conditional=True, max_age=31536000)
