"""Çekim planı (shoot-plan) API — videographer + management scoped."""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"
MON = "2026-05-18"  # 2026-W21 pazartesi
TUE = "2026-05-19"
VG = {"sub": "3", "email": "vg@test.com", "name": "Videografçı", "role": "videographer"}


def _client(client, name):
    return client.post("/api/clients", json={"name": name},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _assign_vg(client_id, sub=VG["sub"], slot="videographer_shoot"):
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=client_id, role_slot=slot, user_id=sub))
    db.session.commit()


@pytest.fixture
def env(client):
    login_as(client, MANAGER)
    a = _client(client, "VG Atanan")
    b = _client(client, "VG Atanmayan")
    _assign_vg(a)
    return a, b


def _create(client, client_id, scheduled_date=MON):
    return client.post("/api/sharing/shoot-plan",
                       json={"client_id": client_id, "scheduled_date": scheduled_date},
                       headers=csrf_headers(client))


def test_shoot_plan_designer_403(client, env):
    login_as(client, DESIGNER)
    assert client.get(f"/api/sharing/shoot-plan?week_iso={WK}").status_code == 403


def test_shoot_create_management(client, env):
    a, b = env
    r = _create(client, a)
    assert r.status_code == 201
    assert r.get_json()["task"]["scheduled_date"] == MON


def test_shoot_create_videographer_atanan(client, env):
    a, b = env
    login_as(client, VG)
    assert _create(client, a).status_code == 201


def test_shoot_create_videographer_atanmayan_403(client, env):
    a, b = env
    login_as(client, VG)
    assert _create(client, b).status_code == 403


def test_shoot_plan_get_gunler_ve_havuz(client, env):
    a, b = env
    _create(client, a, MON)
    _create(client, a, TUE)
    d = client.get(f"/api/sharing/shoot-plan?week_iso={WK}").get_json()
    assert len(d["days"]) == 7
    assert d["days"][0]["date"] == MON
    day_mon = next(x for x in d["days"] if x["date"] == MON)
    assert len(day_mon["tasks"]) == 1
    assert day_mon["tasks"][0]["client_name"] == "VG Atanan"
    # havuz aktif müşterileri içerir
    assert any(c["id"] == a for c in d["pool"])


def test_shoot_reschedule(client, env):
    a, b = env
    tid = _create(client, a, MON).get_json()["task"]["id"]
    r = client.patch(f"/api/sharing/shoot-plan/{tid}",
                     json={"scheduled_date": TUE}, headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["task"]["scheduled_date"] == TUE


def test_shoot_reorder(client, env):
    a, b = env
    t1 = _create(client, a, MON).get_json()["task"]["id"]
    t2 = _create(client, a, MON).get_json()["task"]["id"]
    r = client.patch("/api/sharing/shoot-plan/order",
                     json={"task_ids": [t2, t1]}, headers=csrf_headers(client))
    assert r.status_code == 200
    day = next(x for x in client.get(f"/api/sharing/shoot-plan?week_iso={WK}").get_json()["days"]
               if x["date"] == MON)
    assert [t["id"] for t in day["tasks"]] == [t2, t1]


def test_shoot_done_toggle(client, env):
    a, b = env
    tid = _create(client, a).get_json()["task"]["id"]
    r = client.post(f"/api/sharing/shoot-plan/{tid}/done", headers=csrf_headers(client))
    assert r.get_json()["task"]["status"] == "completed"
    r2 = client.post(f"/api/sharing/shoot-plan/{tid}/done", headers=csrf_headers(client))
    assert r2.get_json()["task"]["status"] == "pending"


def test_shoot_delete(client, env):
    a, b = env
    tid = _create(client, a).get_json()["task"]["id"]
    assert client.delete(f"/api/sharing/shoot-plan/{tid}", headers=csrf_headers(client)).status_code == 200
    day = next(x for x in client.get(f"/api/sharing/shoot-plan?week_iso={WK}").get_json()["days"]
               if x["date"] == MON)
    assert day["tasks"] == []
