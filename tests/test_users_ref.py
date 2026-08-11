"""users_ref projection: upsert on login + /api/users listing."""
from conftest import DESIGNER, MANAGER, login_as


def test_users_listesi_oturumsuz_401(client):
    assert client.get("/api/users").status_code == 401


def test_upsert_ve_listeleme(client, app):
    from extensions import db
    from models import upsert_user_ref

    upsert_user_ref({"sub": "1", "email": "a@test.com", "name": "A", "role": "management"})
    upsert_user_ref({"sub": "2", "email": "b@test.com", "name": "B", "role": "designer"})
    db.session.commit()

    login_as(client, DESIGNER)
    r = client.get("/api/users")
    assert r.status_code == 200
    users = r.get_json()["users"]
    assert {(u["sub"], u["role"]) for u in users} == {("1", "management"), ("2", "designer")}


def test_upsert_ayni_sub_gunceller(client):
    from extensions import db
    from models import upsert_user_ref

    upsert_user_ref({"sub": "1", "email": "a@test.com", "name": "Eski Ad", "role": "designer"})
    db.session.commit()
    upsert_user_ref({"sub": "1", "email": "a@test.com", "name": "Yeni Ad", "role": "management"})
    db.session.commit()

    login_as(client, MANAGER)
    users = client.get("/api/users").get_json()["users"]
    assert len(users) == 1
    assert users[0]["name"] == "Yeni Ad" and users[0]["role"] == "management"
