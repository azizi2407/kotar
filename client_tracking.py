"""Müşteri Takip — `/api/client-tracking/*` [Blueprint: /api/client-tracking].
YALNIZ management (ticari/satış bilgisi).

Ajansın müşteriye satabileceği iş kalemlerini (marka tescili, katalog, web sitesi,
özel proje…) müşteri bazında takip eder. Üç parça:

  * `tracking_items`          — YÖNETİLEBİLİR kalem kataloğu (panelden ekle/sırala)
  * `client_tracking_entries` — müşteri × kalem durum hücresi (upsert, soft-delete YOK)
  * `client_activity_notes`   — tarihli serbest aktivite günlüğü ("en son ne yapıldı")

Buna ek olarak liste ucu üç sinyali MEVCUT verilerden TÜRETİR (elle girilmez, hiçbir
uçtan yazılamaz): son reklam (`ad_campaigns`), son/sıradaki çekim (`shoot_tasks`),
sorumlu ekip (`client_team_assignments`).

LİSTE UCU İNVARYANTI: sorgu sayısı müşteri sayısından BAĞIMSIZ (sabit ~9). Hiçbir
yerde `client.ad_campaigns` / `entry.item` gibi lazy erişim yoktur — her şey toplu
sorgu + Python birleştirme. `tests/test_musteri_takip.py::test_list_query_count_*`
bunu sorgu sayarak zorlar; serializer'a lazy erişim eklenirse o test kırılır.

⚠️ Sayfa ileride management dışına açılırsa: detay ucu kampanya bilgisi döndürüyor.
Mali alan (`amount_spent`) BİLEREK dışarıda bırakıldı — açmadan önce gözden geçir.

CSRF `api.csrf_protect` ile paylaşılır (ads.py/sharing.py deseni).
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
bp.before_request(csrf_protect)  # api ile aynı CSRF (session token)

# Katalog tohumu — ajansın tipik satış kalemleri. Sıra = panelde görünen sıra.
# (key, ad, kategori, lucide ikon adı). `key` yalnız tohumda dolu, idempotans anahtarı.
SEED_ITEMS = (
    ('marka_tescili',         'Marka Tescili',           'hukuki',  'ShieldCheck'),
    ('logo_kurumsal_kimlik',  'Logo / Kurumsal Kimlik',  'tasarim', 'Palette'),
    ('web_sitesi',            'Web Sitesi',              'dijital', 'Globe'),
    ('e_ticaret',             'E-Ticaret Sitesi',        'dijital', 'ShoppingCart'),
    ('google_isletme',        'Google İşletme Profili',  'dijital', 'MapPin'),
    ('sosyal_medya_yonetimi', 'Sosyal Medya Yönetimi',   'dijital', 'Share2'),
    ('reklam_yonetimi',       'Reklam Yönetimi',         'reklam',  'Megaphone'),
    ('katalog',               'Katalog',                 'tasarim', 'BookOpen'),
    ('matbaa_baski',          'Matbaa / Baskı',          'uretim',  'Printer'),
    ('fotograf_cekimi',       'Fotoğraf Çekimi',         'uretim',  'Camera'),
    ('video_cekimi',          'Video Çekimi',            'uretim',  'Clapperboard'),
    ('ozel_proje',            'Özel Proje',              'diger',   'Sparkles'),
)


def _require_management():
    """(user, err) döner — takip verisi yalnız yönetime açık."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='bu sayfa yalnız yönetim içindir'), 403)
    return u, None


def _ensure_seed_items():
    """İlk kullanımda kataloğu tohumla.

    YALNIZ tablo TAMAMEN boşsa çalışır — tek tek anahtar kontrolü yapmaz, çünkü o
    zaman kullanıcının bilerek sildiği kalem her istekte geri dirilirdi. Çok worker'lı
    gunicorn'da yarış olabilir: `key` unique olduğu için ikinci INSERT IntegrityError
    verir ve yutulur (diğer worker zaten tohumlamıştır)."""
    if db.session.query(TrackingItem.id).first() is not None:
        return
    for i, (key, name, category, icon) in enumerate(SEED_ITEMS):
        db.session.add(TrackingItem(key=key, name=name, category=category,
                                    icon=icon, position=i, active=True))
    try:
        db.session.commit()
    except IntegrityError:      # başka worker aynı anda tohumladı — sorun değil
        db.session.rollback()


# --- Doğrulama yardımcıları (ValueError → çağıran 400'e çevirir) ---

def _parse_date(value, field, required=False):
    """'YYYY-MM-DD' → date. Hatalıysa ValueError."""
    if value in (None, ''):
        if required:
            raise ValueError(f'{field} zorunlu')
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f'{field} geçersiz tarih (YYYY-MM-DD bekleniyor)')


def _clean_text(value, field, required=False, limit=None):
    text_value = (value or '').strip() if isinstance(value, str) else ''
    if not text_value:
        if required:
            raise ValueError(f'{field} zorunlu')
        return None
    if limit and len(text_value) > limit:
        raise ValueError(f'{field} en fazla {limit} karakter olabilir')
    return text_value


# Türkçe-duyarlı karşılaştırma tablosu (frontend `lib/week.ts` trFold'un backend eşi).
# Python'un casefold()'u 'İ'yi i+U+0307'ye çevirir → 'web SİTESİ' ile 'Web Sitesi'
# eşleşmez. Bu yüzden TR harfleri önce sadeleştirilir, sonra lower() uygulanır.
_TR_FOLD = str.maketrans({
    'İ': 'i', 'I': 'i', 'ı': 'i', 'Ş': 's', 'ş': 's', 'Ğ': 'g', 'ğ': 'g',
    'Ü': 'u', 'ü': 'u', 'Ö': 'o', 'ö': 'o', 'Ç': 'c', 'ç': 'c',
})


def _fold(value):
    """Kalem adı tekilliği için normalize anahtar."""
    return (value or '').translate(_TR_FOLD).lower()


def _clean_url(value):
    url = _clean_text(value, 'link', limit=1024)
    if url and not url.lower().startswith(('http://', 'https://')):
        raise ValueError('geçersiz link (http/https bekleniyor)')
    return url


def _live_item(item_id):
    """Silinmemiş kalemi getir; yoksa ValueError."""
    item = TrackingItem.query.filter_by(id=item_id, deleted_at=None).first()
    if item is None:
        raise ValueError('kalem bulunamadı')
    return item


def _live_client(client_id):
    """Aktif müşteriyi getir; yoksa ValueError (ads.py deseni)."""
    client = db.session.get(Client, client_id or 0)
    if client is None or client.status != 'active':
        raise ValueError('geçerli bir müşteri seçin')
    return client


def _apply_item(item, data, user, creating=False):
    """Katalog kalemini doğrula + uygula. Ad tekilliği Türkçe-duyarlı _fold() ile
    burada zorlanır (DB'de kısmi unique index yok — dialect ayrışmasını istemiyoruz)."""
    if creating or 'name' in data:
        name = _clean_text(data.get('name'), 'kalem adı', required=True, limit=120)
        clash = (TrackingItem.query.filter(TrackingItem.deleted_at.is_(None))
                 .filter(TrackingItem.id != (item.id or 0)).all())
        if any(_fold(c.name) == _fold(name) for c in clash):
            raise ValueError('bu adda kalem zaten var')
        item.name = name
    if creating or 'category' in data:
        category = (data.get('category') or 'diger').strip().lower()
        if category not in TrackingItem.CATEGORIES:
            raise ValueError(f'geçersiz kategori: {category}')
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
    """Durum hücresini doğrula + uygula."""
    if creating or 'status' in data:
        status = (data.get('status') or 'yok').strip().lower()
        if status not in ClientTrackingEntry.STATUSES:
            raise ValueError(f'geçersiz durum: {status}')
        entry.status = status
    if creating or 'status_date' in data:
        entry.status_date = _parse_date(data.get('status_date'), 'tarih')
    if creating or 'note' in data:
        entry.note = _clean_text(data.get('note'), 'not')
    if creating or 'url' in data:
        entry.url = _clean_url(data.get('url'))
    if creating:
        entry.created_by = user.get('sub')
    entry.updated_by = user.get('sub')
    return entry


def _apply_note(note, data, user, creating=False):
    """Aktivite notunu doğrula + uygula."""
    if creating or 'text' in data:
        note.text = _clean_text(data.get('text'), 'not metni', required=True)
    if creating or 'happened_on' in data:
        note.happened_on = _parse_date(data.get('happened_on'), 'tarih') or dt.date.today()
    if creating or 'item_id' in data:
        item_id = data.get('item_id')
        note.item_id = _live_item(item_id).id if item_id else None
    if creating:
        note.created_by = user.get('sub')
    note.updated_by = user.get('sub')
    return note


# --- Türetilmiş sinyaller (toplu sorgular; hepsi tek GROUP BY) ---

def _ad_signals(client_ids, today):
    """{client_id: (last_date, count, active_flag)} — tek GROUP BY.

    `last_date` = reklamın FİİLEN yayında olduğu en son gün ve BUGÜNÜ GEÇMEZ:
      * henüz başlamamış (start_date > bugün)  → sayılmaz (reklam çıkılmadı)
      * devam ediyor / ileride bitecek         → bugün ("hâlâ yayında")
      * bitmiş                                 → end_date
      * bitiş tarihi yok ve başlamış           → bugün
    Ham MAX(COALESCE(end_date, start_date)) kullanılmaz: gelecekte biten bir kampanya
    "son reklam 27 Tem" gibi İLERİ TARİH gösterirdi (canlı veride görüldü, 2026-07-25).
    Bugünle sınırlamak hem doğru okunur hem de bayatlık hesabını (daysSince) tutarlı kılar.
    CASE kullanılıyor çünkü LEAST/MIN skaleri Postgres ile sqlite arasında ayrışır."""
    effective_day = case(
        (AdCampaign.start_date > today, None),          # henüz yayına girmemiş
        (AdCampaign.end_date.is_(None), today),         # devam ediyor
        (AdCampaign.end_date > today, today),           # ileride bitecek → hâlâ yayında
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
    """{client_id: (last_past_date, next_future_date)} — tek GROUP BY.

    `status='completed'` filtresi BİLEREK kullanılmaz: sahada bu alan işaretlenmiyor
    (2026-07-25 canlı veri: 76 kayıttan 3'ü completed). Bu yüzden "son çekim" =
    tarihi geçmiş en yeni plan, "sıradaki çekim" = gelecekteki en yakın plan."""
    rows = (db.session.query(
        ShootTask.client_id,
        func.max(case((ShootTask.scheduled_date <= today, ShootTask.scheduled_date))),
        func.min(case((ShootTask.scheduled_date > today, ShootTask.scheduled_date))),
    ).filter(ShootTask.client_id.in_(client_ids), ShootTask.scheduled_date.isnot(None))
     .group_by(ShootTask.client_id).all())
    return {r[0]: (r[1], r[2]) for r in rows}


def _team_signals(client_ids):
    """{client_id: [{role_slot, user_id, name}]} — iki sorgu (atamalar + isim tablosu)."""
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
    """{client_id: note_dict} — müşteri başına EN SON not, tek sorgu (window function).
    ROW_NUMBER() Postgres'te ve test sqlite'ında (>=3.25) çalışır."""
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
    """Durum sayacı + `opportunity`: AKTİF kalemler arasında durumu 'yok' olan VEYA
    hiç kaydı olmayanların sayısı = "bu müşteriye satılabilecek kalem sayısı".
    Sayfanın iş değeri bu sayıda; liste bu kolona göre sıralanabilir."""
    counts = {s: 0 for s in ClientTrackingEntry.STATUSES}
    opportunity = 0
    for item_id in active_item_ids:
        entry = entries_by_item.get(str(item_id))
        status = entry['status'] if entry else 'yok'
        counts[status] = counts.get(status, 0) + 1
        if status == 'yok':
            opportunity += 1
    # aktif olmayan (arşiv) kalemlerdeki kayıtlar sayaca girer ama fırsata girmez
    for key, entry in entries_by_item.items():
        if int(key) not in active_item_ids:
            counts[entry['status']] = counts.get(entry['status'], 0) + 1
    counts['opportunity'] = opportunity
    return counts


# --- Uçlar ---

@bp.get('')
@bp.get('/')
def tracking_list():
    """Takip listesi: katalog + müşteri satırları (durum hücreleri, türetilmiş
    sinyaller, son not, özet). Sorgu sayısı müşteri sayısından bağımsızdır."""
    _, err = _require_management()
    if err:
        return err
    _ensure_seed_items()
    today = dt.date.today()

    # (1) müşteriler — yalnız aktif, sadece gereken kolonlar
    cq = (db.session.query(Client.id, Client.name, Client.sector)
          .filter(Client.status == 'active'))
    term = (request.args.get('q') or '').strip()
    if term:
        cq = cq.filter(Client.name.ilike(f'%{term}%'))
    clients = cq.order_by(Client.name.asc()).all()

    # (2) katalog — silinmemiş kalemler (pasifler de döner, panel işaretler)
    items = (TrackingItem.query.filter(TrackingItem.deleted_at.is_(None))
             .order_by(TrackingItem.position.asc(), TrackingItem.id.asc()).all())
    active_item_ids = {i.id for i in items if i.active}

    if not clients:
        return jsonify(items=[i.to_dict() for i in items], clients=[])
    ids = [c.id for c in clients]

    # (3) tüm durum hücreleri — tek sorgu, Python'da müşteriye grupla
    entries = defaultdict(dict)
    for e in ClientTrackingEntry.query.filter(ClientTrackingEntry.client_id.in_(ids)).all():
        # ANAHTAR STRING: JSON nesne anahtarları zaten string olur; frontend
        # `entries[String(item.id)]` ile erişmeli (entries[item.id] runtime'da patlar).
        entries[e.client_id][str(e.item_id)] = e.to_dict()

    # (4-7) türetilmiş sinyaller — her biri toplu
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
    """Müşteri detayı: tüm aktivite notları + son kampanyalar + son/sıradaki çekimler.
    Expand açıldığında çekilir. Kampanyalarda `amount_spent` BİLEREK yok — bu uç
    ileride başka role açılırsa mali veri sızmasın."""
    _, err = _require_management()
    if err:
        return err
    client = db.session.get(Client, client_id)
    if client is None:
        return jsonify(error='müşteri bulunamadı'), 404

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
    """Durum hücresini yaz — UPSERT. Kaynağın kimliği (client_id, item_id) olduğu için
    PUT: aynı gövdeyle tekrar çağırmak aynı sonucu verir, satır çoğalmaz."""
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
        return jsonify(error='not bulunamadı'), 404
    try:
        _apply_note(note, request.get_json(silent=True) or {}, u)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(note=note.to_dict())


@bp.delete('/notes/<int:note_id>')
def note_delete(note_id):
    """Soft-delete — günlük geçmişi korunur, listelerden düşer."""
    u, err = _require_management()
    if err:
        return err
    note = ClientActivityNote.query.filter_by(id=note_id, deleted_at=None).first()
    if note is None:
        return jsonify(error='not bulunamadı'), 404
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
        return jsonify(error='kalem bulunamadı'), 404
    try:
        _apply_item(item, request.get_json(silent=True) or {}, u)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(item=item.to_dict())


@bp.delete('/items/<int:item_id>')
def items_delete(item_id):
    """Soft-delete. Müşteri kayıtları (entries) DB'de kalır ama listede görünmez —
    kalemi geri getirmek yerine gizlemek isteniyorsa `active=false` kullanılmalı."""
    u, err = _require_management()
    if err:
        return err
    item = TrackingItem.query.filter_by(id=item_id, deleted_at=None).first()
    if item is None:
        return jsonify(error='kalem bulunamadı'), 404
    item.deleted_at = utcnow()
    item.updated_by = u.get('sub')
    db.session.commit()
    return jsonify(ok=True)


@bp.post('/items/reorder')
def items_reorder():
    """Katalog sırasını topluca yaz: {order: [id, id, ...]}."""
    u, err = _require_management()
    if err:
        return err
    order = (request.get_json(silent=True) or {}).get('order')
    if not isinstance(order, list) or not order:
        return jsonify(error='order listesi zorunlu'), 400
    rows = {i.id: i for i in TrackingItem.query.filter(TrackingItem.deleted_at.is_(None)).all()}
    unknown = [i for i in order if i not in rows]
    if unknown:
        return jsonify(error=f'bilinmeyen kalem: {unknown[0]}'), 400
    for position, item_id in enumerate(order):
        rows[item_id].position = position
        rows[item_id].updated_by = u.get('sub')
    db.session.commit()
    out = sorted(rows.values(), key=lambda i: (i.position, i.id))
    return jsonify(ok=True, items=[i.to_dict() for i in out])
