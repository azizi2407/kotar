"""img-bucket — yönetici resim deposu (list/upload/delete/convert + public /img)."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

from PIL import Image


def _png(w=10, h=10):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 50, 50)).save(buf, "PNG")
    return buf.getvalue()


def _upload(client, data=None, name="resim.png"):
    return client.post("/api/tools/img-bucket/upload",
                       data={"files": (io.BytesIO(data or _png()), name)},
                       content_type="multipart/form-data", headers=csrf_headers(client))


def test_bucket_list_designer_403(client):
    login_as(client, DESIGNER)
    assert client.get("/api/tools/img-bucket/list").status_code == 403


def test_bucket_total_limit_4gb(client):
    login_as(client, MANAGER)
    lst = client.get("/api/tools/img-bucket/list").get_json()
    assert lst["usage"]["total"] == 4 * 1024 * 1024 * 1024  # genel depo sınırı 4 GB


def test_bucket_upload_ve_list(client):
    login_as(client, MANAGER)
    r = _upload(client)
    assert r.status_code == 200
    saved = r.get_json()["saved"]
    assert len(saved) == 1
    assert saved[0]["url"].endswith("/img/resim.png")
    lst = client.get("/api/tools/img-bucket/list").get_json()
    assert any(f["name"] == "resim.png" for f in lst["files"])


def test_bucket_upload_ayni_ad_benzersizlesir(client):
    login_as(client, MANAGER)
    _upload(client, name="a.png")
    r2 = _upload(client, name="a.png")
    assert r2.get_json()["saved"][0]["name"] == "a_1.png"


def test_bucket_upload_gecersiz_tip(client):
    login_as(client, MANAGER)
    r = client.post("/api/tools/img-bucket/upload",
                    data={"files": (io.BytesIO(b"x"), "kotu.exe")},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.get_json()["saved"] == []
    assert r.get_json()["errors"]


def test_bucket_delete(client):
    login_as(client, MANAGER)
    _upload(client, name="sil.png")
    r = client.post("/api/tools/img-bucket/delete", json={"name": "sil.png"},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    assert not any(f["name"] == "sil.png"
                   for f in client.get("/api/tools/img-bucket/list").get_json()["files"])


def test_bucket_delete_path_traversal_400(client):
    login_as(client, MANAGER)
    r = client.post("/api/tools/img-bucket/delete", json={"name": "../../etc/passwd"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_bucket_convert_webp(client):
    login_as(client, MANAGER)
    _upload(client, name="c.png")
    r = client.post("/api/tools/img-bucket/convert", json={"name": "c.png"},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["name"] == "c.webp"
    names = [f["name"] for f in client.get("/api/tools/img-bucket/list").get_json()["files"]]
    assert "c.webp" in names and "c.png" not in names  # orijinal silindi


def test_public_img_serve(client):
    login_as(client, MANAGER)
    _upload(client, name="pub.png")
    with client.session_transaction() as s:
        s.clear()
    r = client.get("/img/pub.png")  # auth'suz erişilebilir
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    assert len(r.get_data()) > 0
    r.close()  # test client dosya handle'ını elle kapat (ResourceWarning'i önle)


def test_public_img_gecersiz_404(client):
    assert client.get("/img/yok.png").status_code == 404
    assert client.get("/img/kotu.exe").status_code == 404
