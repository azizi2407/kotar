"""local_admin.py — local user management (AUTH_MODE=local, DB-backed, no network).

Covers both the module functions and the `/api/admin/*` endpoints. The global test
default is already AUTH_MODE=local (see conftest.py) — no separate monkeypatch needed.
"""
import pytest

import local_admin
from conftest import login_as
from extensions import db
from models import UserRef
from models_auth import LocalUser
from test_session_csrf import csrf_headers

SUPERADMIN = {'sub': 'local:1', 'email': 'superadmin@example.com', 'name': 'Superadmin',
             'role': 'management'}


def test_create_user_gecici_parola_doner(app):
    with app.app_context():
        out = local_admin.create_user({'email': 'yeni@test.com', 'role': 'designer'})
        assert out['email'] == 'yeni@test.com'
        assert out['role'] == 'designer'
        assert 'temp_password' in out and len(out['temp_password']) > 8
        row = LocalUser.query.filter_by(email='yeni@test.com').one()
        assert row.password_hash != out['temp_password']  # hashed, not plain text


def test_create_user_userref_senkronlar(app):
    with app.app_context():
        out = local_admin.create_user({'email': 'yeni@test.com', 'role': 'designer'})
        ref = UserRef.query.filter_by(email='yeni@test.com').one()
        assert ref.sub == f"local:{out['id']}"
        assert ref.role == 'designer'


def test_create_user_yinelenen_email_409(app):
    with app.app_context():
        local_admin.create_user({'email': 'yeni@test.com', 'role': 'designer'})
        with pytest.raises(local_admin.LocalAdminError) as exc:
            local_admin.create_user({'email': 'yeni@test.com', 'role': 'management'})
        assert exc.value.status == 409


def test_update_user_rol_degistirir(app):
    with app.app_context():
        out = local_admin.create_user({'email': 'yeni@test.com', 'role': 'designer'})
        updated = local_admin.update_user(out['id'], {'role': 'management'})
        assert updated['role'] == 'management'
        assert UserRef.query.filter_by(email='yeni@test.com').one().role == 'management'


def test_update_user_reset_password_yeni_temp_doner(app):
    with app.app_context():
        out = local_admin.create_user({'email': 'yeni@test.com', 'role': 'designer'})
        eski_hash = db.session.get(LocalUser, out["id"]).password_hash
        updated = local_admin.update_user(out['id'], {'reset_password': True})
        assert 'temp_password' in updated
        assert db.session.get(LocalUser, out["id"]).password_hash != eski_hash


def test_update_user_olmayan_id_404(app):
    with app.app_context():
        with pytest.raises(local_admin.LocalAdminError) as exc:
            local_admin.update_user(999, {'role': 'management'})
        assert exc.value.status == 404


# --- /api/admin/* (local dispatch) ---

def test_api_list_requires_superadmin(client):
    login_as(client, {'sub': 'local:2', 'email': 'yonetici@test.com', 'name': 'Y',
                      'role': 'management'})
    assert client.get('/api/admin/users').status_code == 403


def test_api_create_and_list(client):
    login_as(client, SUPERADMIN)
    r = client.post('/api/admin/users', json={'email': 'yeni@test.com', 'role': 'designer'},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    assert 'temp_password' in r.get_json()['user']

    r2 = client.get('/api/admin/users')
    assert r2.status_code == 200
    emails = {u['email'] for u in r2.get_json()['users']}
    assert 'yeni@test.com' in emails


def test_api_update_role(client):
    login_as(client, SUPERADMIN)
    created = client.post('/api/admin/users', json={'email': 'yeni@test.com', 'role': 'designer'},
                          headers=csrf_headers(client)).get_json()['user']
    r = client.patch(f"/api/admin/users/{created['id']}", json={'role': 'management'},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['user']['role'] == 'management'


def test_api_duplicate_email_409(client):
    login_as(client, SUPERADMIN)
    client.post('/api/admin/users', json={'email': 'yeni@test.com', 'role': 'designer'},
               headers=csrf_headers(client))
    r = client.post('/api/admin/users', json={'email': 'yeni@test.com', 'role': 'designer'},
                    headers=csrf_headers(client))
    assert r.status_code == 409
