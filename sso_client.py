"""Identity client — two modes (`AUTH_MODE` env, default `oidc`):

**oidc** — standard OpenID Connect Authorization Code flow. The provider is
configured either via discovery from `OIDC_ISSUER`
(`{issuer}/.well-known/openid-configuration`) or manually via the
`OIDC_AUTHORIZATION_ENDPOINT`/`OIDC_TOKEN_ENDPOINT`/`OIDC_JWKS_URL` env vars
(works with Keycloak/Authentik/Auth0/Google Workspace etc.). The role claim
isn't standard — `OIDC_ROLE_CLAIM` (default `role`) determines which custom
claim/attribute is read; if the claim is missing, `OIDC_DEFAULT_ROLE` (default
`pending`) is assigned. Flow: `/auth/login` → provider → Google/... →
`/auth/callback?code` → exchange the code for a token (`exchange`) → verify the
`id_token` with JWKS (`verify`) → claims are written to the Flask session.

**local** — email/password login; identity isn't delegated to an external
provider (see `local_auth.py`, `models_auth.py`, `auth.py` POST
/auth/local-login`). `OIDCClient` is never set up in this mode.

In both modes, subsequent requests rely only on the signed Flask session cookie
— neither the agency nor the provider has a server-side session table.
"""
import functools
import os
from urllib.parse import urlencode

import jwt
import requests
from flask import abort, current_app, redirect, request, session, url_for

AUTH_MODE = os.getenv('AUTH_MODE', 'oidc').strip().lower()


class OIDCDiscoveryError(RuntimeError):
    """The issuer's discovery document could not be fetched or was invalid.

    Raised from the login/callback paths, NOT at startup — see OIDCClient."""


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
        # Discovery is deliberately LAZY: it runs on the first login attempt,
        # not here. This constructor runs inside create_app(), and a network
        # call there means a transient IdP outage at (re)start takes down the
        # WHOLE app — including public, no-auth pages (/review/<token>, /m/…).
        # With all three endpoints set manually, no network is ever needed.
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        # PyJWKClient fetches + caches the signing keys on first use (lazy).
        self._jwks = jwt.PyJWKClient(jwks_url) if jwks_url else None

    def _discover(self):
        r = requests.get(f'{self.issuer}/.well-known/openid-configuration', timeout=10)
        r.raise_for_status()
        return r.json()

    def _ensure_endpoints(self):
        """Fills the endpoints from discovery on first need; no-op once known.

        Failures raise OIDCDiscoveryError instead of a raw requests exception so
        callers (auth.py) can turn them into a clear 503 — a later attempt
        retries discovery, so a recovered IdP heals without a restart."""
        if self.authorization_endpoint and self.token_endpoint and self._jwks:
            return
        try:
            disc = self._discover()
            self.authorization_endpoint = (self.authorization_endpoint
                                           or disc['authorization_endpoint'])
            self.token_endpoint = self.token_endpoint or disc['token_endpoint']
            if self._jwks is None:
                self._jwks = jwt.PyJWKClient(disc['jwks_uri'])
        except (requests.RequestException, ValueError, KeyError) as e:
            raise OIDCDiscoveryError(
                f'OIDC discovery failed for {self.issuer}: {e}') from e

    def login_url(self, redirect_uri, state):
        self._ensure_endpoints()
        params = {'response_type': 'code', 'client_id': self.client_id,
                  'redirect_uri': redirect_uri, 'scope': self.scope, 'state': state}
        return f'{self.authorization_endpoint}?' + urlencode(params)

    def exchange(self, code, redirect_uri):
        self._ensure_endpoints()
        r = requests.post(self.token_endpoint, data={
            'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': redirect_uri, 'client_id': self.client_id,
            'client_secret': self.client_secret,
        }, timeout=10)
        r.raise_for_status()
        return r.json()  # {access_token, id_token, token_type, expires_in, ...}

    def verify(self, id_token):
        self._ensure_endpoints()
        key = self._jwks.get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, key, algorithms=['RS256', 'ES256'],
                            audience=self.client_id, issuer=self.issuer)
        role = claims.get(self.role_claim) or self.default_role
        return {'sub': claims['sub'], 'email': claims.get('email'),
                'name': claims.get('name'), 'role': role}


def oidc():
    return current_app.extensions['oidc']


def current_user():
    # Under impersonation, session['user'] is the target user (the effective
    # identity); all role checks and created_by/updated_by read this.
    return session.get('user')


def real_user():
    """The REAL (logged-in) identity: the impersonator during impersonation, else the current one."""
    return session.get('impersonator') or session.get('user')


def is_superadmin(u=None):
    """Does u (the real identity if not given) have permission to start impersonation?
    The check is ALWAYS via the real identity — an impersonated user can't escalate."""
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
    """One of the given roles OR management (has access to everything)."""
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
