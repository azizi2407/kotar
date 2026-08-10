"""Müşteri örnek (referans) hesapları — /api/sharing/clients/<id>/reference-accounts.

Vurgu: ONAY KAPISI (üretim rolleri yalnız onaylananı görür), handle
kanonikleştirme (aynı hesap iki satır olmasın) ve rol matrisi.
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
    """Araştırmayla derlenmiş aday — uç yalnız `manual` üretiyor, `research`
    kayıtları toplu betikle giriyor; testte doğrudan yazılır."""
    from extensions import db
    from models_reference import ClientReferenceAccount
    a = ClientReferenceAccount(client_id=cid, handle=handle, source="research",
                               status=status, title=f"{handle} işletmesi")
    db.session.add(a)
    db.session.commit()
    return a


# --- ekleme + kanonikleştirme ----------------------------------------------

@pytest.mark.parametrize("girdi", [
    "ornekhesap",
    "@ornekhesap",
    "instagram.com/ornekhesap",
    "https://www.instagram.com/ornekhesap/",
    "https://instagram.com/ornekhesap?igsh=abc",
])
def test_handle_kanoniklestirilir(client, cid, girdi):
    """Yapıştırılan her biçim aynı handle'a inmeli, yoksa UNIQUE işe yaramaz."""
    login_as(client, MANAGER)
    r = _ekle(client, cid, girdi)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["account"]["handle"] == "ornekhesap"
    assert r.get_json()["account"]["url"] == "https://www.instagram.com/ornekhesap/"


def test_ayni_hesap_iki_kez_eklenemez(client, cid):
    login_as(client, MANAGER)
    assert _ekle(client, cid, "tekrar").status_code == 201
    r = _ekle(client, cid, "@tekrar/")          # farklı yazım, aynı hesap
    assert r.status_code == 409
    assert "zaten var" in r.get_json()["error"]


def test_ayni_hesap_farkli_musteride_serbest(client, cid):
    """Bir hesap birden çok müşteriye örnek olabilir (iki inşaat firması)."""
    login_as(client, MANAGER)
    ikinci = client.post("/api/clients", json={"name": "Diğer"},
                         headers=csrf_headers(client)).get_json()["client"]["id"]
    assert _ekle(client, cid, "ortak").status_code == 201
    assert _ekle(client, ikinci, "ortak").status_code == 201


# "../../etc" → temizleyici ".." bırakıyor; regex en az bir harf/rakam istediği
# için reddedilir (yol kaçışı handle'a dönüşmesin).
@pytest.mark.parametrize("kotu", ["", "@", "ad soyad", "a" * 31, "hesap!", "../../etc",
                                  "...", "___"])
def test_gecersiz_handle_400(client, cid, kotu):
    login_as(client, MANAGER)
    assert _ekle(client, cid, kotu).status_code == 400


def test_elle_eklenen_dogrudan_onayli(client, cid):
    """Yönetimin elle eklediği hesap zaten onun seçimi — ayrıca onaylatmak
    gereksiz bir adım olurdu. Onay kapısı derlenen adaylar için var."""
    login_as(client, MANAGER)
    d = _ekle(client, cid, "elle", title="Elle Eklenen").get_json()["account"]
    assert d["status"] == "approved" and d["source"] == "manual"


# --- onay kapısı ------------------------------------------------------------

def _ata(cid, slot, sub):
    """content_creator/videographer marka rehberini yalnız ATANDIĞI müşteride
    okuyabiliyor (`_require_asset_read`); designer ve management her müşteride."""
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

    # Yönetim hepsini görür — kararı o veriyor
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


# --- yetki ------------------------------------------------------------------

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
