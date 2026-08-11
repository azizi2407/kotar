"""/api/sharing — Sharing Board API tests (Phase 2a)."""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"


@pytest.fixture
def client_id(client):
    """Create a client, return its id (management session stays open)."""
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Board Müşteri"}, headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


def mk_share(client, cid, **kw):
    body = {"client_id": cid, "week_iso": WK, "kind": "post"}
    body.update(kw)
    r = client.post("/api/sharing/shares", json=body, headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["share"]


# --- permissions ---

def test_cards_oturumsuz_401(client):
    assert client.get(f"/api/sharing/cards?week_iso={WK}").status_code == 401


def test_cards_designer_403(client, client_id):
    # Phase 2a: management board, management only
    login_as(client, DESIGNER)
    assert client.get(f"/api/sharing/cards?week_iso={WK}").status_code == 403


def test_share_create_designer_403(client, client_id):
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/shares",
                    json={"client_id": client_id, "week_iso": WK, "kind": "post"},
                    headers=csrf_headers(client))
    assert r.status_code == 403


def test_share_create_csrf_yok_403(client, client_id):
    r = client.post("/api/sharing/shares",
                    json={"client_id": client_id, "week_iso": WK, "kind": "post"})
    assert r.status_code == 403


# --- shares CRUD ---

def test_share_create_varsayilan_draft(client, client_id):
    s = mk_share(client, client_id)
    assert s["status"] == "draft"
    assert s["kind"] == "post"
    assert s["created_by"] == MANAGER["sub"]


def test_share_create_gecersiz_kind_400(client, client_id):
    r = client.post("/api/sharing/shares",
                    json={"client_id": client_id, "week_iso": WK, "kind": "meme"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_share_create_gecersiz_client_404(client):
    login_as(client, MANAGER)
    r = client.post("/api/sharing/shares",
                    json={"client_id": 99999, "week_iso": WK, "kind": "post"},
                    headers=csrf_headers(client))
    assert r.status_code == 404


def test_share_patch_alanlar(client, client_id):
    s = mk_share(client, client_id)
    r = client.patch(f"/api/sharing/shares/{s['id']}",
                     json={"caption_text": "Merhaba", "hashtag_text": "#x", "note": "n"},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    d = r.get_json()["share"]
    assert d["caption_text"] == "Merhaba" and d["hashtag_text"] == "#x"
    assert d["updated_by"] == MANAGER["sub"]


def test_share_publish_dosyali(client, client_id):
    s = mk_share(client, client_id, file_id="f1", file_name="a.jpg")
    r = client.post(f"/api/sharing/shares/{s['id']}/publish", headers=csrf_headers(client))
    assert r.status_code == 200
    d = r.get_json()["share"]
    assert d["status"] == "published"
    assert d["published_at"] and d["published_day_name"]
    assert d["published_by"] == MANAGER["sub"]


def test_share_publish_dosyasiz_notsuz_400(client, client_id):
    """Note is required for a share without a file (old rule)."""
    s = mk_share(client, client_id)
    r = client.post(f"/api/sharing/shares/{s['id']}/publish", headers=csrf_headers(client))
    assert r.status_code == 400


def test_share_publish_dosyasiz_notlu_gecer(client, client_id):
    s = mk_share(client, client_id, note="sadece metin paylaşımı")
    r = client.post(f"/api/sharing/shares/{s['id']}/publish", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["share"]["status"] == "published"


def test_share_unpublish(client, client_id):
    s = mk_share(client, client_id, file_id="f1")
    client.post(f"/api/sharing/shares/{s['id']}/publish", headers=csrf_headers(client))
    r = client.post(f"/api/sharing/shares/{s['id']}/unpublish", headers=csrf_headers(client))
    d = r.get_json()["share"]
    assert d["status"] == "draft"
    assert d["published_at"] is None


def test_share_platform_mark_toggle(client, client_id):
    s = mk_share(client, client_id, file_id="f1")
    client.post(f"/api/sharing/shares/{s['id']}/publish", headers=csrf_headers(client))
    r = client.post(f"/api/sharing/shares/{s['id']}/platform-mark",
                    json={"platform": "linkedin"}, headers=csrf_headers(client))
    assert r.status_code == 200
    assert "linkedin" in r.get_json()["share"]["platforms"]
    # again → remove
    r = client.post(f"/api/sharing/shares/{s['id']}/platform-mark",
                    json={"platform": "linkedin"}, headers=csrf_headers(client))
    assert "linkedin" not in r.get_json()["share"]["platforms"]


def test_share_soft_delete(client, client_id):
    s = mk_share(client, client_id)
    r = client.delete(f"/api/sharing/shares/{s['id']}", headers=csrf_headers(client))
    assert r.status_code == 200
    # not visible in cards
    cards = client.get(f"/api/sharing/cards?week_iso={WK}").get_json()
    ids = [sh["id"] for row in cards["rows"] for sh in row["shares"]]
    assert s["id"] not in ids


# --- weekly card matrix ---

def test_cards_musteri_satiri_ve_shares(client, client_id):
    mk_share(client, client_id, file_id="f1")
    mk_share(client, client_id, kind="story")
    r = client.get(f"/api/sharing/cards?week_iso={WK}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["week_iso"] == WK
    row = next(x for x in data["rows"] if x["client"]["id"] == client_id)
    assert len(row["shares"]) == 2
    assert row["published_count"] == 0
    assert row["total_count"] == 2


def test_cards_baska_hafta_bos(client, client_id):
    mk_share(client, client_id)
    r = client.get("/api/sharing/cards?week_iso=2026-W22").get_json()
    row = next(x for x in r["rows"] if x["client"]["id"] == client_id)
    assert row["shares"] == []


def test_cards_ilerleme_sayaci(client, client_id):
    a = mk_share(client, client_id, file_id="f1")
    mk_share(client, client_id)
    client.post(f"/api/sharing/shares/{a['id']}/publish", headers=csrf_headers(client))
    r = client.get(f"/api/sharing/cards?week_iso={WK}").get_json()
    row = next(x for x in r["rows"] if x["client"]["id"] == client_id)
    assert row["published_count"] == 1 and row["total_count"] == 2


def test_cards_video_sadece_son_revizyon(client, client_id):
    """Video: only the highest revision is visible (old visibility rule)."""
    mk_share(client, client_id, kind="video", revision=0, file_id="v0")
    mk_share(client, client_id, kind="video", revision=1, file_id="v1")
    r = client.get(f"/api/sharing/cards?week_iso={WK}").get_json()
    row = next(x for x in r["rows"] if x["client"]["id"] == client_id)
    vids = [s for s in row["shares"] if s["kind"] == "video"]
    assert len(vids) == 1 and vids[0]["revision"] == 1


# --- priority ---

def test_priority_toggle(client, client_id):
    r = client.post("/api/sharing/priority",
                    json={"client_id": client_id, "week_iso": WK}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["active"] is True
    cards = client.get(f"/api/sharing/cards?week_iso={WK}").get_json()
    row = next(x for x in cards["rows"] if x["client"]["id"] == client_id)
    assert row["priority"] is True
    # again → close
    r = client.post("/api/sharing/priority",
                    json={"client_id": client_id, "week_iso": WK}, headers=csrf_headers(client))
    assert r.get_json()["active"] is False


# --- review link ---

def test_review_link_uret_idempotent(client, client_id):
    r = client.post("/api/sharing/review-link",
                    json={"client_id": client_id, "week_iso": WK}, headers=csrf_headers(client))
    assert r.status_code == 200
    t1 = r.get_json()["token"]
    assert len(t1) >= 32
    r2 = client.post("/api/sharing/review-link",
                     json={"client_id": client_id, "week_iso": WK}, headers=csrf_headers(client))
    assert r2.get_json()["token"] == t1  # same (client,week) → same token


def test_review_link_revoke_sonra_yeni(client, client_id):
    t1 = client.post("/api/sharing/review-link",
                     json={"client_id": client_id, "week_iso": WK},
                     headers=csrf_headers(client)).get_json()["token"]
    client.post("/api/sharing/review-link/revoke",
                json={"token": t1}, headers=csrf_headers(client))
    t2 = client.post("/api/sharing/review-link",
                     json={"client_id": client_id, "week_iso": WK},
                     headers=csrf_headers(client)).get_json()["token"]
    assert t2 != t1


# --- special day card ---

def test_special_card_publish_unpublish(client, client_id):
    from extensions import db
    from models_sharing import SpecialDayEvent
    ev = SpecialDayEvent(day_name="Anneler Günü", month=5, year=2026, active=True)
    db.session.add(ev)
    db.session.commit()
    r = client.post("/api/sharing/special-card/publish",
                    json={"client_id": client_id, "week_iso": WK, "event_id": ev.id},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    cards = client.get(f"/api/sharing/cards?week_iso={WK}").get_json()
    row = next(x for x in cards["rows"] if x["client"]["id"] == client_id)
    assert any(sp["event_id"] == ev.id for sp in row["special_cards"])
    r = client.post("/api/sharing/special-card/unpublish",
                    json={"client_id": client_id, "week_iso": WK, "event_id": ev.id},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    cards = client.get(f"/api/sharing/cards?week_iso={WK}").get_json()
    row = next(x for x in cards["rows"] if x["client"]["id"] == client_id)
    assert row["special_cards"] == []


# --- uploads (picker source) ---

def test_uploads_listesi(client, client_id):
    from extensions import db
    from models_sharing import CardUpload
    db.session.add(CardUpload(client_id=client_id, week_iso=WK, category="post",
                              file_id="u1", file_name="up.jpg"))
    db.session.commit()
    r = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso={WK}")
    assert r.status_code == 200
    ups = r.get_json()["uploads"]
    assert len(ups) == 1 and ups[0]["file_id"] == "u1"


def test_magnific_credits_cache_okur(client, client_id):
    """The credits endpoint reads from the AppSetting cache; if the cache is empty,
    credits=null, no live call."""
    import json as _json

    from extensions import db
    from models import AppSetting
    r = client.get("/api/sharing/magnific-credits")
    assert r.status_code == 200
    assert r.get_json()["credits"] is None
    db.session.add(AppSetting(key="magnific_credits",
                              value=_json.dumps({"available": 500, "at": "2026-07-20T10:00:00"})))
    db.session.commit()
    r = client.get("/api/sharing/magnific-credits")
    body = r.get_json()
    assert body["credits"]["available"] == 500
    assert body["refreshing"] is False


def test_magnific_credits_yalniz_management(client):
    from conftest import DESIGNER, login_as
    login_as(client, DESIGNER)
    assert client.get("/api/sharing/magnific-credits").status_code == 403


def _brief_for(client, cid, ideas=None):
    from extensions import db
    from models_sharing import WeeklyBrief
    b = WeeklyBrief(client_id=cid, week_iso=WK, title="B", status="draft",
                    ideas=ideas if ideas is not None else
                    [{"ad": "Kahve tanıtımı", "içerik": "taze çekirdek", "görsel_tarz": "sıcak tonlar"}])
    db.session.add(b)
    db.session.commit()
    return b


def test_prompt_examples_job_enqueue(client, client_id):
    """The endpoint does NOT run claude synchronously (no CLI in svc-agency) — it
    enqueues a job and returns 202."""
    from extensions import db
    from models import Job
    b = _brief_for(client, client_id)
    r = client.post("/api/sharing/image-gen/prompt-examples",
                    json={"client_id": client_id, "brief_id": b.id},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    job = db.session.get(Job, r.get_json()["job"]["id"])
    assert job.type == "prompt_examples" and job.priority == 10


def test_prompt_examples_brief_yok_404(client, client_id):
    r = client.post("/api/sharing/image-gen/prompt-examples",
                    json={"client_id": client_id, "brief_id": 999999},
                    headers=csrf_headers(client))
    assert r.status_code == 404


def test_convert_prompt_job_enqueue(client, client_id):
    from extensions import db
    from models import Job
    r = client.post("/api/sharing/image-gen/convert-prompt",
                    json={"prompt": "sıcak tonlarda kahve"},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    job = db.session.get(Job, r.get_json()["job"]["id"])
    assert job.type == "prompt_convert"
    assert job.payload["prompt"] == "sıcak tonlarda kahve"


def test_convert_prompt_bos_400(client, client_id):
    r = client.post("/api/sharing/image-gen/convert-prompt", json={"prompt": "  "},
                    headers=csrf_headers(client))
    assert r.status_code == 400


# --- client brand images (logo + standard) ---

def _asset_drive_mocks(monkeypatch):
    import sharing as sharing_mod
    monkeypatch.setattr(sharing_mod.dg, "available", lambda: True)
    monkeypatch.setattr(sharing_mod.dg, "ensure_subfolder", lambda root, name: "folder-marka")
    monkeypatch.setattr(sharing_mod.dg, "upload_file",
                        lambda folder, name, data, mime:
                        {"id": f"drv-{name}", "name": name, "mimeType": mime, "size": len(data)})


def _set_drive_meta(cid):
    from extensions import db
    from models import Client
    c = db.session.get(Client, cid)
    c.drive_meta = {"client_folder_link": "https://drive.google.com/drive/folders/root-abc"}
    db.session.commit()


def test_client_asset_yukle_listele_sil(client, client_id, monkeypatch):
    import io
    _asset_drive_mocks(monkeypatch)
    _set_drive_meta(client_id)
    r = client.post(f"/api/sharing/clients/{client_id}/assets",
                    data={"kind": "standard", "label": "Beyaz peynir etiketi",
                          "file": (io.BytesIO(b"IMGDATA"), "etiket.png", "image/png")},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    asset = r.get_json()["asset"]
    assert asset["kind"] == "standard" and asset["label"] == "Beyaz peynir etiketi"
    r = client.get(f"/api/sharing/clients/{client_id}/assets")
    assert [a["id"] for a in r.get_json()["assets"]] == [asset["id"]]
    r = client.delete(f"/api/sharing/clients/{client_id}/assets/{asset['id']}",
                      headers=csrf_headers(client))
    assert r.status_code == 200
    assert client.get(f"/api/sharing/clients/{client_id}/assets").get_json()["assets"] == []


def test_client_asset_logo_tekil(client, client_id, monkeypatch):
    """New logo soft-deletes the old one — the list always keeps a SINGLE logo."""
    import io
    _asset_drive_mocks(monkeypatch)
    _set_drive_meta(client_id)
    for name in ("logo1.png", "logo2.png"):
        r = client.post(f"/api/sharing/clients/{client_id}/assets",
                        data={"kind": "logo",
                              "file": (io.BytesIO(b"L"), name, "image/png")},
                        headers=csrf_headers(client))
        assert r.status_code == 201
    logos = [a for a in client.get(f"/api/sharing/clients/{client_id}/assets")
             .get_json()["assets"] if a["kind"] == "logo"]
    assert len(logos) == 1 and logos[0]["file_name"] == "logo2.png"


def test_client_asset_gorsel_disi_reddedilir(client, client_id, monkeypatch):
    import io
    _asset_drive_mocks(monkeypatch)
    _set_drive_meta(client_id)
    r = client.post(f"/api/sharing/clients/{client_id}/assets",
                    data={"kind": "standard",
                          "file": (io.BytesIO(b"PDF"), "dosya.pdf", "application/pdf")},
                    headers=csrf_headers(client))
    assert r.status_code == 400


# --- brand image read/download permission gate (2026-08-04) ---

def _make_asset(client, client_id, monkeypatch, name="logo.png"):
    """Uploads a brand image as management, returns the asset dict."""
    import io
    _asset_drive_mocks(monkeypatch)
    _set_drive_meta(client_id)
    r = client.post(f"/api/sharing/clients/{client_id}/assets",
                    data={"kind": "logo", "file": (io.BytesIO(b"L"), name, "image/png")},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    return r.get_json()["asset"]


def _assign(client_id, sub, slot):
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=client_id, role_slot=slot, user_id=sub))
    db.session.commit()


def test_asset_indirme_management_ve_designer(client, client_id, monkeypatch):
    """Download streams via the service account; the designer has access even when not
    assigned (the 'Other Clients' tiles on the board are also fully actionable)."""
    import sharing as sharing_mod
    from conftest import DESIGNER, login_as
    asset = _make_asset(client, client_id, monkeypatch, "marka.png")
    monkeypatch.setattr(sharing_mod.dg, "download_file", lambda fid: b"BYTES")

    url = f"/api/sharing/clients/{client_id}/assets/{asset['id']}/download"
    r = client.get(url)
    assert r.status_code == 200 and r.data == b"BYTES"
    assert "marka.png" in r.headers["Content-Disposition"]

    login_as(client, DESIGNER)
    r = client.get(url)
    assert r.status_code == 200 and r.data == b"BYTES"


def test_asset_okuma_atanmamis_videographer_403(client, client_id, monkeypatch):
    from conftest import login_as
    vg = {"sub": "77", "email": "vg@test.com", "name": "VG", "role": "videographer"}
    asset = _make_asset(client, client_id, monkeypatch)
    login_as(client, vg)
    assert client.get(f"/api/sharing/clients/{client_id}/assets").status_code == 403
    assert client.get(
        f"/api/sharing/clients/{client_id}/assets/{asset['id']}/download").status_code == 403


def test_asset_okuma_atanmis_videographer_200(client, client_id, monkeypatch):
    import sharing as sharing_mod
    from conftest import login_as
    vg = {"sub": "78", "email": "vg2@test.com", "name": "VG2", "role": "videographer"}
    asset = _make_asset(client, client_id, monkeypatch)
    _assign(client_id, vg["sub"], "videographer_shoot")
    monkeypatch.setattr(sharing_mod.dg, "download_file", lambda fid: b"BYTES")
    login_as(client, vg)
    assert client.get(f"/api/sharing/clients/{client_id}/assets").status_code == 200
    r = client.get(f"/api/sharing/clients/{client_id}/assets/{asset['id']}/download")
    assert r.status_code == 200 and r.data == b"BYTES"


def test_asset_indirme_oturumsuz_401_ve_yok_404(client, client_id, monkeypatch):
    asset = _make_asset(client, client_id, monkeypatch)
    assert client.get(
        f"/api/sharing/clients/{client_id}/assets/999999/download").status_code == 404
    # a deleted asset is also 404
    client.delete(f"/api/sharing/clients/{client_id}/assets/{asset['id']}",
                  headers=csrf_headers(client))
    assert client.get(
        f"/api/sharing/clients/{client_id}/assets/{asset['id']}/download").status_code == 404
    with client.session_transaction() as s:
        s.clear()
    assert client.get(
        f"/api/sharing/clients/{client_id}/assets/{asset['id']}/download").status_code == 401
