"""/api/session (oturum + CSRF token) ve CSRF zorlaması testleri."""
from conftest import DESIGNER, MANAGER, login_as


def test_session_oturum_yoksa_401(client):
    r = client.get("/api/session")
    assert r.status_code == 401


def test_session_user_ve_csrf_dondurur(client):
    login_as(client, MANAGER)
    r = client.get("/api/session")
    assert r.status_code == 200
    data = r.get_json()
    assert data["user"]["email"] == MANAGER["email"]
    assert isinstance(data["csrf"], str) and len(data["csrf"]) >= 32


def test_csrf_ayni_oturumda_sabit(client):
    login_as(client, MANAGER)
    t1 = client.get("/api/session").get_json()["csrf"]
    t2 = client.get("/api/session").get_json()["csrf"]
    assert t1 == t2


def test_mutasyon_csrf_token_olmadan_403(client):
    login_as(client, MANAGER)
    client.get("/api/session")  # token üretilsin
    r = client.post("/api/clients", json={"name": "Test"})
    assert r.status_code == 403


def test_mutasyon_yanlis_csrf_token_403(client):
    login_as(client, MANAGER)
    client.get("/api/session")
    r = client.post("/api/clients", json={"name": "Test"},
                    headers={"X-CSRFToken": "sahte-token"})
    assert r.status_code == 403


def csrf_headers(client):
    """Geçerli CSRF header'ı üret (testlerde ortak)."""
    return {"X-CSRFToken": client.get("/api/session").get_json()["csrf"]}


def test_mutasyon_dogru_csrf_ile_gecer(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Test"}, headers=csrf_headers(client))
    assert r.status_code == 201


def test_get_istekleri_csrf_gerektirmez(client):
    login_as(client, DESIGNER)
    r = client.get("/api/clients")
    assert r.status_code == 200
