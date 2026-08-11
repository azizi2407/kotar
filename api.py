"""Agency JSON API — consumed by the panel SPA. Session comes from SSO (Flask session).

Authorization model: reading = team roles (management/designer/videographer/content_creator),
writing = management only. Mutations (POST/PUT/PATCH/DELETE) require a CSRF token:
the token is fetched from /api/session and sent via the X-CSRFToken header.
"""
import functools
import hmac
import secrets
from datetime import date, timedelta

from flask import Blueprint, jsonify, request, session
from sqlalchemy.exc import IntegrityError

import os

import ai_usage
import client_provision
import jobqueue
import notifications
import ntfy_gateway
from extensions import db
from models import (Client, ClientContact, ClientContract, ClientLocation,
                    ClientTeamAssignment, Notification, NotificationPref,
                    UserHiddenClient, UserRef, iso, utcnow)
from models_sharing import WeeklyBrief
from notify_rules import NORMAL, SEVERITIES
from sso_client import current_user, is_superadmin, real_user

bp = Blueprint('api', __name__)

READ_ROLES = {'management', 'designer', 'videographer', 'content_creator'}

# PUBLIC address the phone subscribes to — separate from `NTFY_BASE_URL`, which is
# internal (localhost); the panel shows this link/QR code to the user.
NTFY_PUBLIC_URL = os.getenv('NTFY_PUBLIC_URL', 'https://ntfy.example.com')


@bp.before_request
def csrf_protect():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = session.get('csrf')
        header = request.headers.get('X-CSRFToken', '')
        if not token or not hmac.compare_digest(token, header):
            return jsonify(error='CSRF validation failed'), 403


def auth_required(write=False):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u:
                return jsonify(error='no active session'), 401
            allowed = {'management'} if write else READ_ROLES
            if u.get('role') not in allowed:
                return jsonify(error='you are not authorized for this action'), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def _require_superadmin():
    """Server admin actions are superadmin-only (real identity). Returns (user, err)."""
    u = current_user()
    if not u:
        return None, (jsonify(error='no active session'), 401)
    if not is_superadmin(real_user()):
        return None, (jsonify(error='you are not authorized for this action'), 403)
    return u, None


@bp.get('/session')
def session_info():
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    imp = session.get('impersonator')
    import sharing as _sharing
    from sso_client import AUTH_MODE
    return jsonify(user=u, csrf=session['csrf'],
                   impersonating=bool(imp),
                   real_user=imp or u,             # real (logged-in) identity
                   can_impersonate=is_superadmin(),  # whether the real identity is superadmin
                   auth_mode=AUTH_MODE,             # panel shows "change password" when 'local'
                   # "My Clients" flagging permission (see sharing.OWNER_EMAIL /
                   # AGENCY_OWNER_EMAIL env) — if empty, no one matches.
                   is_agency_owner=bool(_sharing.OWNER_EMAIL) and
                   u.get('email') == _sharing.OWNER_EMAIL)


@bp.get('/me')
def me():
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    return jsonify(user=u)


# --- impersonation ("view as user") — superadmin only ---

@bp.post('/impersonate')
def impersonate_start():
    """Store the real identity (superadmin), switch session['user'] to the target.
    Authorization is ALWAYS via the real identity — the impersonated user can't call this."""
    real = real_user()
    if not real:
        return jsonify(error='no active session'), 401
    if not is_superadmin(real):
        return jsonify(error='not authorized'), 403
    data = request.get_json(silent=True) or {}
    target = UserRef.query.filter_by(sub=str(data.get('sub') or '')).first()
    if target is None or target.sub == real.get('sub'):
        return jsonify(error='user not found'), 404
    session['impersonator'] = real  # stored on first use; stays real across target switches
    session['user'] = {'sub': target.sub, 'email': target.email,
                       'name': target.name, 'role': target.role}
    return jsonify(user=session['user'], impersonating=True, real_user=real)


@bp.post('/impersonate/stop')
def impersonate_stop():
    real = session.get('impersonator')
    if not real:
        return jsonify(error='you are already using your own identity'), 400
    session['user'] = real
    session.pop('impersonator', None)
    return jsonify(user=real, impersonating=False)


@bp.get('/users')
def users():
    if not current_user():
        return jsonify(error='no active session'), 401
    refs = UserRef.query.order_by(UserRef.name).all()
    return jsonify(users=[r.to_dict() for r in refs])


# --- personal preferences (user-scoped; belongs to the USER, not the page) ---

# Whitelist: prevents key inflation. If a new page needs hiding, a value is added
# here (the table's `scope` column is already ready for it).
PREF_SCOPES = ('videographer_upload',)


def _pref_user():
    """(user, err) — session + panel role. Preferences are always written to the
    EFFECTIVE identity (under impersonation, the target user's preference is edited)."""
    u = current_user()
    if not u:
        return None, (jsonify(error='no active session'), 401)
    if u.get('role') not in READ_ROLES:
        return None, (jsonify(error='you are not authorized for this action'), 403)
    return u, None


def _hidden_set(sub, scope):
    return sorted(cid for (cid,) in db.session.query(UserHiddenClient.client_id)
                  .filter_by(owner_sub=str(sub), scope=scope).all())


@bp.get('/prefs/hidden-clients')
def prefs_hidden_clients_get():
    """Clients the user has hidden in this scope.

    ISOLATION IS STRUCTURAL: `owner_sub` is always `current_user()['sub']`; the
    endpoint does NOT accept a parameter that could address another user's preference."""
    u, err = _pref_user()
    if err:
        return err
    scope = request.args.get('scope') or PREF_SCOPES[0]
    if scope not in PREF_SCOPES:
        return jsonify(error=f'invalid scope: {scope}'), 400
    return jsonify(scope=scope, client_ids=_hidden_set(u['sub'], scope))


@bp.put('/prefs/hidden-clients')
def prefs_hidden_clients_put():
    """Write one client's hidden state: {scope, client_id, hidden}.

    The response is the FULL new set — so the panel doesn't have to derive it
    locally and risk drift. `hidden=true` is an idempotent upsert (no-op if the
    row exists), `false` DELETES the row (no soft-delete: it would occupy the
    UNIQUE slot)."""
    u, err = _pref_user()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    scope = data.get('scope') or PREF_SCOPES[0]
    if scope not in PREF_SCOPES:
        return jsonify(error=f'invalid scope: {scope}'), 400
    client_id = data.get('client_id')
    if not isinstance(client_id, int) or db.session.get(Client, client_id) is None:
        return jsonify(error='client not found'), 404

    row = UserHiddenClient.query.filter_by(owner_sub=str(u['sub']), scope=scope,
                                           client_id=client_id).first()
    if data.get('hidden'):
        if row is None:
            db.session.add(UserHiddenClient(owner_sub=str(u['sub']), scope=scope,
                                            client_id=client_id))
            try:
                db.session.commit()
            except IntegrityError:      # concurrent identical toggle — already hidden
                db.session.rollback()
    elif row is not None:
        db.session.delete(row)
        db.session.commit()
    return jsonify(scope=scope, client_ids=_hidden_set(u['sub'], scope))


# --- clients ---

SCALAR_FIELDS = ('name', 'sector', 'notes', 'client_email', 'instagram_url',
                 'google_drive_url', 'special_days_token', 'sharing_playbook')


def _apply_payload(c, data):
    """Apply fields from the payload; leave unsent fields untouched (partial)."""
    for f in SCALAR_FIELDS:
        if f in data:
            setattr(c, f, data[f])
    if 'contract' in data and data['contract'] is not None:
        if c.contract is None:
            c.contract = ClientContract()
        for f in ClientContract.FIELDS:
            if f in data['contract']:
                setattr(c.contract, f, data['contract'][f])
    if 'contacts' in data:
        c.contacts = [ClientContact(**{k: p.get(k) for k in ('name', 'email', 'phone', 'notes')})
                      for p in (data['contacts'] or [])]
    if 'locations' in data:
        c.locations = [ClientLocation(name=p.get('name'), address=p.get('address'))
                       for p in (data['locations'] or [])]
    if 'team_assignments' in data:
        # UNIQUE(client_id, role_slot): old rows must be deleted before new ones are inserted
        c.team_assignments = []
        if c.id is not None:
            db.session.flush()
        c.team_assignments = [ClientTeamAssignment(role_slot=slot, user_id=str(uid))
                              for slot, uid in (data['team_assignments'] or {}).items() if uid]


def _client_json(c, full=False):
    """Role-aware client JSON — THE SINGLE GATE.

    Roles other than management (designer/content_creator/videographer) read the
    client in the Brand Guide context; commercial (`contract`) and contact
    (`client_email`, `contacts`, `locations`) fields, plus internal notes and
    `special_days_token`, are NEVER included in the response. `write=True`
    endpoints are already management-gated so no distinction is needed there,
    but both read endpoints go through here."""
    return c.to_dict(full=full, sensitive=current_user().get('role') == 'management')


@bp.get('/clients')
@auth_required()
def clients_list():
    status = request.args.get('status', 'active')
    q = Client.query.filter_by(status=status)
    term = request.args.get('q', '').strip()
    if term:
        q = q.filter(Client.name.ilike(f'%{term}%'))
    return jsonify(clients=[_client_json(c) for c in q.order_by(Client.name).all()])


@bp.post('/clients')
@auth_required(write=True)
def clients_create():
    data = request.get_json(silent=True) or {}
    if not (data.get('name') or '').strip():
        return jsonify(error='name field is required'), 400
    c = Client(created_by=current_user()['sub'])
    _apply_payload(c, data)
    db.session.add(c)
    db.session.commit()
    # Provision the new client's Drive folder tree (Cutover C2). Best-effort:
    # if Drive is unreachable/errors, client creation is unaffected (swallowed internally).
    client_provision.provision_client_folders(c)
    return jsonify(client=c.to_dict(full=True)), 201


def _get_or_404(client_id):
    c = db.session.get(Client, client_id)
    if c is None:
        return None, (jsonify(error='client not found'), 404)
    return c, None


@bp.get('/clients/<int:client_id>')
@auth_required()
def clients_detail(client_id):
    c, err = _get_or_404(client_id)
    if err:
        return err
    return jsonify(client=_client_json(c, full=True))


@bp.patch('/clients/<int:client_id>')
@auth_required(write=True)
def clients_update(client_id):
    c, err = _get_or_404(client_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if 'name' in data and not (data['name'] or '').strip():
        return jsonify(error='name cannot be empty'), 400
    _apply_payload(c, data)
    c.updated_at = utcnow()
    c.updated_by = current_user()['sub']
    db.session.commit()
    return jsonify(client=c.to_dict(full=True))


@bp.post('/clients/assign-designer')
@auth_required(write=True)
def clients_assign_designer():
    """Bulk designer assignment — touches ONLY the 'designer' slot (videographer/
    content_creator slots are preserved). Body: {assignments:[{client_id, user_id|null}]}.
    user_id empty/null → that client's designer assignment is removed. Unknown client_id is skipped."""
    data = request.get_json(silent=True) or {}
    items = data.get('assignments')
    if not isinstance(items, list):
        return jsonify(error='assignments list is required'), 400
    updated = 0
    for it in items:
        cid = (it or {}).get('client_id')
        if db.session.get(Client, cid) is None:
            continue
        uid = (it or {}).get('user_id')
        row = ClientTeamAssignment.query.filter_by(client_id=cid, role_slot='designer').one_or_none()
        if uid:
            if row is None:
                db.session.add(ClientTeamAssignment(
                    client_id=cid, role_slot='designer', user_id=str(uid)))
            else:
                row.user_id = str(uid)
        elif row is not None:
            db.session.delete(row)
        updated += 1
    db.session.commit()
    return jsonify(updated=updated)


@bp.delete('/clients/<int:client_id>')
@auth_required(write=True)
def clients_delete(client_id):
    c, err = _get_or_404(client_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    c.status = 'deleted'
    c.deleted_at = utcnow()
    c.deleted_by = current_user()['sub']
    c.deleted_reason = data.get('reason')
    db.session.commit()
    return jsonify(client=c.to_dict(full=True))


@bp.post('/clients/<int:client_id>/restore')
@auth_required(write=True)
def clients_restore(client_id):
    c, err = _get_or_404(client_id)
    if err:
        return err
    c.status = 'active'
    c.restored_at = utcnow()
    c.restored_by = current_user()['sub']
    db.session.commit()
    return jsonify(client=c.to_dict(full=True))


@bp.post('/clients/<int:client_id>/provision-drive')
@auth_required(write=True)
def clients_provision_drive(client_id):
    """(Re-)set up the Drive folder tree for an existing client / fill in what's missing.
    Idempotent; for a migrated client it uses the existing root, doesn't open a new root under the content root."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    result = client_provision.provision_client_folders(c)
    if result is None:
        return jsonify(error='could not set up Drive folder (missing Drive credentials/root, or an error occurred)'), 502
    return jsonify(client=c.to_dict(full=True), provision=result)


# --- Phase 3: vault Ayar integration (brief on/off · read Ayar · catch-up · onboarding) ---

def _upcoming_weeks(n=3):
    """n weeks starting from the current ISO week ('this week → +(n-1)').
    Default 3 → this week, +1, +2 (K7 catch-up range)."""
    base = date.today()
    out = []
    for i in range(n):
        y, w, _ = (base + timedelta(weeks=i)).isocalendar()
        out.append(f'{y}-W{w:02d}')
    return out


AGENCY_NAME = os.getenv('AGENCY_NAME', 'Kotar')

ONBOARDING_PROMPT_TEMPLATE = """\
Sen """ + AGENCY_NAME + """ ajansının içerik stratejistisin. Aşağıdaki müşteri için bir \
**marka Ayar'ı** hazırla. Çıktıyı ben panelde bu müşterinin "Vault Ayar" sekmesindeki \
Düzenle formuna gireceğim — DOSYA YAZMA; alanları aşağıdaki başlıklarla, kopyalanabilir \
biçimde ver.

Müşteri: {ad}
Sektör: {sektor}   (panel kanonik — değiştirme)
(Bu müşteri panelde client_id: {client_id} ile zaten kayıtlı; eşleştirme paneldeki satırla \
otomatik olur — id'yi bir yere yazmana/taşımana gerek yok.)

Önce müşteriyi araştır (web sitesi, Instagram, sektör bağlamı), sonra alanları üret. \
Bilmediğin alanı UYDURMA — boş bırak ya da bana sor.

Şu alanları üret (panel "Vault Ayar" formundaki alanlarla birebir):

Frontmatter alanları:
- Marka Sesi (brand_voice): ton tanımlayıcıları (ör. "Resmi, sıcak, güven veren")
- Hedef Kitle (target_audience)
- Birincil CTA (cta): (ör. "danışın", "rezervasyon yapın")
- Kaçınılacaklar (forbidden): madde listesi
- Renk Paleti (color_palette): #RRGGBB listesi — bilmiyorsan boş bırak, uydurma
- İçerik Dağılımı (content_mix): {{ carousel, editorial_single, reel }} sayıları
- Hashtag setleri (hashtags): {{ konu: [...], marka: [...], sektor: [...] }}
- Paylaşım Günleri (posting_days)
- Haftalık Fikir Sayısı (ideas_per_week): varsayılan 5
- Caption Ayarları (caption_settings): {{ tone, emoji_limit, hashtag_count, lang, char_limit }} (opsiyonel)

Serbest metin bölümleri:
- Marka Rehberi — markanın ana bağlamı, değer cümlesi, görsel dil
- İçerik Sütunları (Content Pillars) — 3-5 tematik eksen (STEERING; brief üretimini yönlendirir)
- Caption Stil İpuçları
- Kampanya Hedefleri

Panele girip Kaydet'e bastığımda ajansın haftalık üretimi (claude -p) bu Ayar'a göre otomatik \
brief üretir. "Üretim Geçmişi" gibi bir alan EKLEME — sistem otomatik yönetir.
"""


@bp.patch('/clients/<int:client_id>/brief-enabled')
@auth_required(write=True)
def clients_brief_enabled(client_id):
    """Brief on/off (brief-inactive toggle). `{enabled: bool}` → `Client.brief_enabled`."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return jsonify(error='enabled field is required'), 400
    c.brief_enabled = bool(data['enabled'])
    c.updated_at = utcnow()
    c.updated_by = current_user()['sub']
    db.session.commit()
    return jsonify(client=c.to_dict())


# Ayar fields — type groups written into the brand_profile JSON (vault-sema-taslak.md §2.1).
# NOT a vault FILE — the panel DB is the sole authority (Option A — editable Ayar).
_AYAR_STR_FIELDS = ('brand_voice', 'target_audience', 'cta', 'content_pillars', 'guide_md')
_AYAR_LIST_FIELDS = ('forbidden', 'color_palette', 'posting_days')
_AYAR_DICT_FIELDS = ('content_mix', 'hashtags')


def _ayar_from_client(c):
    """Builds a clean Ayar schema from `Client.brand_profile` + `caption_settings`.
    Empty/missing fields come back with a sensible default ('' / [] / {}) — callers won't blow up."""
    bp = c.brand_profile if isinstance(c.brand_profile, dict) else {}
    cs = c.caption_settings if isinstance(c.caption_settings, dict) else {}
    return {
        'brand_voice': bp.get('brand_voice') or '',
        'target_audience': bp.get('target_audience') or '',
        'cta': bp.get('cta') or '',
        'forbidden': bp.get('forbidden') or [],
        'color_palette': bp.get('color_palette') or [],
        'content_mix': bp.get('content_mix') or {},
        'content_pillars': bp.get('content_pillars') or '',
        'guide_md': bp.get('guide_md') or '',
        'hashtags': bp.get('hashtags') or {},
        'posting_days': bp.get('posting_days') or [],
        'ideas_per_week': bp.get('ideas_per_week') or 5,
        'caption_settings': cs,
    }


@bp.get('/clients/<int:client_id>/vault-ayar')
@auth_required()
def clients_vault_ayar(client_id):
    """Builds the client's Ayar READ-ONLY from the panel DB (`brand_profile` +
    `caption_settings`) — NOT a vault FILE (Option A). If brand_profile is empty/None,
    returns onboarding: `{onboarding: true}` + an empty template.

    **Reading is open to ALL production roles** (2026-07-27): Ayar is a brand
    production guide — designer/content creator/videographer read it from the
    `/marka-rehberi` page. It contains NO commercial or contact info (those fields
    also drop out of `_client_json`), so READ_ROLES is safe. Writing (PUT) stays
    management-only."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    bp = c.brand_profile if isinstance(c.brand_profile, dict) else {}
    if not bp:
        return jsonify(onboarding=True, ayar=_ayar_from_client(c))
    return jsonify(ayar=_ayar_from_client(c))


@bp.put('/clients/<int:client_id>/vault-ayar')
@auth_required(write=True)
def clients_vault_ayar_kaydet(client_id):
    """WRITES the client's Ayar to the panel DB (Option A — editable, NO git/vault).
    Body is a partial merge: incoming fields are validated and written into
    `brand_profile` (string/list/dict/int types) + `caption_settings` (separate
    column); unsent fields are preserved. Takes effect immediately — caption and
    brief generation read these. management-gated. Returns the current Ayar."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}

    # merge brand_profile with the existing value — copy into a new dict so SQLAlchemy detects the mutation.
    bp = dict(c.brand_profile) if isinstance(c.brand_profile, dict) else {}
    for f in _AYAR_STR_FIELDS:
        if f in data:
            if not isinstance(data[f], str):
                return jsonify(error=f'{f} must be a string'), 400
            bp[f] = data[f]
    for f in _AYAR_LIST_FIELDS:
        if f in data:
            if not isinstance(data[f], list):
                return jsonify(error=f'{f} must be a list'), 400
            bp[f] = data[f]
    for f in _AYAR_DICT_FIELDS:
        if f in data:
            if not isinstance(data[f], dict):
                return jsonify(error=f'{f} must be an object'), 400
            bp[f] = data[f]
    if 'ideas_per_week' in data:
        v = data['ideas_per_week']
        # bool is a subclass of int — exclude it explicitly (True/False can't be ideas_per_week).
        if isinstance(v, bool) or not isinstance(v, int):
            return jsonify(error='ideas_per_week must be an integer'), 400
        bp['ideas_per_week'] = v

    # caption_settings is a separate column — partial merge (unsent keys are preserved).
    cs = dict(c.caption_settings) if isinstance(c.caption_settings, dict) else {}
    if 'caption_settings' in data:
        if not isinstance(data['caption_settings'], dict):
            return jsonify(error='caption_settings must be an object'), 400
        cs.update(data['caption_settings'])

    c.brand_profile = bp
    c.caption_settings = cs
    c.updated_at = utcnow()
    c.updated_by = current_user()['sub']
    db.session.commit()
    return jsonify(ayar=_ayar_from_client(c))


@bp.post('/clients/<int:client_id>/catch-up')
@auth_required(write=True)
def clients_catch_up(client_id):
    """Enqueues brief jobs for this client for the MISSING weeks in the 'this week → +2'
    range (K7). A week that already has a `WeeklyBrief` is skipped; the dedup_key is
    consistent with the existing one (`brief:{cid}:{week}`). Returns how many weeks were enqueued."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    weeks = _upcoming_weeks(3)
    existing = {b.week_iso for b in WeeklyBrief.query.filter(
        WeeklyBrief.client_id == c.id, WeeklyBrief.week_iso.in_(weeks)).all()}
    enqueued = []
    for wk in weeks:
        if wk in existing:
            continue
        jobqueue.enqueue('brief', {'client_id': c.id, 'week_iso': wk}, priority=0,
                         dedup_key=f'brief:{c.id}:{wk}', created_by=current_user()['sub'])
        enqueued.append(wk)
    return jsonify(enqueued=len(enqueued), weeks=enqueued), 202


@bp.get('/clients/<int:client_id>/onboarding-prompt')
@auth_required(write=True)
def clients_onboarding_prompt(client_id):
    """Returns the TEXT of a 'starter prompt' to paste into a fresh claude session for
    a new client — asks it to produce the brand's Ayar file in the new schema."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    prompt = ONBOARDING_PROMPT_TEMPLATE.format(
        ad=c.name, client_id=c.id, sektor=c.sector or '(sektör belirtilmemiş)')
    return jsonify(prompt=prompt)


# --- notifications (in-panel bell) ---

NOTIFICATION_LIST_LIMIT = 30


@bp.get('/notifications')
def notifications_list():
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    sub = u['sub']
    base = Notification.query.filter_by(recipient_sub=sub)
    # Badge count is computed with a SQL COUNT — without pulling unread rows into
    # memory unbounded (see Phase 0 review M4).
    unread_count = base.filter_by(read_at=None).count()
    # Pagination (for the notification history page): offset + limit. The bell calls
    # with the defaults (offset=0, limit=30). has_more: fetch limit+1 and check for the extra.
    offset = max(request.args.get('offset', 0, type=int) or 0, 0)
    limit = request.args.get('limit', NOTIFICATION_LIST_LIMIT, type=int) or NOTIFICATION_LIST_LIMIT
    limit = max(1, min(limit, 100))
    rows = (base.order_by(Notification.created_at.desc())
            .offset(offset).limit(limit + 1).all())
    has_more = len(rows) > limit
    items = rows[:limit]
    return jsonify(notifications=[n.to_dict() for n in items],
                   unread_count=unread_count, has_more=has_more)


@bp.post('/notifications/<int:notif_id>/read')
def notification_read(notif_id):
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    n = Notification.query.filter_by(id=notif_id, recipient_sub=u['sub']).first()
    if n is None:
        return jsonify(error='notification not found'), 404
    if n.read_at is None:
        n.read_at = utcnow()
        db.session.commit()
    return jsonify(notification=n.to_dict())


@bp.post('/notifications/read-all')
def notifications_read_all():
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    unread = Notification.query.filter_by(recipient_sub=u['sub'], read_at=None).all()
    now = utcnow()
    for n in unread:
        n.read_at = now
    db.session.commit()
    return jsonify(ok=True, count=len(unread))


# --- notification preferences (ntfy, 2026-08-05) ---------------------------------
# Endpoints ALWAYS look at the session owner's own record; user_sub is never taken
# from the body (so no one can read another user's topic / push notifications to their phone).

def _own_pref(sub, create=False):
    """The user's preference record. `create=True` generates a random topic on first
    open — the topic is 32-hex because ntfy's read authorization relies on the name being secret."""
    pref = db.session.get(NotificationPref, sub)
    if pref is None and create:
        pref = NotificationPref(user_sub=sub, ntfy_topic=secrets.token_hex(16))
        db.session.add(pref)
        db.session.commit()
    return pref


@bp.get('/notification-prefs')
def notification_prefs_get():
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    # GET CREATES the record: a topic is needed to show to the user opening the
    # panel's settings card. The record alone doesn't turn on push (`ntfy_enabled`
    # is false) — the opt-in rule lives in the `ntfy_enabled` flag, not the record's existence.
    pref = _own_pref(u['sub'], create=True)
    # `server_url` + `ntfy_topic` are returned SEPARATELY: the ntfy app wants them in
    # separate fields when subscribing (server / topic). The combined `subscribe_url`
    # exists for opening in the browser and for the QR code.
    return jsonify(prefs=pref.to_dict(), severities=list(SEVERITIES),
                   channel_ready=ntfy_gateway.available(),
                   server_url=NTFY_PUBLIC_URL,
                   subscribe_url=f'{NTFY_PUBLIC_URL}/{pref.ntfy_topic}')


@bp.put('/notification-prefs')
def notification_prefs_put():
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    data = request.get_json(silent=True) or {}
    pref = _own_pref(u['sub'], create=True)
    if 'ntfy_enabled' in data:
        pref.ntfy_enabled = bool(data['ntfy_enabled'])
    if 'min_severity' in data:
        sev = str(data['min_severity'] or '').strip()
        if sev not in SEVERITIES:
            return jsonify(error=f'min_severity must be one of {"|".join(SEVERITIES)}'), 400
        pref.min_severity = sev
    for alan in ('quiet_start', 'quiet_end'):
        if alan in data:
            raw = data[alan]
            if raw is None or raw == '':
                setattr(pref, alan, None)
                continue
            try:
                saat = int(raw)
            except (TypeError, ValueError):
                return jsonify(error=f'{alan} must be an hour between 0-23'), 400
            if not 0 <= saat <= 23:
                return jsonify(error=f'{alan} must be an hour between 0-23'), 400
            setattr(pref, alan, saat)
    # Request a new topic (to cut off old devices' subscriptions) — if the topic
    # leaks, the only fix is to rotate it, so it's left in the user's hands.
    if data.get('rotate_topic'):
        pref.ntfy_topic = secrets.token_hex(16)
    db.session.commit()
    return jsonify(prefs=pref.to_dict(),
                   subscribe_url=f'{NTFY_PUBLIC_URL}/{pref.ntfy_topic}')


@bp.get('/announce/recipients')
def announce_recipients():
    """People an announcement can be sent to (management only). Roles are also
    returned — so the panel can do bulk selection like "all designers"."""
    u = current_user()
    if not u or u.get('role') != 'management':
        return jsonify(error='not authorized'), 403
    rows = UserRef.query.order_by(UserRef.name).all()
    return jsonify(users=[{'sub': r.sub, 'name': r.name or r.email, 'role': r.role}
                          for r in rows],
                   severities=list(SEVERITIES))


@bp.post('/announce')
def announce():
    """Manual announcement: a manager writes the notification, CHOOSES recipients
    and severity (2026-08-05, project owner request). The difference from the
    catalog's automatic types is that severity comes from the SENDER, not the
    data — the "everyone should see this now" decision is made by a human.
    Recipients are given via `subs` (person list) and/or `roles` (role list), the
    union is deduplicated; sending to yourself is filtered out (the sender
    already knows)."""
    u = current_user()
    if not u or u.get('role') != 'management':
        return jsonify(error='not authorized'), 403
    data = request.get_json(silent=True) or {}
    baslik = (data.get('title') or '').strip()
    govde = (data.get('body') or '').strip()
    if not baslik:
        return jsonify(error='title is required'), 400
    if len(baslik) > 200:
        return jsonify(error='title must be at most 200 characters'), 400
    if len(govde) > 2000:
        return jsonify(error='message must be at most 2000 characters'), 400
    sev = (data.get('severity') or NORMAL).strip()
    if sev not in SEVERITIES:
        return jsonify(error=f'severity must be one of {"|".join(SEVERITIES)}'), 400

    subs = {str(s) for s in (data.get('subs') or []) if str(s).strip()}
    roller = {str(r) for r in (data.get('roles') or []) if str(r).strip()}
    if roller:
        subs |= {r.sub for r in UserRef.query.filter(UserRef.role.in_(roller)).all()}
    subs.discard(str(u.get('sub')))
    if not subs:
        return jsonify(error='select at least one recipient'), 400

    gonderen = u.get('name') or u.get('email') or 'Management'
    notifs = notifications.notify_announcement(
        subs, baslik, f'{govde}\n\n— {gonderen}'.strip(), severity=sev,
        link=(data.get('link') or None))
    return jsonify(ok=True, sent=len(notifs)), 201


@bp.post('/notification-prefs/test')
def notification_prefs_test():
    """Test notification — so the user can verify their subscription. DELIBERATELY
    skips the threshold/quiet hours: the question here isn't "does this notification
    pass the filter", it's "is the channel working"."""
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    pref = _own_pref(u['sub'], create=True)
    if not ntfy_gateway.available():
        return jsonify(error='ntfy channel is not configured (NTFY_BASE_URL/NTFY_TOKEN)'), 503
    ok = ntfy_gateway.send(pref.ntfy_topic, 'Test notification',
                           'Panel notifications are reaching your phone.',
                           severity=NORMAL, click_url=None)
    if not ok:
        return jsonify(error='could not reach ntfy server'), 502
    return jsonify(ok=True)
