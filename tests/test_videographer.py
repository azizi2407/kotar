"""Videographer businesses (video flag) + photos endpoints."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"
VG = {"sub": "3", "email": "vg@t.com", "name": "V", "role": "videographer"}


@pytest.fixture
def fake_photo_drive(monkeypatch):
    """Mock the Drive layer for photo upload (subfolder + upload + permission)."""
    calls = {"ensure": [], "upload": [], "grant": []}

    def fake_ensure(parent_id, name):
        calls["ensure"].append({"parent": parent_id, "name": name})
        return "PHOTOS_FOLDER"

    def fake_upload(folder_id, filename, data, mime):
        # The real endpoint now passes a stream (file object); also accept bytes.
        payload = data.read() if hasattr(data, "read") else data
        calls["upload"].append({"folder_id": folder_id, "filename": filename, "size": len(payload)})
        return {"id": f"file_{len(calls['upload'])}", "name": filename, "mimeType": mime, "size": str(len(payload))}

    def fake_grant(file_id):
        calls["grant"].append(file_id)

    import drive_gateway
    monkeypatch.setattr(drive_gateway, "ensure_subfolder", fake_ensure)
    monkeypatch.setattr(drive_gateway, "upload_file", fake_upload)
    monkeypatch.setattr(drive_gateway, "grant_anyone_reader", fake_grant)
    return calls


def _mk_client(client, name="Foto Müşteri", drive_root=None):
    cid = client.post("/api/clients", json={"name": name}, headers=csrf_headers(client)).get_json()["client"]["id"]
    if drive_root:
        from extensions import db
        from models import Client
        c = db.session.get(Client, cid)
        c.drive_meta = {"client_folder_link": f"https://drive.google.com/drive/folders/{drive_root}"}
        db.session.commit()
    return cid


def _post_photo(client, cid, name="cekim.jpg", shoot_date="2026-05-20"):
    return client.post(
        "/api/sharing/videographer/photos/upload",
        data={"client_id": str(cid), "shoot_date": shoot_date,
              "files": (io.BytesIO(b"jpgbytes"), name)},
        content_type="multipart/form-data", headers=csrf_headers(client))


def test_photo_upload_designer_403(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    login_as(client, DESIGNER)
    assert _post_photo(client, cid).status_code == 403


def test_photo_upload_boyut_asimi_atlanir(client, fake_photo_drive, monkeypatch):
    # Photo over 500 MB: goes into the error list, not uploaded to Drive (others continue).
    import sharing
    monkeypatch.setattr(sharing, "MAX_UPLOAD_BYTES", 3)
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    r = _post_photo(client, cid).get_json()  # 'jpgbytes' = 8 bytes > 3
    assert r["saved"] == [] and any("500 MB" in e for e in r["errors"])
    assert fake_photo_drive["upload"] == []


def test_photo_upload_drive_koku_yok_400(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client)  # no drive_meta
    r = _post_photo(client, cid)
    assert r.status_code == 400
    assert fake_photo_drive["upload"] == []  # didn't go to Drive


def test_photo_upload_olusturur_ve_public(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    r = _post_photo(client, cid, name="fotoğraf.jpg")
    assert r.status_code == 201, r.get_json()
    saved = r.get_json()["saved"]
    assert len(saved) == 1 and saved[0]["file_name"] == "fotoğraf.jpg"
    # "Çekim Fotoğrafları" subfolder was created under the root
    assert fake_photo_drive["ensure"][0]["parent"] == "ROOT1"
    assert fake_photo_drive["upload"][0]["folder_id"] == "PHOTOS_FOLDER"
    assert fake_photo_drive["grant"]  # public reader permission was granted
    # shows up in the list
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"]
    assert any(x["file_name"] == "fotoğraf.jpg" for x in p)


def test_photo_upload_designer_karta_klasor_linki(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    _post_photo(client, cid)
    rows = client.get(f"/api/sharing/designer/cards?week_iso={WK}").get_json()["rows"]
    row = next(r for r in rows if r["client"]["id"] == cid)
    assert row["photos_folder_url"] and "PHOTOS_FOLDER" in row["photos_folder_url"]


def test_photo_delete_soft(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    pid = _post_photo(client, cid).get_json()["saved"][0]["id"]
    assert client.delete(f"/api/sharing/videographer/photos/{pid}",
                         headers=csrf_headers(client)).status_code == 200
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"]
    assert p == []


def test_businesses_designer_403(client):
    login_as(client, DESIGNER)
    assert client.get(f"/api/sharing/videographer/businesses?week_iso={WK}").status_code == 403


def test_businesses_liste_ve_mark(client):
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "İşletme"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    # initially unmarked
    b = client.get(f"/api/sharing/videographer/businesses?week_iso={WK}").get_json()["businesses"]
    row = next(x for x in b if x["client_id"] == cid)
    assert row["has_video"] is False
    # videographer marks it
    login_as(client, VG)
    r = client.post("/api/sharing/videographer/businesses/mark",
                    json={"client_id": cid, "week_iso": WK, "has_video": True},
                    headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["has_video"] is True
    b2 = client.get(f"/api/sharing/videographer/businesses?week_iso={WK}").get_json()["businesses"]
    assert next(x for x in b2 if x["client_id"] == cid)["has_video"] is True


def test_photos_liste(client):
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Foto Müşteri"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    from extensions import db
    from models_sharing import VideographerPhoto
    db.session.add(VideographerPhoto(client_id=cid, shoot_date="2026-05-20",
                                     file_id="PH1", file_name="cekim.jpg"))
    db.session.commit()
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"]
    assert len(p) == 1 and p[0]["file_id"] == "PH1" and p[0]["client_name"] == "Foto Müşteri"


# --- photo page: usage flag + bulk delete + zip (2026-07-19) ---

def _seed_photos(cid, n=2):
    from extensions import db
    from models_sharing import VideographerPhoto
    ids = []
    for i in range(n):
        p = VideographerPhoto(client_id=cid, shoot_date="2026-05-20",
                              file_id=f"PH{i}", file_name=f"cekim{i}.jpg", file_size=1000 + i)
        db.session.add(p)
        db.session.flush()
        ids.append(p.id)
    db.session.commit()
    return ids


def test_photo_get_used_ve_size_alanlari(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    _seed_photos(cid, 1)
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"][0]
    assert p["used"] is False and p["used_at"] is None and p["file_size"] == 1000


def test_mark_used_designer_ve_geri_al(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]
    # designer marks as used
    login_as(client, DESIGNER)
    r = client.post(f"/api/sharing/videographer/photos/{pid}/used",
                    json={"used": True}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["used"] is True
    # GET endpoint is management/videographer; switch back to MANAGER for verification
    login_as(client, MANAGER)
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"][0]
    assert p["used"] is True and p["used_at"]
    # undo (designer)
    login_as(client, DESIGNER)
    r2 = client.post(f"/api/sharing/videographer/photos/{pid}/used",
                     json={"used": False}, headers=csrf_headers(client))
    assert r2.get_json()["used"] is False
    login_as(client, MANAGER)
    p2 = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"][0]
    assert p2["used"] is False and p2["used_at"] is None


def test_mark_used_videographer_403(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]
    login_as(client, VG)
    r = client.post(f"/api/sharing/videographer/photos/{pid}/used",
                    json={"used": True}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_bulk_delete_management(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    ids = _seed_photos(cid, 3)
    r = client.post("/api/sharing/videographer/photos/bulk-delete",
                    json={"ids": ids}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["deleted"] == 3
    assert client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"] == []


def test_bulk_delete_eksik_id_errors(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    ids = _seed_photos(cid, 1)
    r = client.post("/api/sharing/videographer/photos/bulk-delete",
                    json={"ids": ids + [99999]}, headers=csrf_headers(client)).get_json()
    assert r["deleted"] == 1 and any("99999" in e for e in r["errors"])


def test_bulk_delete_videographer_yetkisiz_atlar(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    ids = _seed_photos(cid, 2)
    # unassigned videographer → _can_shoot False → nothing gets deleted
    login_as(client, VG)
    r = client.post("/api/sharing/videographer/photos/bulk-delete",
                    json={"ids": ids}, headers=csrf_headers(client)).get_json()
    assert r["deleted"] == 0 and len(r["errors"]) == 2


def test_download_zip(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    ids = _seed_photos(cid, 2)
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "download_file", lambda fid: b"IMGBYTES-" + fid.encode())
    r = client.post("/api/sharing/videographer/photos/download-zip",
                    json={"ids": ids}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.mimetype == "application/zip"
    import io as _io
    import zipfile
    zf = zipfile.ZipFile(_io.BytesIO(r.data))
    assert len(zf.namelist()) == 2


def test_download_zip_bos_ids_400(client):
    login_as(client, MANAGER)
    r = client.post("/api/sharing/videographer/photos/download-zip",
                    json={"ids": []}, headers=csrf_headers(client))
    assert r.status_code == 400


def test_photo_rename(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]  # cekim0.jpg
    calls = []
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "rename_file", lambda fid, name: calls.append((fid, name)))
    r = client.post(f"/api/sharing/videographer/photos/{pid}/rename",
                    json={"name": "düğün çekimi.jpg"}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["file_name"] == "düğün çekimi.jpg"
    assert calls and calls[0][1] == "düğün çekimi.jpg"  # also renamed in Drive
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"][0]
    assert p["file_name"] == "düğün çekimi.jpg"


def test_photo_rename_uzanti_korunur(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]  # cekim0.jpg
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: False)  # skip Drive, DB-only
    r = client.post(f"/api/sharing/videographer/photos/{pid}/rename",
                    json={"name": "yeni ad"}, headers=csrf_headers(client))
    assert r.get_json()["file_name"] == "yeni ad.jpg"  # original extension appended


def test_photo_rename_bos_ad_400(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]
    r = client.post(f"/api/sharing/videographer/photos/{pid}/rename",
                    json={"name": "  "}, headers=csrf_headers(client))
    assert r.status_code == 400


def test_photo_rename_videographer_yetkisiz_403(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]
    login_as(client, VG)  # unassigned videographer
    r = client.post(f"/api/sharing/videographer/photos/{pid}/rename",
                    json={"name": "x.jpg"}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_photo_download_tekil(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "download_file", lambda fid: b"RAWIMG")
    r = client.get(f"/api/sharing/videographer/photos/{pid}/download")
    assert r.status_code == 200 and r.data == b"RAWIMG"
    assert "attachment" in r.headers["Content-Disposition"]


# --- Designer access (designer board shoot-photos modal, full action) ---

def test_photos_liste_designer_gorur(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    _seed_photos(cid, 2)
    login_as(client, DESIGNER)  # even an unassigned designer can see it
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"]
    assert len(p) == 2


def test_photo_download_designer(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "download_file", lambda fid: b"RAWIMG")
    login_as(client, DESIGNER)
    r = client.get(f"/api/sharing/videographer/photos/{pid}/download")
    assert r.status_code == 200 and r.data == b"RAWIMG"


def test_download_zip_designer(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    ids = _seed_photos(cid, 2)
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "download_file", lambda fid: b"IMG-" + fid.encode())
    login_as(client, DESIGNER)  # an unassigned designer can also download the zip
    r = client.post("/api/sharing/videographer/photos/download-zip",
                    json={"ids": ids}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.mimetype == "application/zip"


def test_photos_liste_pending_403(client):
    PENDING = {"sub": "9", "email": "p@t.com", "name": "P", "role": "pending"}
    login_as(client, PENDING)
    assert client.get("/api/sharing/videographer/photos").status_code == 403


# --- Phase 5: videographer suggestion bot (step 17) ---

def _mk_idea(cid, reason="ilham veren öneri", shoot_idea="mutfak çekimi",
             link="https://youtube.com/watch?v=x", status="new"):
    from extensions import db
    from models_sharing import VideographerIdea
    idea = VideographerIdea(client_id=cid, reference_link=link, reason=reason,
                            shoot_idea=shoot_idea, status=status)
    db.session.add(idea)
    db.session.commit()
    return idea


def test_ideas_generate_enqueue_dedup(client):
    """Manually trigger idea generation → a videographer_ideas job (client-triggered). Dedup:
    pressing repeatedly for the same client results in a single active job (dedup_key), low priority (batch)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Öneri Kafe"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    r1 = client.post("/api/sharing/videographer/ideas/generate",
                     json={"client_id": cid}, headers=csrf_headers(client))
    assert r1.status_code == 202
    j1 = r1.get_json()["job"]
    assert j1["type"] == "videographer_ideas"
    # second trigger → same job (dedup)
    r2 = client.post("/api/sharing/videographer/ideas/generate",
                     json={"client_id": cid}, headers=csrf_headers(client))
    assert r2.get_json()["job"]["id"] == j1["id"]
    from models import Job
    assert Job.query.filter_by(type="videographer_ideas").count() == 1
    assert Job.query.filter_by(type="videographer_ideas").first().priority == 0


def test_ideas_generate_designer_403(client):
    """Designer cannot generate ideas (management + videographer only)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "K"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/videographer/ideas/generate",
                    json={"client_id": cid}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_ideas_list(client):
    """Idea list: returns the client's 'new' cards (newest first)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Liste Kafe"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    _mk_idea(cid, reason="öneri 1")
    _mk_idea(cid, reason="öneri 2")
    ideas = client.get(f"/api/sharing/videographer/ideas?client_id={cid}").get_json()["ideas"]
    assert len(ideas) == 2
    assert {i["reason"] for i in ideas} == {"öneri 1", "öneri 2"}


def test_ideas_like_cekim_listesine_ekler(client):
    """Un-gameable: "like" → creates the corresponding SHOOT LIST record (ShootTask) and the idea's
    status becomes 'accepted' (shoot plan domain — useShootMutations path)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Beğen Kafe"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    idea = _mk_idea(cid, shoot_idea="bahar menüsü reel")
    from extensions import db
    from models_sharing import ShootTask, VideographerIdea
    assert ShootTask.query.count() == 0
    r = client.post(f"/api/sharing/videographer/ideas/{idea.id}/like",
                    json={"scheduled_date": "2026-05-20"}, headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    # shoot list record was created (the idea's shoot concept was moved into a task)
    tasks = ShootTask.query.filter_by(client_id=cid).all()
    assert len(tasks) == 1
    assert tasks[0].title == "bahar menüsü reel"
    # idea was marked as accepted
    assert db.session.get(VideographerIdea, idea.id).status == "accepted"


def test_ideas_skip(client):
    """"skip" → idea status='skipped' (drops out of the list)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Atla Kafe"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    idea = _mk_idea(cid)
    r = client.post(f"/api/sharing/videographer/ideas/{idea.id}/skip", json={},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    from extensions import db
    from models_sharing import VideographerIdea
    assert db.session.get(VideographerIdea, idea.id).status == "skipped"
    # no longer shows up in the 'new'-filtered list
    ideas = client.get(f"/api/sharing/videographer/ideas?client_id={cid}").get_json()["ideas"]
    assert ideas == []


# --- local copy (2026-07-30): required for the /m/<file_id> permanent link ---

def test_photo_upload_LOKAL_KOPYA_birakir(client, fake_photo_drive):
    """Photo upload now also writes to media_store (previously it streamed straight to
    Drive → the permanent link would hit Drive from day one)."""
    import media_store
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    r = _post_photo(client, cid)
    assert r.status_code == 201
    fid = r.get_json()["saved"][0]["file_id"]
    assert media_store.has_original(fid), "foto lokal kopyası yazılmadı"


def test_photo_upload_lokal_kopya_ICERIGI_dogru(client, fake_photo_drive):
    """Un-gameable: the file must not just exist but carry the correct bytes —
    content could get corrupted since the `stage` flow is shared with the Drive upload."""
    import media_store
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    fid = _post_photo(client, cid).get_json()["saved"][0]["file_id"]
    with open(media_store.find_original(fid), "rb") as fh:
        assert fh.read() == b"jpgbytes"


def test_photo_upload_DRIVE_yuklemesi_bozulmadi(client, fake_photo_drive):
    """Counter-check: adding the local copy must not shrink the bytes going to Drive
    (the file could end up going to Drive as 0 bytes since the stage flow consumes it)."""
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    _post_photo(client, cid)
    assert fake_photo_drive["upload"][0]["size"] == len(b"jpgbytes")


def test_photo_upload_drive_hatasinda_gecici_kopya_birakmaz(client, fake_photo_drive,
                                                            monkeypatch):
    """If Drive blows up, the `stage` temp file must be cleaned up (no disk leak)."""
    import drive_gateway
    import media_store

    def patla(*a, **k):
        raise drive_gateway.DriveError("drive down")

    monkeypatch.setattr(drive_gateway, "upload_file", patla)
    import glob
    import os
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    # find out the originals/ directory and immediately clean up the probe (leave nothing behind).
    probe = media_store.stage(io.BytesIO(b"x"))
    orig_dir = os.path.dirname(probe)
    media_store.discard(probe)
    once = len(glob.glob(os.path.join(orig_dir, ".tmp-*")))

    r = _post_photo(client, cid)
    assert r.status_code == 400 and r.get_json()["saved"] == []
    # Upload blew up → `discard` was called, no .tmp- leftover remained. (Can't check
    # with has_original: media_store is shared across the session, and since the fake Drive
    # returns 'file_1' in every test, previous tests' copies are still sitting there.)
    sonra = len(glob.glob(os.path.join(orig_dir, ".tmp-*")))
    assert sonra == once, "geçici dosya temizlenmedi (disk sızıntısı)"
