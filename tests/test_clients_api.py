"""/api/clients CRUD + role/permission tests."""
import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers

PENDING = {"sub": "9", "email": "bekleyen@test.com", "name": "Bekleyen", "role": "pending"}

TAM_PAYLOAD = {
    "name": "Örnek Kafe",
    "sector": "Yeme-İçme",
    "notes": "Haftalık 4 tasarım",
    "client_email": "kafe@ornek.com",
    "instagram_url": "https://instagram.com/ornekkafe",
    "google_drive_url": "https://drive.google.com/drive/folders/abc",
    "contract": {
        "weekly_content_count": 4, "post_count": 3, "story_count": 5,
        "content_plan": "aylık", "content_types": ["post", "reels"],
        "special_sharing_types": [], "description": "Standart paket",
        "vat_rate": 20, "fee_effective_date": "2026-01-01",
        "video_shooting_enabled": True, "weekly_video_count": 1,
        "photo_shooting_enabled": True, "weekly_photo_count": 2,
        "drone_usage": False, "location_notes": "Merkez şube",
    },
    "contacts": [{"name": "Ali Veli", "email": "ali@ornek.com", "phone": "0555", "notes": ""}],
    "locations": [{"name": "Merkez", "address": "Muratpaşa/Antalya"}],
    "team_assignments": {"designer": "2", "content_creator": "3"},
}


def olustur(client, payload=None):
    r = client.post("/api/clients", json=payload or {"name": "Test Müşteri"},
                    headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["client"]


# --- Permissions ---

def test_liste_oturumsuz_401(client):
    assert client.get("/api/clients").status_code == 401


def test_liste_pending_rolu_403(client):
    login_as(client, PENDING)
    assert client.get("/api/clients").status_code == 403


def test_liste_ekip_rolu_okuyabilir(client):
    login_as(client, DESIGNER)
    assert client.get("/api/clients").status_code == 200


def test_yazma_management_disi_403(client):
    login_as(client, DESIGNER)
    r = client.post("/api/clients", json={"name": "X"}, headers=csrf_headers(client))
    assert r.status_code == 403


# --- CRUD ---

def test_olustur_minimal(client):
    login_as(client, MANAGER)
    c = olustur(client)
    assert c["id"] > 0
    assert c["name"] == "Test Müşteri"
    assert c["status"] == "active"
    assert c["created_by"] == MANAGER["sub"]


def test_olustur_isimsiz_400(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"sector": "x"}, headers=csrf_headers(client))
    assert r.status_code == 400


def test_olustur_tam_payload_ve_detay(client):
    login_as(client, MANAGER)
    c = olustur(client, TAM_PAYLOAD)
    r = client.get(f"/api/clients/{c['id']}")
    assert r.status_code == 200
    d = r.get_json()["client"]
    assert d["sector"] == "Yeme-İçme"
    assert d["contract"]["weekly_content_count"] == 4
    assert d["contract"]["drone_usage"] is False
    assert d["contacts"][0]["name"] == "Ali Veli"
    assert d["locations"][0]["address"] == "Muratpaşa/Antalya"
    assert d["team_assignments"]["designer"] == "2"


def test_liste_varsayilan_sadece_aktif(client):
    login_as(client, MANAGER)
    a = olustur(client, {"name": "Aktif"})
    s = olustur(client, {"name": "Silinecek"})
    client.delete(f"/api/clients/{s['id']}", headers=csrf_headers(client))
    r = client.get("/api/clients").get_json()
    adlar = [c["name"] for c in r["clients"]]
    assert "Aktif" in adlar and "Silinecek" not in adlar


def test_liste_status_filtresi_deleted(client):
    login_as(client, MANAGER)
    s = olustur(client, {"name": "Silinen"})
    client.delete(f"/api/clients/{s['id']}", headers=csrf_headers(client))
    r = client.get("/api/clients?status=deleted").get_json()
    assert [c["name"] for c in r["clients"]] == ["Silinen"]


def test_liste_arama(client):
    login_as(client, MANAGER)
    olustur(client, {"name": "Kahveci Ahmet"})
    olustur(client, {"name": "Berber Mehmet"})
    r = client.get("/api/clients?q=kahve").get_json()
    assert [c["name"] for c in r["clients"]] == ["Kahveci Ahmet"]


def test_guncelle_alanlar_ve_updated_by(client):
    login_as(client, MANAGER)
    c = olustur(client)
    r = client.patch(f"/api/clients/{c['id']}",
                     json={"name": "Yeni Ad", "sector": "Turizm"},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    d = r.get_json()["client"]
    assert d["name"] == "Yeni Ad" and d["sector"] == "Turizm"
    assert d["updated_by"] == MANAGER["sub"]


def test_guncelle_ic_koleksiyonlar_replace(client):
    login_as(client, MANAGER)
    c = olustur(client, TAM_PAYLOAD)
    r = client.patch(
        f"/api/clients/{c['id']}",
        json={"contacts": [{"name": "Yeni Kişi", "email": "", "phone": "", "notes": ""}],
              "team_assignments": {"designer": "5"}},
        headers=csrf_headers(client))
    d = r.get_json()["client"]
    assert [k["name"] for k in d["contacts"]] == ["Yeni Kişi"]
    assert d["team_assignments"] == {"designer": "5"}
    # Fields not sent in the PATCH are preserved
    assert d["locations"][0]["name"] == "Merkez"
    assert d["contract"]["weekly_content_count"] == 4


def test_soft_delete_ve_restore(client):
    login_as(client, MANAGER)
    c = olustur(client)
    r = client.delete(f"/api/clients/{c['id']}", json={"reason": "anlaşma bitti"},
                      headers=csrf_headers(client))
    assert r.status_code == 200
    d = client.get(f"/api/clients/{c['id']}").get_json()["client"]
    assert d["status"] == "deleted"
    assert d["deleted_by"] == MANAGER["sub"]
    assert d["deleted_reason"] == "anlaşma bitti"
    assert d["deleted_at"]

    r = client.post(f"/api/clients/{c['id']}/restore", headers=csrf_headers(client))
    assert r.status_code == 200
    d = client.get(f"/api/clients/{c['id']}").get_json()["client"]
    assert d["status"] == "active"
    assert d["restored_by"] == MANAGER["sub"]


def test_olmayan_id_404(client):
    login_as(client, MANAGER)
    assert client.get("/api/clients/9999").status_code == 404


# --- Role-aware field narrowing (2026-07-27) -------------------------
# Production roles can read the client record in the Brand Directory context;
# commercial and contact fields are NOT theirs to see. `_client_json()` never puts
# these in the response.

# Keys that must NEVER appear in the response (for non-management roles).
GIZLI_DETAY = ("contract", "contacts", "locations", "notes",
               "special_days_token", "client_email")


@pytest.mark.parametrize("rol", [DESIGNER, CONTENT_CREATOR, VIDEOGRAPHER])
def test_detay_uretim_rolu_ticari_ve_iletisim_alanlarini_GORMEZ(client, rol):
    login_as(client, MANAGER)
    c = olustur(client, TAM_PAYLOAD)
    login_as(client, rol)
    r = client.get(f"/api/clients/{c['id']}")
    assert r.status_code == 200
    d = r.get_json()["client"]
    for k in GIZLI_DETAY:
        assert k not in d, f"{rol['role']} rolüne {k} sızdı"
    # Fields needed for the directory must stay IN PLACE — narrowing shouldn't cut too much.
    assert d["name"] == TAM_PAYLOAD["name"]
    assert d["sector"] == TAM_PAYLOAD["sector"]
    assert "google_drive_url" in d and "team_assignments" in d


def test_detay_management_tum_alanlari_GORUR(client):
    login_as(client, MANAGER)
    c = olustur(client, TAM_PAYLOAD)
    d = client.get(f"/api/clients/{c['id']}").get_json()["client"]
    for k in GIZLI_DETAY:
        assert k in d, f"management {k} alanını kaybetti"
    assert d["contract"]["vat_rate"] == 20
    assert d["contacts"][0]["phone"] == "0555"
    assert d["locations"][0]["name"] == "Merkez"


def test_liste_uretim_rolu_client_email_GORMEZ(client):
    login_as(client, MANAGER)
    olustur(client, TAM_PAYLOAD)
    login_as(client, DESIGNER)
    rows = client.get("/api/clients").get_json()["clients"]
    assert rows and all("client_email" not in c for c in rows)
    # The list already has a limited schema; `contract` was never there, and that should stay so.
    assert all("contract" not in c for c in rows)


def test_liste_management_client_email_GORUR(client):
    login_as(client, MANAGER)
    olustur(client, TAM_PAYLOAD)
    rows = client.get("/api/clients").get_json()["clients"]
    assert rows[0]["client_email"] == TAM_PAYLOAD["client_email"]
