"""Sharing Board domain modelleri (Faz 2a çekirdeği).

Eski monolitin paralel iki modeli (yeni `sharing_shares` + terk edilmiş
`sharing_card_status`) analiz edildi: **shares yetkili**, card_status yalnız
W20-W22 tarihsel arşiv (bkz LegacyCardStatus). Ana eksen `(client_id, week_iso)`,
week_iso = ISO hafta string'i "YYYY-Www".

PostgreSQL dönüşümü: durum/tür alanları native PG enum (sqlite'ta VARCHAR+CHECK'e
düşer), yarı-yapısal alanlar JSONB (sqlite'ta JSON), gerçek FK + timestamptz.
`legacy_mongo_id` idempotent göç anahtarı.
"""
from sqlalchemy.dialects.postgresql import JSONB

from extensions import db
from models import iso, utcnow

# PG'de JSONB, sqlite testlerinde JSON'a düşer.
# none_as_null=True: Python None → SQL NULL (JSON 'null' skaleri değil) → IS NULL
# sorguları tutarlı, gereksiz JSON-null yazımı olmaz.
JSONB_ = JSONB(none_as_null=True).with_variant(db.JSON(none_as_null=True), 'sqlite')

SHARE_KINDS = ('post', 'story', 'video', 'linkedin')
SHARE_STATUSES = ('draft', 'published')
REVISION_KINDS = ('design', 'video')
REVISION_STATUSES = ('open', 'resolved')
SHOOT_STATUSES = ('pending', 'completed')

ShareKind = db.Enum(*SHARE_KINDS, name='share_kind')
ShareStatus = db.Enum(*SHARE_STATUSES, name='share_status')
RevisionKind = db.Enum(*REVISION_KINDS, name='revision_kind')
RevisionStatus = db.Enum(*REVISION_STATUSES, name='revision_status')
ShootStatus = db.Enum(*SHOOT_STATUSES, name='shoot_status')


class Share(db.Model):
    """Yetkili paylaşım kaydı — satır = tek paylaşım."""
    __tablename__ = 'shares'
    __table_args__ = (db.Index('ix_shares_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    kind = db.Column(ShareKind, nullable=False)
    status = db.Column(ShareStatus, nullable=False, default='draft')

    file_id = db.Column(db.String(128))
    file_name = db.Column(db.String(512))
    original_name = db.Column(db.String(512))
    caption_text = db.Column(db.Text)
    hashtag_text = db.Column(db.Text)
    note = db.Column(db.Text)

    published_day_name = db.Column(db.String(32))
    published_at = db.Column(db.DateTime(timezone=True))
    published_by = db.Column(db.String(64))
    planned_date = db.Column(db.Date)
    planned_time = db.Column(db.String(8))

    platforms = db.Column(JSONB_)       # {instagram|story|linkedin: {published_at, url}}
    client_review = db.Column(JSONB_)   # {status: approved|revision_requested, note, at}
    transcript = db.Column(db.Text)     # video ses transkripti (media_worker; caption bağlamı)
    caption_suggestions = db.Column(JSONB_)  # {captions:[...], hashtags} — son üretilen (kalıcı; modal kapansa bile durur)
    revision = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
            'kind': self.kind, 'status': self.status,
            'file_id': self.file_id, 'file_name': self.file_name,
            'original_name': self.original_name,
            'caption_text': self.caption_text, 'hashtag_text': self.hashtag_text,
            'note': self.note,
            'published_day_name': self.published_day_name,
            'published_at': iso(self.published_at), 'published_by': self.published_by,
            'planned_date': self.planned_date.isoformat() if self.planned_date else None,
            'planned_time': self.planned_time,
            'platforms': self.platforms or {},
            'client_review': self.client_review,
            'revision': self.revision,
            'has_transcript': bool(self.transcript),
            'caption_suggestions': self.caption_suggestions,
            'created_at': iso(self.created_at), 'created_by': self.created_by,
            'updated_at': iso(self.updated_at), 'updated_by': self.updated_by,
        }


class CardUpload(db.Model):
    """Drive'a yüklenen ham dosya kaydı (paylaşım seçici kaynağı)."""
    __tablename__ = 'card_uploads'
    __table_args__ = (db.Index('ix_uploads_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    card_index = db.Column(db.Integer)
    category = db.Column(db.String(32))
    file_id = db.Column(db.String(128))
    file_name = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger)
    drive_created_time = db.Column(db.String(40))
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True))
    deleted_at = db.Column(db.DateTime(timezone=True))
    request_id = db.Column(db.String(24))
    upload_uuid = db.Column(db.String(64))
    adopted = db.Column(db.Boolean, default=False)
    revision = db.Column(db.Integer, default=0)
    backfilled = db.Column(db.Boolean, default=False)
    moved_at = db.Column(db.DateTime(timezone=True))
    moved_from_week_iso = db.Column(db.String(16))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
            'card_index': self.card_index, 'category': self.category,
            'file_id': self.file_id, 'file_name': self.file_name,
            'mime_type': self.mime_type, 'file_size': self.file_size,
            'uploaded_by': self.uploaded_by, 'uploaded_at': iso(self.uploaded_at),
            'revision': self.revision,
        }


class ReviewLink(db.Model):
    """Public /review/<token> için tek-kullanımlık olmayan onay linki."""
    __tablename__ = 'review_links'
    __table_args__ = (db.Index('ix_review_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {'id': self.id, 'token': self.token, 'client_id': self.client_id,
                'week_iso': self.week_iso, 'revoked': self.revoked,
                'created_at': iso(self.created_at)}


class RevisionRequest(db.Model):
    """Revizyon talebi (tasarım veya video). Tam akış Faz 2c; tablo+durum burada."""
    __tablename__ = 'revision_requests'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    share_id = db.Column(db.Integer, db.ForeignKey('shares.id'))
    card_index = db.Column(db.Integer)
    kind = db.Column(RevisionKind, nullable=False, default='video')
    status = db.Column(RevisionStatus, nullable=False, default='open')
    category = db.Column(db.String(32))
    note = db.Column(db.Text)
    requested_by = db.Column(db.String(64))
    requested_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    resolved_by = db.Column(db.String(64))
    resolved_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
                'share_id': self.share_id, 'kind': self.kind, 'status': self.status,
                'category': self.category, 'note': self.note,
                'requested_at': iso(self.requested_at)}


class ClientPriority(db.Model):
    """Haftalık öncelik bayrağı (aktif = cleared_at NULL)."""
    __tablename__ = 'client_priority'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    set_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    set_by = db.Column(db.String(64))
    cleared_at = db.Column(db.DateTime(timezone=True))
    cleared_reason = db.Column(db.String(64))


class SpecialDayEvent(db.Model):
    """Özel gün/hafta kaynağı. client_id NULL=global, dolu=müşteriye özel."""
    __tablename__ = 'special_days_events'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    day_name = db.Column(db.String(255))
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)
    type = db.Column(db.String(32))
    date_num = db.Column(db.Integer)
    date_start = db.Column(db.Integer)
    date_end = db.Column(db.Integer)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    # onay kapısı: server_default = mevcut/elle-girme kayıtlar (approved/manual),
    # ORM default = yeni AI insert'leri (draft/ai). İkisi AYRI olmalı (geriye uyum).
    status = db.Column(db.String(16), nullable=False, server_default='approved', default='draft')
    generated_by = db.Column(db.String(16), nullable=False, server_default='manual', default='ai')

    def to_dict(self):
        return {'id': self.id, 'day_name': self.day_name, 'description': self.description,
                'active': self.active, 'month': self.month, 'year': self.year,
                'type': self.type, 'date_num': self.date_num,
                'date_start': self.date_start, 'date_end': self.date_end,
                'client_id': self.client_id,
                'status': self.status, 'generated_by': self.generated_by}


class SpecialCardStatus(db.Model):
    """Bir özel günün bir haftada yayınlandığı işareti."""
    __tablename__ = 'special_card_status'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_iso', 'event_id'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('special_days_events.id'), nullable=False)
    published_at = db.Column(db.DateTime(timezone=True))
    published_by = db.Column(db.String(64))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
                'event_id': self.event_id, 'published_at': iso(self.published_at)}


class SpecialDaySelection(db.Model):
    """Müşterinin bir ay için seçtiği özel günler (event id listesi)."""
    __tablename__ = 'special_day_selections'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    selected_event_ids = db.Column(JSONB_)   # [yeni SpecialDayEvent.id, ...]
    token = db.Column(db.String(64))
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)


class CaptionHistory(db.Model):
    """Seçilmiş caption geçmişi (yeniden kullanım için)."""
    __tablename__ = 'caption_history'
    __table_args__ = (db.Index('ix_caption_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16))
    card_index = db.Column(db.Integer)
    source = db.Column(db.String(32))
    caption_text = db.Column(db.Text)
    hashtag_text = db.Column(db.Text)
    selected_at = db.Column(db.DateTime(timezone=True))
    selected_by = db.Column(db.String(64))


class ShootTask(db.Model):
    """Videografçı çekim planı görevi (Kanban kartı). scheduled_date = gün kolonu,
    position = kolon içi sıra."""
    __tablename__ = 'shoot_tasks'
    __table_args__ = (db.Index('ix_shoot_date', 'scheduled_date'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))  # nullable = ad-hoc
    title = db.Column(db.String(512))
    content_type = db.Column(db.String(64))
    scheduled_date = db.Column(db.Date)
    start_time = db.Column(db.String(8))
    end_time = db.Column(db.String(8))
    status = db.Column(ShootStatus, nullable=False, default='pending')
    priority = db.Column(db.String(16), nullable=False, default='normal')  # normal|high|urgent
    assigned_to = db.Column(db.String(64))
    assigned_by = db.Column(db.String(64))
    position = db.Column(db.Integer, nullable=False, default=0)
    location_note = db.Column(db.Text)
    equipment = db.Column(JSONB_)
    drive_links = db.Column(JSONB_)
    completed_by = db.Column(db.String(64))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    updated_by = db.Column(db.String(64))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id, 'title': self.title,
            'content_type': self.content_type,
            'scheduled_date': self.scheduled_date.isoformat() if self.scheduled_date else None,
            'start_time': self.start_time, 'end_time': self.end_time,
            'status': self.status, 'priority': self.priority,
            'assigned_to': self.assigned_to, 'position': self.position,
            'location_note': self.location_note, 'equipment': self.equipment,
        }


class VideographerBusinessMark(db.Model):
    """Bir müşterinin o hafta 'videosu var' işareti."""
    __tablename__ = 'videographer_business_marks'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    has_video = db.Column(db.Boolean, default=False)
    marked_at = db.Column(db.DateTime(timezone=True))
    marked_by = db.Column(db.String(64))


class VideographerPhoto(db.Model):
    """Çekimden yüklenen fotoğraf (Drive referansı)."""
    __tablename__ = 'videographer_photos'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    shoot_date = db.Column(db.String(32))
    folder_id = db.Column(db.String(128))
    file_id = db.Column(db.String(128))
    file_name = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger)
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True))
    deleted_at = db.Column(db.DateTime(timezone=True))
    # designer bu fotoğrafı kullandığında işaretlenir (designer board sonraki işte
    # bağlanacak; şimdilik used-mark ucundan set edilir). used_at NULL = kullanılmadı.
    used_at = db.Column(db.DateTime(timezone=True))
    used_by = db.Column(db.String(64))


class DepotFile(db.Model):
    """Videograf Deposu (2026-07-25) — tüm videograflar + yönetim için ORTAK serbest
    dosya alanı. Müşteriye ve haftaya BAĞLI DEĞİL (bilinçli: depo bir çalışma alanı,
    teslim kanalı değil).

    Drive'da `<içerik kökü>/Videograf Deposu` altında TEK DÜZ klasör; kişi/ay alt klasörü yok.
    Kota (ortak 5 GB) sayaç kolonuyla DEĞİL, her istekte `SUM(file_size)` ile ölçülür
    (`depot._used_bytes`) — sayaç, Drive yüklemesi ile DB commit'i arasındaki her
    çökmede kalıcı drift üretir ve mutabakat işi gerektirirdi.

    Silme İKİ KATMANLI: DB'de soft-delete (`deleted_at` → kota anında boşalır) +
    Drive'da çöp kutusu (`dg.trash_file`, 30 gün geri alınabilir). Drive tarafı hata
    verse bile DB satırı soft-delete edilir; aksi halde tek Drive hıçkırığı dosyayı
    silinemez yapıp kotayı kalıcı meşgul ederdi.

    `media_store`'a KOPYALANMAZ: depo dosyalarını hiçbir uç lokal servis etmiyor
    (Drive kanonik, önizleme/stream yok) — 5 GB'ı 21 gün diskte ikinci kez tutmanın
    karşılığı olmaz."""
    __tablename__ = 'depot_files'
    __table_args__ = (db.Index('ix_depot_files_uploaded', 'deleted_at', 'uploaded_at'),)

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.String(128), nullable=False, unique=True)   # Drive dosya id
    folder_id = db.Column(db.String(128))            # 'Videograf Deposu' klasör id (iz)
    file_name = db.Column(db.String(512), nullable=False)
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger, nullable=False)   # kota aritmetiği — ASLA NULL
    note = db.Column(db.String(300))                 # opsiyonel açıklama
    uploaded_by = db.Column(db.String(64))           # SSO sub
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))

    def to_dict(self, uploader_name=None):
        return {'id': self.id, 'file_id': self.file_id, 'file_name': self.file_name,
                'mime_type': self.mime_type, 'file_size': self.file_size,
                'note': self.note, 'uploaded_by': self.uploaded_by,
                'uploader_name': uploader_name, 'uploaded_at': iso(self.uploaded_at)}


class VideographerIdea(db.Model):
    """AI trend-öneri kartı (Faz 5, step 17): videografçıya `link + neden + çekim
    fikri`. `videographer_ideas` handler küratörlü YouTube/Vimeo RSS trendlerini
    (handler-içi Python çekme, `ai_claude` DIŞINDA) toplayıp `wrap_untrusted` ile
    sararak `ai_claude.run`'a süzdürür; üretilen öneriler status='new' yazılır.
    Videografçı beğenir (çekim listesine ShootTask olarak eklenir → status='accepted')
    ya da atlar (status='skipped')."""
    __tablename__ = 'videographer_ideas'
    __table_args__ = (db.Index('ix_vg_ideas_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    reference_link = db.Column(db.Text)   # ilham veren trend videosunun linki
    reason = db.Column(db.Text)           # neden bu müşteriye uygun
    shoot_idea = db.Column(db.Text)       # somut çekim fikri
    status = db.Column(db.String(16), nullable=False, default='new')  # new|accepted|skipped
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id,
                'reference_link': self.reference_link, 'reason': self.reason,
                'shoot_idea': self.shoot_idea, 'status': self.status,
                'created_at': iso(self.created_at)}


class ImageGeneration(db.Model):
    """AI görsel üretim izi (Faz 6, step 19). GATE 18 kararı (faz6-magnific-spike.md):
    MCP-via-`claude -p` NO-GO (headless subprocess Magnific'e bağlanamıyor) → üretim
    Magnific/Freepik REST API + `x-magnific-api-key` ile handler-içi `requests`
    (videographer GATE 16 deseni; `ai_claude` savunması korunur). Opsiyonel prompt
    rafinasyonu tek-atış `ai_claude.run` (MCP kapalı). Üretilen asset Drive/müşteri
    klasörüne düşer, satır status='pending' (onay bekler) ile açılır; management onaylar
    (status='approved') ya da yeniden üretir. KVKK (spike §5): yalnız müşteri onayı
    (Client.brand_profile.ai_image_consent) verilmişse üretim yapılır — onaysız müşteride
    Magnific'e görsel GÖNDERİLMEZ. Üretim izi (hangi görsel/müşteri/ne zaman) burada
    tutulur (denetlenebilirlik)."""
    __tablename__ = 'image_generations'
    __table_args__ = (db.Index('ix_imggen_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    brief_id = db.Column(db.Integer, db.ForeignKey('weekly_briefs.id'))  # opsiyonel onaylı brief girdisi
    prompt = db.Column(db.Text)          # üretim için kullanılan (varsa rafine edilmiş) prompt
    refs = db.Column(JSONB_)             # referans görsel id/URL listesi
    settings = db.Column(JSONB_)         # {type, model, effort, refine, prompt}
    asset_id = db.Column(db.String(128))  # Magnific creation id
    result_url = db.Column(db.Text)       # Magnific asset URL
    drive_file_id = db.Column(db.String(128))
    drive_file_name = db.Column(db.String(512))
    # onay kapısı: yeni üretim status='pending' (onay bekler); management onaylar/yeniden üretir
    status = db.Column(db.String(16), nullable=False, default='pending')  # pending|approved|rejected
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    reviewed_by = db.Column(db.String(64))
    reviewed_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'brief_id': self.brief_id,
                'prompt': self.prompt, 'refs': self.refs or [], 'settings': self.settings or {},
                'asset_id': self.asset_id, 'result_url': self.result_url,
                'drive_file_id': self.drive_file_id, 'drive_file_name': self.drive_file_name,
                'status': self.status, 'created_at': iso(self.created_at),
                'created_by': self.created_by}


class WeeklyBrief(db.Model):
    """Haftalık içerik brief'i (müşteri × hafta). Bir vault'tan senkronlanır;
    içerik fikirleri + intro + ham markdown. Ekip okur (üretim değil)."""
    __tablename__ = 'weekly_briefs'
    __table_args__ = (db.Index('ix_brief_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    title = db.Column(db.String(512))
    intro = db.Column(db.Text)
    raw_md = db.Column(db.Text)
    ideas = db.Column(JSONB_)
    frontmatter = db.Column(JSONB_)
    week_notes = db.Column(JSONB_)
    source_path = db.Column(db.String(512))
    # AI brief'lerde (synced_at NULL) "en güncel" seçimi COALESCE(synced_at, created_at)
    # ile created_at'e düşer → created_at boş kalmamalı. default=utcnow: handler ayrıca
    # açıkça set etse de (step 13) diğer insert yolları için güvenli varsayılan. Import
    # kayıtları created_at'i kendileri verir (geriye uyum bozulmaz).
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    synced_at = db.Column(db.DateTime(timezone=True))
    # ONAY KAPISI KALDIRILDI (2026-07-30, proje sahibi kararı: "brief'ler üretildikten sonra onay
    # beklemesin, hepsi doğrudan işlensin"). Eskiden ORM default 'draft' idi → her AI brief'i
    # elle onay bekliyordu ve onaylanana kadar caption bağlamına girmiyordu. Artık AI brief'i
    # de doğar doğmaz 'approved' → caption/görsel akışı beklemeden okur.
    # Kaldırmanın güvenli olmasının nedeni: brief HİÇBİR müşteri-facing yüzeyde görünmüyor
    # (review.py / special_days.py / lente.py'de tek referans yok) — yalnız ekip içi + AI
    # bağlamı. Düzeltme yolu onay değil YENİDEN ÜRETİM (`brief_handler` force, aynı satırı
    # üzerine yazar). `generated_by` DEĞİŞMEDİ: 'ai' vs 'import' ayrımı hâlâ anlamlı (köken).
    # Okuma tarafındaki approved-only süzgeç (sharing.brief) bilinçli olarak KALDI: artık
    # taslak üretilmiyor, ama elde kalan/geri yüklenen bir taslak yanlışlıkla akışa girmesin.
    status = db.Column(db.String(16), nullable=False, server_default='approved', default='approved')
    generated_by = db.Column(db.String(16), nullable=False, server_default='import', default='ai')

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
                'title': self.title, 'intro': self.intro, 'raw_md': self.raw_md,
                'ideas': self.ideas or [], 'frontmatter': self.frontmatter or {},
                'week_notes': self.week_notes or {},
                'status': self.status, 'generated_by': self.generated_by,
                'synced_at': iso(self.synced_at)}


class WeeklyCanvas(db.Model):
    """Haftalık planlama canvas'ı — konumlanmış kart (parent_weekly_task) matrisi.
    Yjs gerçek-zamanlı state ATILDI; kartlar JSONB (position/size/color/title)."""
    __tablename__ = 'weekly_canvas'
    __table_args__ = (db.UniqueConstraint('week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    week_iso = db.Column(db.String(16), nullable=False)   # board_key ("YYYY-Www")
    year = db.Column(db.Integer)
    week_number = db.Column(db.Integer)
    archived = db.Column(db.Boolean, default=False)
    tasks = db.Column(JSONB_)                              # [{task_id,title,color,position,size,...}]
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    last_modified_by = db.Column(db.String(64))

    def to_dict(self):
        return {'id': self.id, 'week_iso': self.week_iso, 'archived': self.archived,
                'tasks': self.tasks or [], 'updated_at': iso(self.updated_at)}


class DriveThumbnail(db.Model):
    """Drive thumbnail kalıcı cache'i (eski Mongo drive_thumbnails yerine Postgres)."""
    __tablename__ = 'drive_thumbnails'
    file_id = db.Column(db.String(128), primary_key=True)
    width = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.LargeBinary)
    mime = db.Column(db.String(128))
    cached_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class LegacyCardStatus(db.Model):
    """Terk edilmiş sharing_card_status arşivi (W20-W22 caption üretim geçmişi).

    Canlı akışa KARIŞMAZ; yalnız veri kaybını önlemek için ham JSONB olarak saklanır.
    """
    __tablename__ = 'legacy_card_status'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    week_iso = db.Column(db.String(16))
    card_index = db.Column(db.Integer)
    raw = db.Column(JSONB_)


class UploadReview(db.Model):
    """Müşterinin onay sayfasındaki **yükleme bazlı** kararı (2026-07-24).

    Onay sayfası artık sharing board paylaşımlarını değil, tasarımcının o hafta
    yüklediği post/video dosyalarını (`card_uploads`) gösterir; müşterinin
    onay/revize kararı da burada tutulur. `upload_id` UNIQUE → yükleme başına tek
    (en güncel) karar; müşteri fikrini değiştirirse üzerine yazılır. Eski
    `Share.client_review` kayıtları OLDUĞU GİBİ kalır (geçmiş bozulmaz)."""
    __tablename__ = 'card_upload_reviews'
    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('card_uploads.id'),
                          nullable=False, unique=True)
    status = db.Column(db.String(24), nullable=False)  # approved | revision_requested
    note = db.Column(db.Text)
    at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'status': self.status, 'note': self.note, 'at': iso(self.at)}


class ReviewExcludedUpload(db.Model):
    """Onay sayfasından ELLE kaldırılan yükleme (yönetim/tasarımcı, 2026-07-24).

    Müşteri kaldırılanı görmez; personel aynı sayfayı açtığında kaldırılmış olarak
    görür ve geri alabilir (yıkıcı değil — `card_uploads` satırına dokunulmaz).
    Kapsam yükleme bazlıdır: link iptal edilip yenisi üretilse de karar korunur."""
    __tablename__ = 'review_excluded_uploads'
    upload_id = db.Column(db.Integer, db.ForeignKey('card_uploads.id'), primary_key=True)
    excluded_by = db.Column(db.String(64))   # SSO sub
    excluded_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class PreApprovalLink(db.Model):
    """Ön-onay linki (2026-07-24): tasarımcı üretir, YÖNETİME gönderir.

    Müşteri onay linkinin (`ReviewLink`) iç muadili — aynı (client, week) kapsamı,
    ama sayfayı yalnız personel açabilir ve kararı yalnız yönetim verir. Token
    ayrı tablodadır; `/review/<token>` her iki token tipini de çözer."""
    __tablename__ = 'pre_approval_links'
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)


class UploadPreApproval(db.Model):
    """Yöneticinin ön-onay kararı (2026-07-24) — yükleme bazında.

    Müşteri kararından (`UploadReview`) AYRI tutulur: biri iç kapı, diğeri müşteri
    geri bildirimi. `upload_id` UNIQUE → yükleme başına tek/en güncel karar.

    **Kademeli kapı:** bir (müşteri, hafta) için EN AZ BİR ön-onay kararı varsa,
    müşteri onay linki yalnız `approved` olanları gösterir; hiç karar yoksa kapı
    devrede değildir (eski haftalar ve alışılmış akış kırılmaz) — bkz.
    `sharing.review_visible_uploads`."""
    __tablename__ = 'card_upload_pre_approvals'
    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('card_uploads.id'),
                          nullable=False, unique=True)
    status = db.Column(db.String(24), nullable=False)  # approved | revision_requested
    note = db.Column(db.Text)
    decided_by = db.Column(db.String(64))              # SSO sub (yönetici)
    at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'status': self.status, 'note': self.note, 'at': iso(self.at)}


class ClientApprovalLink(db.Model):
    """Müşteri onay linki — **elle seçilmiş** yükleme listesi (2026-08-06).

    `ReviewLink`'in ikizi DEĞİL, bilinçli olarak ayrı bir akış (proje sahibi kararı: eski
    `/review/<token>` akışına dokunulmadı). İki temel fark:

    1. **Kapsam (client_id, week_iso) değil, açık liste:** `upload_ids` linkin
       üretildiği anda dondurulur. Sonradan yüklenen dosya bu linke SIZMAZ —
       müşteriye "şunları gönderdim" diyen kişi ne gönderdiğini bilir. Bu yüzden
       hafta sınırı da yoktur: sharing board'daki hafta ± 1 hafta penceresinden
       seçim yapılır, üç haftanın dosyaları tek linkte buluşabilir.
    2. **Not defteri:** müşteri sayfanın sağındaki alana serbest not yazar
       (`note`), otomatik kaydedilir. `note_notified_at` ilk yazımda bildirim
       gönderildiğini işaretler — her tuş vuruşunda çan çalmasın diye.

    Onay/revize kararları AYRI tutulmaz: `card_upload_reviews`'a (UploadReview)
    yazılır → sharing board'daki ✅/📝 rozetleri ve mevcut `client_review`
    bildirimleri bu akışta da olduğu gibi çalışır.
    """
    __tablename__ = 'client_approval_links'
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    upload_ids = db.Column(JSONB_, nullable=False)   # int listesi, seçim sırasıyla
    note = db.Column(db.Text)
    note_at = db.Column(db.DateTime(timezone=True))
    note_notified_at = db.Column(db.DateTime(timezone=True))
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {'id': self.id, 'token': self.token, 'client_id': self.client_id,
                'upload_ids': list(self.upload_ids or []), 'note': self.note,
                'note_at': iso(self.note_at), 'revoked': self.revoked,
                'created_at': iso(self.created_at)}
