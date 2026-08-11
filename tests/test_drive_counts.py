"""drive-counts batch endpoint (fix for the board-open thundering herd)."""
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


def test_drive_counts_designer_403(client):
    login_as(client, DESIGNER)
    assert client.get("/api/sharing/drive-counts?week_iso=2026-W21").status_code == 403


def test_drive_counts_batch(client, monkeypatch):
    import drive_gateway
    from extensions import db
    from models import Client, ClientWeekFolder
    login_as(client, MANAGER)
    a = client.post("/api/clients", json={"name": "A"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    b = client.post("/api/clients", json={"name": "B"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    db.session.add(ClientWeekFolder(client_id=a, week_number=21, folder_id="FA"))
    db.session.add(ClientWeekFolder(client_id=b, week_number=21, folder_id="FB"))
    db.session.commit()
    # fake the count + fake Drive "available"
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "count_files", lambda fid: {"FA": 4, "FB": 7}.get(fid))
    # clear the cache (don't let it carry over from previous tests)
    import sharing
    sharing._counts_cache.clear()
    r = client.get("/api/sharing/drive-counts?week_iso=2026-W21")
    assert r.status_code == 200
    counts = r.get_json()["counts"]
    assert counts[str(a)] == 4 and counts[str(b)] == 7


def test_drive_counts_drive_yoksa_bos(client, monkeypatch):
    import drive_gateway, sharing
    login_as(client, MANAGER)
    monkeypatch.setattr(drive_gateway, "available", lambda: False)
    sharing._counts_cache.clear()
    r = client.get("/api/sharing/drive-counts?week_iso=2026-W40")
    assert r.status_code == 200 and r.get_json()["counts"] == {}
