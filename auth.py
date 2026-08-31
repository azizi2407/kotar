"""Agency auth endpoints.

`AUTH_MODE=oidc` (default): identity is delegated to an external OIDC provider.
  `/auth/login` → provider's authorization endpoint → Google/... →
  `/auth/callback?code` → exchange the code for a token → verify `id_token` with JWKS →
  claims are written to the Flask session.
`AUTH_MODE=local`: `/auth/login` redirects to the panel's own `/login` page (SPA);
  that page POSTs email/password to `/auth/local-login` (see `local_auth.py`).

In both modes, subsequent requests trust only the signed Flask session cookie.
"""
import logging
import secrets

from flask import Blueprint, abort, current_app, jsonify, redirect, request, session, url_for

from extensions import db
from models import upsert_user_ref
from sso_client import AUTH_MODE, OIDCDiscoveryError, oidc

bp = Blueprint('auth', __name__)
log = logging.getLogger(__name__)

# Discovery runs lazily on the first login (see sso_client.OIDCClient), so an
# unreachable IdP surfaces HERE as a 503 instead of killing app startup. The
# next attempt retries discovery — a recovered IdP heals without a restart.
_IDP_DOWN = ('The identity provider could not be reached. '
             'Please try again in a moment.')


@bp.get('/login')
def login():
    if AUTH_MODE == 'local':
        nxt = session.get('next')
        return redirect('/panel/login' + ('?next=' + nxt if nxt else ''))
    return_url = current_app.config['AGENCY_BASE_URL'].rstrip('/') + url_for('auth.callback')
    state = secrets.token_urlsafe(24)
    session['oauth_state'] = state
    try:
        target = oidc().login_url(return_url, state)
    except OIDCDiscoveryError:
        log.exception('OIDC discovery failed during login')
        return _IDP_DOWN, 503
    return redirect(target)


@bp.get('/callback')
def callback():
    if AUTH_MODE != 'oidc':
        abort(404)
    code = request.args.get('code')
    if not code:
        abort(400, 'missing code parameter')
    state = request.args.get('state')
    if not state or state != session.pop('oauth_state', None):
        abort(400, 'state could not be verified')
    return_url = current_app.config['AGENCY_BASE_URL'].rstrip('/') + url_for('auth.callback')
    try:
        data = oidc().exchange(code, return_url)
        claims = oidc().verify(data['id_token'])
    except OIDCDiscoveryError:
        log.exception('OIDC discovery failed during callback')
        return _IDP_DOWN, 503
    _start_session(claims)
    nxt = session.pop('next', None) or '/panel/'
    return redirect(nxt)


@bp.post('/local-login')
def local_login():
    """AUTH_MODE=local: email/password login. The panel's /login page calls this via
    fetch — this is NOT subject to `/api/*`'s CSRF protection (the session isn't
    established yet here, so there can't be a CSRF token anyway — same rationale as OIDC `/callback`)."""
    if AUTH_MODE != 'local':
        abort(404)
    data = request.get_json(silent=True) or {}
    import local_auth
    user = local_auth.authenticate(data.get('email'), data.get('password'))
    if user is None:
        return jsonify(error='incorrect email or password'), 401
    _start_session({'sub': f'local:{user.id}', 'email': user.email,
                    'name': user.name, 'role': user.role})
    return jsonify(ok=True)


@bp.post('/change-password')
def change_password():
    """AUTH_MODE=local: the session owner changes their own password (current password + new)."""
    if AUTH_MODE != 'local':
        abort(404)
    u = session.get('user')
    if not u or not str(u.get('sub', '')).startswith('local:'):
        return jsonify(error='no active session'), 401
    data = request.get_json(silent=True) or {}
    current_pw, new_pw = data.get('current_password'), data.get('new_password')
    if not new_pw or len(new_pw) < 8:
        return jsonify(error='new password must be at least 8 characters'), 400
    from models_auth import LocalUser
    user_id = int(u['sub'].split(':', 1)[1])
    row = db.session.get(LocalUser, user_id)
    if row is None or not row.check_password(current_pw or ''):
        return jsonify(error='current password is incorrect'), 401
    row.set_password(new_pw)
    db.session.commit()
    return jsonify(ok=True)


def _start_session(claims):
    session['user'] = {
        'sub': claims['sub'], 'email': claims['email'],
        'name': claims.get('name'), 'role': claims.get('role', 'pending'),
    }
    session['csrf'] = secrets.token_urlsafe(32)
    session.permanent = True
    upsert_user_ref(session['user'])
    db.session.commit()


@bp.get('/logout')
def logout():
    session.clear()
    return redirect('/panel/')
