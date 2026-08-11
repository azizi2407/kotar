"""Client Tracking — `/api/client-tracking/*` [Blueprint: /api/client-tracking].
management ONLY (commercial/sales info).

Tracks, per client, the work items the agency can sell to them (trademark
registration, catalog, website, custom project...). Three parts:

  * `tracking_items`          — MANAGEABLE item catalog (add/reorder from the panel)
  * `client_tracking_entries` — client × item status cell (upsert, NO soft-delete)
  * `client_activity_notes`   — dated free-text activity log ("what was last done")

In addition, the list endpoint DERIVES three signals from EXISTING data (not
entered manually, not writable from any endpoint): last ad (`ad_campaigns`),
last/next shoot (`shoot_tasks`), responsible team (`client_team_assignments`).

LIST ENDPOINT INVARIANT: query count is INDEPENDENT of client count (fixed ~9).
There is no lazy access anywhere like `client.ad_campaigns` / `entry.item` — it's
all bulk queries + merging in Python. `tests/test_musteri_takip.py::test_list_query_count_*`
enforces this by counting queries; adding lazy access to the serializer breaks that test.

Warning: if this page is ever opened up beyond management: the detail endpoint
returns campaign info. The financial field (`amount_spent`) was DELIBERATELY left
out — review before opening it up.

CSRF is shared via `api.csrf_protect` (the ads.py/sharing.py pattern).
"""
import datetime as dt
from collections import defaultdict

from flask import Blueprint, jsonify, request
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError

from api import csrf_protect
from extensions import db
from models import (Client, ClientActivityNote, ClientTeamAssignment, ClientTrackingEntry,
                    AdCampaign, TrackingItem, UserRef, utcnow)
from models_sharing import ShootTask
from sso_client import current_user

bp = Blueprint('client_tracking', __name__)
bp.before_request(csrf_protect)  # same CSRF as api (session token)

# Catalog seed — the agency's typical sales items. Order = order shown in the panel.
# (key, name, category, lucide icon name). `key` is only populated by the seed, an idempotency key.
SEED_ITEMS = (
    ('marka_tescili',         'Trademark Registration',  'hukuki',  'ShieldCheck'),
    ('logo_kurumsal_kimlik',  'Logo / Corporate Identity', 'tasarim', 'Palette'),
    ('web_sitesi',            'Website',                 'dijital', 'Globe'),
    ('e_ticaret',             'E-Commerce Site',         'dijital', 'ShoppingCart'),
    ('google_isletme',        'Google Business Profile', 'dijital', 'MapPin'),
    ('sosyal_medya_yonetimi', 'Social Media Management', 'dijital', 'Share2'),
    ('reklam_yonetimi',       'Ad Management',           'reklam',  'Megaphone'),
    ('katalog',               'Catalog',                 'tasarim', 'BookOpen'),
    ('matbaa_baski',          'Printing',                'uretim',  'Printer'),
    ('fotograf_cekimi',       'Photo Shoot',             'uretim',  'Camera'),
    ('video_cekimi',          'Video Shoot',             'uretim',  'Clapperboard'),
    ('ozel_proje',            'Custom Project',          'diger',   'Sparkles'),
)


def _require_management():
    """Returns (user, err) — tracking data is management-only."""
    u = current_user()
    if not u:
        return None, (jsonify(error='no active session'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='this page is management only'), 403)
    return u, None


def _ensure_seed_items():
    """Seed the catalog on first use.

    Only runs when the table is COMPLETELY empty — it doesn't check keys one by
    one, because then an item the user deliberately deleted would come back on
    every request. There can be a race under multi-worker gunicorn: since `key` is
    unique, the second INSERT raises IntegrityError and it's swallowed (another
    worker already seeded it)."""
    if db.session.query(TrackingItem.id).first() is not None:
        return
    for i, (key, name, category, icon) in enumerate(SEED_ITEMS):
        db.session.add(TrackingItem(key=key, name=name, category=category,
                                    icon=icon, position=i, active=True))
    try:
        db.session.commit()
    except IntegrityError:      # another worker seeded it at the same time — fine
        db.session.rollback()


# --- Validation helpers (ValueError → caller converts to 400) ---

def _parse_date(value, field, required=False):
    """'YYYY-MM-DD' → date. Raises ValueError if invalid."""
    if value in (None, ''):
        if required:
            raise ValueError(f'{field} is required')
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f'{field} is not a valid date (expected YYYY-MM-DD)')


def _clean_text(value, field, required=False, limit=None):
    text_value = (value or '').strip() if isinstance(value, str) else ''
    if not text_value:
        if required:
            raise ValueError(f'{field} is required')
        return None
    if limit and len(text_value) > limit:
        raise ValueError(f'{field} can be at most {limit} characters')
    return text_value


# Turkish-aware comparison table (backend counterpart of the frontend `lib/week.ts` trFold).
# Python's casefold() turns 'İ' into i+U+0307 → 'web SİTESİ' doesn't match 'Web Sitesi'.
# So Turkish letters are folded first, then lower() is applied.
_TR_FOLD = str.maketrans({
    'İ': 'i', 'I': 'i', 'ı': 'i', 'Ş': 's', 'ş': 's', 'Ğ': 'g', 'ğ': 'g',
    'Ü': 'u', 'ü': 'u', 'Ö': 'o', 'ö': 'o', 'Ç': 'c', 'ç': 'c',
})


def _fold(value):
    """Normalized key for item-name uniqueness."""
    return (value or '').translate(_TR_FOLD).lower()


def _clean_url(value):
    url = _clean_text(value, 'link', limit=1024)
    if url and not url.lower().startswith(('http://', 'https://')):
        raise ValueError('invalid link (expected http/https)')
    return url


def _live_item(item_id):
    """Fetch a non-deleted item; raises ValueError if not found."""
    item = TrackingItem.query.filter_by(id=item_id, deleted_at=None).first()
    if item is None:
        raise ValueError('item not found')
    return item


def _live_client(client_id):
    """Fetch the active client; raises ValueError if not found (ads.py pattern)."""
    client = db.session.get(Client, client_id or 0)
    if client is None or client.status != 'active':
        raise ValueError('select a valid client')
    return client


def _apply_item(item, data, user, creating=False):
    """Validate + apply a catalog item. Name uniqueness is enforced here with the
    Turkish-aware _fold() (there's no partial unique index in the DB — we want to avoid dialect divergence)."""
    if creating or 'name' in data:
        name = _clean_text(data.get('name'), 'item name', required=True, limit=120)
        clash = (TrackingItem.query.filter(TrackingItem.deleted_at.is_(None))
                 .filter(TrackingItem.id != (item.id or 0)).all())
        if any(_fold(c.name) == _fold(name) for c in clash):
            raise ValueError('an item with this name already exists')
        item.name = name
    if creating or 'category' in data:
        category = (data.get('category') or 'diger').strip().lower()
        if category not in TrackingItem.CATEGORIES:
            raise ValueError(f'invalid category: {category}')
        item.category = category
    if creating or 'icon' in data:
        item.icon = _clean_text(data.get('icon'), 'ikon', limit=40)
    if creating or 'active' in data:
        item.active = bool(data.get('active', True))
    if creating:
        item.created_by = user.get('sub')
        top = db.session.query(func.max(TrackingItem.position)).scalar()
        item.position = (top if top is not None else -1) + 1
    item.updated_by = user.get('sub')
    return item


def _apply_entry(entry, data, user, creating=False):
    """Validate + apply a status cell."""
    if creating or 'status' in data:
        status = (data.get('status') or 'yok').strip().lower()
        if status not in ClientTrackingEntry.STATUSES:
            raise ValueError(f'invalid status: {status}')
        entry.status = status
    if creating or 'status_date' in data:
        entry.status_date = _parse_date(data.get('status_date'), 'date')
    if creating or 'note' in data:
        entry.note = _clean_text(data.get('note'), 'note')
    if creating or 'url' in data:
        entry.url = _clean_url(data.get('url'))
    if creating:
        entry.created_by = user.get('sub')
    entry.updated_by = user.get('sub')
    return entry


def _apply_note(note, data, user, creating=False):
    """Validate + apply an activity note."""
    if creating or 'text' in data:
        note.text = _clean_text(data.get('text'), 'note text', required=True)
    if creating or 'happened_on' in data:
        note.happened_on = _parse_date(data.get('happened_on'), 'date') or dt.date.today()
    if creating or 'item_id' in data:
        item_id = data.get('item_id')
        note.item_id = _live_item(item_id).id if item_id else None
    if creating:
        note.created_by = user.get('sub')
    note.updated_by = user.get('sub')
    return note


# --- Derived signals (bulk queries; each a single GROUP BY) ---

def _ad_signals(client_ids, today):
    """{client_id: (last_date, count, active_flag)} — one GROUP BY.

    `last_date` = the latest day the ad was ACTUALLY live, and NEVER EXCEEDS today:
      * not started yet (start_date > today)    → not counted (the ad hasn't run)
      * ongoing / ends in the future             → today ("still live")
      * ended                                    → end_date
      * no end date and already started          → today
    Raw MAX(COALESCE(end_date, start_date)) is not used: a campaign ending in the
    future would show a FUTURE date like "last ad Jul 27" (seen in live data,
    2026-07-25). Capping at today reads correctly and also keeps the staleness
    calculation (daysSince) consistent. CASE is used because LEAST/MIN semantics
    diverge between Postgres and sqlite."""
    effective_day = case(
        (AdCampaign.start_date > today, None),          # not live yet
        (AdCampaign.end_date.is_(None), today),         # ongoing
        (AdCampaign.end_date > today, today),           # ends in the future → still live
        else_=AdCampaign.end_date,
    )
    rows = (db.session.query(
        AdCampaign.client_id,
        func.max(effective_day),
        func.count(AdCampaign.id),
        func.max(case((and_(AdCampaign.status == 'active',
                            or_(AdCampaign.end_date.is_(None),
                                AdCampaign.end_date >= today)), 1), else_=0)),
    ).filter(AdCampaign.deleted_at.is_(None), AdCampaign.client_id.in_(client_ids))
     .group_by(AdCampaign.client_id).all())
    return {r[0]: (r[1], r[2] or 0, bool(r[3])) for r in rows}


def _shoot_signals(client_ids, today):
    """{client_id: (last_past_date, next_future_date)} — one GROUP BY.

    The `status='completed'` filter is DELIBERATELY not used: this field isn't
    marked in practice (2026-07-25 live data: 3 of 76 records were completed). So
    "last shoot" = the most recent plan whose date has passed, "next shoot" = the
    nearest plan in the future."""
    rows = (db.session.query(
        ShootTask.client_id,
        func.max(case((ShootTask.scheduled_date <= today, ShootTask.scheduled_date))),
        func.min(case((ShootTask.scheduled_date > today, ShootTask.scheduled_date))),
    ).filter(ShootTask.client_id.in_(client_ids), ShootTask.scheduled_date.isnot(None))
     .group_by(ShootTask.client_id).all())
    return {r[0]: (r[1], r[2]) for r in rows}


def _team_signals(client_ids):
    """{client_id: [{role_slot, user_id, name}]} — two queries (assignments + name table)."""
    rows = (ClientTeamAssignment.query
            .filter(ClientTeamAssignment.client_id.in_(client_ids)).all())
    names = {u.sub: (u.name or u.email)
             for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}
    out = defaultdict(list)
    for r in rows:
        out[r.client_id].append({'role_slot': r.role_slot, 'user_id': r.user_id,
                                 'name': names.get(r.user_id)})
    return out


def _last_notes(client_ids):
    """{client_id: note_dict} — the LATEST note per client, one query (window function).
    ROW_NUMBER() works on Postgres and in the test sqlite (>=3.25)."""
    rn = func.row_number().over(
        partition_by=ClientActivityNote.client_id,
        order_by=(ClientActivityNote.happened_on.desc(), ClientActivityNote.id.desc()),
    ).label('rn')
    sub = (db.session.query(
        ClientActivityNote.id, ClientActivityNote.client_id,
        ClientActivityNote.happened_on, ClientActivityNote.text,
        ClientActivityNote.item_id, ClientActivityNote.created_by, rn)
        .filter(ClientActivityNote.deleted_at.is_(None),
                ClientActivityNote.client_id.in_(client_ids)).subquery())
    rows = db.session.query(sub).filter(sub.c.rn == 1).all()
    if not rows:
        return {}
    names = {u.sub: (u.name or u.email)
             for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}
    return {r.client_id: {
        'id': r.id, 'client_id': r.client_id, 'item_id': r.item_id,
        'happened_on': r.happened_on.isoformat() if r.happened_on else None,
        'text': r.text, 'created_by': r.created_by,
        'author_name': names.get(r.created_by),
    } for r in rows}


def _summary(entries_by_item, active_item_ids):
    """Status counts + `opportunity`: among ACTIVE items, the count of those whose
    status is 'yok' (none) OR that have no record at all = "how many items could
    be sold to this client". The page's business value is in this number; the
    list can be sorted by this column."""
    counts = {s: 0 for s in ClientTrackingEntry.STATUSES}
    opportunity = 0
    for item_id in active_item_ids:
        entry = entries_by_item.get(str(item_id))
        status = entry['status'] if entry else 'yok'
        counts[status] = counts.get(status, 0) + 1
        if status == 'yok':
            opportunity += 1
    # records on inactive (archived) items count toward the total but not toward opportunity
    for key, entry in entries_by_item.items():
        if int(key) not in active_item_ids:
            counts[entry['status']] = counts.get(entry['status'], 0) + 1
    counts['opportunity'] = opportunity
    return counts


# --- Endpoints ---

@bp.get('')
@bp.get('/')
def tracking_list():
    """Tracking list: catalog + client rows (status cells, derived signals, last
    note, summary). Query count is independent of client count."""
    _, err = _require_management()
    if err:
        return err
    _ensure_seed_items()
    today = dt.date.today()

    # (1) clients — active only, only the needed columns
    cq = (db.session.query(Client.id, Client.name, Client.sector)
          .filter(Client.status == 'active'))
    term = (request.args.get('q') or '').strip()
    if term:
        cq = cq.filter(Client.name.ilike(f'%{term}%'))
    clients = cq.order_by(Client.name.asc()).all()

    # (2) catalog — non-deleted items (inactive ones are also returned, the panel flags them)
    items = (TrackingItem.query.filter(TrackingItem.deleted_at.is_(None))
             .order_by(TrackingItem.position.asc(), TrackingItem.id.asc()).all())
    active_item_ids = {i.id for i in items if i.active}

    if not clients:
        return jsonify(items=[i.to_dict() for i in items], clients=[])
    ids = [c.id for c in clients]

    # (3) all status cells — one query, group by client in Python
    entries = defaultdict(dict)
    for e in ClientTrackingEntry.query.filter(ClientTrackingEntry.client_id.in_(ids)).all():
        # KEY IS A STRING: JSON object keys become strings anyway; the frontend
        # must access via `entries[String(item.id)]` (entries[item.id] blows up at runtime).
        entries[e.client_id][str(e.item_id)] = e.to_dict()

    # (4-7) derived signals — each bulk
    ads = _ad_signals(ids, today)
    shoots = _shoot_signals(ids, today)
    teams = _team_signals(ids)
    notes = _last_notes(ids)

    rows = []
    for c in clients:
        last_ad, ad_count, ad_active = ads.get(c.id, (None, 0, False))
        last_shoot, next_shoot = shoots.get(c.id, (None, None))
        client_entries = entries.get(c.id, {})
        rows.append({
            'client_id': c.id, 'client_name': c.name, 'sector': c.sector,
            'entries': client_entries,
            'signals': {
                'last_ad_date': last_ad.isoformat() if last_ad else None,
                'ad_active': ad_active, 'ad_count': ad_count,
                'last_shoot_date': last_shoot.isoformat() if last_shoot else None,
                'next_shoot_date': next_shoot.isoformat() if next_shoot else None,
                'team': teams.get(c.id, []),
            },
            'last_note': notes.get(c.id),
            'summary': _summary(client_entries, active_item_ids),
        })
    return jsonify(items=[i.to_dict() for i in items], clients=rows)


@bp.get('/clients/<int:client_id>')
def tracking_detail(client_id):
    """Client detail: all activity notes + recent campaigns + last/next shoots.
    Fetched when expand is opened. `amount_spent` is DELIBERATELY absent from
    campaigns — so financial data doesn't leak if this endpoint is opened to another role later."""
    _, err = _require_management()
    if err:
        return err
    client = db.session.get(Client, client_id)
    if client is None:
        return jsonify(error='client not found'), 404

    note_rows = (ClientActivityNote.query
                 .filter_by(client_id=client_id, deleted_at=None)
                 .order_by(ClientActivityNote.happened_on.desc(),
                           ClientActivityNote.id.desc()).limit(100).all())
    names = {u.sub: (u.name or u.email)
             for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}

    campaigns = (AdCampaign.query.filter_by(client_id=client_id, deleted_at=None)
                 .order_by(AdCampaign.start_date.desc(), AdCampaign.id.desc()).limit(5).all())
    shoots = (ShootTask.query.filter(ShootTask.client_id == client_id,
                                     ShootTask.scheduled_date.isnot(None))
              .order_by(ShootTask.scheduled_date.desc()).limit(5).all())

    return jsonify(
        client={'id': client.id, 'name': client.name, 'sector': client.sector,
                'client_email': client.client_email, 'instagram_url': client.instagram_url},
        notes=[n.to_dict(author_name=names.get(n.created_by)) for n in note_rows],
        ads=[{'id': a.id, 'title': a.title, 'platform': a.platform, 'status': a.status,
              'start_date': a.start_date.isoformat() if a.start_date else None,
              'end_date': a.end_date.isoformat() if a.end_date else None} for a in campaigns],
        shoots=[{'id': s.id, 'title': s.title, 'status': s.status,
                 'scheduled_date': s.scheduled_date.isoformat()} for s in shoots],
    )


@bp.put('/clients/<int:client_id>/entries/<int:item_id>')
def entry_upsert(client_id, item_id):
    """Write a status cell — UPSERT. Since the resource's identity is (client_id,
    item_id), it's PUT: calling it again with the same body gives the same result, no row duplication."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        _live_client(client_id)
        _live_item(item_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    entry = ClientTrackingEntry.query.filter_by(client_id=client_id, item_id=item_id).first()
    creating = entry is None
    if creating:
        entry = ClientTrackingEntry(client_id=client_id, item_id=item_id)
    try:
        _apply_entry(entry, data, u, creating=creating)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    if creating:
        db.session.add(entry)
    db.session.commit()
    return jsonify(entry=entry.to_dict())


@bp.post('/clients/<int:client_id>/notes')
def note_create(client_id):
    u, err = _require_management()
    if err:
        return err
    note = ClientActivityNote(client_id=client_id)
    try:
        _live_client(client_id)
        _apply_note(note, request.get_json(silent=True) or {}, u, creating=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db.session.add(note)
    db.session.commit()
    return jsonify(note=note.to_dict()), 201


@bp.patch('/notes/<int:note_id>')
def note_update(note_id):
    u, err = _require_management()
    if err:
        return err
    note = ClientActivityNote.query.filter_by(id=note_id, deleted_at=None).first()
    if note is None:
        return jsonify(error='note not found'), 404
    try:
        _apply_note(note, request.get_json(silent=True) or {}, u)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(note=note.to_dict())


@bp.delete('/notes/<int:note_id>')
def note_delete(note_id):
    """Soft-delete — the log history is preserved, it just drops out of lists."""
    u, err = _require_management()
    if err:
        return err
    note = ClientActivityNote.query.filter_by(id=note_id, deleted_at=None).first()
    if note is None:
        return jsonify(error='note not found'), 404
    note.deleted_at = utcnow()
    note.updated_by = u.get('sub')
    db.session.commit()
    return jsonify(ok=True)


@bp.get('/items')
def items_list():
    _, err = _require_management()
    if err:
        return err
    _ensure_seed_items()
    rows = (TrackingItem.query.filter(TrackingItem.deleted_at.is_(None))
            .order_by(TrackingItem.position.asc(), TrackingItem.id.asc()).all())
    return jsonify(items=[i.to_dict() for i in rows])


@bp.post('/items')
def items_create():
    u, err = _require_management()
    if err:
        return err
    item = TrackingItem()
    try:
        _apply_item(item, request.get_json(silent=True) or {}, u, creating=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db.session.add(item)
    db.session.commit()
    return jsonify(item=item.to_dict()), 201


@bp.patch('/items/<int:item_id>')
def items_update(item_id):
    u, err = _require_management()
    if err:
        return err
    item = TrackingItem.query.filter_by(id=item_id, deleted_at=None).first()
    if item is None:
        return jsonify(error='item not found'), 404
    try:
        _apply_item(item, request.get_json(silent=True) or {}, u)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(item=item.to_dict())


@bp.delete('/items/<int:item_id>')
def items_delete(item_id):
    """Soft-delete. Client records (entries) stay in the DB but don't show in the
    list — if the intent is to hide an item rather than delete it, use `active=false` instead."""
    u, err = _require_management()
    if err:
        return err
    item = TrackingItem.query.filter_by(id=item_id, deleted_at=None).first()
    if item is None:
        return jsonify(error='item not found'), 404
    item.deleted_at = utcnow()
    item.updated_by = u.get('sub')
    db.session.commit()
    return jsonify(ok=True)


@bp.post('/items/reorder')
def items_reorder():
    """Write the catalog order in bulk: {order: [id, id, ...]}."""
    u, err = _require_management()
    if err:
        return err
    order = (request.get_json(silent=True) or {}).get('order')
    if not isinstance(order, list) or not order:
        return jsonify(error='order list is required'), 400
    rows = {i.id: i for i in TrackingItem.query.filter(TrackingItem.deleted_at.is_(None)).all()}
    unknown = [i for i in order if i not in rows]
    if unknown:
        return jsonify(error=f'unknown item: {unknown[0]}'), 400
    for position, item_id in enumerate(order):
        rows[item_id].position = position
        rows[item_id].updated_by = u.get('sub')
    db.session.commit()
    out = sorted(rows.values(), key=lambda i: (i.position, i.id))
    return jsonify(ok=True, items=[i.to_dict() for i in out])
