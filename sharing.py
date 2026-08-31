"""Sharing Board API (/api/sharing) — Phase 2a core.

Management board: weekly card matrix (client row + sharing cards + special
day cards + priority). shares is the authoritative model. Writes are
management-only; CSRF is shared with the api blueprint. Drive thumbnails/
counts are wired up in Phase 2b.
"""
import io
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
import uuid
from datetime import date, timedelta

from flask import (Blueprint, Response, has_request_context, jsonify, request,
                   send_file, session)

import ai_context
import drive_gateway as dg
import jobqueue
import media_store
import notifications
import revision_match
from api import csrf_protect
from client_provision import _folder_link
from extensions import db
from models import AppSetting, Client, ClientWeekFolder, Job, utcnow
from models_sharing import (CardUpload, ClientApprovalLink, ClientPriority,
                            DriveThumbnail, ImageGeneration,
                            PreApprovalLink, ReviewExcludedUpload,
                            UploadPreApproval, UploadReview,
                            ReviewLink, REVISION_KINDS, RevisionRequest, Share, SHARE_KINDS,
                            ShootTask, SpecialCardStatus, SpecialDayEvent,
                            SpecialDaySelection, VideographerBusinessMark,
                            VideographerIdea, VideographerPhoto, WeeklyBrief)
from sso_client import current_user, is_superadmin

bp = Blueprint('sharing', __name__)
bp.before_request(csrf_protect)  # same CSRF as api (session token)

log = logging.getLogger(__name__)

# Drive file-count in-memory cache. TTL 60s.
_count_cache = {}       # (client_id, week_iso) -> (count, ts)  [single endpoint]
_counts_cache = {}      # week_iso -> ({client_id: count}, ts)  [batch endpoint]
_COUNT_TTL = 60


def _week_number(week_iso):
    try:
        return int(week_iso.split('-W')[1])
    except (IndexError, ValueError, AttributeError):
        return None

# TR day names (for published_day_name) — Monday=0
TR_DAYS = ['PAZARTESİ', 'SALI', 'ÇARŞAMBA', 'PERŞEMBE', 'CUMA', 'CUMARTESİ', 'PAZAR']


def _require_management():
    u = current_user()
    if not u:
        return None, (jsonify(error='not signed in'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='not authorized for this action'), 403)
    return u, None


def _require_designer_or_management():
    """The relaxed twin of `_require_management`: designer also passes.

    The designer already sees ALL clients' cards on the board and has full
    action rights there; the client media page is a continuation of the same
    surface."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not signed in'), 401)
    if u.get('role') not in ('management', 'designer'):
        return None, (jsonify(error='not authorized for this action'), 403)
    return u, None


def _get_share_or_404(share_id):
    s = db.session.get(Share, share_id)
    if s is None or s.deleted_at is not None:
        return None, (jsonify(error='share not found'), 404)
    return s, None


def _client_or_404(client_id):
    c = db.session.get(Client, client_id)
    if c is None:
        return None, (jsonify(error='client not found'), 404)
    return c, None


def _hidden_client_ids(sub, scope):
    """Client ids the user has hidden on this page (2026-07-25) — SINGLE query.
    Preference is PERSONAL: `owner_sub` is always the active identity, another
    user's preference can never be read."""
    from models import UserHiddenClient
    return {cid for (cid,) in db.session.query(UserHiddenClient.client_id).filter_by(
        owner_sub=str(sub), scope=scope).all()}


def _assigned_client_ids(sub, slot):
    from models import ClientTeamAssignment
    return {a.client_id for a in ClientTeamAssignment.query.filter_by(
        role_slot=slot, user_id=str(sub)).all()}


def _is_assigned(sub, client_id, *slots):
    from models import ClientTeamAssignment
    return ClientTeamAssignment.query.filter(
        ClientTeamAssignment.client_id == client_id,
        ClientTeamAssignment.user_id == str(sub),
        ClientTeamAssignment.role_slot.in_(slots)).first() is not None


# The "My Clients / Other Clients" split on the management board: clients are
# checked off by a single manager (OWNER_EMAIL). Checked clients are kept in
# the 'manager' slot; since checking is only open to OWNER_EMAIL, slot
# occupancy means "owner's client" (no need to look at user_id). For the
# owner, the clients they've checked are "My Clients"; for other managers
# it's the opposite (the complement).
OWNER_EMAIL = os.getenv('AGENCY_OWNER_EMAIL', '')
MANAGER_SLOT = 'manager'


def _manager_owned_ids():
    """Set of client ids with the 'manager' slot filled (checked by the owner)."""
    from models import ClientTeamAssignment
    return {a.client_id for a in ClientTeamAssignment.query.filter_by(
        role_slot=MANAGER_SLOT).all()}


# Upload ceiling: 500 MB/file. The app's MAX_CONTENT_LENGTH leaves 512 MB of
# headroom for overflow; here the product limit (500 MB) is enforced with a
# meaningful 413 (covers photo + video + content uploads alike).
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _can_upload(user, client_id, category):
    """management can upload anywhere; designer to EVERY client and EVERY category
    (2026-08-05: the video restriction was lifted — designers also upload
    editing/animation videos, the file goes through the same path as what the
    videographer uploads); videographer to EVERY client but only video
    (2026-07-21: parity with designer — the assignment requirement was dropped,
    a full-action decision)."""
    role = user.get('role')
    if role == 'management':
        return True
    if role == 'designer':
        return True
    if role == 'videographer' and category == 'video':
        return True
    return False


def _visible_shares(shares):
    """For video, only drafts at the highest revision are visible (legacy visibility rule).

    Published videos are always kept. For drafts the rule is a revision
    threshold, NOT a "single winner": all drafts whose revision EQUALS the
    week's highest one are kept — if there are 3 separate videos that week
    (all revision=0), all three are visible; only drafts stuck at an older
    revision are hidden.
    """
    videos = [s for s in shares if s.kind == 'video']
    others = [s for s in shares if s.kind != 'video']
    if not videos:
        return shares
    published = [s for s in videos if s.status == 'published']
    drafts = [s for s in videos if s.status != 'published']
    keep = list(published)
    if drafts:
        maxrev = max((s.revision or 0) for s in drafts)
        keep += [s for s in drafts if (s.revision or 0) >= maxrev]
    return others + keep


# --- shares CRUD ---

SCALAR = ('file_id', 'file_name', 'original_name', 'caption_text', 'hashtag_text', 'note')



# Upload categories shown on the approval page (project owner, 2026-07-24): story/linkedin are hidden.
REVIEW_CATEGORIES = ('post', 'video')


def review_visible_uploads(client_id, week_iso, include_excluded=False):
    """The UPLOADS shown by the approval link — SINGLE SOURCE OF TRUTH.

    Source is `card_uploads`: post/video files the designer uploaded that week
    (NOT the ones marked "shared" on the sharing board — project owner,
    2026-07-24). Excludes deleted ones and those staff removed from the page
    (`ReviewExcludedUpload`).

    Both the public approval page (`review._review_uploads`) and the
    `share_count` in link generation read from here → the singular/plural
    decision in the message the designer copies always stays consistent with
    the content count shown on the page.
    """
    rows = (CardUpload.query
            .filter(CardUpload.client_id == client_id,
                    CardUpload.week_iso == week_iso,
                    CardUpload.deleted_at.is_(None),
                    CardUpload.category.in_(REVIEW_CATEGORIES))
            .order_by(CardUpload.uploaded_at.asc().nullslast(), CardUpload.id)
            .all())
    if include_excluded or not rows:
        return rows
    ids = [r.id for r in rows]
    excluded = {e.upload_id for e in ReviewExcludedUpload.query
                .filter(ReviewExcludedUpload.upload_id.in_(ids)).all()}
    rows = [r for r in rows if r.id not in excluded]
    return _apply_pre_approval_gate(rows)


def pre_approval_map(upload_ids):
    """Upload id -> UploadPreApproval (only those a decision has been made on)."""
    if not upload_ids:
        return {}
    return {p.upload_id: p for p in UploadPreApproval.query
            .filter(UploadPreApproval.upload_id.in_(list(upload_ids))).all()}


def _apply_pre_approval_gate(rows):
    """GRADUAL GATE (project owner, 2026-07-24): if AT LEAST ONE pre-approval
    decision has been made for this (client, week), the client only sees the
    `approved` ones. If no decision exists at all, the gate is NOT active —
    weeks that don't use the pre-approval flow work as before."""
    decided = pre_approval_map([r.id for r in rows])
    if not decided:
        return rows
    return [r for r in rows
            if decided.get(r.id) is not None and decided[r.id].status == 'approved']

def _apply_share(s, data):
    for f in SCALAR:
        if f in data:
            setattr(s, f, data[f])
    if 'planned_date' in data:
        v = data['planned_date']
        s.planned_date = date.fromisoformat(v) if v else None
    if 'planned_time' in data:
        s.planned_time = data['planned_time']
    if 'revision' in data:
        s.revision = int(data['revision'] or 0)


_TR_AY = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
          'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def _special_date_label(ev):
    """TR date label for a special day: single '23 Temmuz' / range '21–25 Temmuz'."""
    m = _TR_AY[ev.month] if ev.month and 1 <= ev.month <= 12 else ''
    if ev.date_num:
        return f"{ev.date_num} {m}".strip()
    if ev.date_start and ev.date_end:
        return f"{ev.date_start}–{ev.date_end} {m}".strip()
    return m


def _event_in_week(ev, week_dates):
    """Does the event fall on any day of the week (single day OR range)?"""
    for d in week_dates:
        if d.year != ev.year or d.month != ev.month:
            continue
        if ev.date_num and d.day == ev.date_num:
            return True
        if not ev.date_num and ev.date_start and ev.date_end and ev.date_start <= d.day <= ev.date_end:
            return True
    return False


def _week_special_days(ids, week_dates):
    """Clients' SELECTED special days that fall in this week: {client_id: [card,...]}.

    Only events the client themselves selected (SpecialDaySelection) that are
    also active + approved, and whose date falls in the week. Shown as an
    informational card in the board's card strip."""
    if not ids or not week_dates:
        return {}
    months = {d.month for d in week_dates}
    years = {d.year for d in week_dates}
    pairs = {(d.month, d.year) for d in week_dates}
    sels = SpecialDaySelection.query.filter(
        SpecialDaySelection.client_id.in_(ids),
        SpecialDaySelection.month.in_(months),
        SpecialDaySelection.year.in_(years)).all()
    sel_ids, all_ids = {}, set()
    for s in sels:
        if (s.month, s.year) not in pairs:
            continue
        chosen = set(s.selected_event_ids or [])
        if not chosen:
            continue
        sel_ids.setdefault(s.client_id, set()).update(chosen)
        all_ids.update(chosen)
    if not all_ids:
        return {}
    events = {e.id: e for e in SpecialDayEvent.query.filter(
        SpecialDayEvent.id.in_(all_ids), SpecialDayEvent.active.is_(True),
        SpecialDayEvent.status == 'approved').all()}
    out = {}
    for cid, eids in sel_ids.items():
        cards = [
            {'event_id': ev.id, 'day_name': ev.day_name,
             'type': 'day' if ev.date_num else 'week', 'date_label': _special_date_label(ev)}
            for ev in (events.get(i) for i in eids)
            if ev is not None and _event_in_week(ev, week_dates)]
        if cards:
            cards.sort(key=lambda c: c['date_label'])
            out[cid] = cards
    return out


def _build_rows(clients, week_iso, assigned_ids=None):
    """Build weekly board rows (shared) for the given client list.

    If assigned_ids is None, every row gets assigned=True (management view);
    if a set is given (designer view), each row is flagged by whether c.id is
    in the set — the designer board uses this for the "My Clients" / "Other
    Clients" split."""
    ids = [c.id for c in clients]
    if not ids:
        return []
    by_client, sp_by_client, revs_by_client = {}, {}, {}
    for s in Share.query.filter(Share.week_iso == week_iso, Share.deleted_at.is_(None),
                                Share.client_id.in_(ids)).all():
        by_client.setdefault(s.client_id, []).append(s)
    for sp in SpecialCardStatus.query.filter(SpecialCardStatus.week_iso == week_iso,
                                             SpecialCardStatus.client_id.in_(ids)).all():
        sp_by_client.setdefault(sp.client_id, []).append(sp)
    for r in RevisionRequest.query.filter(RevisionRequest.week_iso == week_iso,
                                          RevisionRequest.status == 'open',
                                          RevisionRequest.client_id.in_(ids)).all():
        revs_by_client.setdefault(r.client_id, []).append(r)
    prios = {p.client_id for p in ClientPriority.query.filter(
        ClientPriority.week_iso == week_iso, ClientPriority.cleared_at.is_(None),
        ClientPriority.client_id.in_(ids)).all()}
    upload_counts = dict(
        db.session.query(CardUpload.client_id, db.func.count(CardUpload.id))
        .filter(CardUpload.week_iso == week_iso, CardUpload.deleted_at.is_(None),
                CardUpload.client_id.in_(ids))
        .group_by(CardUpload.client_id).all())
    # The client's UPLOAD-level decisions on the approval page (2026-07-24) —
    # shown on the board as a badge. Single JOIN query (NO per-client query).
    review_counts = {}
    for cid_, status_, n in (
            db.session.query(CardUpload.client_id, UploadReview.status,
                             db.func.count(UploadReview.id))
            .join(UploadReview, UploadReview.upload_id == CardUpload.id)
            .filter(CardUpload.week_iso == week_iso, CardUpload.deleted_at.is_(None),
                    CardUpload.client_id.in_(ids),
                    CardUpload.category.in_(REVIEW_CATEGORIES))
            .group_by(CardUpload.client_id, UploadReview.status).all()):
        entry = review_counts.setdefault(cid_, {'approved': 0, 'revision_requested': 0})
        if status_ in entry:
            entry[status_] = n

    # Pre-approval decisions (internal management gate, 2026-07-24) — board badges.
    pre_counts = {}
    for cid_, status_, n in (
            db.session.query(CardUpload.client_id, UploadPreApproval.status,
                             db.func.count(UploadPreApproval.id))
            .join(UploadPreApproval, UploadPreApproval.upload_id == CardUpload.id)
            .filter(CardUpload.week_iso == week_iso, CardUpload.deleted_at.is_(None),
                    CardUpload.client_id.in_(ids),
                    CardUpload.category.in_(REVIEW_CATEGORIES))
            .group_by(CardUpload.client_id, UploadPreApproval.status).all()):
        entry = pre_counts.setdefault(cid_, {'approved': 0, 'revision_requested': 0})
        if status_ in entry:
            entry[status_] = n

    # This week's video uploads — shown on the management board's card strip
    # (like special day cards) as an info card (2026-07-21, videographer flow).
    video_uploads = {}
    all_video_fids = []
    # Delete permission is BAKED INTO the item (2026-07-31): the rule
    # (management unrestricted / videographer only their own uploads) should
    # live in a single place — having the panel rebuild the `uploaded_by`
    # comparison would silently drift when the rule changes.
    # `has_request_context` is required: `_build_rows` can also be called
    # outside a request (script/test); `current_user()` would reach into the
    # session there and blow up.
    _u = (current_user() or {}) if has_request_context() else {}
    _yonetim = _u.get('role') == 'management'
    for vu in (CardUpload.query.filter(CardUpload.week_iso == week_iso,
                                       CardUpload.category == 'video',
                                       CardUpload.deleted_at.is_(None),
                                       CardUpload.client_id.in_(ids))
               .order_by(CardUpload.id).all()):
        video_uploads.setdefault(vu.client_id, []).append({
            'id': vu.id, 'file_id': vu.file_id, 'file_name': vu.file_name,
            'uploaded_at': vu.uploaded_at.isoformat() if vu.uploaded_at else None,
            'local': media_store.has_original(vu.file_id),
            'can_delete': _yonetim or (vu.uploaded_by == _u.get('sub'))})
        if vu.file_id:
            all_video_fids.append(vu.file_id)
    # "Shared" = the video file appears in a Share row (2026-07-25). The
    # videographer page lists the UNSHARED ones as a "pending queue". A SINGLE
    # BATCHED QUERY — querying inside the loop is FORBIDDEN (this function runs
    # for 31 clients).
    shared_video_fids = set()
    if all_video_fids:
        shared_video_fids = {fid for (fid,) in db.session.query(Share.file_id).filter(
            Share.file_id.in_(all_video_fids), Share.deleted_at.is_(None)).all() if fid}
    # Bake the flag into the items (2026-07-29): the videographer page now
    # lists ALL of the week's videos and marks the shared ones with a badge —
    # it used to not show them at all, so the videographer couldn't watch
    # their own uploaded video once it was shared. Single pass; `video_pending`
    # shares the same objects.
    for _lst in video_uploads.values():
        for d in _lst:
            d['shared'] = bool(d['file_id']) and d['file_id'] in shared_video_fids
    # Videographer photo folder — source link for the designer (if any, week-independent)
    photo_folders = {}
    for pcid, fid in (db.session.query(VideographerPhoto.client_id, VideographerPhoto.folder_id)
                      .filter(VideographerPhoto.client_id.in_(ids),
                              VideographerPhoto.deleted_at.is_(None),
                              VideographerPhoto.folder_id.isnot(None)).all()):
        photo_folders.setdefault(pcid, fid)
    # This week's Drive folder — "Open Folder" link (no Drive call, DB only)
    week_folders = {}
    wn = _week_number(week_iso)
    if wn:
        for fcid, fid in (db.session.query(ClientWeekFolder.client_id, ClientWeekFolder.folder_id)
                          .filter(ClientWeekFolder.client_id.in_(ids),
                                  ClientWeekFolder.week_number == wn,
                                  ClientWeekFolder.folder_id.isnot(None)).all()):
            week_folders.setdefault(fcid, fid)

    # Selected special days falling in this week (client × week) — shown in the board's card strip.
    special_days = _week_special_days(ids, _week_dates(week_iso))

    rows = []
    for c in clients:
        vis = sorted(_visible_shares(by_client.get(c.id, [])), key=lambda s: s.id)
        crevs = revs_by_client.get(c.id, [])
        # `local`: does the file have a 21-day server copy (frontend source selection)
        share_dicts = []
        for s in vis:
            d = s.to_dict()
            d['local'] = bool(s.file_id) and media_store.has_original(s.file_id)
            share_dicts.append(d)
        rows.append({
            'client': {'id': c.id, 'name': c.name, 'instagram_url': c.instagram_url},
            'assigned': (True if assigned_ids is None else c.id in assigned_ids),
            'shares': share_dicts,
            'special_cards': [sp.to_dict() for sp in sp_by_client.get(c.id, [])],
            'special_days': special_days.get(c.id, []),
            'priority': c.id in prios,
            'published_count': sum(1 for s in vis if s.status == 'published'),
            'total_count': len(vis),
            'upload_count': upload_counts.get(c.id, 0),
            # Pre-approval (internal management gate) decisions
            'pre_approved_count': pre_counts.get(c.id, {}).get('approved', 0),
            'pre_revision_count': pre_counts.get(c.id, {}).get('revision_requested', 0),
            # Client decisions on the approval page (per upload)
            'upload_approved_count': review_counts.get(c.id, {}).get('approved', 0),
            'upload_revision_count': review_counts.get(c.id, {}).get('revision_requested', 0),
            'open_revision_count': len(crevs),
            'revision_share_ids': [r.share_id for r in crevs if r.share_id],
            # `video_uploads` is ALL of the week's videos — the management/designer
            # board card strip looks at this, its semantics are UNCHANGED. The
            # videographer page uses the derived fields below (unshared =
            # pending queue).
            'video_uploads': video_uploads.get(c.id, []),
            'video_total_count': len(video_uploads.get(c.id, [])),
            # ACTUAL pending count (not clipped to 5 — the "…7 pending" text reads this)
            'video_pending_count': sum(
                1 for d in video_uploads.get(c.id, [])
                if d['file_id'] and d['file_id'] not in shared_video_fids),
            # Shown: the 5 most recent pending
            'video_pending': sorted(
                (d for d in video_uploads.get(c.id, [])
                 if d['file_id'] and d['file_id'] not in shared_video_fids),
                key=lambda d: (d['uploaded_at'] or '', d['id']), reverse=True)[:5],
            'photos_folder_url': (
                _folder_link(photo_folders[c.id]) if c.id in photo_folders else None),
            'drive_folder_url': (
                _folder_link(week_folders[c.id]) if c.id in week_folders else None),
        })
    return rows


@bp.get('/cards')
def cards():
    u, err = _require_management()
    if err:
        return err
    week_iso = request.args.get('week_iso', '')
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    # The "My Clients / Other Clients" split: the owner (OWNER_EMAIL) sees the
    # ones they've checked; other managers see the ones the owner hasn't
    # checked (the complement) as "My Clients".
    owned = _manager_owned_ids()
    if u.get('email') == OWNER_EMAIL:
        assigned_ids = owned
    else:
        assigned_ids = {c.id for c in clients} - owned
    return jsonify(week_iso=week_iso, rows=_build_rows(clients, week_iso, assigned_ids))


@bp.post('/manager-clients')
def set_manager_client():
    """The owner (OWNER_EMAIL) marks/unmarks a client as "mine" (manager slot)."""
    u, err = _require_management()
    if err:
        return err
    if u.get('email') != OWNER_EMAIL:
        return jsonify(error='not authorized for this action'), 403
    from models import ClientTeamAssignment
    data = request.get_json(silent=True) or {}
    c, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    owned = bool(data.get('owned'))
    row = ClientTeamAssignment.query.filter_by(
        client_id=c.id, role_slot=MANAGER_SLOT).first()
    if owned and not row:
        db.session.add(ClientTeamAssignment(
            client_id=c.id, role_slot=MANAGER_SLOT, user_id=str(u['sub'])))
    elif not owned and row:
        db.session.delete(row)
    db.session.commit()
    return jsonify(client_id=c.id, owned=owned)


@bp.get('/designer/cards')
def designer_cards():
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    week_iso = request.args.get('week_iso', '')
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    # The designer sees all clients; the assigned flag is baked into rows so
    # assigned ones split into "My Clients" and the rest into "Other Clients".
    # Action rights are open regardless of group (full-action decision).
    assigned_ids = None
    if u['role'] == 'designer':
        assigned_ids = _assigned_client_ids(u['sub'], 'designer')
    return jsonify(week_iso=week_iso, rows=_build_rows(clients, week_iso, assigned_ids))


@bp.get('/videographer/cards')
def videographer_cards():
    """Videographer video-upload board (2026-07-21). Identical to the designer
    board's pattern: returns ALL active clients; assigned ones
    (videographer_shoot|edit) are flagged with `assigned` ("My Clients"/"Other
    Clients" grouping). Upload rights are open regardless of group (video
    category, full-action decision)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'videographer'):
        return jsonify(error='not authorized'), 403
    week_iso = request.args.get('week_iso', '')
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    # Personal hiding (2026-07-25): filtering happens BEFORE _build_rows — that
    # function runs several batched queries for the client set, so not
    # querying hidden ones at all is cheaper. A frontend-side filter would
    # still transfer all of the hidden client's shares/upload data.
    hidden = _hidden_client_ids(u['sub'], 'videographer_upload')
    visible = [c for c in clients if c.id not in hidden]
    assigned_ids = None
    if u['role'] == 'videographer':
        assigned_ids = (_assigned_client_ids(u['sub'], 'videographer_shoot')
                        | _assigned_client_ids(u['sub'], 'videographer_edit'))
    return jsonify(week_iso=week_iso,
                   rows=_build_rows(visible, week_iso, assigned_ids),
                   hidden_client_ids=sorted(hidden), hidden_count=len(hidden))


@bp.post('/shares')
def share_create():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    if kind not in SHARE_KINDS:
        return jsonify(error=f'invalid kind (post/story/video/linkedin)'), 400
    if not data.get('week_iso'):
        return jsonify(error='week_iso is required'), 400
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    s = Share(client_id=data['client_id'], week_iso=data['week_iso'], kind=kind,
              status='draft', created_by=u['sub'])
    _apply_share(s, data)
    db.session.add(s)
    db.session.commit()
    return jsonify(share=s.to_dict()), 201


@bp.get('/shares/<int:share_id>')
def share_detail(share_id):
    _, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    return jsonify(share=s.to_dict())


@bp.patch('/shares/<int:share_id>')
def share_update(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    _apply_share(s, request.get_json(silent=True) or {})
    s.updated_at = utcnow()
    s.updated_by = u['sub']
    db.session.commit()
    return jsonify(share=s.to_dict())


@bp.delete('/shares/<int:share_id>')
def share_delete(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    s.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.post('/shares/<int:share_id>/publish')
def share_publish(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    if not s.file_id and not (s.note or '').strip():
        return jsonify(error='a note is required for a share with no file'), 400
    now = utcnow()
    s.status = 'published'
    s.published_at = now
    s.published_day_name = TR_DAYS[now.weekday()]
    s.published_by = u['sub']
    db.session.commit()
    return jsonify(share=s.to_dict())


@bp.post('/shares/<int:share_id>/unpublish')
def share_unpublish(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    s.status = 'draft'
    s.published_at = None
    s.published_day_name = None
    s.published_by = None
    db.session.commit()
    return jsonify(share=s.to_dict())


@bp.post('/shares/<int:share_id>/platform-mark')
def share_platform_mark(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    platform = (request.get_json(silent=True) or {}).get('platform')
    if platform not in ('instagram', 'story', 'linkedin', 'facebook'):
        return jsonify(error='invalid platform'), 400
    platforms = dict(s.platforms or {})
    if platform in platforms:
        del platforms[platform]
    else:
        platforms[platform] = {'marked_at': utcnow().isoformat()}
    s.platforms = platforms
    db.session.commit()
    return jsonify(share=s.to_dict())


def _shift_week(week_iso, delta):
    """'2026-W21' → ISO week string shifted by delta weeks ('2026-W20')."""
    m = re.match(r'(\d{4})-W(\d{2})', week_iso or '')
    if not m:
        return None
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1) + timedelta(weeks=delta)
    iso = monday.isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


def _extract_folder_id(drive_meta):
    """Extract the Drive folder id from clients.drive_meta's root folder link."""
    if not isinstance(drive_meta, dict):
        return None
    for k in ('client_folder_link', 'content_root_folder_link', 'video_root'):
        link = drive_meta.get(k)
        if isinstance(link, str):
            m = re.search(r'/folders/([A-Za-z0-9_-]+)', link)
            if m:
                return m.group(1)
    return None


def _resolve_week_folder(client, week_iso, create=False):
    """The client's Drive folder id for that week; if missing, create it under the root with create=True."""
    wn = _week_number(week_iso)
    if not wn:
        return None
    wf = ClientWeekFolder.query.filter_by(client_id=client.id, week_number=wn).first()
    if wf and wf.folder_id:
        return wf.folder_id
    if not create:
        return None
    root = _extract_folder_id(client.drive_meta)
    if not root:
        return None
    try:
        fid = dg.ensure_subfolder(root, str(wn))
    except dg.DriveError:
        return None
    db.session.add(ClientWeekFolder(client_id=client.id, week_number=wn,
                                    folder_id=fid, name=str(wn)))
    db.session.commit()
    return fid


@bp.post('/upload')
def upload():
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    client_id = request.form.get('client_id', type=int)
    week_iso = request.form.get('week_iso', '')
    category = request.form.get('category') or 'post'
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='no file'), 400
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    if not _can_upload(u, client_id, category):
        return jsonify(error='not authorized to upload for this client'), 403
    folder_id = _resolve_week_folder(c, week_iso, create=True)
    if not folder_id:
        return jsonify(error='no Drive folder exists for this week and it could not be created'), 400
    # Measure the size without pulling the stream into RAM (werkzeug spools large chunks to disk).
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify(error='File exceeds the 500 MB limit.'), 413
    mime = f.mimetype or 'application/octet-stream'
    # First a local temp copy (candidate for the 21-day store); the Drive
    # upload streams from this file — stays bounded by the RAM chunk size. If
    # the temp file can't be written, fall back to streaming.
    tmp = media_store.stage(f.stream)
    try:
        if tmp:
            with open(tmp, 'rb') as fh:
                meta = dg.upload_file(folder_id, f.filename, fh, mime)
        else:
            f.stream.seek(0)
            meta = dg.upload_file(folder_id, f.filename, f.stream, mime)
    except dg.DriveError as e:
        media_store.discard(tmp)
        log.exception('Drive yükleme başarısız (client=%s week=%s dosya=%s boyut=%s)',
                      client_id, week_iso, f.filename, size)
        return jsonify(error=f'Drive upload failed: {e}'), 502
    # Local copy (21 days) — the board + approval page serve from the server
    # during this window. Best-effort: if it can't be persisted, the upload is
    # still valid (Drive is canonical).
    media_store.commit(tmp, meta.get('id'), meta.get('mimeType') or mime, f.filename)
    # Grant video 'anyone with the link → can view' permission AT UPLOAD TIME
    # (2026-07-25): the "Copy" button on the videographer page puts the Drive
    # link on the clipboard; without the permission that link would hit a
    # permission wall outside the agency. VIDEO ONLY — images go through the
    # proxy (`/api/sharing/media/<id>`), no need to open those up.
    # Best-effort: if it fails, the upload is still valid (same pattern as vg_photo_upload).
    if category == 'video':
        try:
            dg.grant_anyone_reader(meta.get('id'))
        except Exception as e:  # noqa: BLE001 — permission is best-effort, upload is critical
            log.warning('video izni verilemedi (%s): %s', meta.get('id'), e)
        # Browser-compatible derivative (2026-08-01) — phone videos come in as
        # 4K/HEVC and Android Chrome can't open them; the `/m/<file_id>` page
        # plays the derivative, "Download" serves the original. Transcoding
        # takes ~1 min of CPU → NOT inside the request, done in media_worker.
        # `dedup_key` prevents a second job for the same file (don't transcode
        # twice if the upload is retried).
        try:
            jobqueue.enqueue('web_variant', {'file_id': meta.get('id')},
                             dedup_key=meta.get('id'))
        except Exception as e:  # noqa: BLE001 — queueing is best-effort
            log.warning('web türevi kuyruğa alınamadı (%s): %s', meta.get('id'), e)
    cu = CardUpload(
        client_id=client_id, week_iso=week_iso, category=category,
        file_id=meta.get('id'), file_name=meta.get('name') or f.filename,
        mime_type=meta.get('mimeType') or f.mimetype,
        file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else size,
        uploaded_by=u['sub'], uploaded_at=utcnow(),
        upload_uuid=request.form.get('upload_uuid') or uuid.uuid4().hex)
    db.session.add(cu)
    _auto_resolve(client_id, week_iso, category)  # close the open revision
    db.session.commit()
    payload = cu.to_dict()
    # Revision detection (2026-08-01): if the name pattern points to an older
    # version, that version is deleted. AFTER the upload and in a separate
    # try: an error here (rule/Drive/notification) must not invalidate the
    # upload — the file is already on Drive and in the DB.
    if category == 'video':
        try:
            _supersede_previous_videos(cu)
        except Exception:  # noqa: BLE001 — deletion is best-effort, upload is critical
            log.exception('revize taraması başarısız (upload=%s)', cu.id)
    # Management needs to see the upload: they're the ones who kick off the
    # approval/sharing flow (2026-08-05). Video and content are SEPARATE types
    # — management routes them differently (video card vs. sharing card).
    # Batch uploads hit the 30-min coalesce (notifications).
    try:
        if category == 'video':
            notifications.notify_video_uploaded(client_id, week_iso, u.get('role'))
        else:
            notifications.notify_content_uploaded(client_id, week_iso, category)
    except Exception:  # noqa: BLE001 — notification is best-effort, upload is critical
        log.exception('yükleme bildirimi başarısız (upload=%s)', cu.id)
    return jsonify(upload=payload), 201


def _video_korumali_mi(up):
    """Should this video be protected from automatic deletion? If so, the reason text.

    Deleting a shared video SILENTLY breaks the item on the client approval
    page (`Share.file_id` is plain text, no FK) — so automatic deletion
    doesn't touch it, it's left to a human. One with a client approval/
    pre-approval record is in the same class: the client has already seen
    that file, deleting it behind their back breaks the audit trail."""
    if not up.file_id:
        return None
    paylasim = (Share.query
                .filter_by(file_id=up.file_id, deleted_at=None)
                .first())
    if paylasim is not None:
        return 'it is in an active share'
    if UploadReview.query.filter_by(upload_id=up.id).first() is not None:
        return 'it has a client review record'
    if UploadPreApproval.query.filter_by(upload_id=up.id).first() is not None:
        return 'it has a pre-approval record'
    return None


def _supersede_previous_videos(cu):
    """If the new video is a revision, delete the older versions it supersedes.

    The rule lives in `revision_match` (pure, tested separately); here there's
    only the DB filter, safety gates, and deletion. Best-effort: nothing that
    blows up here should invalidate the upload — the caller wraps it in
    try/except.

    Scope is SAME CLIENT only. The week constraint is DELIBERATELY absent: in
    real data, some revision pairs cross the week boundary (`KIDS HOME - 24`
    W24 → `- 24 - 2` W25); a week condition would miss those."""
    if not cu.file_name:
        return
    onceki = (CardUpload.query
              .filter(CardUpload.client_id == cu.client_id,
                      CardUpload.category == 'video',
                      CardUpload.deleted_at.is_(None),
                      CardUpload.id != cu.id,
                      CardUpload.uploaded_at < cu.uploaded_at)
              .all())
    eskiler = revision_match.find_superseded(cu.file_name, onceki)
    if not eskiler:
        return

    silinen, korunan = [], []
    for up in eskiler:
        gerekce = _video_korumali_mi(up)
        if gerekce:
            korunan.append((up.file_name, gerekce))
            continue
        ad = up.file_name
        _hard_delete_video(up)
        silinen.append(ad)
        log.info('revize geldi (%s) → eski sürüm silindi: %s', cu.file_name, ad)

    if silinen:
        notifications.notify_old_video_removed(
            cu.client_id, cu.week_iso, cu.file_name, silinen)
    if korunan:
        notifications.notify_old_video_kept(
            cu.client_id, cu.week_iso, cu.file_name,
            [ad for ad, _ in korunan], korunan[0][1])


def _auto_resolve(client_id, week_iso, category):
    """A new upload closes the open revision: video→video kind, other→design kind."""
    kind = 'video' if category == 'video' else 'design'
    open_reqs = RevisionRequest.query.filter_by(
        client_id=client_id, week_iso=week_iso, kind=kind, status='open').all()
    for r in open_reqs:
        r.status = 'resolved'
        r.resolved_at = utcnow()
    if open_reqs:
        notifications.notify_revision_resolved(kind, client_id, week_iso)


def _week_folder_files(client, week_iso, published_ids):
    """Unshared files in a week's Drive folder (published file_ids are excluded).
    Empty list if the folder doesn't exist."""
    fid = _resolve_week_folder(client, week_iso, create=False)
    if not fid:
        return {'week_iso': week_iso, 'folder_id': None, 'files': []}
    try:
        files = dg.list_files(fid, media_only=True)
    except dg.DriveError:
        files = []
    out = [{'id': f['id'], 'name': f.get('name'), 'mime': f.get('mimeType')}
           for f in files if f['id'] not in published_ids]
    return {'week_iso': week_iso, 'folder_id': fid, 'files': out}


@bp.get('/movable-files')
def movable_files():
    """Unshared content in the previous + next week folders (movable to this
    week). Shared = a published Share.file_id."""
    u, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    week_iso = request.args.get('week_iso', '')
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    if not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    published_ids = {s.file_id for s in Share.query.filter(
        Share.client_id == client_id, Share.status == 'published',
        Share.file_id.isnot(None)).all()}
    prev_iso = _shift_week(week_iso, -1)
    next_iso = _shift_week(week_iso, 1)
    return jsonify(
        previous=_week_folder_files(c, prev_iso, published_ids),
        next=_week_folder_files(c, next_iso, published_ids))


@bp.post('/move-files')
def move_files():
    """Move selected files from the source week folder to the target (this) week folder."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    from_iso = data.get('from_week_iso')
    to_iso = data.get('to_week_iso')
    file_ids = [fid for fid in (data.get('file_ids') or []) if fid]
    if not from_iso or not to_iso or not file_ids:
        return jsonify(error='from_week_iso, to_week_iso and file_ids are required'), 400
    if not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    from_folder = _resolve_week_folder(c, from_iso, create=False)
    to_folder = _resolve_week_folder(c, to_iso, create=True)
    if not from_folder or not to_folder:
        return jsonify(error='source or target week folder not found'), 400
    moved, errors = 0, []
    for fid in file_ids:
        try:
            meta = dg.move_file(fid, to_folder, from_folder)
        except dg.DriveError as e:
            errors.append(f'{fid}: {e}')
            continue
        ups = CardUpload.query.filter_by(client_id=client_id, file_id=fid,
                                         deleted_at=None).all()
        if ups:
            # move the matching CardUpload record to the target week (traceability)
            for up in ups:
                up.moved_from_week_iso = up.week_iso
                up.week_iso = to_iso
                up.moved_at = utcnow()
        else:
            # A file with no panel record (placed directly on Drive): generate a
            # CardUpload for the target week so it shows up in the content
            # pool/board. Otherwise the file moves on Drive but is invisible
            # anywhere in the panel.
            size = meta.get('size')
            db.session.add(CardUpload(
                client_id=client_id, week_iso=to_iso, file_id=fid,
                file_name=meta.get('name'), mime_type=meta.get('mimeType'),
                file_size=int(size) if str(size or '').isdigit() else None,
                uploaded_by=u['sub'], uploaded_at=utcnow(),
                upload_uuid=uuid.uuid4().hex, moved_from_week_iso=from_iso,
                moved_at=utcnow(), backfilled=True))
        moved += 1
    db.session.commit()
    return jsonify(moved=moved, errors=errors)


# --- shoot plan ---

def _week_dates(week_iso):
    m = re.match(r'(\d{4})-W(\d{2})', week_iso or '')
    if not m:
        return []
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return [monday + timedelta(days=i) for i in range(7)]


def _can_shoot(user, client_id):
    """management anywhere; videographer to their assigned client, or ad-hoc (no client_id)."""
    if user.get('role') == 'management':
        return True
    if user.get('role') == 'videographer':
        return client_id is None or _is_assigned(
            user['sub'], client_id, 'videographer_shoot', 'videographer_edit')
    return False


def _can_view_photos(user, client_id):
    """Right to view/download shoot photos. management and designer for every
    client (designer full-action decision); videographer only for clients
    where _can_shoot applies. Upload/delete/rename are NOT in this scope —
    those stay restricted to _can_shoot."""
    role = user.get('role')
    if role in ('management', 'designer'):
        return True
    if role == 'videographer':
        return _can_shoot(user, client_id)
    return False


@bp.get('/shoot-plan')
def shoot_plan():
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'videographer'):
        return jsonify(error='not authorized'), 403
    dates = _week_dates(request.args.get('week_iso', ''))
    if not dates:
        return jsonify(error='invalid week_iso'), 400

    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    if u['role'] == 'videographer':
        allowed = (_assigned_client_ids(u['sub'], 'videographer_shoot')
                   | _assigned_client_ids(u['sub'], 'videographer_edit'))
        clients = [c for c in clients if c.id in allowed]
    names = {c.id: c.name for c in Client.query.all()}
    allowed_ids = {c.id for c in clients}

    q = ShootTask.query.filter(ShootTask.scheduled_date.in_(dates))
    tasks = q.order_by(ShootTask.position, ShootTask.id).all()
    if u['role'] == 'videographer':
        tasks = [t for t in tasks if t.client_id in allowed_ids or t.client_id is None]

    by_date = {d.isoformat(): [] for d in dates}
    for t in tasks:
        key = t.scheduled_date.isoformat() if t.scheduled_date else None
        if key in by_date:
            td = t.to_dict()
            td['client_name'] = names.get(t.client_id)
            by_date[key].append(td)
    days = [{'date': d.isoformat(), 'tasks': by_date[d.isoformat()]} for d in dates]
    pool = [{'id': c.id, 'name': c.name} for c in clients]
    return jsonify(week_iso=request.args.get('week_iso'), days=days, pool=pool)


@bp.post('/shoot-plan')
def shoot_create():
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    if not _can_shoot(u, client_id):
        return jsonify(error='not authorized to plan shoots for this client'), 403
    try:
        sched = date.fromisoformat(data['scheduled_date']) if data.get('scheduled_date') else None
    except (ValueError, TypeError):
        return jsonify(error='invalid date'), 400
    maxpos = db.session.query(db.func.max(ShootTask.position)).filter_by(
        scheduled_date=sched).scalar()
    t = ShootTask(
        client_id=client_id, scheduled_date=sched,
        start_time=data.get('start_time'), end_time=data.get('end_time'),
        content_type=data.get('content_type'), location_note=data.get('location_note'),
        priority=data.get('priority', 'normal'),
        assigned_to=data.get('assigned_to') or (u['sub'] if u['role'] == 'videographer' else None),
        assigned_by=u['sub'], position=(maxpos or 0) + 1, created_by=u['sub'])
    db.session.add(t)
    db.session.commit()
    return jsonify(task=t.to_dict()), 201


def _shoot_or_err(u, task_id):
    t = db.session.get(ShootTask, task_id)
    if t is None:
        return None, (jsonify(error='task not found'), 404)
    if not _can_shoot(u, t.client_id):
        return None, (jsonify(error='not authorized'), 403)
    return t, None


@bp.patch('/shoot-plan/order')
def shoot_reorder():
    u = current_user()
    if not u or u.get('role') not in ('management', 'videographer'):
        return jsonify(error='not authorized'), 403
    ids = (request.get_json(silent=True) or {}).get('task_ids', [])
    for pos, tid in enumerate(ids):
        t = db.session.get(ShootTask, tid)
        if t and _can_shoot(u, t.client_id):
            t.position = pos
    db.session.commit()
    return jsonify(ok=True)


@bp.patch('/shoot-plan/<int:task_id>')
def shoot_update(task_id):
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    t, err = _shoot_or_err(u, task_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if 'scheduled_date' in data:
        try:
            t.scheduled_date = date.fromisoformat(data['scheduled_date']) if data['scheduled_date'] else None
        except (ValueError, TypeError):
            return jsonify(error='invalid date'), 400
    for f in ('start_time', 'end_time', 'priority', 'location_note', 'content_type'):
        if f in data:
            setattr(t, f, data[f])
    if 'position' in data:
        t.position = int(data['position'] or 0)
    t.updated_at = utcnow()
    t.updated_by = u['sub']
    db.session.commit()
    return jsonify(task=t.to_dict())


@bp.post('/shoot-plan/<int:task_id>/done')
def shoot_done(task_id):
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    t, err = _shoot_or_err(u, task_id)
    if err:
        return err
    if t.status == 'completed':
        t.status = 'pending'
        t.completed_at = t.completed_by = None
    else:
        t.status = 'completed'
        t.completed_at = utcnow()
        t.completed_by = u['sub']
    db.session.commit()
    return jsonify(task=t.to_dict())


@bp.delete('/shoot-plan/<int:task_id>')
def shoot_delete(task_id):
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    t, err = _shoot_or_err(u, task_id)
    if err:
        return err
    db.session.delete(t)
    db.session.commit()
    return jsonify(ok=True)


# --- videographer: businesses + photos ---

def _require_vg():
    u = current_user()
    if not u:
        return None, (jsonify(error='not signed in'), 401)
    if u.get('role') not in ('management', 'videographer'):
        return None, (jsonify(error='not authorized'), 403)
    return u, None


def _require_photo_access():
    """For shoot photo view/download endpoints: management, designer, or
    videographer. Per-client permission is checked separately via _can_view_photos."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not signed in'), 401)
    if u.get('role') not in ('management', 'designer', 'videographer'):
        return None, (jsonify(error='not authorized'), 403)
    return u, None


@bp.get('/videographer/businesses')
def vg_businesses():
    _, err = _require_vg()
    if err:
        return err
    week_iso = request.args.get('week_iso', '')
    marks = {m.client_id: m.has_video
             for m in VideographerBusinessMark.query.filter_by(week_iso=week_iso).all()}
    clients = Client.query.filter_by(status='active').order_by(Client.name).all()
    return jsonify(businesses=[
        {'client_id': c.id, 'client_name': c.name, 'has_video': bool(marks.get(c.id))}
        for c in clients])


@bp.post('/videographer/businesses/mark')
def vg_business_mark():
    u, err = _require_vg()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    m = VideographerBusinessMark.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso).one_or_none()
    if m is None:
        m = VideographerBusinessMark(client_id=data['client_id'], week_iso=week_iso)
        db.session.add(m)
    m.has_video = bool(data.get('has_video'))
    m.marked_at = utcnow()
    m.marked_by = u['sub']
    db.session.commit()
    return jsonify(has_video=m.has_video)


@bp.get('/videographer/photos')
def vg_photos():
    # Designer also has access (to use shoot photos) — alongside management + videographer.
    _, err = _require_photo_access()
    if err:
        return err
    names = {c.id: c.name for c in Client.query.all()}
    q = VideographerPhoto.query.filter_by(deleted_at=None)
    if request.args.get('client_id'):
        q = q.filter_by(client_id=int(request.args['client_id']))
    photos = q.order_by(VideographerPhoto.shoot_date.desc().nullslast()).all()
    return jsonify(photos=[
        {'id': p.id, 'client_id': p.client_id, 'client_name': names.get(p.client_id),
         'file_id': p.file_id, 'file_name': p.file_name, 'shoot_date': p.shoot_date,
         'file_size': p.file_size, 'used': p.used_at is not None,
         'used_at': p.used_at.isoformat() if p.used_at else None}
        for p in photos])


PHOTOS_SUBFOLDER = 'Çekim Fotoğrafları'
IMAGE_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif', 'tif', 'tiff'}


@bp.post('/videographer/photos/upload')
def vg_photo_upload():
    """Uploads the photos the videographer shot into the "Çekim Fotoğrafları"
    folder under the client's Drive root; serves as a source for designers."""
    u, err = _require_vg()
    if err:
        return err
    client_id = request.form.get('client_id', type=int)
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    if not _can_shoot(u, client_id):
        return jsonify(error='not authorized to upload photos for this client'), 403
    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return jsonify(error='no file'), 400
    root = _extract_folder_id(c.drive_meta)
    if not root:
        return jsonify(error="client's Drive root folder is not set"), 400
    try:
        folder_id = dg.ensure_subfolder(root, PHOTOS_SUBFOLDER)
    except dg.DriveError as e:
        return jsonify(error=f'folder could not be created: {e}'), 502
    shoot_date = request.form.get('shoot_date') or None
    saved, errors = [], []
    for f in files:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in IMAGE_EXT:
            errors.append(f'{f.filename}: not a photo')
            continue
        # Measure size from the stream + stream the upload to Drive (the whole file never enters RAM).
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            errors.append(f'{f.filename}: exceeds the 500 MB limit')
            continue
        mime = f.mimetype or 'image/jpeg'
        # Local temp copy (candidate for the 21-day store) — the Drive upload
        # streams from this file, staying bounded by the RAM chunk size (same
        # pattern as `upload()`). If the temp file can't be written, fall back
        # to streaming. **Added 2026-07-30**: the local copy is required so
        # photos too can be served from the server via the `/m/<file_id>`
        # permanent link; before this, photo uploads streamed straight to
        # Drive and never entered `media_store` (the link would fall through
        # to Drive from day one).
        tmp = media_store.stage(f.stream)
        try:
            if tmp:
                with open(tmp, 'rb') as fh:
                    meta = dg.upload_file(folder_id, f.filename, fh, mime)
            else:
                f.stream.seek(0)
                meta = dg.upload_file(folder_id, f.filename, f.stream, mime)
        except dg.DriveError as e:
            media_store.discard(tmp)
            log.exception('çekim fotoğrafı yüklenemedi (client=%s dosya=%s boyut=%s)',
                          client_id, f.filename, size)
            errors.append(f'{f.filename}: {e}')
            continue
        # Best-effort: if it can't be persisted, the upload is still valid (Drive is canonical).
        media_store.commit(tmp, meta.get('id'), meta.get('mimeType') or mime, f.filename)
        try:
            dg.grant_anyone_reader(meta.get('id'))
        except dg.DriveError:
            pass  # public permission is best-effort; upload is still valid if it fails
        p = VideographerPhoto(
            client_id=client_id, shoot_date=shoot_date, folder_id=folder_id,
            file_id=meta.get('id'), file_name=meta.get('name') or f.filename,
            mime_type=meta.get('mimeType') or f.mimetype,
            file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else size,
            uploaded_by=u['sub'], uploaded_at=utcnow())
        db.session.add(p)
        db.session.flush()
        saved.append({'id': p.id, 'file_id': p.file_id, 'file_name': p.file_name,
                      'shoot_date': p.shoot_date})
    db.session.commit()
    # Designers/content creators are waiting on these photos (2026-08-05). The
    # notification goes only to that client's slots — management is not in
    # this flow. Uploaded in batches (a 47-photo shoot has been observed) →
    # 30-min coalesce, in notifications.
    if saved:
        try:
            notifications.notify_photos_uploaded(client_id, len(saved))
        except Exception:  # noqa: BLE001 — notification is best-effort, upload is critical
            log.exception('çekim fotoğrafı bildirimi başarısız (client=%s)', client_id)
    return jsonify(saved=saved, errors=errors), (201 if saved else 400)


@bp.delete('/videographer/photos/<int:photo_id>')
def vg_photo_delete(photo_id):
    u, err = _require_vg()
    if err:
        return err
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='photo not found'), 404
    if not _can_shoot(u, p.client_id):
        return jsonify(error='not authorized'), 403
    p.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.delete('/videographer/uploads/<int:upload_id>')
def vg_upload_hard_delete(upload_id):
    """PERMANENTLY delete a videographer's VIDEO upload (project owner decision, 2026-07-31).

    NOT a twin of the existing `DELETE /uploads/<id>` endpoint: that one is
    superadmin-only and soft-deletes (`deleted_at`), this one actually
    deletes. In order:
      1. Approval records linked by FK (`upload_review`, `upload_pre_approval`,
         `review_excluded_upload`) — if not cleaned up, DELETE raises an FK error.
      2. The `card_uploads` row.
      3. The local copy on the server (`media_store.remove`) — if left in
         place, the deleted video would still be reachable for another 21
         days via `/m/<file_id>`.
      4. The Drive file, into the **trash** (not permanent deletion; gives a
         wrong click a 30-day recovery window). Best-effort: if Drive fails,
         the panel record is still deleted and the response says
         `drive_ok:false` (same pattern as depot.py).

    Permissions: videographer and **designer** (2026-08-05, designers also
    upload video) can delete ONLY their own uploads (`uploaded_by`);
    management is unrestricted. The role list is specific to this endpoint,
    NOT `_require_vg`: adding designer to `_require_vg` would also open up the
    shoot plan and photo endpoints. Scope is `category='video'` only — this
    endpoint can't touch a designer's POST/story upload (that goes through
    the superadmin-only soft-delete endpoint). The `can_delete` flag on the
    board (`_build_rows`) follows the same rule: management unrestricted /
    others only their own upload.
    A shared video is NOT blocked (project owner decision: warn in the panel, still delete)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'videographer', 'designer'):
        return jsonify(error='not authorized'), 403
    up = CardUpload.query.filter_by(id=upload_id, category='video',
                                    deleted_at=None).first()
    if up is None:
        return jsonify(error='video not found'), 404
    if u.get('role') != 'management' and up.uploaded_by != u.get('sub'):
        return jsonify(error='you can only delete videos you uploaded yourself'), 403

    return jsonify(ok=True, drive_ok=_hard_delete_video(up))


def _hard_delete_video(up):
    """PERMANENTLY delete a video upload; returns True if Drive succeeded.

    Has two callers — the manual "Delete" button (`vg_upload_hard_delete`) and
    automatic deletion when a revision arrives (`_supersede_previous_videos`).
    Kept shared on purpose: if written separately, one of them (e.g. web
    derivative cleanup) would silently fall behind. Permission checking is the
    caller's job, NOT done here."""
    file_id = up.file_id
    UploadReview.query.filter_by(upload_id=up.id).delete(synchronize_session=False)
    UploadPreApproval.query.filter_by(upload_id=up.id).delete(synchronize_session=False)
    ReviewExcludedUpload.query.filter_by(upload_id=up.id).delete(synchronize_session=False)
    db.session.delete(up)
    db.session.commit()

    if file_id:
        media_store.remove(file_id)      # original + preview + web derivative
    drive_ok = True
    if file_id and dg.available():
        try:
            dg.trash_file(file_id)
        except dg.DriveError:
            log.exception('video Drive çöpüne taşınamadı: %s', file_id)
            drive_ok = False
    return drive_ok


@bp.post('/videographer/photos/bulk-delete')
def vg_photos_bulk_delete():
    """Bulk soft-delete of selected photos. Only ones with _can_shoot permission
    are deleted; unauthorized/not-found ids are written to errors."""
    u, err = _require_vg()
    if err:
        return err
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get('ids', [])
           if str(i).lstrip('-').isdigit()]
    if not ids:
        return jsonify(error='ids are required'), 400
    deleted, errors = 0, []
    photos = VideographerPhoto.query.filter(
        VideographerPhoto.id.in_(ids), VideographerPhoto.deleted_at.is_(None)).all()
    found = {p.id for p in photos}
    for p in photos:
        if not _can_shoot(u, p.client_id):
            errors.append(f'{p.id}: not authorized')
            continue
        p.deleted_at = utcnow()
        deleted += 1
    for missing in [i for i in ids if i not in found]:
        errors.append(f'{missing}: not found')
    db.session.commit()
    return jsonify(deleted=deleted, errors=errors)


@bp.post('/videographer/photos/download-zip')
def vg_photos_download_zip():
    """Download selected photos as a single zip. Bytes are pulled from Drive;
    name collisions get a -1/-2 suffix. Only ones with _can_shoot permission are included."""
    import io
    import zipfile
    u, err = _require_photo_access()
    if err:
        return err
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get('ids', [])
           if str(i).lstrip('-').isdigit()]
    if not ids:
        return jsonify(error='ids are required'), 400
    if not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    photos = VideographerPhoto.query.filter(
        VideographerPhoto.id.in_(ids), VideographerPhoto.deleted_at.is_(None)).all()
    photos = [p for p in photos if p.file_id and _can_view_photos(u, p.client_id)]
    if not photos:
        return jsonify(error='no photos to download'), 404
    buf = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in photos:
            try:
                data = dg.download_file(p.file_id)
            except dg.DriveError:
                continue
            name = p.file_name or f'{p.file_id}.jpg'
            if name in used_names:
                stem, dot, ext = name.rpartition('.')
                base = stem if dot else name
                n = 1
                while name in used_names:
                    name = f'{base}-{n}{dot}{ext}' if dot else f'{base}-{n}'
                    n += 1
            used_names.add(name)
            zf.writestr(name, data)
    if not used_names:
        return jsonify(error='files could not be downloaded'), 502
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='application/zip', headers={
        'Content-Disposition': 'attachment; filename="fotograflar.zip"'})


@bp.post('/videographer/photos/<int:photo_id>/used')
def vg_photo_mark_used(photo_id):
    """Mark/unmark a photo as 'used by designer'. Permission: management or
    designer (the trigger on the designer board will call this endpoint in a follow-up task)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='photo not found'), 404
    used = bool((request.get_json(silent=True) or {}).get('used', True))
    p.used_at = utcnow() if used else None
    p.used_by = u['sub'] if used else None
    db.session.commit()
    return jsonify(used=p.used_at is not None,
                   used_at=p.used_at.isoformat() if p.used_at else None)


@bp.post('/videographer/photos/<int:photo_id>/rename')
def vg_photo_rename(photo_id):
    """Rename a photo (Drive + DB). Extension is preserved; permission: _can_shoot."""
    u, err = _require_vg()
    if err:
        return err
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='photo not found'), 404
    if not _can_shoot(u, p.client_id):
        return jsonify(error='not authorized'), 403
    raw = ((request.get_json(silent=True) or {}).get('name') or '').strip()
    # strip path separators / control characters, cap the length
    raw = re.sub(r'[\\/\x00-\x1f]', '', raw)[:200].strip()
    if not raw:
        return jsonify(error='name cannot be empty'), 400
    # if the user didn't give an extension, keep the original one
    old_ext = (p.file_name or '').rsplit('.', 1)[-1] if '.' in (p.file_name or '') else ''
    if old_ext and '.' not in raw:
        raw = f'{raw}.{old_ext}'
    if p.file_id and dg.available():
        try:
            dg.rename_file(p.file_id, raw)
        except dg.DriveError as e:
            return jsonify(error=f'Drive rename failed: {e}'), 502
    p.file_name = raw
    db.session.commit()
    return jsonify(id=p.id, file_name=p.file_name)


@bp.get('/videographer/photos/<int:photo_id>/download')
def vg_photo_download(photo_id):
    """Download a single photo under its original name (byte stream from Drive). GET → no CSRF needed."""
    from urllib.parse import quote
    u, err = _require_photo_access()
    if err:
        return err
    p = db.session.get(VideographerPhoto, photo_id)
    if p is None or p.deleted_at is not None:
        return jsonify(error='photo not found'), 404
    if not _can_view_photos(u, p.client_id):
        return jsonify(error='not authorized'), 403
    if not p.file_id or not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    try:
        data = dg.download_file(p.file_id)
    except dg.DriveError as e:
        return jsonify(error=f'download failed: {e}'), 502
    name = p.file_name or f'{p.file_id}.jpg'
    ascii_fallback = re.sub(r'[^A-Za-z0-9._-]', '_', name) or 'foto.jpg'
    return Response(data, mimetype=p.mime_type or 'application/octet-stream', headers={
        'Content-Disposition': (f"attachment; filename=\"{ascii_fallback}\"; "
                                f"filename*=UTF-8''{quote(name)}")})


# --- videographer suggestion bot (Phase 5, step 17) ---

@bp.get('/videographer/ideas')
def vg_ideas():
    """The client's AI trend suggestions (default only 'new'; filter with ?status=).
    Newest first. Visible to management + videographer."""
    _, err = _require_vg()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id is required'), 400
    status = request.args.get('status', 'new')
    q = VideographerIdea.query.filter_by(client_id=client_id)
    if status:
        q = q.filter_by(status=status)
    ideas = q.order_by(VideographerIdea.created_at.desc().nullslast(),
                       VideographerIdea.id.desc()).all()
    return jsonify(ideas=[i.to_dict() for i in ideas])


@bp.post('/videographer/ideas/generate')
def vg_ideas_generate():
    """Manually trigger the suggestion bot (client-triggered — GATE 16 §5.2). Low
    priority (batch) + dedup (won't queue a new job if one is already active
    for the same client). Trend scanning + generation is done from the queue
    via `ai_worker.videographer_ideas_handler`."""
    u, err = _require_vg()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    client_id = data['client_id']
    job = jobqueue.enqueue('videographer_ideas', {'client_id': client_id}, priority=0,
                           dedup_key=f'videographer_ideas:{client_id}', created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


def _idea_or_err(u, idea_id):
    idea = db.session.get(VideographerIdea, idea_id)
    if idea is None:
        return None, (jsonify(error='suggestion not found'), 404)
    if not _can_shoot(u, idea.client_id):
        return None, (jsonify(error='not authorized'), 403)
    return idea, None


@bp.post('/videographer/ideas/<int:idea_id>/like')
def vg_idea_like(idea_id):
    """"Like → add to shoot list": creates a SHOOT PLAN task (ShootTask) from the
    suggestion (shoot idea → title, reason → note) and marks the suggestion
    status='accepted'. scheduled_date is optional (if omitted, sits in the
    pool/dateless). Writes to the shoot plan domain (same ShootTask record as
    useShootMutations)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    idea, err = _idea_or_err(u, idea_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        sched = date.fromisoformat(data['scheduled_date']) if data.get('scheduled_date') else None
    except (ValueError, TypeError):
        return jsonify(error='invalid date'), 400
    maxpos = db.session.query(db.func.max(ShootTask.position)).filter_by(
        scheduled_date=sched).scalar()
    task = ShootTask(
        client_id=idea.client_id, scheduled_date=sched,
        title=idea.shoot_idea or 'AI suggestion', content_type='suggestion',
        location_note=idea.reason,
        assigned_to=(u['sub'] if u.get('role') == 'videographer' else None),
        assigned_by=u['sub'], position=(maxpos or 0) + 1, created_by=u['sub'])
    db.session.add(task)
    idea.status = 'accepted'
    db.session.commit()
    return jsonify(task=task.to_dict(), idea=idea.to_dict()), 201


@bp.post('/videographer/ideas/<int:idea_id>/skip')
def vg_idea_skip(idea_id):
    """"Skip": marks the suggestion status='skipped' (drops off the list)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    idea, err = _idea_or_err(u, idea_id)
    if err:
        return err
    idea.status = 'skipped'
    db.session.commit()
    return jsonify(idea=idea.to_dict())


# --- revision requests ---

@bp.post('/revision-request')
def revision_create():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    if kind not in REVISION_KINDS:
        return jsonify(error='invalid kind (design/video)'), 400
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    rev = RevisionRequest(
        client_id=data['client_id'], week_iso=data.get('week_iso'), kind=kind,
        share_id=data.get('share_id'), category=data.get('category'),
        note=data.get('note'), requested_by=u['sub'], status='open')
    db.session.add(rev)
    db.session.commit()
    notifications.notify_revision_requested(kind, rev.client_id, rev.week_iso, rev.note)
    return jsonify(revision=rev.to_dict()), 201


@bp.get('/revisions')
def revisions_list():
    if not current_user():
        return jsonify(error='not signed in'), 401
    q = RevisionRequest.query
    if request.args.get('status'):
        q = q.filter_by(status=request.args['status'])
    if request.args.get('kind'):
        q = q.filter_by(kind=request.args['kind'])
    if request.args.get('client_id'):
        q = q.filter_by(client_id=int(request.args['client_id']))
    revs = q.order_by(RevisionRequest.requested_at.desc()).all()
    return jsonify(revisions=[r.to_dict() for r in revs])


@bp.post('/revisions/<int:rev_id>/resolve')
def revision_resolve(rev_id):
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer', 'videographer'):
        return jsonify(error='not authorized'), 403
    rev = db.session.get(RevisionRequest, rev_id)
    if rev is None:
        return jsonify(error='revision not found'), 404
    rev.status = 'resolved'
    rev.resolved_by = u['sub']
    rev.resolved_at = utcnow()
    db.session.commit()
    notifications.notify_revision_resolved(rev.kind, rev.client_id, rev.week_iso)
    return jsonify(revision=rev.to_dict())


def _used_file_ids(file_ids):
    """Does a sharing card ALREADY exist for these files — even as a draft.

    ShareModal's file picker hides ones already in use, so a second card
    doesn't get opened on the same file and produce duplicate content. Queried
    WEEK-INDEPENDENTLY: the file may have been moved to another week, its card
    stays there but it's still "used". A SINGLE batched query — a
    `Share.query` per upload would cause N+1."""
    if not file_ids:
        return set()
    return {fid for (fid,) in db.session.query(Share.file_id)
            .filter(Share.file_id.in_(file_ids), Share.deleted_at.is_(None))
            .distinct().all()}


@bp.get('/uploads')
def uploads():
    _, err = _require_management()
    if err:
        return err
    q = CardUpload.query.filter_by(deleted_at=None)
    if request.args.get('client_id'):
        q = q.filter_by(client_id=int(request.args['client_id']))
    if request.args.get('week_iso'):
        q = q.filter_by(week_iso=request.args['week_iso'])
    ups = q.order_by(CardUpload.uploaded_at.desc().nullslast()).all()
    used = _used_file_ids({u.file_id for u in ups if u.file_id})
    out = []
    for u in ups:
        d = u.to_dict()
        d['local'] = bool(u.file_id) and media_store.has_original(u.file_id)
        d['used'] = u.file_id in used
        out.append(d)
    return jsonify(uploads=out)


# On the client media page, a block is rendered even for weeks with no files
# (so there's a drop target) — this many neighboring weeks around the current one.
MEDIA_WEEK_WINDOW = 4


def _current_week_iso():
    iso = date.today().isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


@bp.get('/clients/<int:client_id>/media')
def client_media(client_id):
    """ALL of a client's uploads, grouped by week — the client media page.

    NOT a twin of `GET /uploads`: that one is management-only and is
    ShareModal's file-picker pool (single week, flat list). This endpoint is
    also open to the designer, returns ALL weeks, and also lists file-less
    neighboring weeks so there's a drag-and-drop target.

    Shoot photos do NOT appear here: `VideographerPhoto` has no concept of a
    week (it's on the `shoot_date` axis) → can't be moved, the panel fetches
    those from a separate endpoint.
    """
    _, err = _require_designer_or_management()
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    ups = (CardUpload.query
           .filter_by(client_id=client_id, deleted_at=None)
           .order_by(CardUpload.uploaded_at.desc().nullslast())
           .all())
    used = _used_file_ids({u.file_id for u in ups if u.file_id})
    by_week = {}
    for u in ups:
        d = u.to_dict()
        d['local'] = bool(u.file_id) and media_store.has_original(u.file_id)
        d['used'] = u.file_id in used
        d['moved_from_week_iso'] = u.moved_from_week_iso
        by_week.setdefault(u.week_iso, []).append(d)
    cur = _current_week_iso()
    for delta in range(-MEDIA_WEEK_WINDOW, MEDIA_WEEK_WINDOW + 1):
        wk = _shift_week(cur, delta)
        if wk:
            by_week.setdefault(wk, [])
    # "YYYY-Www" is zero-padded, so plain string sorting is chronological.
    weeks = [{'week_iso': w, 'uploads': by_week[w]}
             for w in sorted(by_week, reverse=True)]
    return jsonify(client_id=client_id, weeks=weeks)


@bp.post('/uploads/move-week')
def uploads_move_week():
    """Move selected uploads to the target week — including in the Drive folder.

    NOT a twin of `POST /move-files`: that one takes Drive `file_id`s + a
    SINGLE source week and adopts files with NO panel record. This endpoint
    works via panel records (`upload_ids`) → the source week is read from each
    record itself, so a selection spanning MIXED weeks moves in a single
    request. Open to the designer.

    Partial-success contract: a file that errors on Drive is skipped and
    written to `errors`, the rest keep moving — one file blowing up must not
    crash the whole batch move.
    """
    _, err = _require_designer_or_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    to_iso = data.get('to_week_iso')
    ids = [i for i in (data.get('upload_ids') or []) if isinstance(i, int)]
    if not ids or not to_iso or not _week_number(to_iso):
        return jsonify(error='upload_ids and a valid to_week_iso are required'), 400
    ups = CardUpload.query.filter(CardUpload.id.in_(ids),
                                  CardUpload.deleted_at.is_(None)).all()
    if not ups:
        return jsonify(error='no uploads found to move'), 404
    client_ids = {up.client_id for up in ups}
    if len(client_ids) > 1:
        return jsonify(error="only one client's uploads can be moved per request"), 400
    c, cerr = _client_or_404(client_ids.pop())
    if cerr:
        return cerr
    if not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    to_folder = _resolve_week_folder(c, to_iso, create=True)
    if not to_folder:
        return jsonify(error='target week folder not found and could not be created'), 400
    moved, errors = 0, []
    for up in ups:
        if up.week_iso == to_iso:
            continue
        if up.file_id:
            # If there's no source folder record, None is passed: in that case
            # `dg.move_file` reads and derives the file's current parents.
            from_folder = _resolve_week_folder(c, up.week_iso, create=False)
            try:
                dg.move_file(up.file_id, to_folder, from_folder)
            except dg.DriveError as e:
                errors.append(f'{up.file_name or up.id}: {e}')
                continue
        up.moved_from_week_iso = up.week_iso
        up.week_iso = to_iso
        up.moved_at = utcnow()
        moved += 1
    db.session.commit()
    return jsonify(moved=moved, errors=errors)


@bp.delete('/uploads/<int:upload_id>')
def upload_delete(upload_id):
    """Delete an uploaded card (CardUpload) — superadmin ONLY. Soft-delete
    (deleted_at); it disappears from the board/picker and can be restored.
    The Drive file is not touched. Permission is checked via the real identity
    (impersonation-protected)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if not is_superadmin():
        return jsonify(error='only a superadmin can delete uploads'), 403
    up = CardUpload.query.filter_by(id=upload_id, deleted_at=None).first()
    if up is None:
        return jsonify(error='upload not found'), 404
    up.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


# --- priority ---

@bp.post('/priority')
def priority_toggle():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    p = ClientPriority.query.filter_by(client_id=data['client_id'], week_iso=week_iso).one_or_none()
    if p is None:
        p = ClientPriority(client_id=data['client_id'], week_iso=week_iso)
        db.session.add(p)
    if p.cleared_at is None and p.set_at is not None and p.id is not None:
        # was active → clear it
        p.cleared_at = utcnow()
        active = False
    else:
        p.set_at = utcnow()
        p.set_by = u['sub']
        p.cleared_at = None
        p.cleared_reason = None
        active = True
    db.session.commit()
    # A priority flag means "bump this up" — if the production team never sees
    # it, the flag changes nothing (2026-08-05). Only notified WHEN THE FLAG IS
    # SET, not when it's cleared.
    if active:
        try:
            notifications.notify_priority_marked(data['client_id'], week_iso, u.get('name'))
        except Exception:  # noqa: BLE001 — notification is best-effort
            log.exception('öncelik bildirimi başarısız (client=%s)', data.get('client_id'))
    return jsonify(active=active)


# --- review link ---

def _grant_video_perms(file_ids):
    """Grant video files 'anyone with link → reader' so the "open in Drive"
    link on the approval page can be opened without a session/with a foreign
    Google account.

    VIDEO ONLY: images go through the proxy, no need to open those to
    everyone. Best-effort — if it fails, the link still works (the client sees
    the poster, hits a permission wall on Drive). Idempotent. Runs in the
    background: the Drive API call takes ~200-500ms per file, the manager's
    button shouldn't wait on it.
    """
    for fid in file_ids:
        try:
            dg.grant_anyone_reader(fid)
        except Exception as e:  # noqa: BLE001 — permission is best-effort, link is not critical
            log.warning('review-link izin verilemedi (%s): %s', fid, e)


def _spawn_video_perm_grant(client_id, week_iso):
    fids = [s.file_id for s in Share.query.filter_by(
        client_id=client_id, week_iso=week_iso, kind='video', deleted_at=None).all()
        if s.file_id]
    if not fids:
        return
    threading.Thread(target=_grant_video_perms, args=(fids,), daemon=True).start()


@bp.post('/review-link')
def review_link():
    # The designer also generates approval links (from their own board, for any client — full action).
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    existing = ReviewLink.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso, revoked=False).first()
    # Permissions are refreshed on every call: videos added after the link was created should also be covered.
    _spawn_video_perm_grant(data['client_id'], week_iso)
    count = len(review_visible_uploads(data['client_id'], week_iso))
    if existing:
        return jsonify(token=existing.token, share_count=count)
    link = ReviewLink(token=secrets.token_urlsafe(32), client_id=data['client_id'],
                      week_iso=week_iso, created_by=u['sub'])
    db.session.add(link)
    db.session.commit()
    return jsonify(token=link.token, share_count=count)


@bp.post('/pre-approval-link')
def pre_approval_link():
    """Generate a pre-approval link (designer or management) - sent to the manager.

    A token SEPARATE from the client link; only staff can open the page,
    only management can decide. The existing link is reused for the same
    (client, week)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    week_iso = data.get('week_iso')
    count = len(review_visible_uploads(data['client_id'], week_iso, include_excluded=True))
    existing = PreApprovalLink.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso, revoked=False).first()
    if existing:
        return jsonify(token=existing.token, share_count=count)
    link = PreApprovalLink(token=secrets.token_urlsafe(32), client_id=data['client_id'],
                           week_iso=week_iso, created_by=u['sub'])
    db.session.add(link)
    db.session.commit()
    return jsonify(token=link.token, share_count=count)


@bp.post('/review-link/revoke')
def review_link_revoke():
    u, err = _require_management()
    if err:
        return err
    token = (request.get_json(silent=True) or {}).get('token')
    link = ReviewLink.query.filter_by(token=token).one_or_none()
    if link is None:
        return jsonify(error='link not found'), 404
    link.revoked = True
    db.session.commit()
    return jsonify(ok=True)


# --- client approval link (manual selection, 2026-08-06) ---

# Week window shown in the modal: the board's week ± this many (project owner:
# "the current week, the previous week and the next week").
APPROVAL_WEEK_WINDOW = 1


def _published_file_ids(file_ids):
    """Which of these files are used in a card marked PUBLISHED.

    NOT a twin of `_used_file_ids`: that one asks whether a card has been
    opened (including drafts, used by ShareModal's picker so a second card
    doesn't get opened), this one asks whether it's actually published. On the
    client approval link the criterion is published (project owner,
    2026-08-06): a design that only has a draft card should still be
    submittable for approval.
    WEEK-INDEPENDENT: even if the file was moved to another week, its card may
    be published there.
    """
    if not file_ids:
        return set()
    return {fid for (fid,) in db.session.query(Share.file_id)
            .filter(Share.file_id.in_(file_ids), Share.deleted_at.is_(None),
                    Share.status == 'published')
            .distinct().all()}


def _sent_upload_ids(client_id):
    """Upload ids that appear in previously generated approval links for this client.

    Doesn't filter, only flags ("sent before" badge): sending the same design
    a second time is legitimate (resubmitting for approval after a revision),
    but should be visible. Since the number of links per client is small, this
    is merged in Python rather than an inside-JSONB query — so the same code
    path also runs under the sqlite tests."""
    ids = set()
    for (uids,) in db.session.query(ClientApprovalLink.upload_ids).filter_by(
            client_id=client_id).all():
        ids.update(i for i in (uids or []) if isinstance(i, int))
    return ids


@bp.get('/approval-candidates')
def approval_candidates():
    """List for the client approval link modal: post/video uploads within week ±
    APPROVAL_WEEK_WINDOW that are NOT YET PUBLISHED (project owner, 2026-08-06).

    Deliberately SEPARATE from `review_visible_uploads`: that one has a scope
    of (client, week) and the gradual pre-approval gate; here a human makes
    the selection, so the filter is minimal — only the rule "don't resubmit
    something already published" is applied.
    """
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    try:
        client_id = int(request.args.get('client_id', ''))
    except ValueError:
        return jsonify(error='client_id is required'), 400
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    week_iso = request.args.get('week_iso') or _current_week_iso()
    weeks = [w for w in (_shift_week(week_iso, d)
                         for d in range(-APPROVAL_WEEK_WINDOW, APPROVAL_WEEK_WINDOW + 1))
             if w]
    if not weeks:
        return jsonify(error='invalid week_iso'), 400
    ups = (CardUpload.query
           .filter(CardUpload.client_id == client_id,
                   CardUpload.week_iso.in_(weeks),
                   CardUpload.deleted_at.is_(None),
                   CardUpload.category.in_(REVIEW_CATEGORIES))
           .order_by(CardUpload.uploaded_at.asc().nullslast(), CardUpload.id)
           .all())
    published = _published_file_ids({u_.file_id for u_ in ups if u_.file_id})
    ups = [u_ for u_ in ups if u_.file_id not in published]
    reviews = {r.upload_id: r for r in UploadReview.query.filter(
        UploadReview.upload_id.in_([u_.id for u_ in ups])).all()} if ups else {}
    sent = _sent_upload_ids(client_id)
    by_week = {w: [] for w in weeks}
    for u_ in ups:
        d = u_.to_dict()
        d['local'] = bool(u_.file_id) and media_store.has_original(u_.file_id)
        d['sent_before'] = u_.id in sent
        rv = reviews.get(u_.id)
        d['review'] = rv.to_dict() if rv else None
        by_week.setdefault(u_.week_iso, []).append(d)
    return jsonify(client_id=client_id, week_iso=week_iso,
                   weeks=[{'week_iso': w, 'uploads': by_week.get(w, [])} for w in weeks])


@bp.post('/approval-link')
def approval_link():
    """Generate a client approval link for the selected uploads → `/onay/<token>`.

    The existing link is reused for the same client + the SAME selection
    (someone copying the same selection twice shouldn't get two different
    links). If the selection changes, a NEW link is generated: a link's
    content is frozen — changing what's behind a link already sent to a
    client would split "what they approved" from "what they saw".
    """
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    client_id = data['client_id']
    ids = [i for i in (data.get('upload_ids') or []) if isinstance(i, int)]
    if not ids:
        return jsonify(error='select at least one item'), 400
    ups = (CardUpload.query
           .filter(CardUpload.id.in_(ids),
                   CardUpload.client_id == client_id,
                   CardUpload.deleted_at.is_(None),
                   CardUpload.category.in_(REVIEW_CATEGORIES))
           .all())
    if len(ups) != len(set(ids)):
        return jsonify(error='selection contains invalid items'), 400
    # Order is preserved: the client sees things on the page in the order the designer selected them.
    valid = {u_.id for u_ in ups}
    ordered = [i for i in dict.fromkeys(ids) if i in valid]
    _spawn_upload_perm_grant(ups)
    existing = ClientApprovalLink.query.filter_by(
        client_id=client_id, revoked=False).all()
    for link in existing:
        if list(link.upload_ids or []) == ordered:
            return jsonify(token=link.token, count=len(ordered), reused=True)
    link = ClientApprovalLink(token=secrets.token_urlsafe(32), client_id=client_id,
                              upload_ids=ordered, created_by=u['sub'])
    db.session.add(link)
    db.session.commit()
    return jsonify(token=link.token, count=len(ordered), reused=False)


def _spawn_upload_perm_grant(uploads):
    """Grant "anyone with the link" permission to the selected videos (in the background).

    `_spawn_video_perm_grant` reads from Share records; in this flow there's
    no sharing card, the source is uploads directly. The page streams the
    video through itself while a local copy exists, but once the 21-day
    window expires it falls through to Drive — if the permission isn't
    granted, the client can't open that video."""
    fids = [u.file_id for u in uploads if u.file_id and u.category == 'video']
    if not fids:
        return
    threading.Thread(target=_grant_video_perms, args=(fids,), daemon=True).start()


@bp.post('/approval-link/revoke')
def approval_link_revoke():
    u, err = _require_management()
    if err:
        return err
    token = (request.get_json(silent=True) or {}).get('token')
    link = ClientApprovalLink.query.filter_by(token=token).one_or_none()
    if link is None:
        return jsonify(error='link not found'), 404
    link.revoked = True
    db.session.commit()
    return jsonify(ok=True)


@bp.get('/approval-links')
def approval_links():
    """The client's generated approval links — the modal's "previous submissions" list.
    The note the client wrote is also read from here (the only way to read it in the panel)."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer'):
        return jsonify(error='not authorized'), 403
    try:
        client_id = int(request.args.get('client_id', ''))
    except ValueError:
        return jsonify(error='client_id is required'), 400
    rows = (ClientApprovalLink.query.filter_by(client_id=client_id)
            .order_by(ClientApprovalLink.id.desc()).limit(20).all())
    return jsonify(links=[dict(r.to_dict(), count=len(r.upload_ids or [])) for r in rows])


# --- special day card ---

@bp.post('/special-card/publish')
def special_publish():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    if db.session.get(SpecialDayEvent, data.get('event_id')) is None:
        return jsonify(error='special day not found'), 404
    week_iso = data.get('week_iso')
    sp = SpecialCardStatus.query.filter_by(
        client_id=data['client_id'], week_iso=week_iso, event_id=data['event_id']).one_or_none()
    if sp is None:
        sp = SpecialCardStatus(client_id=data['client_id'], week_iso=week_iso,
                               event_id=data['event_id'])
        db.session.add(sp)
    sp.published_at = utcnow()
    sp.published_by = u['sub']
    db.session.commit()
    return jsonify(special_card=sp.to_dict())


# --- special days ---

SD_EVENT_FIELDS = ('day_name', 'description', 'active', 'month', 'year', 'type',
                   'date_num', 'date_start', 'date_end', 'client_id')


@bp.get('/special-days/events')
def sd_events():
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return jsonify(error='not authorized'), 403
    q = SpecialDayEvent.query.filter_by(active=True)
    if request.args.get('month'):
        q = q.filter_by(month=request.args.get('month', type=int))
    if request.args.get('year'):
        q = q.filter_by(year=request.args.get('year', type=int))
    client_id = request.args.get('client_id', type=int)
    evs = [e for e in q.order_by(SpecialDayEvent.date_num, SpecialDayEvent.day_name).all()
           if e.client_id is None or e.client_id == client_id]
    return jsonify(events=[e.to_dict() for e in evs])


@bp.get('/special-days/overview')
def sd_overview():
    """Calendar view: the month's special days **SELECTED BY CLIENTS** + which
    brands selected each one. Same roles as sd_events.

    **RULE (2026-07-31, project owner): an unselected day does NOT appear on
    the calendar.** The calendar is not a "board of suggested days", it's a
    board of "days clients wanted content for this month". The previous
    behavior returned the month's ENTIRE catalog; ones with no selection
    showed up as gray chips on the calendar, filling the grid with
    suggestions and making real commitments invisible.

    Brand mapping is NOW FROM A SINGLE SOURCE: `SpecialDaySelection` (what the
    client checked off from their selection link). An event's `client_id`
    (a client-specific day) does **NOT automatically count as selected** —
    the selection page (`special_days._events_for`) also offers
    client-specific days alongside general ones, meaning a client-specific
    day can also be checked; if it wasn't checked, it's also "offered but not
    selected" and there's no reason for it to behave differently from general
    days.

    Side effect (intended): since the selection page only offers
    `status='approved'` days, a draft (`draft`) day can never be selected at
    all → so it never appears on the calendar either. Catalog management
    (drafts, unselected, deleted) still lives in the LIST view via
    `sd_events` as before — this endpoint only feeds the calendar."""
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return jsonify(error='not authorized'), 403
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    evs = (SpecialDayEvent.query.filter_by(active=True, month=month, year=year)
           .order_by(SpecialDayEvent.date_num, SpecialDayEvent.day_name).all())
    names = {c.id: c.name for c in Client.query.filter_by(status='active').all()}
    # event_id -> names of clients who selected it (from the selection link)
    selected = {}
    for s in SpecialDaySelection.query.filter_by(month=month, year=year).all():
        cname = names.get(s.client_id)
        if not cname:
            continue
        for eid in (s.selected_event_ids or []):
            selected.setdefault(eid, set()).add(cname)
    items = []
    for e in evs:
        cl = selected.get(e.id)
        if not cl:
            continue        # an unselected day does NOT enter the calendar (see docstring)
        items.append({**e.to_dict(), 'client_names': sorted(cl)})
    return jsonify(items=items)


@bp.post('/special-days/events')
def sd_event_create():
    _, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if not (data.get('day_name') or '').strip():
        return jsonify(error='day_name is required'), 400
    # manual entry (management) → stays approved/manual; AI inserts stay draft/ai via the ORM default.
    e = SpecialDayEvent(active=True, status='approved', generated_by='manual')
    for f in SD_EVENT_FIELDS:
        if f in data:
            setattr(e, f, data[f])
    db.session.add(e)
    db.session.commit()
    return jsonify(event=e.to_dict()), 201


@bp.patch('/special-days/events/<int:event_id>')
def sd_event_update(event_id):
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e is None:
        return jsonify(error='special day not found'), 404
    for f in SD_EVENT_FIELDS:
        if f in (request.get_json(silent=True) or {}):
            setattr(e, f, request.get_json()[f])
    db.session.commit()
    return jsonify(event=e.to_dict())


@bp.delete('/special-days/events/<int:event_id>')
def sd_event_delete(event_id):
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e:
        db.session.delete(e)
        db.session.commit()
    return jsonify(ok=True)


@bp.post('/special-days/selection-link')
def sd_selection_link():
    _, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    month, year = data.get('month'), data.get('year')
    sel = SpecialDaySelection.query.filter_by(
        client_id=data['client_id'], month=month, year=year).first()
    if sel is None:
        sel = SpecialDaySelection(client_id=data['client_id'], month=month, year=year,
                                  token=secrets.token_urlsafe(24), selected_event_ids=[])
        db.session.add(sel)
        db.session.commit()
    elif not sel.token:
        sel.token = secrets.token_urlsafe(24)
        db.session.commit()
    return jsonify(token=sel.token)


@bp.post('/special-day-events/<int:event_id>/approve')
def sd_event_approve(event_id):
    """Approve a draft (AI-generated) special day → status='approved'."""
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e is None:
        return jsonify(error='special day not found'), 404
    e.status = 'approved'
    db.session.commit()
    return jsonify(event=e.to_dict())


@bp.post('/special-day-events/<int:event_id>/reject')
def sd_event_reject(event_id):
    """Reject → leave it as a draft (status='draft'); stays hidden from read surfaces."""
    _, err = _require_management()
    if err:
        return err
    e = db.session.get(SpecialDayEvent, event_id)
    if e is None:
        return jsonify(error='special day not found'), 404
    e.status = 'draft'
    db.session.commit()
    return jsonify(event=e.to_dict())


def _next_month_year():
    """The next calendar month (month, year) relative to today — December → next year's January.
    (Same as ai_worker._next_month; duplicated here since sharing→ai_worker would be a circular import.)"""
    d = date.today()
    return (1, d.year + 1) if d.month == 12 else (d.month + 1, d.year)


@bp.post('/special-day-events/generate')
def sd_event_generate():
    """Manually trigger the special day bot (spec 5→3, "extra research via prompt"). If no
    month is given, uses next month. Low priority (batch) + dedup (won't queue a new job
    if one is already active for the same month)."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    month, year = data.get('month'), data.get('year')
    if not month or not year:
        month, year = _next_month_year()
    month, year = int(month), int(year)
    payload = {'month': month, 'year': year}
    prompt = (data.get('prompt') or '').strip()
    if prompt:
        payload['prompt'] = prompt
    job = jobqueue.enqueue('special_days', payload, priority=0,
                           dedup_key=f'special_days:{year}-{month}', created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


# --- client caption settings (Phase 1b — read/write client defaults) ---

@bp.get('/clients/<int:client_id>/caption-settings')
def client_caption_settings_get(client_id):
    """Returns the client's caption generation defaults (empty object if none)."""
    _, err = _require_management()
    if err:
        return err
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    return jsonify(caption_settings=c.caption_settings or {})


@bp.put('/clients/<int:client_id>/caption-settings')
def client_caption_settings_put(client_id):
    """Saves the client's caption generation defaults. Only known schema keys
    (ai_context.CAPTION_SETTINGS_DEFAULTS) are accepted, the rest are dropped."""
    _, err = _require_management()
    if err:
        return err
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    data = request.get_json(silent=True) or {}
    c.caption_settings = {k: v for k, v in data.items()
                          if k in ai_context.CAPTION_SETTINGS_DEFAULTS}
    db.session.commit()
    return jsonify(caption_settings=c.caption_settings)


# --- caption generation (async job → ai_worker caption handler) ---

@bp.post('/shares/<int:share_id>/caption')
def share_caption(share_id):
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    # Generate-time settings (Phase 1b): added to the payload from the
    # request body's `settings` (if omitted, the old `{share_id}` payload is
    # kept — backward compatible).
    data = request.get_json(silent=True) or {}
    settings = data.get('settings')
    payload = {'share_id': s.id}
    if isinstance(settings, dict) and settings:
        payload['settings'] = settings
    # Regenerate (feedback): user feedback + the previous disliked caption.
    feedback = (data.get('feedback') or '').strip()
    if feedback:
        payload['feedback'] = feedback[:500]
        prev = (data.get('previous_caption') or '').strip()
        if prev:
            payload['previous_caption'] = prev[:2000]
    # dedup: normally hitting the same share repeatedly doesn't produce a new
    # job (returns the active job). Regenerate-with-feedback wants a fresh
    # result EVERY TIME, so no dedup (always a fresh job).
    dedup = None if feedback else f'caption:{s.id}'
    job = jobqueue.enqueue('caption', payload, priority=10,
                           dedup_key=dedup, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.post('/shares/<int:share_id>/media')
def share_media(share_id):
    """Queue media processing for a video (frames + optional transcript) → media_worker.
    The body's `use_transcript` (default False) is the only thing that sends the video's audio to whisper."""
    u, err = _require_management()
    if err:
        return err
    s, e404 = _get_share_or_404(share_id)
    if e404:
        return e404
    data = request.get_json(silent=True) or {}
    payload = {'share_id': s.id}
    if data.get('use_transcript'):
        payload['use_transcript'] = True
    job = jobqueue.enqueue('media', payload, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.get('/jobs/<int:job_id>')
def job_status(job_id):
    _, err = _require_management()
    if err:
        return err
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify(error='job not found'), 404
    return jsonify(job=job.to_dict())


# --- AI image generation (Phase 6, step 19) ---
# GATE 18 decision: generation happens in ai_worker.image_gen_handler via
# Magnific/Freepik REST + API key (NO MCP). These endpoints only queue/list/
# approve the job. The KVKK (Turkish data-protection law) consent gate
# (spike §5) is applied BOTH here (a pre-check — fast feedback to the user)
# AND in the handler (the actual gate). Writes are management-only (client
# images go to a 3rd party).

@bp.get('/image-generations')
def image_generations_list():
    """The client's AI image generations (newest first). Filter with ?status= (default all)."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id is required'), 400
    q = ImageGeneration.query.filter_by(client_id=client_id)
    status = request.args.get('status')
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(ImageGeneration.created_at.desc().nullslast(),
                      ImageGeneration.id.desc()).all()
    return jsonify(image_generations=[r.to_dict() for r in rows])


@bp.post('/image-generations/generate')
def image_generation_generate():
    """Manually trigger AI image generation (client + reference + preset +
    optional approved brief). Low priority (batch) + dedup (won't queue a new
    job if one is already active for the same client). Generation happens from
    the queue via `ai_worker.image_gen_handler` (REST + API key). KVKK
    pre-check: generation is NOT started for a client without consent (the
    actual gate is in the handler)."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    c, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    # KVKK consent gate pre-check (spike §5) — no submission to Magnific for a client without consent.
    prof = c.brand_profile or {}
    if not prof.get('ai_image_consent'):
        return jsonify(error='client has not given AI image consent (data protection). '
                             'ai_image_consent approval is required on the client record.'), 409
    payload = {'client_id': c.id, 'refs': data.get('refs') or [],
               'settings': data.get('settings') or {}, 'created_by': u['sub']}
    if data.get('brief_id') is not None:
        payload['brief_id'] = data['brief_id']
    job = jobqueue.enqueue('image_gen', payload, priority=0,
                           dedup_key=f'image_gen:{c.id}', created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


def _image_gen_or_err(idn):
    row = db.session.get(ImageGeneration, idn)
    if row is None:
        return None, (jsonify(error='generation not found'), 404)
    return row, None


@bp.post('/image-generations/<int:gen_id>/approve')
def image_generation_approve(gen_id):
    """Approve a generated image (status='approved'). Approval gate: management only."""
    u, err = _require_management()
    if err:
        return err
    row, rerr = _image_gen_or_err(gen_id)
    if rerr:
        return rerr
    row.status = 'approved'
    row.reviewed_by = u['sub']
    row.reviewed_at = utcnow()
    db.session.commit()
    return jsonify(image_generation=row.to_dict())


@bp.post('/image-generations/<int:gen_id>/regenerate')
def image_generation_regenerate(gen_id):
    """Regenerate (8→4 loop): mark the existing generation status='rejected' and
    queue a NEW image_gen job with the same client/reference/settings. Dedup:
    if a job is already active for the same client, returns that one (repeated
    presses are a no-op)."""
    u, err = _require_management()
    if err:
        return err
    row, rerr = _image_gen_or_err(gen_id)
    if rerr:
        return rerr
    row.status = 'rejected'
    row.reviewed_by = u['sub']
    row.reviewed_at = utcnow()
    payload = {'client_id': row.client_id, 'refs': row.refs or [],
               'settings': row.settings or {}, 'created_by': u['sub']}
    if row.brief_id is not None:
        payload['brief_id'] = row.brief_id
    job = jobqueue.enqueue('image_gen', payload, priority=0,
                           dedup_key=f'image_gen:{row.client_id}', created_by=u['sub'])
    db.session.commit()
    return jsonify(job=job.to_dict(), image_generation=row.to_dict()), 202


@bp.get('/brief')
def brief():
    u = current_user()
    if not u:
        return jsonify(error='not signed in'), 401
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return jsonify(error='not authorized'), 403
    client_id = request.args.get('client_id', type=int)
    week_iso = request.args.get('week_iso', '')
    # The designer sees every client's brief (full action — so the "Other
    # Clients" tiles can also open Brief); content_creator/videographer is
    # limited to their assigned client.
    if u['role'] not in ('management', 'designer') and not _is_assigned(
            u['sub'], client_id, 'designer', 'content_creator',
            'videographer_shoot', 'videographer_edit'):
        return jsonify(error="you don't have access to this client"), 403
    # DEFAULT approved-only read. Since 2026-07-30 an AI brief is 'approved'
    # the moment it's born (the approval gate was removed — see
    # models_sharing.WeeklyBrief.status), so in the normal flow this filter
    # hides nothing. It STAYS anyway: a leftover/restored old draft shouldn't
    # silently enter the flow. `include_draft=1` is management-only and only
    # useful for seeing those old records; even if a non-management role sends
    # it, drafts are NEVER shown.
    q = WeeklyBrief.query.filter_by(client_id=client_id, week_iso=week_iso)
    include_draft = (u['role'] == 'management'
                     and request.args.get('include_draft', '') in ('1', 'true', 'yes'))
    if not include_draft:
        q = q.filter_by(status='approved')
    # Ordering by COALESCE(synced_at, created_at): synced_at is NULL for an AI
    # brief — nullslast() would push those behind old imports; falling back
    # to created_at picks the newest one.
    b = q.order_by(db.func.coalesce(WeeklyBrief.synced_at, WeeklyBrief.created_at).desc()).first()
    return jsonify(brief=b.to_dict() if b else None)


@bp.get('/magnific-credits')
def magnific_credits():
    """Remaining Magnific credit (panel top-bar badge). Reads from cache
    (AppSetting) — NO live MCP call. Refreshed: by a timer twice a day + after
    every image generation (ai_worker.magnific_credits_handler).
    refreshing=whether an active refresh job exists."""
    import json as _json
    _, err = _require_management()
    if err:
        return err
    raw = AppSetting.get('magnific_credits')
    credits = None
    if raw:
        try:
            credits = _json.loads(raw)
        except ValueError:
            credits = None
    refreshing = Job.query.filter(Job.type == 'magnific_credits',
                                  Job.status.in_(('queued', 'running'))).first() is not None
    return jsonify(credits=credits, refreshing=refreshing)


@bp.post('/image-gen/prompt-examples')
def image_gen_prompt_examples():
    """3 example image prompts from the brief — job enqueue (runs in the claude
    worker; the web process CANNOT run claude — no CLI in svc-agency). The
    panel waits on the job with pollJob; result is job.result.examples.
    Doesn't spend Magnific credits."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    _, cerr = _client_or_404(data.get('client_id'))
    if cerr:
        return cerr
    brief = WeeklyBrief.query.filter_by(id=data.get('brief_id'),
                                        client_id=data['client_id']).first()
    if brief is None:
        return jsonify(error='brief not found'), 404
    job = jobqueue.enqueue('prompt_examples',
                           {'client_id': data['client_id'], 'brief_id': brief.id},
                           priority=10,  # interactive — same priority as caption
                           dedup_key=f'prompt_examples:{data["client_id"]}:{brief.id}',
                           created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.post('/image-gen/convert-prompt')
def image_gen_convert_prompt():
    """Convert the prompt to English+JSON — job enqueue (in the claude worker).
    The panel waits with pollJob; result is job.result.prompt. Doesn't spend
    Magnific credits."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify(error='prompt is required'), 400
    job = jobqueue.enqueue('prompt_convert', {'prompt': prompt[:8000]},
                           priority=10, dedup_key=None, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


# --- client brand images (logo + fixed standard images) ---

ASSET_KINDS = ('logo', 'standard')
ASSET_MAX_BYTES = 20 * 1024 * 1024  # brand image size ceiling (20 MB)


def _require_asset_read(client_id):
    """Brand image READ gate — same pattern as the /brief endpoint: management
    and designer for every client (the designer board shows all clients with
    full action), other production roles only their assigned client.
    Write/delete stays management-only."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not signed in'), 401)
    if u.get('role') not in ('management', 'designer', 'content_creator', 'videographer'):
        return None, (jsonify(error='not authorized'), 403)
    if u['role'] not in ('management', 'designer') and not _is_assigned(
            u['sub'], client_id, 'designer', 'content_creator',
            'videographer_shoot', 'videographer_edit'):
        return None, (jsonify(error="you don't have access to this client"), 403)
    return u, None


@bp.get('/clients/<int:client_id>/assets')
def client_assets_list(client_id):
    """The client's brand images (logo + standard), non-deleted ones."""
    from models import ClientAsset
    _, err = _require_asset_read(client_id)
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    rows = (ClientAsset.query.filter_by(client_id=client_id, deleted_at=None)
            .order_by(ClientAsset.kind, ClientAsset.id.desc()).all())
    return jsonify(assets=[a.to_dict() for a in rows])


@bp.get('/clients/<int:client_id>/assets/<int:asset_id>/download')
def client_asset_download(client_id, asset_id):
    """Download a brand image. Pulls the file from Drive with the SERVICE
    ACCOUNT and streams it; the `drive.google.com/uc?export=download` link
    would depend on the user's own Drive access — logos live in the agency
    account's folder, which the designer/videographer has no permission on.
    Permissions are thus moved entirely to the panel's role gate."""
    from models import ClientAsset
    _, err = _require_asset_read(client_id)
    if err:
        return err
    a = ClientAsset.query.filter_by(id=asset_id, client_id=client_id,
                                    deleted_at=None).first()
    if a is None:
        return jsonify(error='image not found'), 404
    if not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    try:
        data = dg.download_file(a.file_id)
    except dg.DriveError as e:
        log.warning('marka görseli indirilemedi asset=%s: %s', a.id, e)
        return jsonify(error='file could not be downloaded from Drive'), 502
    return send_file(io.BytesIO(data),
                     mimetype=a.mime_type or 'application/octet-stream',
                     as_attachment=True,
                     download_name=a.file_name or f'asset-{a.id}')


@bp.post('/clients/<int:client_id>/assets')
def client_asset_upload(client_id):
    """Upload a brand image (multipart: kind, label?, file). The file goes into
    the 'Marka Görselleri' folder under the client's Drive root. kind='logo' is
    singular: the new one soft-deletes the old one."""
    from models import ClientAsset
    u, err = _require_management()
    if err:
        return err
    c, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    kind = (request.form.get('kind') or '').strip()
    if kind not in ASSET_KINDS:
        return jsonify(error='kind must be logo or standard'), 400
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify(error='file is required'), 400
    if not (f.mimetype or '').startswith('image/'):
        return jsonify(error='only image files (image/*) can be uploaded'), 400
    data = f.read()
    if len(data) > ASSET_MAX_BYTES:
        return jsonify(error='file exceeds the 20 MB limit'), 413
    if not dg.available():
        return jsonify(error='Drive is unavailable'), 503
    root = _extract_folder_id(c.drive_meta)
    if not root:
        return jsonify(error="client's Drive root folder is not set"), 400
    folder = dg.ensure_subfolder(root, 'Marka Görselleri')
    meta = dg.upload_file(folder, f.filename, data, f.mimetype)
    if kind == 'logo':  # logo is singular — drop previous active logos
        for old in ClientAsset.query.filter_by(client_id=client_id, kind='logo',
                                               deleted_at=None).all():
            old.deleted_at = utcnow()
    a = ClientAsset(
        client_id=client_id, kind=kind,
        file_id=meta.get('id'), file_name=meta.get('name') or f.filename,
        mime_type=meta.get('mimeType') or f.mimetype,
        file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else len(data),
        label=(request.form.get('label') or '').strip()[:256] or None,
        uploaded_by=u['sub'], uploaded_at=utcnow())
    db.session.add(a)
    db.session.commit()
    return jsonify(asset=a.to_dict()), 201


@bp.delete('/clients/<int:client_id>/assets/<int:asset_id>')
def client_asset_delete(client_id, asset_id):
    """Remove a brand image (soft delete — the Drive file is not touched)."""
    from models import ClientAsset
    _, err = _require_management()
    if err:
        return err
    a = ClientAsset.query.filter_by(id=asset_id, client_id=client_id,
                                    deleted_at=None).first()
    if a is None:
        return jsonify(error='image not found'), 404
    a.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.get('/image-gen/briefs')
def image_gen_briefs():
    """The client's brief list for the AI image generation form (drafts included, newest first)."""
    u, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    rows = (WeeklyBrief.query.filter_by(client_id=client_id)
            .order_by(WeeklyBrief.week_iso.desc()).all())
    return jsonify(briefs=[{'id': b.id, 'week_iso': b.week_iso,
                            'title': b.title, 'status': getattr(b, 'status', None)}
                           for b in rows])


@bp.post('/brief/generate')
def brief_generate():
    """Manually trigger the weekly brief (BriefPage "Generate/Regenerate").
    `{client_id, week_iso}` required, `force` optional. Low priority (batch,
    same as fan-out) + dedup (won't queue a new job if one is already active
    for the same client+week).

    NO `force` → the handler is idempotent: if a brief already exists it's
    skipped without generating (no generation spent). `force=true` → the
    handler OVERWRITES the existing row IN PLACE (the old text isn't kept).
    This is the only fix path since the approval gate was removed on
    2026-07-30: a bad brief can no longer be stopped by "not approving" it, it
    gets fixed by regenerating. `force` is folded into the dedup key —
    otherwise a pending normal job would swallow the force request."""
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    week_iso = (data.get('week_iso') or '').strip()
    if not client_id or not week_iso:
        return jsonify(error='client_id and week_iso are required'), 400
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    force = data.get('force') in (True, 1, '1', 'true', 'yes')
    payload = {'client_id': client_id, 'week_iso': week_iso}
    dedup = f'brief:{client_id}:{week_iso}'
    if force:
        payload['force'] = True
        dedup += ':force'
    job = jobqueue.enqueue('brief', payload, priority=0,
                           dedup_key=dedup, created_by=u['sub'])
    return jsonify(job=job.to_dict()), 202


@bp.post('/brief/<int:brief_id>/approve')
def brief_approve(brief_id):
    """Approve a draft (AI-generated) brief → status='approved'."""
    _, err = _require_management()
    if err:
        return err
    b = db.session.get(WeeklyBrief, brief_id)
    if b is None:
        return jsonify(error='brief not found'), 404
    b.status = 'approved'
    db.session.commit()
    return jsonify(brief=b.to_dict())


@bp.post('/brief/<int:brief_id>/reject')
def brief_reject(brief_id):
    """Reject → leave it as a draft (status='draft')."""
    _, err = _require_management()
    if err:
        return err
    b = db.session.get(WeeklyBrief, brief_id)
    if b is None:
        return jsonify(error='brief not found'), 404
    b.status = 'draft'
    db.session.commit()
    return jsonify(brief=b.to_dict())


@bp.post('/brief/<int:brief_id>/notes')
def brief_notes(brief_id):
    """Panel→DB writeback (Phase 3): approval/selection/feedback → PARTIAL merge
    into `WeeklyBrief.week_notes` JSONB. Sent keys are overwritten,
    unsent ones are kept. (Option A: DB is the single authority; the vault is
    retired — there's no separate vault writeback.)"""
    _, err = _require_management()
    if err:
        return err
    b = db.session.get(WeeklyBrief, brief_id)
    if b is None:
        return jsonify(error='brief not found'), 404
    data = request.get_json(silent=True) or {}
    notes = dict(b.week_notes or {})
    for k in ('durum', 'onay_tarihi', 'secilen_fikirler', 'gun_atamasi', 'geri_bildirim'):
        if k in data:
            notes[k] = data[k]
    b.week_notes = notes   # new dict assignment → JSONB change is detected
    db.session.commit()
    return jsonify(brief=b.to_dict())


# --- Drive (thumbnail + count) ---

@bp.get('/thumbnail/<file_id>')
def thumbnail(file_id):
    if not current_user():
        return jsonify(error='not signed in'), 401
    # Local preview (first 21 days) — served without ever hitting Drive.
    prev = media_store.find_preview(file_id)
    if prev:
        try:
            resp = send_file(prev, mimetype='image/jpeg', conditional=True)
            resp.headers['Cache-Control'] = 'private, max-age=86400'
            return resp
        except OSError:
            pass  # fall through to Drive if the local file can't be read
    width = min(int(request.args.get('w', 400) or 400), 1024)
    cached = db.session.get(DriveThumbnail, (file_id, width))
    if cached:
        return Response(bytes(cached.data), mimetype=cached.mime,
                        headers={'Cache-Control': 'private, max-age=86400'})
    if not dg.available():
        return '', 404
    try:
        data, mime = dg.thumbnail_bytes(file_id, width)
    except dg.DriveError:
        return '', 502
    if not data:
        return '', 404
    db.session.merge(DriveThumbnail(file_id=file_id, width=width, data=data, mime=mime))
    db.session.commit()
    return Response(data, mimetype=mime,
                    headers={'Cache-Control': 'private, max-age=86400'})


@bp.get('/media/<file_id>')
def media_file(file_id):
    """Local original (21-day window) — Range-enabled (video seek).

    `?dl=1` → download to the browser (attachment); `&name=` the file name to
    download as (Turkish is preserved, werkzeug encodes it via RFC5987). On a
    download request, if the local copy has expired, the original is pulled
    from Drive — the full size always comes through. For non-download
    (inline/video) requests, if there's no local copy, 404 → the frontend
    falls back to Drive."""
    if not current_user():
        return jsonify(error='not signed in'), 401
    dl = request.args.get('dl') == '1'
    name = request.args.get('name') or file_id
    path = media_store.find_original(file_id)
    if path:
        mime = (mimetypes.guess_type(name)[0] or mimetypes.guess_type(path)[0]
                or 'application/octet-stream')
        try:
            resp = send_file(path, mimetype=mime, conditional=True,
                             as_attachment=dl, download_name=(name if dl else None))
            resp.headers['Cache-Control'] = 'private, max-age=3600'
            return resp
        except OSError:
            return '', 404
    # No local copy (21 days expired): only pull the full size from Drive on a download request.
    if dl and dg.available():
        try:
            data = dg.download_file(file_id)
        except dg.DriveError:
            return '', 502
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        return send_file(io.BytesIO(data), mimetype=mime,
                         as_attachment=True, download_name=name)
    return '', 404


@bp.get('/drive-counts')
def drive_counts():
    """Drive file count for that week across all active clients — a SINGLE
    request (instead of ~40 concurrent ones on board load). Drive counts run
    in parallel via a thread pool, 60s cache."""
    _, err = _require_management()
    if err:
        return err
    week_iso = request.args.get('week_iso', '')
    hit = _counts_cache.get(week_iso)
    if hit and (time.monotonic() - hit[1]) < _COUNT_TTL:
        return jsonify(counts=hit[0])
    wn = _week_number(week_iso)
    counts = {}
    if wn and dg.available():
        cids = [c.id for c in Client.query.filter_by(status='active').all()]
        folders = {wf.client_id: wf.folder_id for wf in ClientWeekFolder.query.filter(
            ClientWeekFolder.week_number == wn, ClientWeekFolder.client_id.in_(cids),
            ClientWeekFolder.folder_id.isnot(None)).all()}
        if folders:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as ex:
                results = ex.map(lambda it: (it[0], dg.count_files(it[1])), folders.items())
                counts = {str(cid): cnt for cid, cnt in results if cnt is not None}
    _counts_cache[week_iso] = (counts, time.monotonic())
    return jsonify(counts=counts)


@bp.get('/drive-count/<int:client_id>')
def drive_count(client_id):
    """File count in the client's Drive folder for that week (lazy, cached).
    None = no folder or Drive was unreachable."""
    if not current_user():
        return jsonify(error='not signed in'), 401
    week_iso = request.args.get('week_iso', '')
    key = (client_id, week_iso)
    hit = _count_cache.get(key)
    if hit and (time.monotonic() - hit[1]) < _COUNT_TTL:
        return jsonify(count=hit[0])
    # The board fires ~40 concurrent requests on load; any error (DB/Drive)
    # should return null instead of 500 (badge is hidden), so the page doesn't break.
    try:
        wn = _week_number(week_iso)
        wf = ClientWeekFolder.query.filter_by(client_id=client_id, week_number=wn).first() if wn else None
        count = dg.count_files(wf.folder_id) if (wf and dg.available()) else None
    except Exception:
        db.session.rollback()
        count = None
    _count_cache[key] = (count, time.monotonic())
    return jsonify(count=count)


@bp.post('/special-card/unpublish')
def special_unpublish():
    u, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    sp = SpecialCardStatus.query.filter_by(
        client_id=data.get('client_id'), week_iso=data.get('week_iso'),
        event_id=data.get('event_id')).one_or_none()
    if sp:
        db.session.delete(sp)
        db.session.commit()
    return jsonify(ok=True)


# --- reference accounts (2026-08-07) -----------------------------------------

# Instagram handle: letters/digits/dot/underscore, max 30. **At least one
# letter or digit is REQUIRED** — otherwise a sequence like '..' would count
# as valid and a pasted '../../etc' would turn into a handle (caught in testing).
REFERENCE_HANDLE_RE = re.compile(r'^(?=.*[A-Za-z0-9])[A-Za-z0-9._]{1,30}$')


def _handle_temizle(ham):
    """Reduces whatever the user pasted down to a plain handle.

    Input doesn't come in one shape: '@name', 'instagram.com/name', a full
    URL, a trailing '/'. If a single canonical form isn't enforced, the same
    account gets added as two different rows and the UNIQUE constraint
    becomes useless."""
    s = (ham or '').strip()
    s = re.sub(r'^https?://', '', s, flags=re.I)
    s = re.sub(r'^(www\.)?instagram\.com/', '', s, flags=re.I)
    s = s.split('?')[0].split('/')[0].lstrip('@').strip()
    return s


@bp.get('/clients/<int:client_id>/reference-accounts')
def reference_accounts_list(client_id):
    """The client's reference accounts.

    Production roles see ONLY approved ones (that's the list in the brand
    guide); management sees all of them — they review the candidate list and
    make the decision."""
    from models_reference import ClientReferenceAccount
    u, err = _require_asset_read(client_id)      # same read gate as the brand guide
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    q = ClientReferenceAccount.query.filter_by(client_id=client_id)
    if u.get('role') != 'management':
        q = q.filter_by(status='approved')
    rows = q.order_by(ClientReferenceAccount.status,
                      ClientReferenceAccount.followers.desc().nullslast(),
                      ClientReferenceAccount.id).all()
    return jsonify(accounts=[r.to_dict() for r in rows])


@bp.post('/clients/<int:client_id>/reference-accounts')
def reference_account_add(client_id):
    """Manually add a reference account (management). The same handle can't be added twice."""
    from models_reference import ClientReferenceAccount
    u, err = _require_management()
    if err:
        return err
    _, cerr = _client_or_404(client_id)
    if cerr:
        return cerr
    data = request.get_json(silent=True) or {}
    handle = _handle_temizle(data.get('handle'))
    if not REFERENCE_HANDLE_RE.match(handle or ''):
        return jsonify(error='enter a valid Instagram username'), 400
    mevcut = ClientReferenceAccount.query.filter_by(
        client_id=client_id, handle=handle).first()
    if mevcut:
        return jsonify(error=f'@{handle} already exists for this client',
                       account=mevcut.to_dict()), 409
    acc = ClientReferenceAccount(
        client_id=client_id, handle=handle,
        title=(data.get('title') or '').strip()[:200] or None,
        note=(data.get('note') or '').strip() or None,
        source='manual', added_by=u['sub'],
        # A manually added account is already management's choice — making it
        # go through approval too would be an unnecessary step. The approval
        # gate exists for compiled candidates.
        status='approved', decided_by=u['sub'], decided_at=utcnow())
    db.session.add(acc)
    db.session.commit()
    return jsonify(account=acc.to_dict()), 201


@bp.patch('/clients/<int:client_id>/reference-accounts/<int:acc_id>')
def reference_account_update(client_id, acc_id):
    """Make a decision (approved/rejected) or edit the note — management."""
    from models_reference import ClientReferenceAccount, REFERENCE_STATUSES
    u, err = _require_management()
    if err:
        return err
    acc = ClientReferenceAccount.query.filter_by(id=acc_id, client_id=client_id).first()
    if acc is None:
        return jsonify(error='account not found'), 404
    data = request.get_json(silent=True) or {}
    if 'status' in data:
        if data['status'] not in REFERENCE_STATUSES:
            return jsonify(error='invalid status'), 400
        acc.status = data['status']
        acc.decided_by = u['sub']
        acc.decided_at = utcnow()
    if 'note' in data:
        acc.note = (data.get('note') or '').strip() or None
    if 'title' in data:
        acc.title = (data.get('title') or '').strip()[:200] or None
    db.session.commit()
    return jsonify(account=acc.to_dict())


@bp.delete('/clients/<int:client_id>/reference-accounts/<int:acc_id>')
def reference_account_delete(client_id, acc_id):
    from models_reference import ClientReferenceAccount
    _, err = _require_management()
    if err:
        return err
    acc = ClientReferenceAccount.query.filter_by(id=acc_id, client_id=client_id).first()
    if acc is None:
        return jsonify(error='account not found'), 404
    db.session.delete(acc)
    db.session.commit()
    return jsonify(ok=True)
