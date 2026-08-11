"""/api/sharing/upload — file upload (server→Drive). Drive layer is mocked."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def client_id(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Upload Müşteri"}, headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


@pytest.fixture
def fake_drive(monkeypatch):
    """Fake out drive_gateway.upload_file without hitting the network.

    `grant_anyone_reader` is also patched (2026-07-25: video upload now grants
    permission) — otherwise the real call would raise DriveAuthError in the env
    check and get silently swallowed, leaving the test's intent unclear. Grant
    calls are in the `calls.grants` list."""
    class _Calls(list):
        """Existing tests use `calls` like a list; subclassed for the extra
        `grants` field (can't assign an attribute to a plain list)."""
        grants = ()

    calls = _Calls()
    grants = []

    def fake_upload(folder_id, filename, data, mime):
        # The real endpoint now passes a stream (file-like object); accept bytes too.
        payload = data.read() if hasattr(data, "read") else data
        calls.append({"folder_id": folder_id, "filename": filename, "size": len(payload), "mime": mime})
        return {"id": "yeni_file_id", "name": filename, "mimeType": mime, "size": str(len(payload))}

    import drive_gateway
    monkeypatch.setattr(drive_gateway, "upload_file", fake_upload)
    monkeypatch.setattr(drive_gateway, "grant_anyone_reader", lambda fid: grants.append(fid))
    calls.grants = grants          # attach to the list object (existing tests treat calls as a list)
    return calls


def _week_folder(client_id, week_number=21, folder_id="FOLDER123"):
    from extensions import db
    from models import ClientWeekFolder
    db.session.add(ClientWeekFolder(client_id=client_id, week_number=week_number,
                                    folder_id=folder_id, name=str(week_number)))
    db.session.commit()


def _post_file(client, client_id, week_iso="2026-W21", category="post", name="a.jpg"):
    return client.post(
        "/api/sharing/upload",
        data={"client_id": str(client_id), "week_iso": week_iso, "category": category,
              "file": (io.BytesIO(b"jpegdata"), name)},
        content_type="multipart/form-data",
        headers=csrf_headers(client))


def test_upload_designer_atanmamis_da_201(client, client_id, fake_drive):
    # New behavior: designer can upload even to an unassigned client (full action).
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, DESIGNER)
    r = _post_file(client, client_id)
    assert r.status_code == 201, r.get_json()


def test_upload_designer_video_201(client, client_id, fake_drive):
    """2026-08-05: designer can also upload video, and the file goes through the VIDEOGRAPHER PATH.

    Opening the role gate alone isn't enough — the actual requirement is video handling:
    the "anyone with the link" Drive permission and the browser-compatible derivative
    (`web_variant`) job. Both of these depend on the category, not the role; this test locks that in."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, DESIGNER)
    jobs = []
    import jobqueue
    from unittest.mock import patch
    with patch.object(jobqueue, "enqueue", lambda kind, payload, **kw: jobs.append((kind, payload))):
        r = _post_file(client, client_id, category="video", name="v.mp4")
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["upload"]["category"] == "video"
    assert fake_drive.grants == ["yeni_file_id"]          # anyone-reader permission granted
    assert [k for k, _ in jobs] == ["web_variant"]        # transcode job was enqueued


def test_upload_boyut_asimi_413(client, client_id, fake_drive, monkeypatch):
    # Shrink the product limit (MAX_UPLOAD_BYTES) → even an 8-byte file exceeds it → meaningful 413.
    import sharing
    monkeypatch.setattr(sharing, "MAX_UPLOAD_BYTES", 3)
    _week_folder(client_id, 21, "F")
    r = _post_file(client, client_id)  # 'jpegdata' = 8 bytes > 3
    assert r.status_code == 413
    assert "500 MB" in r.get_json()["error"]
    assert fake_drive == []  # Drive is never reached when the limit is exceeded


def test_upload_dosyasiz_400(client, client_id, fake_drive):
    r = client.post("/api/sharing/upload",
                    data={"client_id": str(client_id), "week_iso": "2026-W21"},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 400


def test_upload_klasor_yok_400(client, client_id, fake_drive):
    # no week folder + no drive_meta → cannot be created
    r = _post_file(client, client_id)
    assert r.status_code == 400
    assert fake_drive == []  # Drive was never reached


def test_upload_kayit_olusturur(client, client_id, fake_drive):
    _week_folder(client_id, 21, "FOLDER123")
    r = _post_file(client, client_id, name="görsel.jpg")
    assert r.status_code == 201, r.get_json()
    up = r.get_json()["upload"]
    assert up["file_id"] == "yeni_file_id"
    assert up["file_name"] == "görsel.jpg"
    assert up["category"] == "post"
    # went to the correct folder
    assert fake_drive[0]["folder_id"] == "FOLDER123"
    # shows up in the uploads list
    ups = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso=2026-W21").get_json()["uploads"]
    assert any(u["file_id"] == "yeni_file_id" for u in ups)


def test_upload_hafta_klasoru_yoksa_drive_meta_kokunden_olusturur(client, client_id, fake_drive, monkeypatch):
    from extensions import db
    from models import Client, ClientWeekFolder
    import drive_gateway
    # give the client a root-folder link
    c = db.session.get(Client, client_id)
    c.drive_meta = {"client_folder_link": "https://drive.google.com/drive/folders/ROOT999"}
    db.session.commit()
    created = {}

    def fake_ensure(parent_id, name):
        created["parent"] = parent_id
        created["name"] = name
        return "YENI_HAFTA_FOLDER"
    monkeypatch.setattr(drive_gateway, "ensure_subfolder", fake_ensure)

    r = _post_file(client, client_id, week_iso="2026-W30")
    assert r.status_code == 201
    assert created == {"parent": "ROOT999", "name": "30"}
    assert fake_drive[0]["folder_id"] == "YENI_HAFTA_FOLDER"
    # a ClientWeekFolder record should also have been created
    wf = ClientWeekFolder.query.filter_by(client_id=client_id, week_number=30).first()
    assert wf and wf.folder_id == "YENI_HAFTA_FOLDER"


VIDEOGRAPHER = {"sub": "7", "email": "video@test.com", "name": "Videocu", "role": "videographer"}


def test_upload_videographer_atanmamis_video_201(client, client_id, fake_drive):
    """2026-07-21 designer parity: a videographer can also upload video to an UNASSIGNED client."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="video", name="v.mp4")
    assert r.status_code == 201, r.get_json()


def test_upload_videographer_video_disi_403(client, client_id, fake_drive):
    """A videographer can only upload the video category; post is forbidden."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="post")
    assert r.status_code == 403


def test_videographer_cards_rol_ve_assigned(client, client_id):
    """/videographer/cards: videographer can access, assigned flag is correct; designer gets 403."""
    from extensions import db
    from models import ClientTeamAssignment
    login_as(client, VIDEOGRAPHER)
    r = client.get("/api/sharing/videographer/cards?week_iso=2026-W21")
    assert r.status_code == 200
    rows = r.get_json()["rows"]
    assert all(row["assigned"] is False for row in rows)   # no assignment yet
    db.session.add(ClientTeamAssignment(client_id=client_id, role_slot="videographer_shoot",
                                        user_id=VIDEOGRAPHER["sub"]))
    db.session.commit()
    rows = client.get("/api/sharing/videographer/cards?week_iso=2026-W21").get_json()["rows"]
    assert any(row["assigned"] and row["client"]["id"] == client_id for row in rows)
    login_as(client, DESIGNER)
    assert client.get("/api/sharing/videographer/cards?week_iso=2026-W21").status_code == 403


def test_board_video_uploads_karti(client, client_id, fake_drive):
    """The management board row returns this week's video uploads via `video_uploads`."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="video", name="tanitim.mp4")
    assert r.status_code == 201
    login_as(client, MANAGER)
    rows = client.get("/api/sharing/cards?week_iso=2026-W21").get_json()["rows"]
    row = next(x for x in rows if x["client"]["id"] == client_id)
    assert len(row["video_uploads"]) == 1
    assert row["video_uploads"][0]["file_name"] == "tanitim.mp4"
    assert row["video_uploads"][0]["file_id"]


def test_upload_lokal_kopya_yazar(client, client_id, fake_drive):
    # If the Drive upload succeeds, a 21-day server-side copy is created in the same request.
    import media_store
    _week_folder(client_id, 21, "FOLDER123")
    r = _post_file(client, client_id, name="lokal.jpg")
    assert r.status_code == 201
    path = media_store.find_original("yeni_file_id")  # the id returned by fake_drive
    assert path is not None
    with open(path, "rb") as f:
        assert f.read() == b"jpegdata"


# --- Drive permission (2026-07-25): copied link should also open outside the agency ---

def test_video_yuklemesi_izin_alir(client, client_id, fake_drive):
    from conftest import VIDEOGRAPHER
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="video", name="a.mp4")
    assert r.status_code == 201, r.get_json()
    assert list(fake_drive.grants) == ["yeni_file_id"]


def test_gorsel_yuklemesi_izin_almaz(client, client_id, fake_drive):
    """Images go through the proxy (/api/sharing/media/<id>) — no need to make them public."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, DESIGNER)
    assert _post_file(client, client_id, category="post").status_code == 201
    assert list(fake_drive.grants) == []


def test_izin_hatasi_yuklemeyi_bozmaz(client, client_id, fake_drive, monkeypatch):
    """Permission is best-effort: must still return 201 even if the Drive permission call fails."""
    import drive_gateway
    from conftest import VIDEOGRAPHER

    def boom(fid):
        raise drive_gateway.DriveError("izin yok")

    monkeypatch.setattr(drive_gateway, "grant_anyone_reader", boom)
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    assert _post_file(client, client_id, category="video", name="a.mp4").status_code == 201


# --- `used` flag: a file that already has a card is hidden in the ShareModal picker ------
# ShareModal's file pool filters by this flag, so a second card can't be opened on the
# same file and produce duplicate content (per the project owner, 2026-07-29 — a DRAFT
# card counts too). Uploads are built directly: `fake_drive` returns the same `file_id`
# on every call, so distinct files couldn't be told apart via the real upload endpoint.

def _card_upload(client_id, file_id, week="2026-W31", name=None, category="post"):
    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    cu = CardUpload(client_id=client_id, week_iso=week, category=category,
                    file_id=file_id, file_name=name or f"{file_id}.jpg",
                    mime_type="image/jpeg", file_size=10, uploaded_at=utcnow())
    db.session.add(cu)
    db.session.commit()
    return cu


def _mk_share(client, client_id, file_id, week="2026-W31"):
    r = client.post("/api/sharing/shares", headers=csrf_headers(client), json={
        "client_id": client_id, "week_iso": week, "kind": "post", "file_id": file_id})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["share"]


def _uploads(client, client_id, week="2026-W31"):
    r = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso={week}")
    assert r.status_code == 200
    return {u["file_id"]: u for u in r.get_json()["uploads"]}


def test_used_kartsiz_dosyada_false(client, client_id):
    login_as(client, MANAGER)
    _card_upload(client_id, "serbest")
    assert _uploads(client, client_id)["serbest"]["used"] is False


def test_used_taslak_kart_da_isaretler(client, client_id):
    """A draft card also counts as 'used' — not being published yet doesn't make
    the file reselectable, otherwise two cards would show the same file."""
    login_as(client, MANAGER)
    _card_upload(client_id, "taslakli")
    s = _mk_share(client, client_id, "taslakli")
    assert s["status"] == "draft"
    assert _uploads(client, client_id)["taslakli"]["used"] is True


def test_used_kart_silinince_geri_doner(client, client_id):
    """When a card is deleted, the file should become reselectable — `used` accounts for soft-delete."""
    login_as(client, MANAGER)
    _card_upload(client_id, "geridonen")
    sid = _mk_share(client, client_id, "geridonen")["id"]
    assert _uploads(client, client_id)["geridonen"]["used"] is True
    assert client.delete(f"/api/sharing/shares/{sid}",
                         headers=csrf_headers(client)).status_code == 200
    assert _uploads(client, client_id)["geridonen"]["used"] is False


def test_used_baska_haftadaki_kart_da_sayilir(client, client_id):
    """A file may have been moved to another week via `move_files`; its card stays there
    but it's still used → the query is WEEK-INDEPENDENT."""
    login_as(client, MANAGER)
    _card_upload(client_id, "tasinan", week="2026-W31")
    _mk_share(client, client_id, "tasinan", week="2026-W32")
    assert _uploads(client, client_id, week="2026-W31")["tasinan"]["used"] is True


def test_used_diger_dosyalari_etkilemez(client, client_id):
    login_as(client, MANAGER)
    _card_upload(client_id, "kullanilan")
    _card_upload(client_id, "bos")
    _mk_share(client, client_id, "kullanilan")
    ups = _uploads(client, client_id)
    assert ups["kullanilan"]["used"] is True
    assert ups["bos"]["used"] is False


def test_uploads_sorgu_sayisi_dosya_sayisindan_bagimsiz(client, client_id):
    """If `used` were resolved per-upload with `Share.query`, it would be N+1 — a
    single batched query is required. Query count for 3 files vs 12 files must be THE SAME."""
    from sqlalchemy import event

    from extensions import db
    login_as(client, MANAGER)

    def say(n, week):
        for i in range(n):
            _card_upload(client_id, f"{week}-d{i}", week=week)
        sayac = []
        eng = db.engine

        def before(conn, cur, stmt, params, ctx, many):  # noqa: ANN001
            sayac.append(stmt)

        event.listen(eng, "before_cursor_execute", before)
        try:
            client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso={week}")
        finally:
            event.remove(eng, "before_cursor_execute", before)
        return len(sayac)

    assert say(3, "2026-W40") == say(12, "2026-W41")


def test_uploads_bos_listede_share_sorgusu_atilmaz(client, client_id):
    """When there are no files at all, the Share query is skipped to avoid producing `IN ()`."""
    login_as(client, MANAGER)
    r = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso=2026-W52")
    assert r.get_json()["uploads"] == []
