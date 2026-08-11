"""/api/admin/* — user management. ONLY superadmin (is_superadmin,
impersonation-protected) + CSRF.

When `AUTH_MODE=oidc`, proxies the external provider's admin API (`sso_admin.py`
— provider-specific; if you don't have an endpoint matching the
`list_users`/`create_user`/`update_user` contract, `SSO_ADMIN_TOKEN` stays empty
and these endpoints return 503; in that mode user/role management is expected
to happen from your own IdP console). When `AUTH_MODE=local`, manages the local
user table directly (`local_admin.py`).
"""
import os

from flask import Blueprint, jsonify, request

from api import csrf_protect
from sso_client import current_user, is_superadmin

bp = Blueprint('admin_api', __name__)
bp.before_request(csrf_protect)  # same CSRF as api


def _backend():
    """Reads AUTH_MODE from env AT CALL TIME (NOT an import-time constant like
    sso_client.AUTH_MODE) — a deliberate exception so tests can cover both paths
    (`monkeypatch.setenv`) in a single prod app."""
    if os.getenv('AUTH_MODE', 'oidc').strip().lower() == 'local':
        import local_admin
        return local_admin, local_admin.LocalAdminError
    import sso_admin
    return sso_admin, sso_admin.SsoAdminError


@bp.before_request
def _gate():
    if not current_user():
        return jsonify(error='no active session'), 401
    if not is_superadmin():
        return jsonify(error='only a superadmin can manage users'), 403


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
