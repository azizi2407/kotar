"""Videographer businesses (video işareti) + photos uçları."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"
VG = {"sub": "3", "email": "vg@t.com", "name": "V", "role": "videographer"}


@pytest.fixture
def fake_photo_drive(monkeypatch):
    """Fotoğraf yükleme için Drive katmanını (subfolder + upload + izin) taklit et."""
    calls = {"ensure": [], "upload": [], "grant": []}

    def fake_ensure(parent_id, name):
        calls["ensure"].append({"parent": parent_id, "name": name})
        return "PHOTOS_FOLDER"

    def fake_upload(folder_id, filename, data, mime):
        # Gerçek uç artık akış (dosya-nesnesi) geçirir; bytes'ı da kabul et.
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
    # 500 MB üstü foto: hata listesine düşer, Drive'a yüklenmez (diğerleri sürer).
    import sharing
    monkeypatch.setattr(sharing, "MAX_UPLOAD_BYTES", 3)
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    r = _post_photo(client, cid).get_json()  # 'jpgbytes' = 8 bayt > 3
    assert r["saved"] == [] and any("500 MB" in e for e in r["errors"])
    assert fake_photo_drive["upload"] == []


def test_photo_upload_drive_koku_yok_400(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client)  # drive_meta yok
    r = _post_photo(client, cid)
    assert r.status_code == 400
    assert fake_photo_drive["upload"] == []  # Drive'a gidilmedi


def test_photo_upload_olusturur_ve_public(client, fake_photo_drive):
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    r = _post_photo(client, cid, name="fotoğraf.jpg")
    assert r.status_code == 201, r.get_json()
    saved = r.get_json()["saved"]
    assert len(saved) == 1 and saved[0]["file_name"] == "fotoğraf.jpg"
    # "Çekim Fotoğrafları" alt klasörü kök altında oluşturuldu
    assert fake_photo_drive["ensure"][0]["parent"] == "ROOT1"
    assert fake_photo_drive["upload"][0]["folder_id"] == "PHOTOS_FOLDER"
    assert fake_photo_drive["grant"]  # public reader izni verildi
    # listede görünür
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
    # başta işaretsiz
    b = client.get(f"/api/sharing/videographer/businesses?week_iso={WK}").get_json()["businesses"]
    row = next(x for x in b if x["client_id"] == cid)
    assert row["has_video"] is False
    # videografçı işaretler
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


# --- fotoğraf sayfası: kullanım işareti + toplu sil + zip (2026-07-19) ---

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
    # designer kullanıldı işaretler
    login_as(client, DESIGNER)
    r = client.post(f"/api/sharing/videographer/photos/{pid}/used",
                    json={"used": True}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["used"] is True
    # GET ucu management/videographer; doğrulama için MANAGER'a dön
    login_as(client, MANAGER)
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"][0]
    assert p["used"] is True and p["used_at"]
    # geri al (designer)
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
    # atanmamış videografçı → _can_shoot False → hiçbiri silinmez
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
    assert calls and calls[0][1] == "düğün çekimi.jpg"  # Drive'da da adlandırıldı
    p = client.get(f"/api/sharing/videographer/photos?client_id={cid}").get_json()["photos"][0]
    assert p["file_name"] == "düğün çekimi.jpg"


def test_photo_rename_uzanti_korunur(client, monkeypatch):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    pid = _seed_photos(cid, 1)[0]  # cekim0.jpg
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: False)  # Drive atla, DB-only
    r = client.post(f"/api/sharing/videographer/photos/{pid}/rename",
                    json={"name": "yeni ad"}, headers=csrf_headers(client))
    assert r.get_json()["file_name"] == "yeni ad.jpg"  # orijinal uzantı eklendi


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
    login_as(client, VG)  # atanmamış videografçı
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


# --- Designer erişimi (tasarımcı board çekim fotoğrafları modalı, tam aksiyon) ---

def test_photos_liste_designer_gorur(client):
    login_as(client, MANAGER)
    cid = _mk_client(client)
    _seed_photos(cid, 2)
    login_as(client, DESIGNER)  # atanmamış designer bile görür
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
    login_as(client, DESIGNER)  # atanmamış designer da zip indirebilir
    r = client.post("/api/sharing/videographer/photos/download-zip",
                    json={"ids": ids}, headers=csrf_headers(client))
    assert r.status_code == 200 and r.mimetype == "application/zip"


def test_photos_liste_pending_403(client):
    PENDING = {"sub": "9", "email": "p@t.com", "name": "P", "role": "pending"}
    login_as(client, PENDING)
    assert client.get("/api/sharing/videographer/photos").status_code == 403


# --- Faz 5: videographer öneri botu (step 17) ---

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
    """Öneri üretimini elle tetikle → videographer_ideas job'u (müşteri-tetikli). Dedup:
    aynı müşteri için üst üste basmak tek aktif job (dedup_key), düşük priority (batch)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Öneri Kafe"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    r1 = client.post("/api/sharing/videographer/ideas/generate",
                     json={"client_id": cid}, headers=csrf_headers(client))
    assert r1.status_code == 202
    j1 = r1.get_json()["job"]
    assert j1["type"] == "videographer_ideas"
    # ikinci tetik → aynı job (dedup)
    r2 = client.post("/api/sharing/videographer/ideas/generate",
                     json={"client_id": cid}, headers=csrf_headers(client))
    assert r2.get_json()["job"]["id"] == j1["id"]
    from models import Job
    assert Job.query.filter_by(type="videographer_ideas").count() == 1
    assert Job.query.filter_by(type="videographer_ideas").first().priority == 0


def test_ideas_generate_designer_403(client):
    """Designer öneri üretemez (yalnız management + videographer)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "K"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/videographer/ideas/generate",
                    json={"client_id": cid}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_ideas_list(client):
    """Öneri listesi: müşteriye ait 'new' kartlar döner (en yeni önce)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Liste Kafe"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    _mk_idea(cid, reason="öneri 1")
    _mk_idea(cid, reason="öneri 2")
    ideas = client.get(f"/api/sharing/videographer/ideas?client_id={cid}").get_json()["ideas"]
    assert len(ideas) == 2
    assert {i["reason"] for i in ideas} == {"öneri 1", "öneri 2"}


def test_ideas_like_cekim_listesine_ekler(client):
    """Un-gameable: "beğen" → ilgili ÇEKİM LİSTESİ kaydı (ShootTask) oluşur ve öneri
    status='accepted' olur (çekim planı domain'i — useShootMutations yolu)."""
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
    # çekim listesi kaydı oluştu (öneri çekim fikri task'a taşındı)
    tasks = ShootTask.query.filter_by(client_id=cid).all()
    assert len(tasks) == 1
    assert tasks[0].title == "bahar menüsü reel"
    # öneri kabul edildi olarak işaretlendi
    assert db.session.get(VideographerIdea, idea.id).status == "accepted"


def test_ideas_skip(client):
    """"atla" → öneri status='skipped' (listeden düşer)."""
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
    # 'new' filtreli listede artık görünmez
    ideas = client.get(f"/api/sharing/videographer/ideas?client_id={cid}").get_json()["ideas"]
    assert ideas == []


# --- lokal kopya (2026-07-30): /m/<file_id> kalıcı linki için şart ---

def test_photo_upload_LOKAL_KOPYA_birakir(client, fake_photo_drive):
    """Foto yüklemesi artık media_store'a da yazar (öncesinde doğrudan Drive'a
    akıtılıyordu → kalıcı link ilk günden Drive'a düşerdi)."""
    import media_store
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    r = _post_photo(client, cid)
    assert r.status_code == 201
    fid = r.get_json()["saved"][0]["file_id"]
    assert media_store.has_original(fid), "foto lokal kopyası yazılmadı"


def test_photo_upload_lokal_kopya_ICERIGI_dogru(client, fake_photo_drive):
    """Un-gameable: dosya yalnız var olmakla kalmayıp doğru baytları taşımalı —
    `stage` akışı Drive yüklemesiyle paylaştığı için içerik bozulabilirdi."""
    import media_store
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    fid = _post_photo(client, cid).get_json()["saved"][0]["file_id"]
    with open(media_store.find_original(fid), "rb") as fh:
        assert fh.read() == b"jpgbytes"


def test_photo_upload_DRIVE_yuklemesi_bozulmadi(client, fake_photo_drive):
    """Karşıt kontrol: lokal kopya eklenirken Drive'a giden baytlar eksilmemeli
    (stage akışı tükettiği için dosya Drive'a 0 bayt gidebilirdi)."""
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    _post_photo(client, cid)
    assert fake_photo_drive["upload"][0]["size"] == len(b"jpgbytes")


def test_photo_upload_drive_hatasinda_gecici_kopya_birakmaz(client, fake_photo_drive,
                                                            monkeypatch):
    """Drive patlarsa `stage` geçici dosyası temizlenmeli (disk sızıntısı yok)."""
    import drive_gateway
    import media_store

    def patla(*a, **k):
        raise drive_gateway.DriveError("drive down")

    monkeypatch.setattr(drive_gateway, "upload_file", patla)
    import glob
    import os
    login_as(client, MANAGER)
    cid = _mk_client(client, drive_root="ROOT1")
    # originals/ dizinini öğren ve sondayı hemen temizle (artık bırakma).
    probe = media_store.stage(io.BytesIO(b"x"))
    orig_dir = os.path.dirname(probe)
    media_store.discard(probe)
    once = len(glob.glob(os.path.join(orig_dir, ".tmp-*")))

    r = _post_photo(client, cid)
    assert r.status_code == 400 and r.get_json()["saved"] == []
    # Yükleme patladı → `discard` çağrıldı, geriye .tmp- artığı KALMADI. (has_original
    # ile bakılamaz: media_store oturum boyu paylaşımlı, sahte Drive her testte
    # 'file_1' döndürdüğü için önceki testlerin kopyası orada duruyor.)
    sonra = len(glob.glob(os.path.join(orig_dir, ".tmp-*")))
    assert sonra == once, "geçici dosya temizlenmedi (disk sızıntısı)"
