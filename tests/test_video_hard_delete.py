"""Permanently deleting a videographer's video upload — DELETE /api/sharing/videographer/uploads/<id>.

This endpoint is NOT a twin of the existing superadmin `DELETE /uploads/<id>` endpoint
(soft-delete): it REALLY deletes the row and the review/pre-approval records FK-linked
to it, removes the local copy from disk on the server, and moves the Drive file to
trash (project owner's decision, 2026-07-31). The tests' weight is on "who can delete"
and "what's left behind".
"""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W31"
VG = {"sub": "3", "email": "vg@t.com", "name": "V", "role": "videographer"}
VG2 = {"sub": "9", "email": "vg2@t.com", "name": "V2", "role": "videographer"}


@pytest.fixture
def fake_trash(monkeypatch):
    """Capture the Drive trash call (no network)."""
    calls = []
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "trash_file", lambda fid: calls.append(fid))
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    return calls


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    return client.post("/api/clients", json={"name": "Video Müşteri"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _video(cid, file_id="VIDSIL00001", uploaded_by="3", name="klip.mp4"):
    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    u = CardUpload(client_id=cid, week_iso=WK, category="video", file_id=file_id,
                   file_name=name, uploaded_by=uploaded_by, uploaded_at=utcnow())
    db.session.add(u)
    db.session.commit()
    return u.id


def _delete(client, upload_id):
    return client.delete(f"/api/sharing/videographer/uploads/{upload_id}",
                         headers=csrf_headers(client))


def _row(upload_id):
    from extensions import db
    from models_sharing import CardUpload
    return db.session.get(CardUpload, upload_id)


# --- authorization ------------------------------------------------------------------

def test_videograf_KENDI_yuklemesini_siler(client, cid, fake_trash):
    uid = _video(cid, uploaded_by=VG["sub"])
    login_as(client, VG)
    assert _delete(client, uid).status_code == 200
    assert _row(uid) is None


def test_videograf_BASKASININ_yuklemesini_SILEMEZ(client, cid, fake_trash):
    """Project owner's decision: a videographer only cleans up their own uploads and
    can't touch someone else's work. Management deletes without distinction."""
    uid = _video(cid, uploaded_by=VG["sub"])
    login_as(client, VG2)
    r = _delete(client, uid)
    assert r.status_code == 403
    assert _row(uid) is not None                    # row still there


def test_yonetim_HERKESIN_yuklemesini_siler(client, cid, fake_trash):
    uid = _video(cid, uploaded_by=VG["sub"])
    login_as(client, MANAGER)
    assert _delete(client, uid).status_code == 200
    assert _row(uid) is None


def test_designer_BASKASININ_yuklemesini_SILEMEZ(client, cid, fake_trash):
    """Designer became able to upload video on 2026-08-05 → this endpoint also applies
    to them, but they're subject to the SAME ownership rule as videographer: can't
    touch someone else's video."""
    uid = _video(cid, uploaded_by=VG["sub"])
    login_as(client, DESIGNER)
    assert _delete(client, uid).status_code == 403
    assert _row(uid) is not None


def test_designer_KENDI_yuklemesini_siler(client, cid, fake_trash):
    uid = _video(cid, uploaded_by=DESIGNER["sub"])
    login_as(client, DESIGNER)
    assert _delete(client, uid).status_code == 200
    assert _row(uid) is None


def test_content_creator_403(client, cid, fake_trash):
    """The role list grew but wasn't opened up to all production roles."""
    from conftest import CONTENT_CREATOR
    uid = _video(cid, uploaded_by=CONTENT_CREATOR["sub"])
    login_as(client, CONTENT_CREATOR)
    assert _delete(client, uid).status_code == 403
    assert _row(uid) is not None


def test_oturumsuz_401(client, cid, fake_trash):
    uid = _video(cid)
    client.get("/auth/logout")
    assert client.delete(f"/api/sharing/videographer/uploads/{uid}").status_code in (401, 403)


# --- scope: VIDEO only ---------------------------------------------------

def test_video_OLMAYAN_yukleme_bu_uctan_silinemez(client, cid, fake_trash):
    """This endpoint is for the videographer page; it must not be possible to delete a
    designer's upload (category='post') from here — that would be deleted under the
    wrong authorization model."""
    from extensions import db
    from models_sharing import CardUpload
    u = CardUpload(client_id=cid, week_iso=WK, category="post", file_id="POST00001",
                   file_name="tasarim.png", uploaded_by=VG["sub"])
    db.session.add(u)
    db.session.commit()
    login_as(client, MANAGER)
    assert _delete(client, u.id).status_code == 404
    assert _row(u.id) is not None


# --- does it actually delete? --------------------------------------------------

def test_SOFT_delete_DEGIL_satir_gercekten_gidiyor(client, cid, fake_trash):
    """The heart of the desired behavior: marking `deleted_at` is NOT enough."""
    uid = _video(cid)
    login_as(client, MANAGER)
    _delete(client, uid)
    from models_sharing import CardUpload
    assert CardUpload.query.filter_by(id=uid).count() == 0


def test_bagli_onay_kayitlari_da_siliniyor(client, cid, fake_trash):
    """`upload_review` / `upload_pre_approval` / `review_excluded_upload` are FK-linked
    to `card_uploads.id` — if not cleaned up, hard delete raises an FK error."""
    from extensions import db
    from models_sharing import (ReviewExcludedUpload, UploadPreApproval, UploadReview)
    uid = _video(cid)
    db.session.add_all([UploadReview(upload_id=uid, status="approved"),
                        UploadPreApproval(upload_id=uid, status="approved"),
                        ReviewExcludedUpload(upload_id=uid, excluded_by="1")])
    db.session.commit()
    login_as(client, MANAGER)
    assert _delete(client, uid).status_code == 200
    assert UploadReview.query.filter_by(upload_id=uid).count() == 0
    assert UploadPreApproval.query.filter_by(upload_id=uid).count() == 0
    assert ReviewExcludedUpload.query.filter_by(upload_id=uid).count() == 0


def test_lokal_kopya_diskten_siliniyor(client, cid, fake_trash):
    """If the 21-day copy were left behind, the deleted video would remain accessible
    via `/m/<file_id>` — permanent deletion would lose its meaning."""
    import media_store
    uid = _video(cid, file_id="VIDDISK0001")
    assert media_store.save_original("VIDDISK0001", b"veri", "video/mp4", "k.mp4")
    login_as(client, MANAGER)
    _delete(client, uid)
    assert media_store.find_original("VIDDISK0001") is None


def test_drive_dosyasi_COPE_tasiniyor(client, cid, fake_trash):
    uid = _video(cid, file_id="VIDDRIVE001")
    login_as(client, MANAGER)
    r = _delete(client, uid)
    assert r.get_json()["drive_ok"] is True
    assert fake_trash == ["VIDDRIVE001"]


def test_drive_patlasa_bile_panel_kaydi_siliniyor(client, cid, monkeypatch):
    """Drive is best-effort: if the record can't be deleted, the user can't clean up
    anything. The response reports the status via `drive_ok:false` (the depot.py
    pattern)."""
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "trash_file",
                        lambda fid: (_ for _ in ()).throw(drive_gateway.DriveError("yok")))
    uid = _video(cid, file_id="VIDDRIVE002")
    login_as(client, MANAGER)
    r = _delete(client, uid)
    assert r.status_code == 200 and r.get_json()["drive_ok"] is False
    assert _row(uid) is None


def test_silinen_video_public_LINKTEN_de_dusuyor(client, cid, fake_trash):
    """A shared `/m/<file_id>` link must die."""
    import media_store
    uid = _video(cid, file_id="VIDPUBSIL01")
    media_store.save_original("VIDPUBSIL01", b"veri", "video/mp4", "k.mp4")
    login_as(client, MANAGER)
    _delete(client, uid)
    client.get("/auth/logout")
    assert client.get("/m/VIDPUBSIL01").status_code == 404


def test_ikinci_silme_404(client, cid, fake_trash):
    uid = _video(cid)
    login_as(client, MANAGER)
    assert _delete(client, uid).status_code == 200
    assert _delete(client, uid).status_code == 404


def test_csrf_zorunlu(client, cid, fake_trash):
    uid = _video(cid)
    login_as(client, MANAGER)
    assert client.delete(f"/api/sharing/videographer/uploads/{uid}").status_code == 403
    assert _row(uid) is not None


# --- board `can_delete` flag ---------------------------------------------
# So the rule lives in one place (backend), it's baked into the board response;
# the panel only looks at the flag. If the flag and the endpoint itself diverge,
# the button lies.

def _board_video(client, cid):
    r = client.get(f"/api/sharing/videographer/cards?week_iso={WK}")
    for row in r.get_json()["rows"]:
        if row["client"]["id"] == cid:
            return row.get("video_uploads") or []
    return []


def test_board_can_delete_videografta_SAHIPLIGE_gore(client, cid):
    _video(cid, file_id="VIDBRD00001", uploaded_by=VG["sub"])
    _video(cid, file_id="VIDBRD00002", uploaded_by="baskasi")
    login_as(client, VG)
    bayrak = {v["file_id"]: v["can_delete"] for v in _board_video(client, cid)}
    assert bayrak == {"VIDBRD00001": True, "VIDBRD00002": False}


def test_board_can_delete_yonetimde_HEPSI_true(client, cid):
    _video(cid, file_id="VIDBRD00003", uploaded_by="baskasi")
    login_as(client, MANAGER)
    assert all(v["can_delete"] for v in _board_video(client, cid))


def test_can_delete_bayragi_UCLA_TUTARLI(client, cid, fake_trash):
    """Converse check: when the flag is False, the endpoint must actually return 403."""
    uid = _video(cid, file_id="VIDBRD00004", uploaded_by="baskasi")
    login_as(client, VG)
    v = [x for x in _board_video(client, cid) if x["id"] == uid][0]
    assert v["can_delete"] is False
    assert _delete(client, uid).status_code == 403
