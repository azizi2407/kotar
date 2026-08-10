"""Font havuzu (2026-08-05) — merkezî font deposu + müşteri ataması.

**Neden `client_assets`'e `kind='font'` eklenmedi:** o tablo `client_id` NOT NULL
ve tek müşteriye bağlı; burada model N:N (Montserrat beş müşteride kullanılıyor,
dosya bir kez duruyor) ve müşterisiz font da geçerli (havuzda deneme fontu).
Ayrıca marka görselleri Drive'da yaşıyor, fontlar **sunucuda** (`data/fonts/`):
önizleme her fontu tarayıcıya indiriyor, Drive proxy'si sayfayı yavaşlatırdı.
"""
from extensions import db
from models import iso, utcnow

# Tarayıcının @font-face ile oynatabildiği formatlar. Uzantı DEĞİL, dosya imzası
# belirler (bkz. fonts.py `_format_of`) — bu dosyalar tarayıcıya inline servis
# ediliyor, "uzantısı .ttf olan her şey" kabul edilemez.
FONT_FORMATS = ('ttf', 'otf', 'woff', 'woff2')
FONT_MIMES = {'ttf': 'font/ttf', 'otf': 'font/otf',
              'woff': 'font/woff', 'woff2': 'font/woff2'}


class Font(db.Model):
    """Havuzdaki tek font DOSYASI (aile değil): 'Montserrat Bold' bir satır,
    'Montserrat Regular' ayrı satır. Panel aileye göre gruplar.

    `sha256` içerik hash'i ve dosya adı: `data/fonts/<sha256>.<ext>`. Aynı dosya
    ikinci kez yüklenirse diske ikinci kopya çıkmaz."""
    __tablename__ = 'fonts'
    __table_args__ = (db.Index('ix_fonts_sha', 'sha256'),
                      db.Index('ix_fonts_family', 'family'))

    id = db.Column(db.Integer, primary_key=True)
    family = db.Column(db.String(160), nullable=False)
    style = db.Column(db.String(64), nullable=False, default='Regular')
    file_name = db.Column(db.String(255), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    format = db.Column(db.String(8), nullable=False)      # ttf | otf | woff | woff2
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    uploaded_by = db.Column(db.String(64))                # SSO sub
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self, clients=None, uploader_name=None, can_delete=None):
        # `can_delete` BACKEND'te hesaplanır (2026-08-06) — panel kuralı yeniden
        # kurmaz. Kural değişince arayüz sessizce ayrışır ve düğme yalan söylerdi;
        # video yüklemelerinde (`_build_rows`) aynı desen kullanılıyor.
        return {'id': self.id, 'family': self.family, 'style': self.style,
                'file_name': self.file_name, 'format': self.format,
                'file_size': self.file_size, 'sha256': self.sha256,
                'uploaded_by': self.uploaded_by, 'uploader_name': uploader_name,
                'uploaded_at': iso(self.uploaded_at),
                'can_delete': bool(can_delete),
                'clients': clients if clients is not None else []}


class FontClient(db.Model):
    """Font ↔ müşteri ataması. Satırın VARLIĞI = atanmış; kaldırma satırı siler
    (soft-delete yok — UNIQUE slotu işgal ederdi, `user_hidden_clients` deseni)."""
    __tablename__ = 'font_clients'
    __table_args__ = (db.UniqueConstraint('font_id', 'client_id', name='uq_font_client'),
                      db.Index('ix_font_clients_client', 'client_id'))

    id = db.Column(db.Integer, primary_key=True)
    font_id = db.Column(db.Integer, db.ForeignKey('fonts.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    assigned_by = db.Column(db.String(64))
    assigned_at = db.Column(db.DateTime(timezone=True), default=utcnow)
