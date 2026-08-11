"""Designer board — scoped cards + scoped upload."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"
PENDING = {"sub": "9", "email": "p@test.com", "name": "P", "role": "pending"}


def _client(client, name):
    return client.post("/api/clients", json={"name": name},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _assign_designer(client_id, sub=DESIGNER["sub"]):
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=client_id, role_slot="designer", user_id=sub))
    db.session.commit()


@pytest.fixture
def two_clients(client):
    login_as(client, MANAGER)
    a = _client(client, "Atanan")
    b = _client(client, "Atanmayan")
    _assign_designer(a)
    return a, b


def test_designer_cards_pending_403(client, two_clients):
    login_as(client, PENDING)
    assert client.get(f"/api/sharing/designer/cards?week_iso={WK}").status_code == 403


def test_designer_cards_hepsini_gorur_assigned_bayragi(client, two_clients):
    # New behavior: designer sees ALL clients; assigned ones have assigned=True
    # ("My Clients"), unassigned ones have assigned=False ("Other Clients").
    a, b = two_clients
    login_as(client, DESIGNER)
    rows = client.get(f"/api/sharing/designer/cards?week_iso={WK}").get_json()["rows"]
    by_id = {r["client"]["id"]: r for r in rows}
    assert a in by_id and b in by_id
    assert by_id[a]["assigned"] is True
    assert by_id[b]["assigned"] is False


def test_designer_cards_management_hepsi_assigned_true(client, two_clients):
    a, b = two_clients
    login_as(client, MANAGER)
    rows = client.get(f"/api/sharing/designer/cards?week_iso={WK}").get_json()["rows"]
    by_id = {r["client"]["id"]: r for r in rows}
    assert a in by_id and b in by_id
    # No distinction in the management view — all are assigned=True.
    assert by_id[a]["assigned"] is True and by_id[b]["assigned"] is True


@pytest.fixture
def fake_drive(monkeypatch):
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "upload_file",
                        lambda *a, **k: {"id": "nf", "name": a[1], "mimeType": a[3], "size": "3"})


def _upload(client, cid, category="post", name="a.jpg"):
    from extensions import db
    from models import ClientWeekFolder
    if not ClientWeekFolder.query.filter_by(client_id=cid, week_number=21).first():
        db.session.add(ClientWeekFolder(client_id=cid, week_number=21, folder_id="F1"))
        db.session.commit()
    return client.post("/api/sharing/upload",
                       data={"client_id": str(cid), "week_iso": WK, "category": category,
                             "file": (io.BytesIO(b"x"), name)},
                       content_type="multipart/form-data", headers=csrf_headers(client))


def test_designer_upload_atanan_201(client, two_clients, fake_drive):
    a, b = two_clients
    login_as(client, DESIGNER)
    assert _upload(client, a).status_code == 201


def test_designer_upload_atanmayan_da_201(client, two_clients, fake_drive):
    # New behavior: designer can also upload for an unassigned client (full action).
    a, b = two_clients
    login_as(client, DESIGNER)
    assert _upload(client, b).status_code == 201


def test_designer_upload_video_201(client, two_clients, fake_drive):
    """2026-08-05: the video restriction was removed — designer can also upload video
    for an unassigned client (same 'full action' decision as post/story)."""
    a, b = two_clients
    login_as(client, DESIGNER)
    assert _upload(client, a, category="video", name="v.mp4").status_code == 201
    assert _upload(client, b, category="video", name="v2.mp4").status_code == 201


def test_designer_review_link_uretebilir(client, two_clients):
    # Designer can generate a review link — even for an unassigned client (b) (full action).
    a, b = two_clients
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/review-link",
                    json={"client_id": b, "week_iso": WK}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["token"]
