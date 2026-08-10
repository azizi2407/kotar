"""OIDC sağlayıcı admin API istemcisi — kullanıcı yönetimi proxy'si (AUTH_MODE=oidc).

Kimlik dış sağlayıcıya ait; agency DB'sine yazmaz. `SSO_ADMIN_TOKEN` (secret
manager) ile `SSO_BASE_URL`'in `/admin/users` (GET/POST) ve `/admin/users/<id>`
(PATCH) uçlarını çağırır — bu üç uç sözleşmesi kendi IdP'nizde yoksa
`available()` False döner ve uçlar 503 verir; o durumda kullanıcı/rol yönetimini
doğrudan IdP konsolunuzdan yapmanız beklenir. Uçlar `admin_api.py`'de superadmin
kapısıyla sarılır."""
import os

import requests
from flask import current_app


class SsoAdminError(Exception):
    """sso admin API hatası — status uca aynen yansıtılır."""
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
        raise SsoAdminError('kullanıcı yönetimi yapılandırılmamış (SSO_ADMIN_TOKEN yok)', 503)
    try:
        r = requests.request(method, f'{_base()}/admin{path}',
                             headers={'Authorization': f'Bearer {_token()}'},
                             json=json, timeout=10)
    except requests.RequestException as e:
        raise SsoAdminError(f'sso erişilemedi: {e}', 502)
    if r.status_code >= 400:
        try:
            msg = (r.json() or {}).get('error') or r.text
        except ValueError:
            msg = r.text
        raise SsoAdminError(msg or f'sso hatası ({r.status_code})', r.status_code)
    return r.json() if r.content else {}


def list_users():
    return _call('GET', '/users').get('users', [])


def create_user(data):
    return _call('POST', '/users', json=data).get('user')


def update_user(user_id, data):
    return _call('PATCH', f'/users/{user_id}', json=data).get('user')
