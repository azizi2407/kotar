"""Pytest infrastructure — agency panel.

CRITICAL: create_app() runs as soon as app.py is imported, and it reads env; so
env variables are set at module level, BEFORE the app is imported.
Tests never touch the real Postgres/network (tempfile sqlite). AUTH_MODE=local (OIDC
discovery is not used in tests because it reaches the network — see sso_client.OIDCClient):
the session is faked by writing the user directly into the session (the login_as helper).
Tests that exercise the `AUTH_MODE=oidc` path (admin_api's sso_admin proxy)
do their own `monkeypatch.setenv('AUTH_MODE', 'oidc')` — see test_admin_api.py.
"""
import atexit
import os
import shutil
import tempfile

import pytest

# --- Isolated environment BEFORE app import (module level) ---
_TEST_DIR = tempfile.mkdtemp(prefix="agency-test-")
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DIR, "test.db")
os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["AUTH_MODE"] = "local"
os.environ["IMG_BUCKET_DIR"] = os.path.join(_TEST_DIR, "img-bucket")
os.environ["MEDIA_STORE_DIR"] = os.path.join(_TEST_DIR, "media")
# Fixed Fernet key for mail module tests (crypto/service/api). test_mail_crypto
# sets up the no-key scenario separately with its own monkeypatch/reload.
os.environ["MAIL_ENC_KEY"] = "aTFslVpR-SPzeI1ekN9dl4g3EEBoInuRuVxw5TIVCuo="
# Impersonation/user-management tests treat this address as superadmin (see sharing.py's
# OWNER_EMAIL and app.py's SUPERADMIN_EMAILS default — empty in prod, fixed here for tests).
os.environ["SUPERADMIN_EMAILS"] = "superadmin@example.com"

from app import app as flask_app  # noqa: E402
from extensions import db  # noqa: E402

flask_app.config["TESTING"] = True


@pytest.fixture(scope="session")
def app():
    return flask_app


@pytest.fixture(autouse=True)
def _ctx_and_clean(app):
    """App context + a clean DB for every test (all tables from scratch)."""
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    yield
    db.session.rollback()
    db.session.remove()
    ctx.pop()


@pytest.fixture
def client(app):
    return app.test_client()


MANAGER = {"sub": "1", "email": "yonetici@test.com", "name": "Yönetici", "role": "management"}
DESIGNER = {"sub": "2", "email": "tasarimci@test.com", "name": "Tasarımcı", "role": "designer"}
CONTENT_CREATOR = {"sub": "3", "email": "icerik@test.com", "name": "İçerikçi",
                   "role": "content_creator"}
VIDEOGRAPHER = {"sub": "4", "email": "video@test.com", "name": "Videografçı",
                "role": "videographer"}
PENDING = {"sub": "9", "email": "yeni@test.com", "name": "Yeni", "role": "pending"}


def login_as(client, user=MANAGER):
    """Fake the SSO flow: write the user claims directly into the session."""
    with client.session_transaction() as sess:
        sess["user"] = dict(user)
    return user
