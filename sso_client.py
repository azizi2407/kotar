"""Kimlik istemcisi — iki mod (`AUTH_MODE` env, varsayılan `oidc`):

**oidc** — standart OpenID Connect Authorization Code akışı. Sağlayıcı
`OIDC_ISSUER`'dan (discovery: `{issuer}/.well-known/openid-configuration`) ya da
`OIDC_AUTHORIZATION_ENDPOINT`/`OIDC_TOKEN_ENDPOINT`/`OIDC_JWKS_URL` env'leriyle
elle yapılandırılır (Keycloak/Authentik/Auth0/Google Workspace vb. ile çalışır).
Rol claim'i standart değildir — `OIDC_ROLE_CLAIM` (varsayılan `role`) hangi custom
claim/attribute'un okunacağını belirler; claim yoksa `OIDC_DEFAULT_ROLE`
(varsayılan `pending`) atanır. Akış: `/auth/login` → sağlayıcı → Google/... →
`/auth/callback?code` → code'u token'a çevir (`exchange`) → `id_token`'ı JWKS ile
doğrula (`verify`) → claim'ler Flask session'a yazılır.

**local** — e-posta/parola girişi; kimlik dış sağlayıcıya delege edilmez (bkz.
`local_auth.py`, `models_auth.py`, `auth.py` POST /auth/local-login`). Bu modda
`OIDCClient` hiç kurulmaz.

Her iki modda da sonraki istekler yalnız imzalı Flask session cookie'sine güvenir
— ne agency'de ne sağlayıcıda sunucu tarafı oturum tablosu vardır.
"""
import functools
import os
from urllib.parse import urlencode

import jwt
import requests
from flask import abort, current_app, redirect, request, session, url_for

AUTH_MODE = os.getenv('AUTH_MODE', 'oidc').strip().lower()


class OIDCClient:
    def __init__(self, issuer, client_id, client_secret, *,
                 authorization_endpoint=None, token_endpoint=None, jwks_url=None,
                 scope='openid email profile', role_claim='role', default_role='pending'):
        self.issuer = issuer.rstrip('/')
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.role_claim = role_claim
        self.default_role = default_role
        disc = None
        if not (authorization_endpoint and token_endpoint and jwks_url):
            disc = self._discover()
        self.authorization_endpoint = authorization_endpoint or disc['authorization_endpoint']
        self.token_endpoint = token_endpoint or disc['token_endpoint']
        self._jwks = jwt.PyJWKClient(jwks_url or disc['jwks_uri'])  # public key'i çeker + cache'ler

    def _discover(self):
        r = requests.get(f'{self.issuer}/.well-known/openid-configuration', timeout=10)
        r.raise_for_status()
        return r.json()

    def login_url(self, redirect_uri, state):
        params = {'response_type': 'code', 'client_id': self.client_id,
                  'redirect_uri': redirect_uri, 'scope': self.scope, 'state': state}
        return f'{self.authorization_endpoint}?' + urlencode(params)

    def exchange(self, code, redirect_uri):
        r = requests.post(self.token_endpoint, data={
            'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': redirect_uri, 'client_id': self.client_id,
            'client_secret': self.client_secret,
        }, timeout=10)
        r.raise_for_status()
        return r.json()  # {access_token, id_token, token_type, expires_in, ...}

    def verify(self, id_token):
        key = self._jwks.get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, key, algorithms=['RS256', 'ES256'],
                            audience=self.client_id, issuer=self.issuer)
        role = claims.get(self.role_claim) or self.default_role
        return {'sub': claims['sub'], 'email': claims.get('email'),
                'name': claims.get('name'), 'role': role}


def oidc():
    return current_app.extensions['oidc']


def current_user():
    # Impersonation altında session['user'] hedef kullanıcıdır (etkin kimlik);
    # tüm rol kontrolleri ve created_by/updated_by bunu okur.
    return session.get('user')


def real_user():
    """GERÇEK (giriş yapan) kimlik: impersonation sırasında impersonator, yoksa mevcut."""
    return session.get('impersonator') or session.get('user')


def is_superadmin(u=None):
    """u (verilmezse gerçek kimlik) impersonation başlatma yetkisine sahip mi?
    Yetki HER ZAMAN gerçek kimlik üzerinden — impersonate edilen kullanıcı yükseltemez."""
    u = u or real_user()
    if not u:
        return False
    emails = current_app.config.get('SUPERADMIN_EMAILS') or set()
    return (u.get('email') or '').lower() in emails


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            session['next'] = request.url
            return redirect(url_for('auth.login'))
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """İlgili rollerden biri VEYA management (her şeye erişir)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            u = session.get('user')
            if not u:
                session['next'] = request.url
                return redirect(url_for('auth.login'))
            if u.get('role') != 'management' and u.get('role') not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
