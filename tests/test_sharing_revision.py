"""/api/sharing revision request flow + auto-resolve + board state."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"


@pytest.fixture
def ctx(client):
    """management + client + a published post share. Returns (client_id, share_id)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Rev Müşteri"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    s = client.post("/api/sharing/shares",
                    json={"client_id": cid, "week_iso": WK, "kind": "post", "file_id": "f1"},
                    headers=csrf_headers(client)).get_json()["share"]
    return cid, s["id"]


def _req(client, cid, **kw):
    body = {"client_id": cid, "week_iso": WK, "kind": "design"}
    body.update(kw)
    return client.post("/api/sharing/revision-request", json=body, headers=csrf_headers(client))


# --- visibility rule (_visible_shares) ---

class _S:
    """Share fake — _visible_shares is a pure function, no DB needed."""
    def __init__(self, id, kind="video", status="draft", revision=0):
        self.id, self.kind, self.status, self.revision = id, kind, status, revision


def test_esit_revizyondaki_farkli_videolar_hepsi_gorunur():
    """Regression: at equal revision, `max()` left only one video → 2 of 3 videos
    were disappearing from both the board and the client approval page."""
    from sharing import _visible_shares
    shares = [_S(1), _S(2), _S(3)]
    assert sorted(s.id for s in _visible_shares(shares)) == [1, 2, 3]


def test_eski_revizyondaki_taslak_video_gizlenir():
    from sharing import _visible_shares
    shares = [_S(1, revision=1), _S(2, revision=2), _S(3, revision=2)]
    assert sorted(s.id for s in _visible_shares(shares)) == [2, 3]


def test_yayinlanmis_video_revizyondan_bagimsiz_korunur():
    from sharing import _visible_shares
    shares = [_S(1, status="published", revision=0), _S(2, revision=5)]
    assert sorted(s.id for s in _visible_shares(shares)) == [1, 2]


def test_video_disi_kindler_dokunulmaz():
    from sharing import _visible_shares
    shares = [_S(1, kind="post"), _S(2, kind="story"), _S(3, kind="video")]
    assert sorted(s.id for s in _visible_shares(shares)) == [1, 2, 3]


def test_revision_request_designer_403(client, ctx):
    cid, sid = ctx
    login_as(client, DESIGNER)
    assert _req(client, cid, share_id=sid).status_code == 403


def test_revision_request_gecersiz_kind_400(client, ctx):
    cid, sid = ctx
    assert _req(client, cid, kind="foo").status_code == 400


def test_revision_request_olustur(client, ctx):
    cid, sid = ctx
    r = _req(client, cid, share_id=sid, note="logo büyüsün")
    assert r.status_code == 201
    rev = r.get_json()["revision"]
    assert rev["status"] == "open" and rev["kind"] == "design"
    assert rev["share_id"] == sid


def test_revisions_listesi_ve_resolve(client, ctx):
    cid, sid = ctx
    rid = _req(client, cid, share_id=sid).get_json()["revision"]["id"]
    lst = client.get("/api/sharing/revisions?status=open").get_json()["revisions"]
    assert any(x["id"] == rid for x in lst)
    r = client.post(f"/api/sharing/revisions/{rid}/resolve", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["revision"]["status"] == "resolved"
    # no longer in the open list
    lst2 = client.get("/api/sharing/revisions?status=open").get_json()["revisions"]
    assert not any(x["id"] == rid for x in lst2)


def test_cards_acik_revizyon_gosterir(client, ctx):
    cid, sid = ctx
    _req(client, cid, share_id=sid)
    row = next(x for x in client.get(f"/api/sharing/cards?week_iso={WK}").get_json()["rows"]
               if x["client"]["id"] == cid)
    assert row["open_revision_count"] == 1
    assert sid in row["revision_share_ids"]


def test_upload_design_revizesini_auto_resolve(client, ctx, monkeypatch):
    cid, sid = ctx
    from extensions import db
    from models import ClientWeekFolder
    import drive_gateway
    db.session.add(ClientWeekFolder(client_id=cid, week_number=21, folder_id="F1"))
    db.session.commit()
    monkeypatch.setattr(drive_gateway, "upload_file",
                        lambda *a, **k: {"id": "nf", "name": a[1], "mimeType": a[3], "size": "3"})
    rid = _req(client, cid, share_id=sid, kind="design").get_json()["revision"]["id"]
    # a design upload (category post) → the open design revision closes
    client.post("/api/sharing/upload",
                data={"client_id": str(cid), "week_iso": WK, "category": "post",
                      "file": (io.BytesIO(b"img"), "x.jpg")},
                content_type="multipart/form-data", headers=csrf_headers(client))
    from models_sharing import RevisionRequest
    assert db.session.get(RevisionRequest, rid).status == "resolved"


def test_upload_video_design_revizesini_kapatmaz(client, ctx, monkeypatch):
    cid, sid = ctx
    from extensions import db
    from models import ClientWeekFolder
    import drive_gateway
    db.session.add(ClientWeekFolder(client_id=cid, week_number=21, folder_id="F1"))
    db.session.commit()
    monkeypatch.setattr(drive_gateway, "upload_file",
                        lambda *a, **k: {"id": "nf", "name": a[1], "mimeType": a[3], "size": "3"})
    rid = _req(client, cid, kind="design").get_json()["revision"]["id"]
    # a video upload must NOT close a design revision
    client.post("/api/sharing/upload",
                data={"client_id": str(cid), "week_iso": WK, "category": "video",
                      "file": (io.BytesIO(b"vid"), "v.mp4")},
                content_type="multipart/form-data", headers=csrf_headers(client))
    from models_sharing import RevisionRequest
    assert db.session.get(RevisionRequest, rid).status == "open"
