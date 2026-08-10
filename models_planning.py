"""Planlama Panosu şeması (2026-07-25) — KİŞİ eksenli serbest pano.

Eski `weekly_canvas` (models_sharing) hafta eksenliydi: `week_iso` UNIQUE → haftada
bir pano, tüm ajans için ortak, her pazartesi boş doğuyordu. Bu modül onun yerini
alır; eksen artık **pano sahibi**:

  * `management`   — tüm yöneticilerin paylaştığı TEK pano (çalışanlar erişemez)
  * `user:<sub>`   — kişi başına ORTAK çalışma alanı (yönetici + o çalışan yazar)

`weekly_canvas` **arşive alındı** — bu tablolar onun verisini göç script'iyle devraldı
(`scripts/migrate_planning_boards.py`), kaynak tablo salt-okunur olarak duruyor
(rollback yolu).

NEDEN JSONB DİZİ DEĞİL, SATIR-BAŞINA-ÖĞE: eski model tüm kartları tek JSONB kolonunda
tutuyordu; tek kartı sürüklemek 588 kartlık kolonun tamamını yeniden yazıyordu (~300 KB
WAL) ve iki kişi aynı anda yazınca biri sessizce kayboluyordu. Satır modeli delta
PATCH'in doğal karşılığıdır; ayrıca durum/son tarih/sorumlu yapısal veri olarak
sorgulanabilir hale gelir.
"""
# `text` sınıf gövdesinde kolon adı olarak kullanılıyor (PlanningItem.text) →
# sqlalchemy.text'i alias'la, yoksa sınıf scope'unda gölgelenir ve çağrılamaz.
from sqlalchemy import text as sa_text

from extensions import db
from models import iso, utcnow
from models_sharing import JSONB_

# Pano türleri ve öğe sözlükleri (uçtaki doğrulama bunlara bakar).
BOARD_KINDS = ('management', 'user')
# `image` (2026-07-28): panoya yapıştırılan/sürüklenen görsel. Dosya sunucuda
# (`planning_images`, pano başına dizin), öğe ona `extra.image = {name,w,h}` ile
# işaret eder — `link` kolonu değil, çünkü orada http(s) doğrulaması var ve bu
# bir dosya adı. `type` kolonu varchar(16), Postgres enum DEĞİL → yeni tür için
# ALTER gerekmedi.
ITEM_TYPES = ('card', 'note', 'region', 'edge', 'image')
ITEM_STATUSES = ('open', 'done')
ITEM_SOURCES = ('panel', 'legacy')


class PlanningBoard(db.Model):
    """Bir planlama panosu. Kimliği tek başına `board_key`'dir.

    NEDEN `UNIQUE(kind, owner_sub)` DEĞİL: Postgres'te NULL'lar birbirine eşit
    sayılmaz → `owner_sub IS NULL` olan yönetim panosundan sessizce BİRDEN FAZLA
    satır oluşabilir ve hangisinin okunduğu `first()` sırasına kalırdı. Tek gerçek
    kaynak `board_key` ('management' | 'user:<sub>'); `kind`/`owner_sub` yalnızca
    okuma/sorgulama kolaylığı için tutulan türev kolonlardır, kısıt taşımazlar.

    `version` her yazmada +1 artar. Bu bir KAPI DEĞİL, "biri yazdı" sinyalidir:
    panel ucuz `/version` ucunu yoklar ve gerekirse panoyu tazeler.

    Pano başlığı DB'de tutulmaz — uçta türetilir ('Yönetim Panosu' / UserRef.name),
    böylece kullanıcı adı değişince pano adı da kendiliğinden güncellenir."""
    __tablename__ = 'planning_boards'

    id = db.Column(db.Integer, primary_key=True)
    board_key = db.Column(db.String(64), nullable=False, unique=True)
    kind = db.Column(db.String(16), nullable=False, default='user')
    owner_sub = db.Column(db.String(64))          # kind='user' ise SSO sub; management'ta NULL
    version = db.Column(db.Integer, nullable=False, server_default=sa_text('1'), default=1)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_modified_by = db.Column(db.String(64))

    items = db.relationship('PlanningItem', backref='board', lazy='select',
                            cascade='all, delete-orphan')

    def to_dict(self, title=None, item_count=None, last_modified_name=None):
        return {'key': self.board_key, 'kind': self.kind, 'owner_sub': self.owner_sub,
                'title': title, 'version': self.version,
                'updated_at': iso(self.updated_at),
                'last_modified_by': self.last_modified_by,
                'last_modified_name': last_modified_name,
                'item_count': item_count}


class PlanningItem(db.Model):
    """Panodaki tek öğe: kart / not / bölge / bağlantı çizgisi.

    `item_key` istemci tarafından üretilir (eski `task_id`'nin karşılığı) ve pano
    içinde benzersizdir — delta PATCH'in adresleme birimi budur.

    SOFT-DELETE BİLEREK YOK: `UNIQUE(board_id, item_key)` ile `deleted_at` birlikte
    çalışmaz — silinmiş satır anahtarı işgal eder, geri-al (Ctrl+Z) aynı item_key'i
    yeniden yazınca IntegrityError verir. Çözümü kısmi index olurdu = Postgres/sqlite
    ayrışması. Aynı gerekçe `client_tracking_entries` için de geçerli (bkz.
    03-veri.md invaryantları). Silme HARD; geri alma satırı yeniden INSERT eder.

    `x`/`y` NOT NULL: eski JSONB kartlarının bir kısmında `position` alanı hiç yoktu
    (2025-W44 ve 2026-W13, 78 kart) ve panelde `left: undefined` ile 0,0'a yığılıyordu.
    Şema bunu kökten kapatır.

    `rev` öğe düzeyinde çakışma tespiti içindir: istemci bildiği rev'i gönderir,
    sunucudaki daha büyükse öğe `conflicts[]`'a girer (son yazan kazanır, panel uyarır).

    `legacy_ref` göç idempotans anahtarıdır ('weekly_canvas:<week_iso>:<task_id>');
    yalnız göç edilen satırlarda doludur, panelden eklenende NULL kalır."""
    __tablename__ = 'planning_items'
    __table_args__ = (
        db.UniqueConstraint('board_id', 'item_key', name='uq_planning_item_key'),
        db.Index('ix_planning_items_board', 'board_id'),
        # assignee indeksi cross-board `GET /assigned` sorgusu için: o uç board_id
        # süzgeci OLMADAN assignee_sub'a bakar, indekssiz seq scan olurdu.
        db.Index('ix_planning_items_assignee', 'assignee_sub'),
        db.Index('ix_planning_items_shoot', 'shoot_task_id'),
        db.Index('ix_planning_items_campaign', 'ad_campaign_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('planning_boards.id'), nullable=False)
    item_key = db.Column(db.String(64), nullable=False)

    type = db.Column(db.String(16), nullable=False, default='card')
    title = db.Column(db.String(300))
    text = db.Column(db.Text)                      # not gövdesi / kart açıklaması
    color = db.Column(db.String(16))               # #rrggbb
    label = db.Column(db.String(80))
    link = db.Column(db.String(1024))              # yalnız http(s) — doğrulama uçta

    x = db.Column(db.Integer, nullable=False, server_default=sa_text('0'), default=0)
    y = db.Column(db.Integer, nullable=False, server_default=sa_text('0'), default=0)
    width = db.Column(db.Integer)                  # NULL = tür varsayılanı (panel karar verir)
    height = db.Column(db.Integer)
    z = db.Column(db.Integer, nullable=False, server_default=sa_text('0'), default=0)

    from_key = db.Column(db.String(64))            # type='edge' kaynak
    to_key = db.Column(db.String(64))              # type='edge' hedef

    status = db.Column(db.String(16), nullable=False, default='open')   # open | done
    due_date = db.Column(db.Date)
    assignee_sub = db.Column(db.String(64))        # users_ref.sub (FK değil — kimlik SSO'da)

    # Domain bağları — kartı panelin geri kalanına bağlar. `extra` jsonb DEĞİL, gerçek
    # FK: doğrulanmış (silinmiş kampanyaya bakan ölü referans oluşmaz), indeksli ve
    # sorgulanabilir. `ondelete='SET NULL'` bilinçli — çekim/kampanya silinince kart
    # yaşamaya devam eder, yalnız bağ kopar (kart planlama verisi, onun kaydı değil).
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    shoot_task_id = db.Column(db.Integer, db.ForeignKey('shoot_tasks.id', ondelete='SET NULL'))
    ad_campaign_id = db.Column(db.Integer, db.ForeignKey('ad_campaigns.id', ondelete='SET NULL'))
    extra = db.Column(JSONB_)                      # göç artıkları (legacy_status/metadata/week_iso)

    source = db.Column(db.String(16), nullable=False, default='panel')  # panel | legacy
    legacy_ref = db.Column(db.String(96), unique=True)
    rev = db.Column(db.Integer, nullable=False, server_default=sa_text('1'), default=1)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))

    def to_dict(self, assignee_name=None, updated_by_name=None,
                client_name=None, shoot_title=None, campaign_title=None):
        """Türetilmiş adlar (…_name/…_title) DIŞARIDAN geçilir, ilişkiden okunmaz.

        Gerekçe: `self.client.name` yazmak öğe başına bir lazy sorgu doğurur ve
        `test_board_get_sorgu_sayisi_oge_sayisindan_bagimsiz` muhafızını kırar.
        Çağıran taraf (`planning._items_payload`) adları tek toplu sorguda çözer."""
        return {
            'item_key': self.item_key, 'type': self.type,
            'title': self.title, 'text': self.text, 'color': self.color,
            'label': self.label, 'link': self.link,
            'x': self.x, 'y': self.y, 'width': self.width, 'height': self.height,
            'z': self.z, 'from_key': self.from_key, 'to_key': self.to_key,
            'status': self.status,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'assignee_sub': self.assignee_sub, 'assignee_name': assignee_name,
            'client_id': self.client_id, 'client_name': client_name,
            'shoot_task_id': self.shoot_task_id, 'shoot_title': shoot_title,
            'ad_campaign_id': self.ad_campaign_id, 'campaign_title': campaign_title,
            'extra': self.extra or {},
            'rev': self.rev, 'updated_at': iso(self.updated_at),
            'updated_by': self.updated_by, 'updated_by_name': updated_by_name,
        }
