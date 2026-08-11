"""Bulk designer assignment — POST /api/clients/assign-designer."""
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


def _client(client, name):
    return client.post("/api/clients", json={"name": name},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _designer_of(client, cid):
    d = client.get(f"/api/clients/{cid}").get_json()["client"]
    return d["team_assignments"].get("designer")


def _assign(client, items):
    return client.post("/api/clients/assign-designer",
                       json={"assignments": items}, headers=csrf_headers(client))


def test_toplu_atama_ve_yeniden_atama(client):
    login_as(client, MANAGER)
    a, b = _client(client, "A"), _client(client, "B")
    r = _assign(client, [{"client_id": a, "user_id": "u1"}, {"client_id": b, "user_id": "u1"}])
    assert r.status_code == 200 and r.get_json()["updated"] == 2
    assert _designer_of(client, a) == "u1" and _designer_of(client, b) == "u1"
    # reassign (a → u2)
    _assign(client, [{"client_id": a, "user_id": "u2"}])
    assert _designer_of(client, a) == "u2" and _designer_of(client, b) == "u1"


def test_atama_kaldir_user_id_null(client):
    login_as(client, MANAGER)
    a = _client(client, "A")
    _assign(client, [{"client_id": a, "user_id": "u1"}])
    assert _designer_of(client, a) == "u1"
    _assign(client, [{"client_id": a, "user_id": None}])
    assert _designer_of(client, a) is None


def test_diger_slotlar_korunur(client):
    login_as(client, MANAGER)
    a = _client(client, "A")
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=a, role_slot="videographer_shoot", user_id="vg1"))
    db.session.commit()
    _assign(client, [{"client_id": a, "user_id": "u1"}])
    d = client.get(f"/api/clients/{a}").get_json()["client"]["team_assignments"]
    assert d.get("designer") == "u1" and d.get("videographer_shoot") == "vg1"


def test_bilinmeyen_client_atlanir(client):
    login_as(client, MANAGER)
    a = _client(client, "A")
    r = _assign(client, [{"client_id": a, "user_id": "u1"}, {"client_id": 999999, "user_id": "u1"}])
    assert r.get_json()["updated"] == 1


def test_assignments_liste_degilse_400(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients/assign-designer", json={"assignments": "x"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_designer_yetkisiz_403(client):
    login_as(client, MANAGER)
    a = _client(client, "A")
    login_as(client, DESIGNER)
    r = _assign(client, [{"client_id": a, "user_id": "u1"}])
    assert r.status_code == 403
