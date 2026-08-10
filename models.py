"""Agency domain modelleri — clients modülü + users_ref projeksiyonu.

Kimlik SSO'da yaşar; buradaki `user_id`/`*_by` alanları SSO `sub` (string) tutar.
users_ref, isim/rol göstermek için hafif bir SSO projeksiyonudur (login'de upsert,
göçte tohumlanır) — yetkili kaynak DEĞİLDİR.

Eski Mongo'dan taşınan kayıtlarda `legacy_mongo_id` idempotent import anahtarıdır.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from extensions import db

# none_as_null=True: Python None → SQL NULL (JSON 'null' skaleri değil).
# TypeEngine örneği kolonlar arası paylaşılabilir.
JSON_ = db.JSON(none_as_null=True)


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat() if dt else None


class UserRef(db.Model):
    __tablename__ = 'users_ref'
    sub = db.Column(db.String(64), primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    role = db.Column(db.String(32), nullable=False, default='pending')
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'sub': self.sub, 'email': self.email, 'name': self.name, 'role': self.role}


def upsert_user_ref(claims):
    """JWT claim'lerinden users_ref satırını ekle/güncelle (commit çağıranın işi)."""
    ref = db.session.get(UserRef, str(claims['sub']))
    if ref is None:
        ref = UserRef(sub=str(claims['sub']))
        db.session.add(ref)
    ref.email = claims['email']
    ref.name = claims.get('name')
    ref.role = claims.get('role', 'pending')
    return ref


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='active')  # active | deleted
    sector = db.Column(db.String(255))
    notes = db.Column(db.Text)
    client_email = db.Column(db.String(255))
    instagram_url = db.Column(db.String(512))
    google_drive_url = db.Column(db.String(512))
    special_days_token = db.Column(db.String(128))
    sharing_playbook = db.Column(JSON_)   # Sharing Board girdisi (Faz 2'de tabloya açılabilir)
    drive_meta = db.Column(JSON_)         # kök klasör linkleri + nadir video/photo klasörleri
    brand_profile = db.Column(JSON_)      # AI akışları için marka bağlamı + müşteri "Ayar" otoritesi:
                                          # brand_voice, target_audience, forbidden, cta, guide_md,
                                          # color_palette, content_mix, content_pillars, hashtags,
                                          # posting_days, ideas_per_week (hepsi opsiyonel)
    caption_settings = db.Column(JSON_)   # caption üretim müşteri-varsayılanı (Faz 1b): model, tone,
                                          # emoji_limit, hashtag_count, lang, use_brief, char_limit (opsiyonel)
    # brief aç/kapa (Faz 3): False → rutin/catch-up bu müşteriye otomatik brief üretmez
    # (brief-pasif). server_default=true: mevcut satırlar ALTER sonrası açık kalır.
    brief_enabled = db.Column(db.Boolean, nullable=False,
                              server_default=text('true'), default=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))
    deleted_reason = db.Column(db.Text)
    restored_at = db.Column(db.DateTime(timezone=True))
    restored_by = db.Column(db.String(64))

    contract = db.relationship('ClientContract', uselist=False, cascade='all, delete-orphan',
                               backref='client')
    contacts = db.relationship('ClientContact', cascade='all, delete-orphan', backref='client',
                               order_by='ClientContact.id')
    locations = db.relationship('ClientLocation', cascade='all, delete-orphan', backref='client',
                                order_by='ClientLocation.id')
    team_assignments = db.relationship('ClientTeamAssignment', cascade='all, delete-orphan',
                                       backref='client')
    week_folders = db.relationship('ClientWeekFolder', cascade='all, delete-orphan',
                                   backref='client', order_by='ClientWeekFolder.week_number')

    def to_dict(self, full=False, sensitive=True):
        """`sensitive=False` → ticari ve iletişim alanları yanıttan TAMAMEN düşer.

        Üretim rolleri (designer/content_creator/videographer) müşteri kaydını
        marka rehberi bağlamında okuyabiliyor (2026-07-27, Marka Rehberi sayfası);
        KDV/ücret (`contract`), müşteri iletişim bilgisi (`client_email`,
        `contacts`), adresler (`locations`), iç notlar ve public sayfa token'ı
        onlara ait değil. Alan gizlenmez, HİÇ konmaz — panelin `undefined`
        okuması "yetkin yok" ile "boş" arasında ayrım gerektirmiyor.
        Rol kararı api.py `_client_json()`'da tek yerde verilir."""
        d = {
            'id': self.id, 'name': self.name, 'status': self.status,
            'sector': self.sector,
            'instagram_url': self.instagram_url,
            'brief_enabled': self.brief_enabled,
            'created_at': iso(self.created_at), 'created_by': self.created_by,
            'updated_at': iso(self.updated_at), 'updated_by': self.updated_by,
            'team_assignments': {a.role_slot: a.user_id for a in self.team_assignments},
        }
        if sensitive:
            d['client_email'] = self.client_email
        if full:
            d.update({
                'google_drive_url': self.google_drive_url,
                'sharing_playbook': self.sharing_playbook,
                'drive_meta': self.drive_meta,
                'deleted_at': iso(self.deleted_at), 'deleted_by': self.deleted_by,
                'deleted_reason': self.deleted_reason,
                'restored_at': iso(self.restored_at), 'restored_by': self.restored_by,
                'week_folders': [w.to_dict() for w in self.week_folders],
            })
            if sensitive:
                d.update({
                    'notes': self.notes,
                    'special_days_token': self.special_days_token,
                    'contract': self.contract.to_dict() if self.contract else None,
                    'contacts': [c.to_dict() for c in self.contacts],
                    'locations': [l.to_dict() for l in self.locations],
                })
        return d


class ClientContract(db.Model):
    """Anlaşma şartları (1:1): sözleşme + çekim + mali alanlar."""
    __tablename__ = 'client_contracts'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, unique=True)
    weekly_content_count = db.Column(db.Integer)
    post_count = db.Column(db.Integer)
    story_count = db.Column(db.Integer)
    content_plan = db.Column(db.String(255))
    content_types = db.Column(JSON_)
    special_sharing_types = db.Column(JSON_)
    description = db.Column(db.Text)
    vat_rate = db.Column(db.Float)
    fee_effective_date = db.Column(db.String(32))
    video_shooting_enabled = db.Column(db.Boolean)
    weekly_video_count = db.Column(db.Integer)
    photo_shooting_enabled = db.Column(db.Boolean)
    weekly_photo_count = db.Column(db.Integer)
    drone_usage = db.Column(db.Boolean)
    location_notes = db.Column(db.Text)

    FIELDS = ('weekly_content_count', 'post_count', 'story_count', 'content_plan',
              'content_types', 'special_sharing_types', 'description', 'vat_rate',
              'fee_effective_date', 'video_shooting_enabled', 'weekly_video_count',
              'photo_shooting_enabled', 'weekly_photo_count', 'drone_usage', 'location_notes')

    def to_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}


class ClientContact(db.Model):
    __tablename__ = 'client_contacts'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(64))
    notes = db.Column(db.Text)

    def to_dict(self):
        return {'name': self.name, 'email': self.email, 'phone': self.phone, 'notes': self.notes}


class ClientLocation(db.Model):
    __tablename__ = 'client_locations'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    name = db.Column(db.String(255))
    address = db.Column(db.Text)

    def to_dict(self):
        return {'name': self.name, 'address': self.address}


class ClientTeamAssignment(db.Model):
    """Sabit rol slotları: videographer_shoot / videographer_edit / content_creator / designer /
    manager (yönetici board'unda "Müşterilerim" işareti; yalnız sahip yönetici koyar)."""
    __tablename__ = 'client_team_assignments'
    __table_args__ = (db.UniqueConstraint('client_id', 'role_slot'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    role_slot = db.Column(db.String(32), nullable=False)
    user_id = db.Column(db.String(64), nullable=False)  # SSO sub


class UserHiddenClient(db.Model):
    """Kişisel "bu müşteriyi bu sayfada göstermeme" tercihi (2026-07-25).

    Satırın VARLIĞI = gizli. Soft-delete BİLEREK YOK — `client_tracking_entries` ile
    aynı gerekçe: UNIQUE slotu işgal eder, geri açma aynı satırı yeniden yazınca
    IntegrityError verir. "Geri aç" = satırı SİL.

    NEDEN tek-satır-JSON-dizi DEĞİL: `planning.py`'nin öğrettiği ders — tek kolonu
    topluca yeniden yazan model, iki sekme/iki istek aynı anda toggle ettiğinde birini
    sessizce kaybeder (read-modify-write). Satır modeli her toggle'ı atomik yapar,
    UNIQUE yarışı emniyete alır ve filtre tek `IN` sorgusuna dönüşür.

    NEDEN `AppSetting` DEĞİL: o GLOBAL key/value deposu (`key` String(64));
    `vg_hidden:<sub>` anahtarlamak semantiği bozar, uzun sub'da taşma riski taşır ve
    değeri sorgulanamaz bir JSON blob'a çevirir. NEDEN `users_ref` kolonu DEĞİL:
    users_ref SSO projeksiyonudur (login'de üzerine yazılır) + yeni kolon prod'da
    elle ALTER ister.

    `scope`: ileride başka bir sayfa (ör. designer board) da gizleme isterse ikinci
    tablo açmak yerine aynı tablo farklı scope ile hizmet eder."""
    __tablename__ = 'user_hidden_clients'
    __table_args__ = (
        db.UniqueConstraint('owner_sub', 'scope', 'client_id',
                            name='uq_hidden_owner_scope_client'),
        db.Index('ix_hidden_owner_scope', 'owner_sub', 'scope'),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_sub = db.Column(db.String(64), nullable=False)   # SSO sub — FK DEĞİL (kimlik SSO'da)
    scope = db.Column(db.String(32), nullable=False, default='videographer_upload')
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class ClientAsset(db.Model):
    """Müşteri marka görselleri: logo + sabit standart görseller (ör. ürün etiketleri).
    AI görsel üretiminde referans olarak seçilir; dosya Drive'da 'Marka Görselleri'
    alt klasöründe. kind='logo' müşteri başına TEK aktif satır (yenisi eskisini
    soft-delete eder); kind='standard' çoklu."""
    __tablename__ = 'client_assets'
    __table_args__ = (db.Index('ix_client_assets_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    kind = db.Column(db.String(16), nullable=False)  # logo | standard
    file_id = db.Column(db.String(128), nullable=False)
    file_name = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger)
    label = db.Column(db.String(256))  # panelde görünen ad (ör. "Beyaz peynir etiketi")
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'kind': self.kind,
                'file_id': self.file_id, 'file_name': self.file_name,
                'mime_type': self.mime_type, 'file_size': self.file_size,
                'label': self.label,
                'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None}


class ClientWeekFolder(db.Model):
    """Hafta bazlı Drive klasörleri (eski week_folders.{1..52} map'inin yerine)."""
    __tablename__ = 'client_week_folders'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_number'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_number = db.Column(db.Integer, nullable=False)
    folder_id = db.Column(db.String(128))
    name = db.Column(db.String(255))
    link = db.Column(db.String(512))

    def to_dict(self):
        return {'week_number': self.week_number, 'folder_id': self.folder_id,
                'name': self.name, 'link': self.link}


class Notification(db.Model):
    """Panel-içi bildirim. Ops Digest (Faz 4) ve revizyon/onay bildirimleri bu
    tablodan akar; panel bell okur. **2026-08-05:** ntfy geri geldi ama bu tablonun
    YERİNE değil — `severity` alanı hangi satırın ayrıca telefona gideceğini
    belirler (karar `notify_rules.should_push_ntfy`, teslimat `ntfy_gateway`)."""
    __tablename__ = 'notifications'
    __table_args__ = (db.Index('ix_notifications_recipient', 'recipient_sub', 'read_at'),)
    id = db.Column(db.Integer, primary_key=True)
    recipient_sub = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    # kritik | normal | bilgi — varsayılan katalogdan gelir (notifications.CATALOG).
    # server_default: mevcut satırlar ALTER sonrası NULL kalmasın (2026-08-05).
    severity = db.Column(db.String(16), nullable=False, default='normal',
                         server_default='normal')
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text)
    link = db.Column(db.String(512))
    read_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {'id': self.id, 'kind': self.kind, 'severity': self.severity,
                'title': self.title, 'body': self.body,
                'link': self.link, 'read_at': iso(self.read_at),
                'created_at': iso(self.created_at)}


class NotificationPref(db.Model):
    """Kişi başına bildirim tercihi (2026-08-05). Kayıt YOKSA telefona hiçbir şey
    gitmez — ntfy bilinçli olarak OPT-IN (panel-içi çan zaten herkeste çalışıyor).

    `ntfy_topic` 32-hex rastgele: ntfy'de okuma yetkisi topic adının gizliliğine
    dayanıyor (yazma token'lı, bkz. ntfy_gateway), o yüzden topic tahmin edilebilir
    olmamalı ve yalnız sahibine gösterilir."""
    __tablename__ = 'notification_prefs'
    user_sub = db.Column(db.String(64), primary_key=True)
    ntfy_topic = db.Column(db.String(64), nullable=False)
    ntfy_enabled = db.Column(db.Boolean, nullable=False, default=False)
    min_severity = db.Column(db.String(16), nullable=False, default='kritik')
    # Sessiz saat aralığı (0-23, saran aralık serbest: 22→08). NULL = yok.
    quiet_start = db.Column(db.Integer)
    quiet_end = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'ntfy_topic': self.ntfy_topic, 'ntfy_enabled': self.ntfy_enabled,
                'min_severity': self.min_severity,
                'quiet_start': self.quiet_start, 'quiet_end': self.quiet_end}


class RateWindow(db.Model):
    """Paylaşımlı fixed-window hız sınırı sayacı (Postgres/sqlite; worker'lar arası
    ortak — eski in-memory per-worker yerine). bucket ör. 'review:<token>'."""
    __tablename__ = 'rate_limits'
    bucket = db.Column(db.String(160), primary_key=True)
    window_start = db.Column(db.Integer, primary_key=True)  # unix pencere başı
    count = db.Column(db.Integer, nullable=False, default=0)


class Job(db.Model):
    """Asenkron iş kuyruğu (Postgres SKIP LOCKED). type:
    caption|media|special_days|brief|ops_digest|videographer_ideas|image_gen|codex_image|similarity|magnific_credits|prompt_examples|prompt_convert. Worker
    (proje sahibi bağlamı) çeker, işler, sonucu yazar. Panel poll'lar. priority: yüksek sayı
    önce claim edilir (interaktif caption=10, batch=0)."""
    __tablename__ = 'jobs'
    __table_args__ = (db.Index('ix_jobs_status_type', 'status', 'type'),)
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='queued')  # queued|running|done|failed
    priority = db.Column(db.Integer, nullable=False, server_default='0', default=0)  # yüksek=önce claim
    payload = db.Column(JSON_)
    result = db.Column(JSON_)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    claimed_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    available_at = db.Column(db.DateTime(timezone=True), nullable=True)  # backoff/retry: bu andan önce claim edilmez

    def to_dict(self):
        return {'id': self.id, 'type': self.type, 'status': self.status,
                'result': self.result, 'created_at': iso(self.created_at),
                'finished_at': iso(self.finished_at)}


class AppSetting(db.Model):
    """Basit key/value global ayar deposu (panelden düzenlenebilir). İlk kullanım:
    `caption_global_rules` — tüm müşteriler için ortak caption/hashtag kuralları
    (vault snapshot'tan seed edilir, bkz. scripts/seed_brand_profiles.py)."""
    __tablename__ = 'app_settings'
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @classmethod
    def get(cls, key, default=None):
        row = db.session.get(cls, key)
        return row.value if row is not None else default

    @classmethod
    def set(cls, key, value):
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value = value
        return row


class AiUsage(db.Model):
    """claude -p çağrı başına token/maliyet izi (Sunucu Ayarları token paneli).

    `ai_claude.run` her başarılı çağrıda bir satır yazar (usage_sink üzerinden).
    Sır/prompt İÇERMEZ — yalnız sayaç. `source` = job tipi (attribution)."""
    __tablename__ = 'ai_usage'
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)
    model = db.Column(db.String(64))
    source = db.Column(db.String(32))
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    cache_read_tokens = db.Column(db.Integer, nullable=False, default=0)
    cache_creation_tokens = db.Column(db.Integer, nullable=False, default=0)
    cost_usd = db.Column(db.Float, nullable=False, default=0.0)


class AdCampaign(db.Model):
    """Reklam takibi (2026-07-24) — müşteri başına reklam çıkışları: tarih aralığı,
    harcanan tutar (₺), platform, durum, sonuç metrikleri ve notlar.

    Yalnız `management` görür/düzenler (mali bilgi). Silme SOFT (`deleted_at`) —
    kayıt geçmişi korunur, listelerden düşer. Tutar `Numeric(12,2)`: para kuruş
    hassasiyetiyle tutulur (Float yuvarlama hatası istemiyoruz); `to_dict` float'a
    çevirir (JSON'da Decimal serialize edilemez)."""
    __tablename__ = 'ad_campaigns'
    __table_args__ = (db.Index('ix_ad_campaigns_client_start', 'client_id', 'start_date'),)

    PLATFORMS = ('meta', 'google', 'tiktok', 'other')
    STATUSES = ('planned', 'active', 'finished')

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    title = db.Column(db.String(255))
    platform = db.Column(db.String(16), nullable=False, default='meta')
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)                      # NULL = devam ediyor / tek gün
    amount_spent = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    status = db.Column(db.String(16), nullable=False, default='active')
    reach = db.Column(db.Integer)                      # erişim (elle girilir)
    clicks = db.Column(db.Integer)                     # tıklama (elle girilir)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.String(64))              # SSO sub
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    client = db.relationship('Client', backref=db.backref('ad_campaigns', lazy='select'))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id,
            'client_name': self.client.name if self.client else None,
            'title': self.title, 'platform': self.platform,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'amount_spent': float(self.amount_spent or 0),
            'status': self.status, 'reach': self.reach, 'clicks': self.clicks,
            'notes': self.notes,
            'created_at': iso(self.created_at), 'updated_at': iso(self.updated_at),
        }


class TrackingItem(db.Model):
    """Müşteri Takip kalem kataloğu (2026-07-25) — ajansın müşteriye satabileceği iş
    kalemlerinin YÖNETİLEBİLİR tanımı (panelden ekle/düzenle/sırala; kod değişmeden).

    İKİ AYRI KAPATMA MEKANİZMASI, kasıtlı:
      * active=False → katalogdan gizler ama MEVCUT müşteri kayıtları
        (client_tracking_entries) durur; "artık satmıyoruz ama geçmiş dursun".
      * deleted_at   → soft-delete; hiçbir yerde listelenmez, satır korunur.

    `key` yalnız TOHUMLANAN kalemlerde dolu (seed idempotans anahtarı); panelden
    eklenende NULL'dır. `name` üzerinde DB unique YOK — kısmi index
    (WHERE deleted_at IS NULL) Postgres/sqlite arasında ayrışırdı; tekillik uçta
    Türkçe-duyarlı normalize (client_tracking._fold) ile zorlanır."""
    __tablename__ = 'tracking_items'
    __table_args__ = (db.Index('ix_tracking_items_order', 'position', 'id'),)

    CATEGORIES = ('hukuki', 'dijital', 'tasarim', 'uretim', 'reklam', 'diger')

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(48), unique=True)         # seed anahtarı; elle eklenende NULL
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(24), nullable=False, default='diger')
    icon = db.Column(db.String(40))                     # lucide ikon adı (panel beyaz-listeyle çözer)
    position = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, server_default=text('true'), default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))               # SSO sub
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'key': self.key, 'name': self.name,
                'category': self.category, 'icon': self.icon,
                'position': self.position, 'active': self.active}


class ClientTrackingEntry(db.Model):
    """Müşteri × kalem DURUM HÜCRESİ (2026-07-25). UNIQUE(client_id, item_id) →
    hücre upsert edilir, asla çoğaltılmaz.

    NEDEN SOFT-DELETE YOK: unique kısıt + `deleted_at` birlikte çalışmaz (silinmiş
    satır slotu işgal eder, yeniden kayıt IntegrityError verir; çözümü kısmi index =
    dialect ayrışması). Bu satır bir belge değil bir HAL; "silmek" = status'ü 'yok'a
    çekmek. Tarihçe `client_activity_notes`'ta yaşar."""
    __tablename__ = 'client_tracking_entries'
    __table_args__ = (
        db.UniqueConstraint('client_id', 'item_id', name='uq_client_tracking_entry'),
        db.Index('ix_client_tracking_entries_client', 'client_id'),
    )

    STATUSES = ('var', 'yok', 'surecte', 'ilgilenmiyor')

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('tracking_items.id'), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='yok')
    status_date = db.Column(db.Date)          # durumun geçerli olduğu gün (ör. tescil tarihi)
    note = db.Column(db.Text)
    url = db.Column(db.String(1024))          # http(s) zorunlu (doğrulama uçta)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'item_id': self.item_id,
                'status': self.status,
                'status_date': self.status_date.isoformat() if self.status_date else None,
                'note': self.note, 'url': self.url,
                'updated_at': iso(self.updated_at), 'updated_by': self.updated_by}


class ClientActivityNote(db.Model):
    """Tarihli serbest aktivite günlüğü (2026-07-25) — "en son neler yapılmış".

    Append-only akış; silme SOFT. Durum hücresinden AYRI tablo olmasının nedeni iki
    verinin farklı şekilde olması: "web sitesi var mı" tek/üzerine-yazılan bir hal,
    "en son ne yapıldı" birikimli bir akış. `item_id` opsiyonel: not bir kaleme
    bağlanabilir ("katalog basıldı" → katalog kalemi) ama bağsız da olabilir."""
    __tablename__ = 'client_activity_notes'
    __table_args__ = (db.Index('ix_client_activity_notes_client_date',
                               'client_id', 'happened_on'),)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    happened_on = db.Column(db.Date, nullable=False)   # olay günü (varsayılan bugün)
    text = db.Column(db.Text, nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('tracking_items.id'))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))              # "yazan" — SSO sub
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self, author_name=None):
        return {'id': self.id, 'client_id': self.client_id, 'item_id': self.item_id,
                'happened_on': self.happened_on.isoformat() if self.happened_on else None,
                'text': self.text, 'created_by': self.created_by,
                'author_name': author_name, 'created_at': iso(self.created_at)}
