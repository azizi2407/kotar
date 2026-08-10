"""Impersonation ("kullanıcı gözünden bak") — yalnız superadmin; yetki gerçek kimlik
üzerinden; start/stop; impersonate edilen kullanıcı yetki yükseltemez."""
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

from extensions import db
from models import UserRef

SUPERADMIN = {"sub": "99", "email": "superadmin@example.com", "name": "Superadmin", "role": "management"}


def _seed_users():
    db.session.add(UserRef(sub="2", email="tasarimci@test.com", name="Tasarımcı", role="designer"))
    db.session.add(UserRef(sub="99", email="superadmin@example.com", name="Superadmin", role="management"))
    db.session.commit()


def test_superadmin_impersonate_baslatir(client):
    _seed_users()
    login_as(client, SUPERADMIN)
    r = client.post("/api/impersonate", json={"sub": "2"}, headers=csrf_headers(client))
    assert r.status_code == 200
    d = r.get_json()
    assert d["impersonating"] is True
    assert d["user"]["sub"] == "2" and d["user"]["role"] == "designer"
    assert d["real_user"]["email"] == "superadmin@example.com"
    # /session artık hedef kullanıcı + impersonation durumu döner
    s = client.get("/api/session").get_json()
    assert s["user"]["role"] == "designer"
    assert s["impersonating"] is True
    assert s["real_user"]["email"] == "superadmin@example.com"
    assert s["can_impersonate"] is True  # gerçek kimlik hâlâ superadmin


def test_non_superadmin_impersonate_403(client):
    """management rolü olsa bile superadmin e-postası değilse impersonate EDEMEZ."""
    _seed_users()
    login_as(client, MANAGER)  # management ama superadmin değil
    r = client.post("/api/impersonate", json={"sub": "2"}, headers=csrf_headers(client))
    assert r.status_code == 403
    s = client.get("/api/session").get_json()
    assert s["can_impersonate"] is False


def test_impersonate_edilen_yetki_yukseltemez(client):
    """Superadmin, designer'ı impersonate ederken bile /impersonate GERÇEK kimlikle
    yetkilenir (session['user']=designer olsa da real=superadmin). Designer'ın kendi
    oturumundan (impersonator YOK) ise 403."""
    _seed_users()
    # düz designer oturumu (impersonation yok) → 403
    login_as(client, DESIGNER)
    r = client.post("/api/impersonate", json={"sub": "99"}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_impersonate_stop_gercek_kimlige_doner(client):
    _seed_users()
    login_as(client, SUPERADMIN)
    client.post("/api/impersonate", json={"sub": "2"}, headers=csrf_headers(client))
    r = client.post("/api/impersonate/stop", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["impersonating"] is False
    s = client.get("/api/session").get_json()
    assert s["user"]["email"] == "superadmin@example.com"
    assert s["impersonating"] is False


def test_impersonate_stop_impersonation_yokken_400(client):
    _seed_users()
    login_as(client, SUPERADMIN)
    r = client.post("/api/impersonate/stop", headers=csrf_headers(client))
    assert r.status_code == 400


def test_impersonate_kendini_veya_yok_404(client):
    _seed_users()
    login_as(client, SUPERADMIN)
    assert client.post("/api/impersonate", json={"sub": "99"},
                       headers=csrf_headers(client)).status_code == 404  # kendisi
    assert client.post("/api/impersonate", json={"sub": "yok"},
                       headers=csrf_headers(client)).status_code == 404  # yok
