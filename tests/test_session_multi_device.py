"""Multi-device session + session lifetime (2026-08-05).

Complaint: "logging in from mobile drops the session on the computer". Diagnosis:
it doesn't — the session lives in Flask's signed cookie, there's no server-side
session record (neither in agency nor SSO), so devices can't drop each other. The
real limit was an 8-hour lifetime, which was extended to 30 days.

These tests lock in two things: device independence and the lifetime being a
sliding window. Both are the kind of thing that can "silently come back" — one if
a server-side store like `Flask-Session` gets added, the other if the duration
setting gets manually shortened.
"""
from datetime import timedelta

from conftest import DESIGNER, MANAGER, login_as


def _oturum_gecerli(c):
    return c.get("/api/session").status_code == 200


def test_iki_cihaz_ayni_anda_bagli_kalir(app):
    """Mobile + desktop, same user: the second login does NOT drop the first."""
    masaustu = app.test_client()
    mobil = app.test_client()

    login_as(masaustu, MANAGER)
    assert _oturum_gecerli(masaustu)

    login_as(mobil, MANAGER)                 # login from second device
    assert _oturum_gecerli(mobil)
    assert _oturum_gecerli(masaustu), "ikinci cihazdaki giriş birincisini düşürdü"


def test_ucuncu_cihaz_da_calisir(app):
    """No limit on device count — 'one mobile, one desktop' is a RESULT, not a rule."""
    cihazlar = [app.test_client() for _ in range(3)]
    for c in cihazlar:
        login_as(c, MANAGER)
    assert all(_oturum_gecerli(c) for c in cihazlar)


def test_bir_cihazda_cikis_digerini_etkilemez(app):
    """Logout is LOCAL: `session.clear()` only empties that cookie."""
    masaustu, mobil = app.test_client(), app.test_client()
    login_as(masaustu, MANAGER)
    login_as(mobil, MANAGER)

    masaustu.get("/auth/logout")
    assert not _oturum_gecerli(masaustu)
    assert _oturum_gecerli(mobil), "bir cihazdaki çıkış diğerini de düşürdü"


def test_farkli_kullanicilar_karismaz(app):
    """Two different users on the same server — identity comes from the cookie,
    shouldn't mix up."""
    a, b = app.test_client(), app.test_client()
    login_as(a, MANAGER)
    login_as(b, DESIGNER)
    assert a.get("/api/session").get_json()["user"]["sub"] == MANAGER["sub"]
    assert b.get("/api/session").get_json()["user"]["sub"] == DESIGNER["sub"]


def test_oturum_omru_ve_kayan_pencere(app):
    """When the lifetime was 8 hours the user had to log back in every morning; 30
    days + sliding window.

    If `SESSION_REFRESH_EACH_REQUEST` gets turned off, the duration would be fixed
    from the moment of login and an active user would get stranded on day 30 — so
    it's explicitly verified here."""
    assert app.config["PERMANENT_SESSION_LIFETIME"] >= timedelta(days=30)
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True


def test_sunucu_tarafi_oturum_deposu_yok(app):
    """As long as the session lives in a signed cookie, devices can't drop each
    other. If a server-side store (Flask-Session/redis) gets added, this guarantee
    is lost."""
    from flask.sessions import SecureCookieSessionInterface
    assert isinstance(app.session_interface, SecureCookieSessionInterface)
