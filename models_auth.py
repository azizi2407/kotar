"""Yerel kimlik doğrulama (AUTH_MODE=local) — e-posta/parola ile giriş.

AUTH_MODE=oidc iken bu tablo kullanılmaz (kimlik dış OIDC sağlayıcısına delege
edilir); tablo yine de `create_all` ile açılır, boş kalır. Parola werkzeug.security
ile hash'lenir (pbkdf2:sha256) — repoda/DB'de düz metin parola hiçbir zaman
saklanmaz. `role` alanı `UserRef.role` ile aynı sözleşmeyi paylaşır (management/
designer/videographer/content_creator/pending).
"""
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import utcnow


class LocalUser(db.Model):
    __tablename__ = 'local_users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    role = db.Column(db.String(32), nullable=False, default='pending')
    password_hash = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='active')  # active | disabled
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        from models import iso
        return {'id': self.id, 'email': self.email, 'name': self.name,
                'role': self.role, 'status': self.status,
                'linked': True,  # yerel hesap: oluşturulduğu andan itibaren parolayla girilebilir
                'last_login': iso(self.last_login_at), 'created_at': iso(self.created_at)}

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)


def generate_temp_password():
    """Yönetici yeni kullanıcı açtığında/parola sıfırladığında üretilen geçici
    parola — yalnız o API yanıtında bir kez döner, DB'ye hash'i yazılır."""
    return secrets.token_urlsafe(9)
