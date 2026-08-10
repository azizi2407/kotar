"""Görsel araçları — image_splitter (split fonksiyonu + /api/tools/image-split)."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

from PIL import Image

import image_tools


def _img_bytes(w, h, fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, fmt)
    return buf.getvalue()


# --- pure fonksiyon ---

def test_split_wide_uc_parca():
    img = Image.new("RGB", (3120, 1350))
    parts = image_tools.split_wide(img)
    assert len(parts) == 3
    assert all(p.size == (1080, 1350) for p in parts)


def test_split_wide_yanlis_boyut_hata():
    with pytest.raises(image_tools.ImageToolError):
        image_tools.split_wide(Image.new("RGB", (1000, 1000)))


def test_split_wide_bytes_isim_ve_uzanti():
    parts = image_tools.split_wide_bytes(_img_bytes(3120, 1350), "png")
    assert [n for n, _, _ in parts] == [
        "bolunmus_gorsel_1.png", "bolunmus_gorsel_2.png", "bolunmus_gorsel_3.png"]


# --- endpoint ---

def _post(client, data, name="w.png"):
    return client.post("/api/tools/image-split",
                       data={"image": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", headers=csrf_headers(client))


def test_image_split_oturumsuz_403(client):
    # CSRF gate önce (POST) → 403
    r = client.post("/api/tools/image-split")
    assert r.status_code == 403


def test_image_split_designer_izinli(client):
    login_as(client, DESIGNER)
    r = _post(client, _img_bytes(3120, 1350))
    assert r.status_code == 200
    assert len(r.get_json()["pieces"]) == 3
    assert r.get_json()["pieces"][0]["data_url"].startswith("data:image/")


def test_image_split_gorselsiz_400(client):
    login_as(client, MANAGER)
    r = client.post("/api/tools/image-split", data={},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 400


def test_image_split_yanlis_boyut_400(client):
    login_as(client, MANAGER)
    r = _post(client, _img_bytes(1000, 1000))
    assert r.status_code == 400
    assert "3120" in r.get_json()["error"]


# --- reels-cover (video kapağı) alt modu ---

def test_make_reels_cover_boyutlar():
    img = Image.new("RGB", (3120, 1350), (40, 80, 120))
    parts = image_tools.make_reels_cover(img)
    # sol (1080×1350) · orta video kapağı (1080×1920) · sağ (1080×1350)
    assert [p.size for p in parts] == [(1080, 1350), (1080, 1920), (1080, 1350)]


def test_make_reels_cover_yanlis_boyut_hata():
    with pytest.raises(image_tools.ImageToolError):
        image_tools.make_reels_cover(Image.new("RGB", (1000, 1000)))


def test_split_wide_bytes_reels_isim():
    parts = image_tools.split_wide_bytes(_img_bytes(3120, 1350), "png", reels=True)
    assert [n for n, _, _ in parts] == [
        "bolunmus_gorsel_1.png", "instagram_video_kapagi.png", "bolunmus_gorsel_3.png"]


def test_image_split_reels_modu(client):
    login_as(client, DESIGNER)
    r = client.post("/api/tools/image-split",
                    data={"image": (io.BytesIO(_img_bytes(3120, 1350)), "w.png"), "mode": "reels"},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 200
    pieces = r.get_json()["pieces"]
    assert len(pieces) == 3
    assert pieces[1]["name"] == "instagram_video_kapagi.png"
    assert pieces[1]["is_cover"] is True


def test_image_split_zip_ciktisi(client):
    # Yanıt, parçaları içeren indirilebilir bir zip data-URL'i de taşır.
    import base64
    import zipfile
    login_as(client, DESIGNER)
    r = _post(client, _img_bytes(3120, 1350), name="kampanya.png")
    d = r.get_json()
    assert d["zip_name"] == "kampanya-parcalar.zip"
    prefix = "data:application/zip;base64,"
    assert d["zip_data_url"].startswith(prefix)
    zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(d["zip_data_url"][len(prefix):])))
    assert zf.namelist() == [p["name"] for p in d["pieces"]]
    assert zf.testzip() is None
