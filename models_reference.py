"""Müşteri örnek (referans) hesapları — 2026-08-07.

Tasarımcı/videograf "bu müşteri için nasıl içerik üretilir" sorusuna bakacağı
örnekler: aynı sektörde, aynı işi yapan Instagram hesapları.

**Onay kapısı var (proje sahibi kararı):** hesaplar `candidate` doğar, yönetim tek tek
inceleyip `approved`/`rejected` işaretler. Marka rehberinde üretim rollerine
YALNIZ `approved` olanlar görünür. Bu, projedeki AI içerik kapısıyla aynı desen:
otomatik derlenen bir liste, insan onayından geçmeden ekibe sunulmaz — yanlış
sektörden ya da ölü bir hesap "ajansın önerisi" gibi görünürdü.
"""
from extensions import db
from models import iso, utcnow

REFERENCE_STATUSES = ('candidate', 'approved', 'rejected')


class ClientReferenceAccount(db.Model):
    """Bir müşteri için örnek alınabilecek Instagram hesabı.

    `handle` müşteri içinde UNIQUE: aynı hesabı iki kez aday göstermek listeyi
    kirletir. Farklı müşterilerde aynı handle serbest — bir hesap birden çok
    müşteriye örnek olabilir (ör. iki inşaat firması).
    """
    __tablename__ = 'client_reference_accounts'
    __table_args__ = (
        db.UniqueConstraint('client_id', 'handle', name='uq_client_reference_handle'),
        db.Index('ix_reference_client_status', 'client_id', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    handle = db.Column(db.String(64), nullable=False)      # '@' YOK, düz kullanıcı adı
    title = db.Column(db.String(200))                      # görünen ad / işletme adı
    note = db.Column(db.Text)                              # neden örnek: ne iyi yapıyor
    followers = db.Column(db.Integer)                      # eklendiği andaki kaba sayı
    # Kaynak izi: elle mi eklendi yoksa araştırmayla mı derlendi. Yönetim
    # incelerken "bunu ben mi eklemiştim" sorusunun cevabı.
    source = db.Column(db.String(16), nullable=False, default='manual')   # manual | research
    status = db.Column(db.String(16), nullable=False, default='candidate')
    added_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    decided_by = db.Column(db.String(64))
    decided_at = db.Column(db.DateTime(timezone=True))

    @property
    def url(self):
        return f'https://www.instagram.com/{self.handle}/'

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'handle': self.handle,
                'title': self.title, 'note': self.note, 'followers': self.followers,
                'url': self.url, 'source': self.source, 'status': self.status,
                'added_by': self.added_by, 'created_at': iso(self.created_at),
                'decided_by': self.decided_by, 'decided_at': iso(self.decided_at)}
