"""Agency JSON API — panel SPA tüketir. Oturum SSO'dan gelir (Flask session).

Yetki modeli: okuma = ekip rolleri (management/designer/videographer/content_creator),
yazma = yalnız management. Mutasyonlar (POST/PUT/PATCH/DELETE) CSRF token ister:
token /api/session'dan alınır, X-CSRFToken header'ıyla gönderilir.
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

# Telefonun abone olacağı PUBLIC adres — `NTFY_BASE_URL` sunucu içi (localhost)
# olduğu için ayrı: panel kullanıcıya bu linki/QR'ı gösterir.
NTFY_PUBLIC_URL = os.getenv('NTFY_PUBLIC_URL', 'https://ntfy.example.com')


@bp.before_request
def csrf_protect():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = session.get('csrf')
        header = request.headers.get('X-CSRFToken', '')
        if not token or not hmac.compare_digest(token, header):
            return jsonify(error='CSRF doğrulaması başarısız'), 403


def auth_required(write=False):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u:
                return jsonify(error='oturum yok'), 401
            allowed = {'management'} if write else READ_ROLES
            if u.get('role') not in allowed:
                return jsonify(error='bu işlem için yetkiniz yok'), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def _require_superadmin():
    """Sunucu yönetim aksiyonları yalnız superadmin (gerçek kimlik). (user, err) döner."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if not is_superadmin(real_user()):
        return None, (jsonify(error='bu işlem için yetkiniz yok'), 403)
    return u, None


@bp.get('/session')
def session_info():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    imp = session.get('impersonator')
    import sharing as _sharing
    from sso_client import AUTH_MODE
    return jsonify(user=u, csrf=session['csrf'],
                   impersonating=bool(imp),
                   real_user=imp or u,             # gerçek (giriş yapan) kimlik
                   can_impersonate=is_superadmin(),  # gerçek kimlik superadmin mi
                   auth_mode=AUTH_MODE,             # 'local' iken panelde "parola değiştir" gösterilir
                   # "Müşterilerim" işaretleme yetkisi (bkz. sharing.OWNER_EMAIL /
                   # AGENCY_OWNER_EMAIL env) — boşsa hiç kimse eşleşmez.
                   is_agency_owner=bool(_sharing.OWNER_EMAIL) and
                   u.get('email') == _sharing.OWNER_EMAIL)


@bp.get('/me')
def me():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    return jsonify(user=u)


# --- impersonation ("kullanıcı gözünden bak") — yalnız superadmin ---

@bp.post('/impersonate')
def impersonate_start():
    """Gerçek kimliği (superadmin) sakla, session['user']'ı hedefe çevir. Yetki HER
    ZAMAN gerçek kimlik üzerinden — impersonate edilen kullanıcı bunu çağıramaz."""
    real = real_user()
    if not real:
        return jsonify(error='oturum yok'), 401
    if not is_superadmin(real):
        return jsonify(error='yetki yok'), 403
    data = request.get_json(silent=True) or {}
    target = UserRef.query.filter_by(sub=str(data.get('sub') or '')).first()
    if target is None or target.sub == real.get('sub'):
        return jsonify(error='kullanıcı bulunamadı'), 404
    session['impersonator'] = real  # ilk kez sakla; kullanıcı değiştirmede gerçek kalır
    session['user'] = {'sub': target.sub, 'email': target.email,
                       'name': target.name, 'role': target.role}
    return jsonify(user=session['user'], impersonating=True, real_user=real)


@bp.post('/impersonate/stop')
def impersonate_stop():
    real = session.get('impersonator')
    if not real:
        return jsonify(error='zaten kendi kimliğinsin'), 400
    session['user'] = real
    session.pop('impersonator', None)
    return jsonify(user=real, impersonating=False)


@bp.get('/users')
def users():
    if not current_user():
        return jsonify(error='oturum yok'), 401
    refs = UserRef.query.order_by(UserRef.name).all()
    return jsonify(users=[r.to_dict() for r in refs])


# --- kişisel tercihler (kullanıcı-kesitli; sayfaya değil KULLANICIYA ait) ---

# Beyaz liste: anahtar enflasyonunu engeller. Yeni bir sayfa gizleme isterse buraya
# bir değer eklenir (tablo `scope` kolonu zaten hazır).
PREF_SCOPES = ('videographer_upload',)


def _pref_user():
    """(user, err) — oturum + panel rolü. Tercih her zaman ETKİN kimliğe yazılır
    (impersonation altında hedef kullanıcının tercihi düzenlenir)."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in READ_ROLES:
        return None, (jsonify(error='bu işlem için yetkiniz yok'), 403)
    return u, None


def _hidden_set(sub, scope):
    return sorted(cid for (cid,) in db.session.query(UserHiddenClient.client_id)
                  .filter_by(owner_sub=str(sub), scope=scope).all())


@bp.get('/prefs/hidden-clients')
def prefs_hidden_clients_get():
    """Kullanıcının bu kapsamda gizlediği müşteriler.

    İZOLASYON YAPISAL: `owner_sub` her zaman `current_user()['sub']`; uç başka
    kullanıcının tercihini adresleyecek bir parametre KABUL ETMEZ."""
    u, err = _pref_user()
    if err:
        return err
    scope = request.args.get('scope') or PREF_SCOPES[0]
    if scope not in PREF_SCOPES:
        return jsonify(error=f'geçersiz kapsam: {scope}'), 400
    return jsonify(scope=scope, client_ids=_hidden_set(u['sub'], scope))


@bp.put('/prefs/hidden-clients')
def prefs_hidden_clients_put():
    """Tek müşterinin gizlilik durumunu yaz: {scope, client_id, hidden}.

    Yanıt TAM yeni kümedir — panel yerel türetme yapıp hata etmesin. `hidden=true`
    idempotent upsert (satır varsa dokunmaz), `false` satırı SİLER (soft-delete yok:
    UNIQUE slotu işgal ederdi)."""
    u, err = _pref_user()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    scope = data.get('scope') or PREF_SCOPES[0]
    if scope not in PREF_SCOPES:
        return jsonify(error=f'geçersiz kapsam: {scope}'), 400
    client_id = data.get('client_id')
    if not isinstance(client_id, int) or db.session.get(Client, client_id) is None:
        return jsonify(error='müşteri bulunamadı'), 404

    row = UserHiddenClient.query.filter_by(owner_sub=str(u['sub']), scope=scope,
                                           client_id=client_id).first()
    if data.get('hidden'):
        if row is None:
            db.session.add(UserHiddenClient(owner_sub=str(u['sub']), scope=scope,
                                            client_id=client_id))
            try:
                db.session.commit()
            except IntegrityError:      # eşzamanlı aynı toggle — zaten gizli
                db.session.rollback()
    elif row is not None:
        db.session.delete(row)
        db.session.commit()
    return jsonify(scope=scope, client_ids=_hidden_set(u['sub'], scope))


# --- clients ---

SCALAR_FIELDS = ('name', 'sector', 'notes', 'client_email', 'instagram_url',
                 'google_drive_url', 'special_days_token', 'sharing_playbook')


def _apply_payload(c, data):
    """Payload'daki alanları uygula; gönderilmeyen alanlara dokunma (partial)."""
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
        # UNIQUE(client_id, role_slot): yeni satırlar insert edilmeden eskiler silinsin
        c.team_assignments = []
        if c.id is not None:
            db.session.flush()
        c.team_assignments = [ClientTeamAssignment(role_slot=slot, user_id=str(uid))
                              for slot, uid in (data['team_assignments'] or {}).items() if uid]


def _client_json(c, full=False):
    """Rol-farkındalıklı müşteri JSON'u — TEK KAPI.

    management dışındaki roller (designer/content_creator/videographer) müşteriyi
    Marka Rehberi bağlamında okur; ticari (`contract`) ve iletişim (`client_email`,
    `contacts`, `locations`) alanları ile iç notlar ve `special_days_token`
    yanıta HİÇ konmaz. Yalnız `write=True` uçları zaten management-gated olduğu
    için orada ayrım gerekmez, ama okuma uçlarının ikisi de buradan geçer."""
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
        return jsonify(error='name alanı zorunlu'), 400
    c = Client(created_by=current_user()['sub'])
    _apply_payload(c, data)
    db.session.add(c)
    db.session.commit()
    # Yeni müşteri Drive klasör ağacı provizyonu (Cutover C2). Best-effort:
    # Drive erişilemez/hata verirse müşteri oluşturma etkilenmez (içeride yutulur).
    client_provision.provision_client_folders(c)
    return jsonify(client=c.to_dict(full=True)), 201


def _get_or_404(client_id):
    c = db.session.get(Client, client_id)
    if c is None:
        return None, (jsonify(error='müşteri bulunamadı'), 404)
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
        return jsonify(error='name boş olamaz'), 400
    _apply_payload(c, data)
    c.updated_at = utcnow()
    c.updated_by = current_user()['sub']
    db.session.commit()
    return jsonify(client=c.to_dict(full=True))


@bp.post('/clients/assign-designer')
@auth_required(write=True)
def clients_assign_designer():
    """Toplu tasarımcı ataması — YALNIZ 'designer' slot'una dokunur (videographer/
    content_creator slotları korunur). Gövde: {assignments:[{client_id, user_id|null}]}.
    user_id boş/null → o müşterinin designer ataması kaldırılır. Bilinmeyen client_id atlanır."""
    data = request.get_json(silent=True) or {}
    items = data.get('assignments')
    if not isinstance(items, list):
        return jsonify(error='assignments listesi zorunlu'), 400
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
    """Mevcut müşteri için Drive klasör ağacını (yeniden) kur / eksikleri tamamla.
    İdempotent; göçen müşteride mevcut kökü kullanır, içerik kökü altında yeni kök açmaz."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    result = client_provision.provision_client_folders(c)
    if result is None:
        return jsonify(error='Drive klasörü kurulamadı (Drive kimliği/kökü yok veya hata)'), 502
    return jsonify(client=c.to_dict(full=True), provision=result)


# --- Faz 3: vault Ayar entegrasyonu (brief aç/kapa · Ayar oku · catch-up · onboarding) ---

def _upcoming_weeks(n=3):
    """İçinde bulunulan ISO haftadan başlayarak n hafta ('bu hafta → +(n-1)').
    Varsayılan 3 → bu hafta, +1, +2 (K7 catch-up aralığı)."""
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
    """Brief aç/kapa (brief-pasif toggle). `{enabled: bool}` → `Client.brief_enabled`."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return jsonify(error='enabled alanı zorunlu'), 400
    c.brief_enabled = bool(data['enabled'])
    c.updated_at = utcnow()
    c.updated_by = current_user()['sub']
    db.session.commit()
    return jsonify(client=c.to_dict())


# Ayar alanları — brand_profile JSON'una yazılan tip grupları (vault-sema-taslak.md §2.1).
# Vault DOSYASI değil, panel DB'si tek otorite (Option A — düzenlenebilir Ayar).
_AYAR_STR_FIELDS = ('brand_voice', 'target_audience', 'cta', 'content_pillars', 'guide_md')
_AYAR_LIST_FIELDS = ('forbidden', 'color_palette', 'posting_days')
_AYAR_DICT_FIELDS = ('content_mix', 'hashtags')


def _ayar_from_client(c):
    """`Client.brand_profile` + `caption_settings`'ten temiz Ayar şeması kurar.
    Boş/eksik alanlar makul default ('' / [] / {}) ile döner — çağıran patlamaz."""
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
    """Müşteri Ayar'ını panel DB'sinden (`brand_profile` + `caption_settings`) SALT-OKU
    kurar (vault DOSYASI DEĞİL — Option A). brand_profile boş/None ise onboarding:
    `{onboarding: true}` + boş şablon döner.

    **Okuma TÜM üretim rollerine açık** (2026-07-27): Ayar bir marka üretim
    rehberi — tasarımcı/içerik üreticisi/videograf `/marka-rehberi` sayfasından
    okur. Ticari veya iletişim bilgisi İÇERMEZ (o alanlar `_client_json`'dan da
    düşer), o yüzden READ_ROLES güvenli. Yazma (PUT) management'ta kalır."""
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
    """Müşteri Ayar'ını panel DB'sine YAZAR (Option A — düzenlenebilir, git/vault YOK).
    Body kısmi merge: gelen alanlar doğrulanıp `brand_profile`'a (string/list/dict/int
    tipleri) + `caption_settings`'e (ayrı kolon) yazılır; gelmeyen alan korunur. Anında
    etkili — caption + brief üretimi bunları okur. management-gated. Güncel Ayar döner."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}

    # brand_profile mevcut değerle merge — yeni dict'e kopyala ki SQLAlchemy mutasyonu algılasın.
    bp = dict(c.brand_profile) if isinstance(c.brand_profile, dict) else {}
    for f in _AYAR_STR_FIELDS:
        if f in data:
            if not isinstance(data[f], str):
                return jsonify(error=f'{f} string olmalı'), 400
            bp[f] = data[f]
    for f in _AYAR_LIST_FIELDS:
        if f in data:
            if not isinstance(data[f], list):
                return jsonify(error=f'{f} liste olmalı'), 400
            bp[f] = data[f]
    for f in _AYAR_DICT_FIELDS:
        if f in data:
            if not isinstance(data[f], dict):
                return jsonify(error=f'{f} sözlük olmalı'), 400
            bp[f] = data[f]
    if 'ideas_per_week' in data:
        v = data['ideas_per_week']
        # bool int'in alt-sınıfı — açıkça dışla (True/False ideas_per_week olamaz).
        if isinstance(v, bool) or not isinstance(v, int):
            return jsonify(error='ideas_per_week tam sayı olmalı'), 400
        bp['ideas_per_week'] = v

    # caption_settings ayrı kolon — kısmi merge (gelmeyen anahtar korunur).
    cs = dict(c.caption_settings) if isinstance(c.caption_settings, dict) else {}
    if 'caption_settings' in data:
        if not isinstance(data['caption_settings'], dict):
            return jsonify(error='caption_settings sözlük olmalı'), 400
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
    """Bu müşteri için 'bu hafta → +2' aralığındaki EKSİK haftaların brief job'larını
    enqueue eder (K7). Zaten `WeeklyBrief`'i olan hafta atlanır; dedup_key mevcutla
    tutarlı (`brief:{cid}:{hafta}`). Kaç hafta enqueue edildiğini döner."""
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
    """Yeni müşteri için taze bir claude session'a yapıştırılacak 'başlangıç prompt'u'
    METNİ döner — markanın Ayar dosyasını yeni şemada üretmesini ister."""
    c, err = _get_or_404(client_id)
    if err:
        return err
    prompt = ONBOARDING_PROMPT_TEMPLATE.format(
        ad=c.name, client_id=c.id, sektor=c.sector or '(sektör belirtilmemiş)')
    return jsonify(prompt=prompt)


# --- bildirimler (panel-içi bell) ---

NOTIFICATION_LIST_LIMIT = 30


@bp.get('/notifications')
def notifications_list():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    sub = u['sub']
    base = Notification.query.filter_by(recipient_sub=sub)
    # Rozet sayısı SQL COUNT ile hesaplanır — okunmamış satırları sınırsız belleğe
    # çekmeden (bkz Faz 0 review M4).
    unread_count = base.filter_by(read_at=None).count()
    # Sayfalama (bildirim geçmişi sayfası için): offset + limit. Bell varsayılanla
    # (offset=0, limit=30) çağırır. has_more: limit+1 çekip fazlalık var mı bakılır.
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
        return jsonify(error='oturum yok'), 401
    n = Notification.query.filter_by(id=notif_id, recipient_sub=u['sub']).first()
    if n is None:
        return jsonify(error='bildirim bulunamadı'), 404
    if n.read_at is None:
        n.read_at = utcnow()
        db.session.commit()
    return jsonify(notification=n.to_dict())


@bp.post('/notifications/read-all')
def notifications_read_all():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    unread = Notification.query.filter_by(recipient_sub=u['sub'], read_at=None).all()
    now = utcnow()
    for n in unread:
        n.read_at = now
    db.session.commit()
    return jsonify(ok=True, count=len(unread))


# --- bildirim tercihleri (ntfy, 2026-08-05) ---------------------------------
# Uçlar HER ZAMAN oturum sahibinin kendi kaydına bakar; gövdede user_sub alınmaz
# (başkasının topic'ini okumak/telefonuna bildirim göndermek mümkün olmasın).

def _own_pref(sub, create=False):
    """Kullanıcının tercih kaydı. `create=True` ilk açılışta rastgele topic üretir —
    topic 32-hex çünkü ntfy'de okuma yetkisi adın gizliliğine dayanıyor."""
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
        return jsonify(error='oturum yok'), 401
    # GET kayıt OLUŞTURUR: panelin ayar kartını açan kullanıcıya gösterilecek bir
    # topic gerekiyor. Kayıt tek başına push açmaz (`ntfy_enabled` false) —
    # opt-in kuralı `ntfy_enabled` bayrağında, kaydın varlığında değil.
    pref = _own_pref(u['sub'], create=True)
    # `server_url` + `ntfy_topic` AYRI dönüyor: ntfy uygulaması abone olurken ikisini
    # ayrı alanlarda istiyor (sunucu / konu). Birleşik `subscribe_url` tarayıcıda
    # açmak ve QR için duruyor.
    return jsonify(prefs=pref.to_dict(), severities=list(SEVERITIES),
                   channel_ready=ntfy_gateway.available(),
                   server_url=NTFY_PUBLIC_URL,
                   subscribe_url=f'{NTFY_PUBLIC_URL}/{pref.ntfy_topic}')


@bp.put('/notification-prefs')
def notification_prefs_put():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    data = request.get_json(silent=True) or {}
    pref = _own_pref(u['sub'], create=True)
    if 'ntfy_enabled' in data:
        pref.ntfy_enabled = bool(data['ntfy_enabled'])
    if 'min_severity' in data:
        sev = str(data['min_severity'] or '').strip()
        if sev not in SEVERITIES:
            return jsonify(error=f'min_severity {"|".join(SEVERITIES)} olmalı'), 400
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
                return jsonify(error=f'{alan} 0-23 arası saat olmalı'), 400
            if not 0 <= saat <= 23:
                return jsonify(error=f'{alan} 0-23 arası saat olmalı'), 400
            setattr(pref, alan, saat)
    # Yeni topic isteği (eski cihazların aboneliğini kesmek için) — topic sızarsa
    # tek çare yenilemek, o yüzden kullanıcının elinde olsun.
    if data.get('rotate_topic'):
        pref.ntfy_topic = secrets.token_hex(16)
    db.session.commit()
    return jsonify(prefs=pref.to_dict(),
                   subscribe_url=f'{NTFY_PUBLIC_URL}/{pref.ntfy_topic}')


@bp.get('/announce/recipients')
def announce_recipients():
    """Anons gönderilebilecek kişiler (yalnız management). Roller de dönüyor —
    panel "tüm tasarımcılar" gibi toplu seçim yapabilsin."""
    u = current_user()
    if not u or u.get('role') != 'management':
        return jsonify(error='yetkiniz yok'), 403
    rows = UserRef.query.order_by(UserRef.name).all()
    return jsonify(users=[{'sub': r.sub, 'name': r.name or r.email, 'role': r.role}
                          for r in rows],
                   severities=list(SEVERITIES))


@bp.post('/announce')
def announce():
    """Elle duyuru: yönetici bildirim yazar, alıcıları ve önem derecesini SEÇER
    (2026-08-05, proje sahibi isteği). Kataloğun otomatik türlerinden farkı, severity'nin
    veriden değil GÖNDERENDEN gelmesi — "bunu herkes şimdi görsün" kararını insan
    veriyor. Alıcılar `subs` (kişi listesi) ve/veya `roles` (rol listesi) ile
    verilir, birleşimi tekilleştirilir; kendine gönderim ayıklanır (gönderen zaten
    biliyor)."""
    u = current_user()
    if not u or u.get('role') != 'management':
        return jsonify(error='yetkiniz yok'), 403
    data = request.get_json(silent=True) or {}
    baslik = (data.get('title') or '').strip()
    govde = (data.get('body') or '').strip()
    if not baslik:
        return jsonify(error='başlık zorunlu'), 400
    if len(baslik) > 200:
        return jsonify(error='başlık en fazla 200 karakter'), 400
    if len(govde) > 2000:
        return jsonify(error='mesaj en fazla 2000 karakter'), 400
    sev = (data.get('severity') or NORMAL).strip()
    if sev not in SEVERITIES:
        return jsonify(error=f'severity {"|".join(SEVERITIES)} olmalı'), 400

    subs = {str(s) for s in (data.get('subs') or []) if str(s).strip()}
    roller = {str(r) for r in (data.get('roles') or []) if str(r).strip()}
    if roller:
        subs |= {r.sub for r in UserRef.query.filter(UserRef.role.in_(roller)).all()}
    subs.discard(str(u.get('sub')))
    if not subs:
        return jsonify(error='en az bir alıcı seçin'), 400

    gonderen = u.get('name') or u.get('email') or 'Yönetim'
    notifs = notifications.notify_announcement(
        subs, baslik, f'{govde}\n\n— {gonderen}'.strip(), severity=sev,
        link=(data.get('link') or None))
    return jsonify(ok=True, sent=len(notifs)), 201


@bp.post('/notification-prefs/test')
def notification_prefs_test():
    """Test bildirimi — kullanıcı aboneliğini doğrulayabilsin. Eşiği/sessiz saati
    BİLEREK atlar: burada soru "kanal çalışıyor mu", "bu bildirim geçer mi" değil."""
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    pref = _own_pref(u['sub'], create=True)
    if not ntfy_gateway.available():
        return jsonify(error='ntfy kanalı yapılandırılmamış (NTFY_BASE_URL/NTFY_TOKEN)'), 503
    ok = ntfy_gateway.send(pref.ntfy_topic, 'Test bildirimi',
                           'Panel bildirimleri telefonuna ulasiyor.',
                           severity=NORMAL, click_url=None)
    if not ok:
        return jsonify(error='ntfy sunucusuna ulaşılamadı'), 502
    return jsonify(ok=True)
