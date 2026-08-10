"""Lokal medya servis uçları — sharing tarafı (Drive'a ağ çıkışı YOK)."""
import io

import pytest
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def client_id(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Medya Müşteri"}, headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


def _jpeg_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1000, 700), (200, 60, 60)).save(buf, "JPEG")
    return buf.getvalue()


def _store(file_id, data=b"vid-bytes", mime="video/mp4", name="v.mp4"):
    import media_store
    assert media_store.save_original(file_id, data, mime, name)


def test_media_ucu_oturumsuz_401(client):
    assert client.get("/api/sharing/media/HERHANGI").status_code == 401


def test_media_ucu_lokal_dosyayi_range_destekli_doner(client):
    login_as(client, MANAGER)
    _store("VIDLOCAL1")
    r = client.get("/api/sharing/media/VIDLOCAL1")
    assert r.status_code == 200
    assert r.data == b"vid-bytes"
    r.close()  # send_file dosya tanıtıcısı — test client'ta elle kapatılır
    # werkzeug 3.0: Accept-Ranges yalnız Range'li istekte yazılır; 206 esas kanıt.
    r2 = client.get("/api/sharing/media/VIDLOCAL1", headers={"Range": "bytes=0-2"})
    assert r2.status_code == 206
    assert r2.data == b"vid"
    assert r2.headers.get("Content-Range", "").startswith("bytes 0-2/")
    r2.close()


def test_media_ucu_lokal_yoksa_404(client):
    login_as(client, MANAGER)
    assert client.get("/api/sharing/media/YOKBOYLEDOSYA1").status_code == 404


def test_thumbnail_lokal_preview_once(client):
    login_as(client, MANAGER)
    _store("IMGLOCAL1", _jpeg_bytes(), "image/jpeg", "f.jpg")
    r = client.get("/api/sharing/thumbnail/IMGLOCAL1")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"  # Drive'a gidilmedi (dg mock'suz ağ çıkışı olsaydı patlardı)
    r.close()  # send_file dosya tanıtıcısı — test client'ta elle kapatılır


def test_build_rows_local_bayraklari(client, client_id):
    import media_store  # noqa: F401 — store env'i conftest kurdu
    from extensions import db
    from models import Client, utcnow
    from models_sharing import CardUpload, Share
    from sharing import _build_rows

    _store("VIDROW1")
    db.session.add(CardUpload(client_id=client_id, week_iso="2026-W21", category="video",
                              file_id="VIDROW1", file_name="v.mp4", uploaded_by="1",
                              uploaded_at=utcnow(), upload_uuid="u1"))
    db.session.add(Share(client_id=client_id, week_iso="2026-W21", kind="post",
                         status="draft", file_id="YOKROW1"))
    db.session.commit()
    c = db.session.get(Client, client_id)
    rows = _build_rows([c], "2026-W21")
    assert rows[0]["video_uploads"][0]["local"] is True
    assert rows[0]["shares"][0]["local"] is False


def test_media_dl_attachment_turkce_isim(client):
    login_as(client, MANAGER)
    _store("IMGDL1", _jpeg_bytes(), "image/jpeg", "f.jpg")
    r = client.get("/api/sharing/media/IMGDL1?dl=1&name=camsa%C5%9F-1.jpg")
    assert r.status_code == 200
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd and "camsa" in cd  # Türkçe isim korunur (RFC5987)
    r.close()


def test_media_dl_lokal_yoksa_drive_fallback(client, monkeypatch):
    # Lokal kopya yoksa indirme isteği Drive'dan orijinali çeker (tam boyut).
    login_as(client, MANAGER)
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "download_file", lambda fid: b"drive-original-bytes")
    r = client.get("/api/sharing/media/YOKLOKAL1?dl=1&name=x.jpg")
    assert r.status_code == 200
    assert r.data == b"drive-original-bytes"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    r.close()


def test_media_dl_yok_ve_drive_yok_404(client, monkeypatch):
    login_as(client, MANAGER)
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: False)
    assert client.get("/api/sharing/media/YOKHIC1?dl=1&name=x.jpg").status_code == 404
