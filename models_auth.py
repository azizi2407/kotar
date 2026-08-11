"""Local authentication (AUTH_MODE=local) — email/password login.

When AUTH_MODE=oidc, this table isn't used (identity is delegated to an
external OIDC provider); the table still gets created via `create_all` but
stays empty. Passwords are hashed with werkzeug.security (pbkdf2:sha256) —
a plaintext password is never stored in the repo/DB. The `role` field shares
the same contract as `UserRef.role` (management/designer/videographer/
content_creator/pending).
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
                'linked': True,  # local account: can log in with a password from the moment it's created
                'last_login': iso(self.last_login_at), 'created_at': iso(self.created_at)}

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)


def generate_temp_password():
    """Temporary password generated when an admin creates a new user /
    resets a password — returned only once, in that API response; only its
    hash is written to the DB."""
    return secrets.token_urlsafe(9)
