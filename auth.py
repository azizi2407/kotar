"""Agency auth uçları.

`AUTH_MODE=oidc` (varsayılan): kimlik dış OIDC sağlayıcısına delege edilir.
  `/auth/login` → sağlayıcı yetkilendirme uç noktası → Google/... →
  `/auth/callback?code` → code'u token'a çevir → `id_token`'ı JWKS ile doğrula →
  claim'ler Flask session'a yazılır.
`AUTH_MODE=local`: `/auth/login` panelin kendi `/login` sayfasına (SPA) yönlendirir;
  bu sayfa POST `/auth/local-login` ile e-posta/parola gönderir (bkz. `local_auth.py`).

Her iki modda da sonraki istekler yalnız imzalı Flask session cookie'sine güvenir.
"""
import secrets

from flask import Blueprint, abort, current_app, jsonify, redirect, request, session, url_for

from extensions import db
from models import upsert_user_ref
from sso_client import AUTH_MODE, oidc

bp = Blueprint('auth', __name__)


@bp.get('/login')
def login():
    if AUTH_MODE == 'local':
        nxt = session.get('next')
        return redirect('/panel/login' + ('?next=' + nxt if nxt else ''))
    return_url = current_app.config['AGENCY_BASE_URL'].rstrip('/') + url_for('auth.callback')
    state = secrets.token_urlsafe(24)
    session['oauth_state'] = state
    return redirect(oidc().login_url(return_url, state))


@bp.get('/callback')
def callback():
    if AUTH_MODE != 'oidc':
        abort(404)
    code = request.args.get('code')
    if not code:
        abort(400, 'code parametresi yok')
    state = request.args.get('state')
    if not state or state != session.pop('oauth_state', None):
        abort(400, 'state doğrulanamadı')
    return_url = current_app.config['AGENCY_BASE_URL'].rstrip('/') + url_for('auth.callback')
    data = oidc().exchange(code, return_url)
    claims = oidc().verify(data['id_token'])
    _start_session(claims)
    nxt = session.pop('next', None) or '/panel/'
    return redirect(nxt)


@bp.post('/local-login')
def local_login():
    """AUTH_MODE=local: e-posta/parola girişi. Panelin /login sayfası bunu fetch ile
    çağırır — `/api/*`'nin CSRF korumasına tabi DEĞİL (oturum burada henüz kurulmadı,
    CSRF token'ı zaten olamaz — OIDC `/callback` ile aynı gerekçe)."""
    if AUTH_MODE != 'local':
        abort(404)
    data = request.get_json(silent=True) or {}
    import local_auth
    user = local_auth.authenticate(data.get('email'), data.get('password'))
    if user is None:
        return jsonify(error='e-posta veya parola hatalı'), 401
    _start_session({'sub': f'local:{user.id}', 'email': user.email,
                    'name': user.name, 'role': user.role})
    return jsonify(ok=True)


@bp.post('/change-password')
def change_password():
    """AUTH_MODE=local: oturum sahibi kendi parolasını değiştirir (mevcut parola + yeni)."""
    if AUTH_MODE != 'local':
        abort(404)
    u = session.get('user')
    if not u or not str(u.get('sub', '')).startswith('local:'):
        return jsonify(error='oturum yok'), 401
    data = request.get_json(silent=True) or {}
    current_pw, new_pw = data.get('current_password'), data.get('new_password')
    if not new_pw or len(new_pw) < 8:
        return jsonify(error='yeni parola en az 8 karakter olmalı'), 400
    from models_auth import LocalUser
    user_id = int(u['sub'].split(':', 1)[1])
    row = db.session.get(LocalUser, user_id)
    if row is None or not row.check_password(current_pw or ''):
        return jsonify(error='mevcut parola hatalı'), 401
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
