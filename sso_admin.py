"""OIDC provider admin API client — user management proxy (AUTH_MODE=oidc).

Identity belongs to the external provider; this doesn't write to the agency DB. Calls
`SSO_BASE_URL`'s `/admin/users` (GET/POST) and `/admin/users/<id>` (PATCH) endpoints
using `SSO_ADMIN_TOKEN` (secret manager) — if your own IdP doesn't implement this
three-endpoint contract, `available()` returns False and the endpoints return 503;
in that case you're expected to manage users/roles directly from your IdP console.
Endpoints are wrapped with the superadmin gate in `admin_api.py`."""
import os

import requests
from flask import current_app


class SsoAdminError(Exception):
    """sso admin API error — status is passed through to the endpoint as-is."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _base():
    return (os.environ.get('SSO_BASE_URL')
            or current_app.config.get('SSO_BASE_URL') or '').rstrip('/')


def _token():
    return (os.environ.get('SSO_ADMIN_TOKEN')
            or current_app.config.get('SSO_ADMIN_TOKEN') or '').strip()


def available():
    return bool(_base() and _token())


def _call(method, path, json=None):
    if not available():
        raise SsoAdminError('user management is not configured (SSO_ADMIN_TOKEN missing)', 503)
    try:
        r = requests.request(method, f'{_base()}/admin{path}',
                             headers={'Authorization': f'Bearer {_token()}'},
                             json=json, timeout=10)
    except requests.RequestException as e:
        raise SsoAdminError(f'sso unreachable: {e}', 502)
    if r.status_code >= 400:
        try:
            msg = (r.json() or {}).get('error') or r.text
        except ValueError:
            msg = r.text
        raise SsoAdminError(msg or f'sso error ({r.status_code})', r.status_code)
    return r.json() if r.content else {}


def list_users():
    return _call('GET', '/users').get('users', [])


def create_user(data):
    return _call('POST', '/users', json=data).get('user')


def update_user(user_id, data):
    return _call('PATCH', f'/users/{user_id}', json=data).get('user')
