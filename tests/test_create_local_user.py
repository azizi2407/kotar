"""scripts/create_local_user.py — the AUTH_MODE=local bootstrap script.

Real DB round-trip (conftest sqlite + app_context autouse); no mocks. Mirrors
tests/test_enqueue_script.py's pattern of testing the script's `run()` core directly.
"""
import pytest

import local_admin
from models import UserRef
from models_auth import LocalUser
from scripts.create_local_user import run


def test_run_creates_user_with_temp_password(app):
    with app.app_context():
        out = run('owner@example.com')
        assert out['email'] == 'owner@example.com'
        assert out['role'] == 'management'
        assert 'temp_password' in out and len(out['temp_password']) > 8
        row = LocalUser.query.filter_by(email='owner@example.com').one()
        assert row.password_hash != out['temp_password']  # hashed, not plain text


def test_run_defaults_to_management_role(app):
    with app.app_context():
        out = run('someone@example.com')
        assert out['role'] == 'management'


def test_run_custom_role_and_name(app):
    with app.app_context():
        out = run('designer@example.com', role='designer', name='A Designer')
        assert out['role'] == 'designer'
        assert out['name'] == 'A Designer'


def test_run_syncs_userref(app):
    with app.app_context():
        out = run('owner@example.com')
        ref = UserRef.query.filter_by(email='owner@example.com').one()
        assert ref.sub == f"local:{out['id']}"
        assert ref.role == 'management'


def test_run_duplicate_email_raises(app):
    with app.app_context():
        run('owner@example.com')
        with pytest.raises(local_admin.LocalAdminError) as exc:
            run('owner@example.com')
        assert exc.value.status == 409
