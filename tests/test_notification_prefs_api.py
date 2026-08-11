"""Notification preferences API + ntfy delivery (2026-08-05).

The channel's HTTP call is mocked — tests never touch the network. Focus: you can't
reach anyone else's record from your own, validation, and the "panel row was written
but the phone didn't get it" distinction.
"""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def ntfy(monkeypatch):
    """Treat the ntfy channel as configured and collect what's sent."""
    gonderilen = []
    import ntfy_gateway
    monkeypatch.setenv('NTFY_BASE_URL', 'http://ntfy.test')
    monkeypatch.setenv('NTFY_TOKEN', 'tk_test')

    def fake_send(topic, title, body, severity='normal', click_url=None):
        gonderilen.append({'topic': topic, 'title': title, 'body': body,
                           'severity': severity, 'click': click_url})
        return True

    monkeypatch.setattr(ntfy_gateway, 'send', fake_send)
    return gonderilen


# --- preference endpoints ----------------------------------------------------

def test_get_kayit_yoksa_olusturur_ve_topic_verir(client, ntfy):
    login_as(client, MANAGER)
    r = client.get("/api/notification-prefs")
    assert r.status_code == 200
    d = r.get_json()
    assert len(d["prefs"]["ntfy_topic"]) == 32          # 16 hex bytes
    assert d["prefs"]["ntfy_enabled"] is False          # OPT-IN: the record alone doesn't turn it on
    assert d["prefs"]["min_severity"] == "kritik"
    assert d["subscribe_url"].endswith(d["prefs"]["ntfy_topic"])
    # the ntfy app wants the server and the topic in SEPARATE fields → both must be returned
    # separately (so the user doesn't have to pull a piece out of the URL, 2026-08-05 feedback).
    assert d["server_url"] and not d["server_url"].endswith(d["prefs"]["ntfy_topic"])
    assert d["subscribe_url"] == f'{d["server_url"]}/{d["prefs"]["ntfy_topic"]}'
    assert d["channel_ready"] is True


def test_topic_kullanicilar_arasi_ayri(client, ntfy):
    login_as(client, MANAGER)
    t1 = client.get("/api/notification-prefs").get_json()["prefs"]["ntfy_topic"]
    client.get("/auth/logout")
    login_as(client, DESIGNER)
    t2 = client.get("/api/notification-prefs").get_json()["prefs"]["ntfy_topic"]
    assert t1 != t2


def test_put_guncelleme(client, ntfy):
    login_as(client, MANAGER)
    r = client.put("/api/notification-prefs",
                   json={"ntfy_enabled": True, "min_severity": "normal",
                         "quiet_start": 22, "quiet_end": 8},
                   headers=csrf_headers(client))
    assert r.status_code == 200
    p = r.get_json()["prefs"]
    assert (p["ntfy_enabled"], p["min_severity"], p["quiet_start"], p["quiet_end"]) \
        == (True, "normal", 22, 8)


def test_put_gecersiz_severity_400(client, ntfy):
    login_as(client, MANAGER)
    r = client.put("/api/notification-prefs", json={"min_severity": "acil"},
                   headers=csrf_headers(client))
    assert r.status_code == 400


@pytest.mark.parametrize("deger", [24, -1, "abc"])
def test_put_gecersiz_saat_400(client, ntfy, deger):
    login_as(client, MANAGER)
    r = client.put("/api/notification-prefs", json={"quiet_start": deger},
                   headers=csrf_headers(client))
    assert r.status_code == 400


def test_put_saat_temizlenebilir(client, ntfy):
    login_as(client, MANAGER)
    client.put("/api/notification-prefs", json={"quiet_start": 22, "quiet_end": 8},
               headers=csrf_headers(client))
    r = client.put("/api/notification-prefs", json={"quiet_start": None, "quiet_end": None},
                   headers=csrf_headers(client))
    assert r.get_json()["prefs"]["quiet_start"] is None


def test_topic_yenileme(client, ntfy):
    login_as(client, MANAGER)
    eski = client.get("/api/notification-prefs").get_json()["prefs"]["ntfy_topic"]
    yeni = client.put("/api/notification-prefs", json={"rotate_topic": True},
                      headers=csrf_headers(client)).get_json()["prefs"]["ntfy_topic"]
    assert yeni != eski and len(yeni) == 32


def test_oturumsuz_401(client, ntfy):
    client.get("/auth/logout")
    assert client.get("/api/notification-prefs").status_code == 401


def test_csrf_zorunlu(client, ntfy):
    login_as(client, MANAGER)
    assert client.put("/api/notification-prefs", json={"ntfy_enabled": True}).status_code == 403


def test_test_ucu_esigi_atlar(client, ntfy):
    """The test notification goes out even with the threshold at 'kritik' and the channel
    off — the question here is 'does the channel work', not 'does this notification pass'."""
    login_as(client, MANAGER)
    r = client.post("/api/notification-prefs/test", headers=csrf_headers(client))
    assert r.status_code == 200
    assert len(ntfy) == 1


def test_kanal_yapilandirilmamissa_test_ucu_503(client, monkeypatch):
    monkeypatch.delenv("NTFY_BASE_URL", raising=False)
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    login_as(client, MANAGER)
    r = client.post("/api/notification-prefs/test", headers=csrf_headers(client))
    assert r.status_code == 503


# --- delivery ------------------------------------------------------------------

def _pref_ac(client, **kw):
    govde = {"ntfy_enabled": True}
    govde.update(kw)
    return client.put("/api/notification-prefs", json=govde,
                      headers=csrf_headers(client)).get_json()["prefs"]


def test_kritik_bildirim_telefona_gider(client, ntfy):
    import notifications
    from extensions import db
    login_as(client, MANAGER)
    pref = _pref_ac(client)                      # default threshold: kritik
    ntfy.clear()
    notifications.push(MANAGER["sub"], "revision_requested", "Revizyon talebi",
                       "Müşteri X — revizyon istendi", link="/panel/sharing")
    db.session.commit()
    assert len(ntfy) == 1
    assert ntfy[0]["topic"] == pref["ntfy_topic"]
    assert ntfy[0]["severity"] == "kritik"
    assert ntfy[0]["click"] == "https://panel.example.com/panel/sharing"


def test_esik_altindaki_bildirim_panele_yazilir_telefona_gitmez(client, ntfy):
    """The distinction matters: the in-panel bell ALWAYS fires, ntfy is selective."""
    import notifications
    from extensions import db
    from models import Notification
    login_as(client, MANAGER)
    _pref_ac(client)                             # threshold: kritik
    ntfy.clear()
    notifications.push(MANAGER["sub"], "revision_resolved", "Revizyon çözüldü", "…")
    db.session.commit()
    assert ntfy == []
    satir = Notification.query.filter_by(kind="revision_resolved").first()
    assert satir is not None and satir.severity == "bilgi"


def test_opt_in_kayitsiz_kullaniciya_gitmez(client, ntfy):
    import notifications
    from extensions import db
    login_as(client, MANAGER)
    ntfy.clear()
    notifications.push("kaydi-olmayan-sub", "ops_health", "Uyarı", "…")
    db.session.commit()
    assert ntfy == []


def _musteri(client):
    """Client + recipient record. `_recipients` reads from UserRef (NOT from the
    session claim) — without a UserRef, the notification has no recipient."""
    from extensions import db
    from models import UserRef
    if db.session.get(UserRef, MANAGER["sub"]) is None:
        db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                               name=MANAGER["name"], role="management"))
        db.session.commit()
    return client.post("/api/clients", json={"name": "Bildirim Müşteri"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def test_musteri_revizyonu_kritige_yukselir(client, ntfy):
    """`client_review` is NORMAL in the catalog; it escalates to KRITIK on a revision status."""
    import notifications
    from models import Notification
    login_as(client, MANAGER)
    cid = _musteri(client)
    _pref_ac(client)
    ntfy.clear()
    notifications.notify_client_review(cid, "2026-W32", "revision_requested")
    satir = Notification.query.filter_by(kind="client_review").first()
    assert satir.severity == "kritik"
    assert len(ntfy) == 1


def test_musteri_onayi_normal_kalir(client, ntfy):
    import notifications
    from models import Notification
    login_as(client, MANAGER)
    cid = _musteri(client)
    _pref_ac(client)                             # threshold kritik → approval doesn't reach the phone
    ntfy.clear()
    notifications.notify_client_review(cid, "2026-W32", "approved")
    satir = Notification.query.filter_by(kind="client_review").first()
    assert satir.severity == "normal"
    assert ntfy == []


# --- header encoding (2026-08-05 production bug) -----------------------------

def test_turkce_baslik_bozulmadan_gonderilir(monkeypatch):
    """In production, "Çekim fotoğrafı" was turning into "?ekim foto?raf?": requests
    encodes a str header as latin-1. The title now goes out as UTF-8 bytes (ntfy accepts it)."""
    import ntfy_gateway
    yakalanan = {}
    monkeypatch.setenv("NTFY_BASE_URL", "http://ntfy.test")
    monkeypatch.setenv("NTFY_TOKEN", "tk_test")

    class _Resp:
        status_code = 200
        text = ""

    def fake_post(url, data=None, headers=None, timeout=None):
        yakalanan.update(headers=headers, data=data)
        return _Resp()

    monkeypatch.setattr(ntfy_gateway.requests, "post", fake_post)
    ntfy_gateway.send("topic", "Çekim fotoğrafı yüklendi", "gövde İĞÜŞÖÇ")
    assert yakalanan["headers"]["Title"] == "Çekim fotoğrafı yüklendi".encode("utf-8")
    assert yakalanan["data"] == "gövde İĞÜŞÖÇ".encode("utf-8")


def test_baslikta_satir_sonu_temizlenir(monkeypatch):
    """A newline in an HTTP header can split the request; since the announcement title
    carries user text, this is an injection surface."""
    import ntfy_gateway
    yakalanan = {}
    monkeypatch.setenv("NTFY_BASE_URL", "http://ntfy.test")
    monkeypatch.setenv("NTFY_TOKEN", "tk_test")

    class _Resp:
        status_code = 200
        text = ""

    monkeypatch.setattr(ntfy_gateway.requests, "post",
                        lambda url, data=None, headers=None, timeout=None:
                        (yakalanan.update(headers=headers), _Resp())[1])
    ntfy_gateway.send("topic", "Satır\nX-Injected: evet\r\nDevam", "x")
    assert yakalanan["headers"]["Title"] == "Satır X-Injected: evet Devam".encode("utf-8")
