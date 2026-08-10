"""Çoklu cihaz oturumu + oturum ömrü (2026-08-05).

Şikayet: "mobilden girince bilgisayardaki oturum düşüyor". Teşhis: düşmüyor —
oturum Flask'ın imzalı çerezinde, sunucuda (ne agency'de ne SSO'da) oturum kaydı
yok, dolayısıyla cihazlar birbirini düşüremez. Gerçek sınır 8 saatlik ömürdü ve
30 güne çıkarıldı.

Bu testler iki şeyi kilitliyor: cihazların bağımsızlığı ve ömrün kayan pencere
olması. İkisi de "sessizce geri gelebilir" cinsten — biri `Flask-Session` gibi
sunucu tarafı bir store eklerse, diğeri süre ayarı elle kısaltılırsa.
"""
from datetime import timedelta

from conftest import DESIGNER, MANAGER, login_as


def _oturum_gecerli(c):
    return c.get("/api/session").status_code == 200


def test_iki_cihaz_ayni_anda_bagli_kalir(app):
    """Mobil + masaüstü aynı kullanıcı: ikinci giriş birinciyi DÜŞÜRMEZ."""
    masaustu = app.test_client()
    mobil = app.test_client()

    login_as(masaustu, MANAGER)
    assert _oturum_gecerli(masaustu)

    login_as(mobil, MANAGER)                 # ikinci cihazdan giriş
    assert _oturum_gecerli(mobil)
    assert _oturum_gecerli(masaustu), "ikinci cihazdaki giriş birincisini düşürdü"


def test_ucuncu_cihaz_da_calisir(app):
    """Cihaz sayısında sınır yok — 'bir mobil bir masaüstü' bir KURAL değil, sonuç."""
    cihazlar = [app.test_client() for _ in range(3)]
    for c in cihazlar:
        login_as(c, MANAGER)
    assert all(_oturum_gecerli(c) for c in cihazlar)


def test_bir_cihazda_cikis_digerini_etkilemez(app):
    """Çıkış YEREL: `session.clear()` yalnız o çerezi boşaltır."""
    masaustu, mobil = app.test_client(), app.test_client()
    login_as(masaustu, MANAGER)
    login_as(mobil, MANAGER)

    masaustu.get("/auth/logout")
    assert not _oturum_gecerli(masaustu)
    assert _oturum_gecerli(mobil), "bir cihazdaki çıkış diğerini de düşürdü"


def test_farkli_kullanicilar_karismaz(app):
    """Aynı sunucuda iki farklı kullanıcı — kimlik çerezden geliyor, karışmamalı."""
    a, b = app.test_client(), app.test_client()
    login_as(a, MANAGER)
    login_as(b, DESIGNER)
    assert a.get("/api/session").get_json()["user"]["sub"] == MANAGER["sub"]
    assert b.get("/api/session").get_json()["user"]["sub"] == DESIGNER["sub"]


def test_oturum_omru_ve_kayan_pencere(app):
    """Ömür 8 saatken kullanıcı her sabah yeniden giriyordu; 30 gün + kayan pencere.

    `SESSION_REFRESH_EACH_REQUEST` kapanırsa süre giriş anından itibaren sabitlenir
    ve aktif kullanıcı 30. günde ortada kalır — o yüzden açıkça doğrulanıyor."""
    assert app.config["PERMANENT_SESSION_LIFETIME"] >= timedelta(days=30)
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True


def test_sunucu_tarafi_oturum_deposu_yok(app):
    """Oturum imzalı çerezde olduğu sürece cihazlar birbirini düşüremez. Sunucu
    tarafı bir store (Flask-Session/redis) eklenirse bu garanti kaybolur."""
    from flask.sessions import SecureCookieSessionInterface
    assert isinstance(app.session_interface, SecureCookieSessionInterface)
