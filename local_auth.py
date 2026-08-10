"""Yerel giriş — kimlik doğrulama çekirdeği (AUTH_MODE=local).

`auth.py` POST /auth/local-login burayı çağırır. Kullanıcı yönetimi (oluşturma/
rol/parola sıfırlama) `local_admin.py`'de — burada yalnız giriş doğrulaması var.
"""
from extensions import db
from models import utcnow
from models_auth import LocalUser


def authenticate(email, password):
    """(email, password) doğruysa LocalUser döner (last_login_at güncellenir, commit
    edilir); yanlışsa/pasifse None.

    Zamanlama yan kanalını azaltmak için kullanıcı bulunamasa da bir hash
    karşılaştırması YAPILIR (sabit-zamana yakın); yine de bu basit bir panel
    girişi — üretimde daha güçlü bir rate-limit önündedir (bkz. ratelimit.py)."""
    email = (email or '').strip().lower()
    user = LocalUser.query.filter_by(email=email).first() if email else None
    dummy_hash = 'pbkdf2:sha256:600000$dummy$0'  # gerçek bir eşleşmesi imkansız sabit hash
    ok = (user or _Dummy(dummy_hash)).check_password(password or '')
    if user is None or not ok or user.status != 'active':
        return None
    user.last_login_at = utcnow()
    db.session.commit()
    return user


class _Dummy:
    """Kullanıcı yoksa da bir hash karşılaştırması yapılsın diye kukla nesne."""
    def __init__(self, h):
        self._h = h

    def check_password(self, raw):
        from werkzeug.security import check_password_hash
        return check_password_hash(self._h, raw)
