"""/api/admin/* — kullanıcı yönetimi. YALNIZ superadmin (is_superadmin,
impersonation-korumalı) + CSRF.

`AUTH_MODE=oidc` iken dış sağlayıcının admin API'sini proxy'ler (`sso_admin.py`
— sağlayıcıya özgüdür, `list_users`/`create_user`/`update_user` sözleşmesine
uyan bir uç noktanız yoksa `SSO_ADMIN_TOKEN` boş kalır ve bu uçlar 503 döner;
o modda kullanıcı/rol yönetimini kendi IdP konsolunuzdan yapmanız beklenir).
`AUTH_MODE=local` iken doğrudan yerel kullanıcı tablosunu yönetir (`local_admin.py`).
"""
import os

from flask import Blueprint, jsonify, request

from api import csrf_protect
from sso_client import current_user, is_superadmin

bp = Blueprint('admin_api', __name__)
bp.before_request(csrf_protect)  # api ile aynı CSRF


def _backend():
    """AUTH_MODE'u ÇAĞRI ANINDA env'den okur (sso_client.AUTH_MODE gibi import-anı
    sabiti DEĞİL) — testler iki yolu da (`monkeypatch.setenv`) tek prod app'te
    kapsayabilsin diye bilinçli bir istisna."""
    if os.getenv('AUTH_MODE', 'oidc').strip().lower() == 'local':
        import local_admin
        return local_admin, local_admin.LocalAdminError
    import sso_admin
    return sso_admin, sso_admin.SsoAdminError


@bp.before_request
def _gate():
    if not current_user():
        return jsonify(error='oturum yok'), 401
    if not is_superadmin():
        return jsonify(error='yalnız superadmin kullanıcı yönetebilir'), 403


@bp.get('/users')
def users():
    backend, _ = _backend()
    return jsonify(users=backend.list_users())


@bp.post('/users')
def create_user():
    backend, Err = _backend()
    try:
        user = backend.create_user(request.get_json(silent=True) or {})
    except Err as e:
        return jsonify(error=str(e)), e.status
    return jsonify(user=user), 201


@bp.patch('/users/<int:user_id>')
def update_user(user_id):
    backend, Err = _backend()
    try:
        user = backend.update_user(user_id, request.get_json(silent=True) or {})
    except Err as e:
        return jsonify(error=str(e)), e.status
    return jsonify(user=user)
