"""Pytest altyapısı — agency paneli.

KRİTİK: app.py import edilir edilmez create_app() çalışır ve env okur; bu yüzden
env değişkenleri app import EDİLMEDEN ÖNCE, modül seviyesinde set edilir.
Testler gerçek Postgres'e/ağa dokunmaz (tempfile sqlite). AUTH_MODE=local (OIDC
discovery ağa çıkardığı için testlerde kullanılmaz — bkz. sso_client.OIDCClient):
oturum, session'a doğrudan user yazılarak taklit edilir (login_as helper'ı).
`AUTH_MODE=oidc` yolunu (admin_api sso_admin proxy'si) egzersiz eden testler
kendi içinde `monkeypatch.setenv('AUTH_MODE', 'oidc')` yapar — bkz. test_admin_api.py.
"""
import atexit
import os
import shutil
import tempfile

import pytest

# --- App import'undan ÖNCE izole ortam (modül seviyesi) ---
_TEST_DIR = tempfile.mkdtemp(prefix="agency-test-")
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DIR, "test.db")
os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["AUTH_MODE"] = "local"
os.environ["IMG_BUCKET_DIR"] = os.path.join(_TEST_DIR, "img-bucket")
os.environ["MEDIA_STORE_DIR"] = os.path.join(_TEST_DIR, "media")
# Mail modülü testleri için sabit Fernet anahtarı (crypto/service/api). test_mail_crypto
# anahtar-yok senaryosunu kendi monkeypatch/reload'ıyla ayrıca kurar.
os.environ["MAIL_ENC_KEY"] = "aTFslVpR-SPzeI1ekN9dl4g3EEBoInuRuVxw5TIVCuo="
# Impersonation/kullanıcı yönetimi testleri bu adresi superadmin sayar (bkz. sharing.py
# OWNER_EMAIL ve app.py SUPERADMIN_EMAILS varsayılanı — prod'da boş, burada testler için sabit).
os.environ["SUPERADMIN_EMAILS"] = "superadmin@example.com"

from app import app as flask_app  # noqa: E402
from extensions import db  # noqa: E402

flask_app.config["TESTING"] = True


@pytest.fixture(scope="session")
def app():
    return flask_app


@pytest.fixture(autouse=True)
def _ctx_and_clean(app):
    """Her test için app context + temiz DB (tüm tablolar sıfırdan)."""
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
    """SSO akışını taklit et: session'a doğrudan user claim'lerini yaz."""
    with client.session_transaction() as sess:
        sess["user"] = dict(user)
    return user
