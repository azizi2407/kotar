"""Client example (reference) accounts — /api/sharing/clients/<id>/reference-accounts.

Focus: APPROVAL GATE (production roles only see approved ones), handle
canonicalization (the same account shouldn't get two rows), and the role matrix.
"""
import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    return client.post("/api/clients", json={"name": "Referans Müşteri"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _ekle(client, cid, handle, **kw):
    return client.post(f"/api/sharing/clients/{cid}/reference-accounts",
                       json={"handle": handle, **kw}, headers=csrf_headers(client))


def _aday_yaz(cid, handle, status="candidate"):
    """A candidate compiled by research — the endpoint only produces `manual`,
    `research` records are entered via a batch script; written directly in the test."""
    from extensions import db
    from models_reference import ClientReferenceAccount
    a = ClientReferenceAccount(client_id=cid, handle=handle, source="research",
                               status=status, title=f"{handle} işletmesi")
    db.session.add(a)
    db.session.commit()
    return a


# --- adding + canonicalization ----------------------------------------------

@pytest.mark.parametrize("girdi", [
    "ornekhesap",
    "@ornekhesap",
    "instagram.com/ornekhesap",
    "https://www.instagram.com/ornekhesap/",
    "https://instagram.com/ornekhesap?igsh=abc",
])
def test_handle_kanoniklestirilir(client, cid, girdi):
    """Every pasted format must resolve to the same handle, otherwise UNIQUE is useless."""
    login_as(client, MANAGER)
    r = _ekle(client, cid, girdi)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["account"]["handle"] == "ornekhesap"
    assert r.get_json()["account"]["url"] == "https://www.instagram.com/ornekhesap/"


def test_ayni_hesap_iki_kez_eklenemez(client, cid):
    login_as(client, MANAGER)
    assert _ekle(client, cid, "tekrar").status_code == 201
    r = _ekle(client, cid, "@tekrar/")          # different spelling, same account
    assert r.status_code == 409
    assert "already exists" in r.get_json()["error"]


def test_ayni_hesap_farkli_musteride_serbest(client, cid):
    """One account can be a reference for multiple clients (two construction firms)."""
    login_as(client, MANAGER)
    ikinci = client.post("/api/clients", json={"name": "Diğer"},
                         headers=csrf_headers(client)).get_json()["client"]["id"]
    assert _ekle(client, cid, "ortak").status_code == 201
    assert _ekle(client, ikinci, "ortak").status_code == 201


# "../../etc" → the sanitizer leaves ".."; rejected because the regex requires
# at least one letter/digit (so a path-escape can't turn into a handle).
@pytest.mark.parametrize("kotu", ["", "@", "ad soyad", "a" * 31, "hesap!", "../../etc",
                                  "...", "___"])
def test_gecersiz_handle_400(client, cid, kotu):
    login_as(client, MANAGER)
    assert _ekle(client, cid, kotu).status_code == 400


def test_elle_eklenen_dogrudan_onayli(client, cid):
    """An account manually added by management is already their choice — requiring
    a further approval step would be redundant. The approval gate exists for compiled candidates."""
    login_as(client, MANAGER)
    d = _ekle(client, cid, "elle", title="Elle Eklenen").get_json()["account"]
    assert d["status"] == "approved" and d["source"] == "manual"


# --- approval gate ------------------------------------------------------------

def _ata(cid, slot, sub):
    """content_creator/videographer can only read the brand guide on a client they're
    ASSIGNED to (`_require_asset_read`); designer and management can on any client."""
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=cid, role_slot=slot, user_id=sub))
    db.session.commit()


def test_uretim_rolleri_yalniz_onaylananlari_gorur(client, cid):
    login_as(client, MANAGER)
    _ata(cid, "content_creator", CONTENT_CREATOR["sub"])
    _ata(cid, "videographer_shoot", VIDEOGRAPHER["sub"])
    _aday_yaz(cid, "beklemede")
    _aday_yaz(cid, "onayli", status="approved")
    _aday_yaz(cid, "reddedilmis", status="rejected")

    # Management sees all of them — they make the decision
    hepsi = client.get(f"/api/sharing/clients/{cid}/reference-accounts").get_json()["accounts"]
    assert {a["handle"] for a in hepsi} == {"beklemede", "onayli", "reddedilmis"}

    for rol in (DESIGNER, CONTENT_CREATOR, VIDEOGRAPHER):
        login_as(client, rol)
        gorunen = client.get(
            f"/api/sharing/clients/{cid}/reference-accounts").get_json()["accounts"]
        assert {a["handle"] for a in gorunen} == {"onayli"}, rol["role"]


def test_karar_verilebilir_ve_geri_alinabilir(client, cid):
    login_as(client, MANAGER)
    a = _aday_yaz(cid, "kararsiz")
    r = client.patch(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                     json={"status": "approved"}, headers=csrf_headers(client))
    assert r.status_code == 200
    d = r.get_json()["account"]
    assert d["status"] == "approved" and d["decided_by"] == MANAGER["sub"]

    r = client.patch(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                     json={"status": "rejected"}, headers=csrf_headers(client))
    assert r.get_json()["account"]["status"] == "rejected"


def test_gecersiz_durum_400(client, cid):
    login_as(client, MANAGER)
    a = _aday_yaz(cid, "x")
    assert client.patch(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                        json={"status": "belki"}, headers=csrf_headers(client)).status_code == 400


def test_not_duzenlenebilir(client, cid):
    login_as(client, MANAGER)
    a = _aday_yaz(cid, "notlu")
    r = client.patch(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                     json={"note": "Reels kurgusu çok iyi"}, headers=csrf_headers(client))
    assert r.get_json()["account"]["note"] == "Reels kurgusu çok iyi"


# --- authorization ------------------------------------------------------------------

def test_yazma_yalniz_yonetim(client, cid):
    a = _aday_yaz(cid, "korumali")
    for rol in (DESIGNER, CONTENT_CREATOR, VIDEOGRAPHER):
        login_as(client, rol)
        assert _ekle(client, cid, "yenihesap").status_code == 403
        assert client.patch(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                            json={"status": "approved"},
                            headers=csrf_headers(client)).status_code == 403
        assert client.delete(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                             headers=csrf_headers(client)).status_code == 403


def test_oturumsuz_401(client, cid):
    with client.session_transaction() as sess:
        sess.clear()
    assert client.get(f"/api/sharing/clients/{cid}/reference-accounts").status_code == 401


def test_silme(client, cid):
    login_as(client, MANAGER)
    a = _aday_yaz(cid, "silinecek")
    assert client.delete(f"/api/sharing/clients/{cid}/reference-accounts/{a.id}",
                         headers=csrf_headers(client)).status_code == 200
    assert client.get(
        f"/api/sharing/clients/{cid}/reference-accounts").get_json()["accounts"] == []


def test_olmayan_musteri_404(client):
    login_as(client, MANAGER)
    assert client.get("/api/sharing/clients/999999/reference-accounts").status_code == 404
