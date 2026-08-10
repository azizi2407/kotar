"""/api/admin/* — superadmin kapısı + sso_admin proxy (AUTH_MODE=oidc, HTTP mock'lu).

Global test varsayılanı AUTH_MODE=local (bkz. conftest.py); bu dosya AUTH_MODE=oidc
yolunu (admin_api._backend() → sso_admin) `monkeypatch.setenv` ile ayrıca kapsar.
Yerel (AUTH_MODE=local) admin akışı için bkz. test_local_admin.py."""
import pytest
import sso_admin
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers

SUPERADMIN = {'sub': '9', 'email': 'superadmin@example.com', 'name': 'Superadmin', 'role': 'management'}


@pytest.fixture(autouse=True)
def _oidc_mode(monkeypatch):
    monkeypatch.setenv('AUTH_MODE', 'oidc')


def test_list_requires_superadmin(client, monkeypatch):
    monkeypatch.setattr(sso_admin, 'list_users', lambda: [{'id': 1, 'email': 'x@y.com'}])
    login_as(client, MANAGER)  # management ama superadmin değil
    assert client.get('/api/admin/users').status_code == 403


def test_list_as_superadmin(client, monkeypatch):
    monkeypatch.setattr(sso_admin, 'list_users',
                        lambda: [{'id': 1, 'email': 'a@example.com', 'role': 'designer'}])
    login_as(client, SUPERADMIN)
    r = client.get('/api/admin/users')
    assert r.status_code == 200
    assert r.get_json()['users'][0]['email'] == 'a@example.com'


def test_no_session_401(client):
    r = client.get('/api/admin/users')
    assert r.status_code == 401


def test_create_proxies(client, monkeypatch):
    captured = {}

    def fake_create(data):
        captured.update(data)
        return {'id': 5, 'email': data['email'], 'role': data['role'], 'status': 'active'}

    monkeypatch.setattr(sso_admin, 'create_user', fake_create)
    login_as(client, SUPERADMIN)
    r = client.post('/api/admin/users',
                    json={'email': 'yeni@example.com', 'role': 'designer'},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    assert captured == {'email': 'yeni@example.com', 'role': 'designer'}
    assert r.get_json()['user']['id'] == 5


def test_update_proxies(client, monkeypatch):
    monkeypatch.setattr(sso_admin, 'update_user',
                        lambda uid, data: {'id': uid, 'role': data.get('role'),
                                           'status': data.get('status')})
    login_as(client, SUPERADMIN)
    r = client.patch('/api/admin/users/7', json={'role': 'management'},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['user']['role'] == 'management'


def test_sso_error_status_propagates(client, monkeypatch):
    def boom(data):
        raise sso_admin.SsoAdminError('bu e-posta zaten kayıtlı', 409)

    monkeypatch.setattr(sso_admin, 'create_user', boom)
    login_as(client, SUPERADMIN)
    r = client.post('/api/admin/users', json={'email': 'v@example.com', 'role': 'designer'},
                    headers=csrf_headers(client))
    assert r.status_code == 409
    assert 'zaten kayıtlı' in r.get_json()['error']
