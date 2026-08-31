"""Image tools — image_splitter (split function + /api/tools/image-split)."""
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


# --- pure function ---

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
        "split_image_1.png", "split_image_2.png", "split_image_3.png"]


# --- endpoint ---

def _post(client, data, name="w.png"):
    return client.post("/api/tools/image-split",
                       data={"image": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", headers=csrf_headers(client))


def test_image_split_oturumsuz_403(client):
    # CSRF gate first (POST) → 403
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


# --- reels-cover (video cover) submode — all THREE are covers ---

def test_make_reels_cover_boyutlar():
    """Whichever tile the video is (left/center/right), all return as 1080×1920 covers."""
    img = Image.new("RGB", (3120, 1350), (40, 80, 120))
    parts = image_tools.make_reels_cover(img)
    assert [p.size for p in parts] == [(1080, 1920)] * 3


def test_make_reels_cover_yanlis_boyut_hata():
    with pytest.raises(image_tools.ImageToolError):
        image_tools.make_reels_cover(Image.new("RGB", (1000, 1000)))


def test_split_wide_bytes_reels_isim():
    parts = image_tools.split_wide_bytes(_img_bytes(3120, 1350), "png", reels=True)
    assert [n for n, _, _ in parts] == [
        "instagram_video_cover_1.png", "instagram_video_cover_2.png",
        "instagram_video_cover_3.png"]


def test_image_split_reels_modu(client):
    login_as(client, DESIGNER)
    r = client.post("/api/tools/image-split",
                    data={"image": (io.BytesIO(_img_bytes(3120, 1350)), "w.png"), "mode": "reels"},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 200
    pieces = r.get_json()["pieces"]
    assert len(pieces) == 3
    assert [p["name"] for p in pieces] == [
        "instagram_video_cover_1.png", "instagram_video_cover_2.png",
        "instagram_video_cover_3.png"]
    assert all(p["is_cover"] is True for p in pieces)


def test_play_overlay_kaynak_daire_alfasi_sabitle_tutarli():
    """Does `_PLAY_CIRCLE_SRC_OPACITY` (133/255) match the ACTUAL circle alpha of
    `assets/play_overlay.png`? If the asset gets re-exported some day and the
    circle alpha shifts, this constant silently becomes wrong — no other test
    would catch it."""
    from collections import Counter
    play = image_tools._load_play_overlay()
    most_common = Counter(play.split()[3].tobytes()).most_common(3)
    circle_alpha = next(v for v, _ in most_common if v not in (0, 255))
    assert circle_alpha == round(image_tools._PLAY_CIRCLE_SRC_OPACITY * 255)


def test_play_overlay_daire_yuzde_40_ucgen_tam_opak():
    """The play button transform: the circle drops to 40% opacity, the triangle
    (alpha=255 in the source PNG) stays UNTOUCHED."""
    assert image_tools._scale_circle_alpha(255) == 255
    # source circle alpha ~133 (52%) → scaled to 40% it should be ~102
    assert abs(image_tools._scale_circle_alpha(133) - 102) <= 1
    assert image_tools._scale_circle_alpha(0) == 0


def test_reels_kapaginda_play_overlay_uygulanmis():
    """End-to-end sanity check: the cover has both fully opaque (triangle) and
    partially transparent (circle) pixels — catches the case where the overlay
    is not composited AT ALL (e.g. the `_apply_play_overlay` call dropping out
    of `_to_cover`). It does NOT prove the scale (40%) is applied correctly —
    see `test_apply_play_overlay_daire_olcegini_cagirir`'s docstring: the
    composite alphas of those two cases coincide within measurement error."""
    img = Image.new("RGB", (3120, 1350), (40, 80, 120))
    cover = image_tools.make_reels_cover(img)[0]
    assert cover.mode == "RGBA"
    # getdata() is deprecated in Pillow (removed in 14) → raw alpha bytes via tobytes().
    alphas = {a for a in cover.split()[3].tobytes() if a not in (0, 255)}
    assert alphas, "no partially transparent pixel found in the circle area"


def test_apply_play_overlay_daire_olcegini_cagirir(monkeypatch):
    """Verifies `_apply_play_overlay` ACTUALLY calls `_scale_circle_alpha`.

    Why call-tracing instead of pixel values: `result.paste(play, xy, play)`
    uses the source's OWN alpha as both content and mask (RGBA-as-own-mask
    paste) — so the COMPOSITE alpha of the scaled (source_alpha=102, 40%) and
    unscaled (source_alpha=133, source 52%) cases nearly coincide when
    measured (194 vs 191), and even though the final RGB color changes
    visibly, the raw alpha channel cannot reliably distinguish them. Tracing
    the wiring directly — this test goes red if the putalpha line is deleted —
    is the sturdier regression gate."""
    calls = []
    original = image_tools._scale_circle_alpha

    def traced(v):
        calls.append(v)
        return original(v)

    monkeypatch.setattr(image_tools, "_scale_circle_alpha", traced)
    cover = Image.new("RGBA", (image_tools.TILE_W, image_tools.REELS_H), (0, 0, 0, 255))
    image_tools._apply_play_overlay(cover)
    assert calls, "_scale_circle_alpha was never called — the play circle may no longer be scaled"


def test_image_split_zip_ciktisi(client):
    # The response also carries a downloadable zip data-URL containing the pieces.
    import base64
    import zipfile
    login_as(client, DESIGNER)
    r = _post(client, _img_bytes(3120, 1350), name="kampanya.png")
    d = r.get_json()
    assert d["zip_name"] == "kampanya-pieces.zip"
    prefix = "data:application/zip;base64,"
    assert d["zip_data_url"].startswith(prefix)
    zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(d["zip_data_url"][len(prefix):])))
    assert zf.namelist() == [p["name"] for p in d["pieces"]]
    assert zf.testzip() is None
