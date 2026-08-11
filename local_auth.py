"""Local login — the authentication core (AUTH_MODE=local).

`auth.py` POST /auth/local-login calls into here. User management (creation/
role/password reset) lives in `local_admin.py` — only login verification is here.
"""
from extensions import db
from models import utcnow
from models_auth import LocalUser


def authenticate(email, password):
    """Returns a LocalUser if (email, password) is correct (last_login_at is
    updated, committed); None if wrong/inactive.

    To reduce the timing side channel, a hash comparison IS PERFORMED even if the
    user isn't found (close to constant-time); still, this is a simple panel
    login — in production it sits behind a stronger rate limit (see ratelimit.py)."""
    email = (email or '').strip().lower()
    user = LocalUser.query.filter_by(email=email).first() if email else None
    dummy_hash = 'pbkdf2:sha256:600000$dummy$0'  # a fixed hash that can never actually match
    ok = (user or _Dummy(dummy_hash)).check_password(password or '')
    if user is None or not ok or user.status != 'active':
        return None
    user.last_login_at = utcnow()
    db.session.commit()
    return user


class _Dummy:
    """A dummy object so a hash comparison still happens even without a user."""
    def __init__(self, h):
        self._h = h

    def check_password(self, raw):
        from werkzeug.security import check_password_hash
        return check_password_hash(self._h, raw)
