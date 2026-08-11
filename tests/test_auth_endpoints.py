"""auth.py — AUTH_MODE=local endpoints (/auth/local-login, /auth/change-password,
/auth/logout) + the /auth/login redirect target. The global test default is
already AUTH_MODE=local (see conftest.py)."""
import local_admin
from extensions import db
from models_auth import LocalUser


def _user(app, email='giris@test.com', password='ilk-parola', role='designer'):
    with app.app_context():
        out = local_admin.create_user({'email': email, 'role': role})
        u = db.session.get(LocalUser, out['id'])
        u.set_password(password)
        db.session.commit()
        return out['id']


def test_login_local_modda_panel_login_sayfasina_yonlendirir(client):
    r = client.get('/auth/login')
    assert r.status_code == 302
    assert r.headers['Location'].startswith('/panel/login')


def test_local_login_dogru_bilgiyle_oturum_acar(app, client):
    _user(app)
    r = client.post('/auth/local-login', json={'email': 'giris@test.com',
                                                'password': 'ilk-parola'})
    assert r.status_code == 200
    assert r.get_json()['ok'] is True
    s = client.get('/api/session')
    assert s.status_code == 200
    assert s.get_json()['user']['email'] == 'giris@test.com'


def test_local_login_yanlis_parola_401(app, client):
    _user(app)
    r = client.post('/auth/local-login', json={'email': 'giris@test.com',
                                                'password': 'yanlis'})
    assert r.status_code == 401
    assert client.get('/api/session').status_code == 401


def test_logout_oturumu_temizler(app, client):
    _user(app)
    client.post('/auth/local-login', json={'email': 'giris@test.com', 'password': 'ilk-parola'})
    assert client.get('/api/session').status_code == 200
    r = client.get('/auth/logout')
    assert r.status_code == 302
    assert client.get('/api/session').status_code == 401


def test_change_password_dogru_akis(app, client):
    _user(app)
    client.post('/auth/local-login', json={'email': 'giris@test.com', 'password': 'ilk-parola'})
    r = client.post('/auth/change-password',
                    json={'current_password': 'ilk-parola', 'new_password': 'yeni-parola-123'})
    assert r.status_code == 200
    # can no longer log in with the old password, can with the new one
    client.get('/auth/logout')
    assert client.post('/auth/local-login',
                       json={'email': 'giris@test.com', 'password': 'ilk-parola'}).status_code == 401
    assert client.post('/auth/local-login',
                       json={'email': 'giris@test.com',
                             'password': 'yeni-parola-123'}).status_code == 200


def test_change_password_yanlis_mevcut_parola_401(app, client):
    _user(app)
    client.post('/auth/local-login', json={'email': 'giris@test.com', 'password': 'ilk-parola'})
    r = client.post('/auth/change-password',
                    json={'current_password': 'yanlis', 'new_password': 'yeni-parola-123'})
    assert r.status_code == 401


def test_change_password_oturumsuz_401(client):
    r = client.post('/auth/change-password',
                    json={'current_password': 'x', 'new_password': 'yeni-parola-123'})
    assert r.status_code == 401


def test_change_password_kisa_parola_400(app, client):
    _user(app)
    client.post('/auth/local-login', json={'email': 'giris@test.com', 'password': 'ilk-parola'})
    r = client.post('/auth/change-password',
                    json={'current_password': 'ilk-parola', 'new_password': 'kisa'})
    assert r.status_code == 400


def test_callback_local_modda_404(client):
    """When AUTH_MODE=local, /auth/callback (OIDC-specific) is ignored."""
    assert client.get('/auth/callback?code=x').status_code == 404


def test_local_login_oidc_modda_404(client, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'AUTH_MODE', 'oidc')
    r = client.post('/auth/local-login', json={'email': 'x@test.com', 'password': 'y'})
    assert r.status_code == 404
