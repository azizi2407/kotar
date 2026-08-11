"""Drive folder link + previous/next week unpublished-content move modal."""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    return client.post("/api/clients", json={"name": "Taşı Müşteri"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _week_folder(client_id, week_number, folder_id):
    from extensions import db
    from models import ClientWeekFolder
    db.session.add(ClientWeekFolder(client_id=client_id, week_number=week_number,
                                    folder_id=folder_id, name=str(week_number)))
    db.session.commit()


# --- Drive folder link on the board row ---

def test_cards_drive_folder_url(client, cid):
    _week_folder(cid, 21, "WK21FOLDER")
    rows = client.get("/api/sharing/cards?week_iso=2026-W21").get_json()["rows"]
    row = next(r for r in rows if r["client"]["id"] == cid)
    assert row["drive_folder_url"] and "WK21FOLDER" in row["drive_folder_url"]


def test_cards_drive_folder_url_yoksa_none(client, cid):
    rows = client.get("/api/sharing/cards?week_iso=2026-W21").get_json()["rows"]
    row = next(r for r in rows if r["client"]["id"] == cid)
    assert row["drive_folder_url"] is None


# --- movable-files: unpublished files in the previous/next week's folder ---

def test_movable_files_designer_403(client, cid):
    login_as(client, DESIGNER)
    assert client.get(f"/api/sharing/movable-files?client_id={cid}&week_iso=2026-W21").status_code == 403


def test_movable_files_onceki_sonraki(client, cid, monkeypatch):
    import drive_gateway
    _week_folder(cid, 20, "WK20")  # previous week
    _week_folder(cid, 22, "WK22")  # next week
    files = {
        "WK20": [{"id": "f1", "name": "a.jpg", "mimeType": "image/jpeg"},
                 {"id": "f2", "name": "b.jpg", "mimeType": "image/jpeg"}],
        "WK22": [{"id": "f3", "name": "c.jpg", "mimeType": "image/jpeg"}],
    }
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "list_files", lambda fid, media_only=False: files.get(fid, []))
    # f1 is published → counts as shared, drops out of the list
    from extensions import db
    from models_sharing import Share
    db.session.add(Share(client_id=cid, week_iso="2026-W20", kind="post",
                         status="published", file_id="f1"))
    db.session.commit()
    r = client.get(f"/api/sharing/movable-files?client_id={cid}&week_iso=2026-W21")
    assert r.status_code == 200
    d = r.get_json()
    prev_ids = [f["id"] for f in d["previous"]["files"]]
    assert prev_ids == ["f2"]  # f1 was excluded because it's shared
    assert d["previous"]["week_iso"] == "2026-W20"
    assert [f["id"] for f in d["next"]["files"]] == ["f3"]


# --- move-files: move selected items to this week ---

def test_move_files_designer_403(client, cid):
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/move-files",
                    json={"client_id": cid, "from_week_iso": "2026-W20",
                          "to_week_iso": "2026-W21", "file_ids": ["f2"]},
                    headers=csrf_headers(client))
    assert r.status_code == 403


def test_move_files_tasir_ve_gunceller(client, cid, monkeypatch):
    import drive_gateway
    from extensions import db
    from models_sharing import CardUpload
    _week_folder(cid, 20, "WK20")
    _week_folder(cid, 21, "WK21")
    # CardUpload record of the file to be moved (previous week)
    db.session.add(CardUpload(client_id=cid, week_iso="2026-W20", category="post",
                             file_id="f2", file_name="b.jpg"))
    db.session.commit()
    moves = []
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "move_file",
                        lambda file_id, new_parent, old_parent=None:
                        moves.append((file_id, new_parent, old_parent)) or {"id": file_id})
    r = client.post("/api/sharing/move-files",
                    json={"client_id": cid, "from_week_iso": "2026-W20",
                          "to_week_iso": "2026-W21", "file_ids": ["f2"]},
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["moved"] == 1
    # correct parents
    assert moves == [("f2", "WK21", "WK20")]
    # CardUpload was updated
    up = CardUpload.query.filter_by(file_id="f2").first()
    assert up.week_iso == "2026-W21" and up.moved_from_week_iso == "2026-W20"
    assert up.moved_at is not None
