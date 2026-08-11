"""Web-compatible video variant — for uploads that won't play in the browser (2026-08-01).

WHY IT EXISTS: videos shot on phones come in as 4K/60fps **HEVC Main 10** (10-bit).
Android Chrome can't open it — pressing play closes without ever showing a frame
(decoder rejection). 1080p HEVC plays fine on the same device, so the problem isn't
the codec itself but the PROFILE's weight; but since Firefox doesn't support HEVC at
all, the scope was chosen as "h264 + 8-bit + anything not ≤1080p" (allowlist).

The original is UNTOUCHED: the `/m/<id>` page plays the variant, the "Download"
button always gives the full-quality original.
"""
import os
import shutil
import subprocess

import pytest

ffmpeg_gerekli = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe yok")


def _video(path, w=64, h=64, codec="libx264", pix="yuv420p", saniye=1):
    """Generate a test video (ffmpeg lavfi source)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={saniye}:size={w}x{h}:rate=10",
         "-c:v", codec, "-pix_fmt", pix, str(path)],
        check=True)
    return str(path)


# --- media.needs_web_variant: who needs a variant ---

@ffmpeg_gerekli
def test_h264_1080p_alti_turev_gerektirmez(tmp_path):
    import media
    p = _video(tmp_path / "ok.mp4", w=320, h=240)
    assert media.needs_web_variant(p) is False


@ffmpeg_gerekli
def test_4k_turev_gerektirir(tmp_path):
    """If the long edge exceeds 1920 it should be downscaled — that was the original
    complaint."""
    import media
    p = _video(tmp_path / "4k.mp4", w=2160, h=3840)
    assert media.needs_web_variant(p) is True


@ffmpeg_gerekli
def test_hevc_turev_gerektirir(tmp_path):
    """Even at 1080p: Firefox never plays HEVC at all."""
    import media
    if not _encoder_var("libx265"):
        pytest.skip("libx265 yok")
    p = _video(tmp_path / "hevc.mp4", codec="libx265")
    assert media.needs_web_variant(p) is True


@ffmpeg_gerekli
def test_10bit_turev_gerektirir(tmp_path):
    """10-bit H.264 has no browser support."""
    import media
    p = _video(tmp_path / "10bit.mp4", pix="yuv420p10le")
    assert media.needs_web_variant(p) is True


def test_okunamayan_dosya_turev_gerektirmez(tmp_path):
    """If ffprobe can't decode it, don't touch it — don't start a transcode based on a
    guess."""
    import media
    p = tmp_path / "bozuk.mp4"
    p.write_bytes(b"bu-video-degil")
    assert media.needs_web_variant(str(p)) is False


def _encoder_var(name):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True)
    return name in out.stdout


# --- media.make_web_variant: the variant itself ---

@ffmpeg_gerekli
def test_turev_1080p_h264_faststart_uretir(tmp_path):
    import media
    src = _video(tmp_path / "src.mp4", w=2160, h=3840)
    dst = str(tmp_path / "web.mp4")
    assert media.make_web_variant(src, dst) is True

    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,width,height,pix_fmt", "-of", "csv=p=0", dst],
        capture_output=True, text=True, check=True)
    codec, w, h, pix = r.stdout.strip().split(",")
    assert codec == "h264"
    assert max(int(w), int(h)) <= 1920, "uzun kenar 1080p'ye inmeli"
    assert pix == "yuv420p", "8-bit olmalı"
    # faststart: moov comes before mdat
    with open(dst, "rb") as f:
        bas = f.read(4096)
    assert bas.index(b"moov") < bas.index(b"mdat") if b"mdat" in bas else True


@ffmpeg_gerekli
def test_turev_en_boy_oranini_korur(tmp_path):
    import media
    src = _video(tmp_path / "genis.mp4", w=3840, h=2160)  # horizontal 4K
    dst = str(tmp_path / "web2.mp4")
    assert media.make_web_variant(src, dst) is True
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", dst],
        capture_output=True, text=True, check=True)
    w, h = (int(x) for x in r.stdout.strip().split(","))
    assert (w, h) == (1920, 1080)


def test_bozuk_kaynak_turev_uretmez(tmp_path):
    import media
    src = tmp_path / "bozuk.mp4"
    src.write_bytes(b"degil")
    dst = str(tmp_path / "cikti.mp4")
    assert media.make_web_variant(str(src), dst) is False
    assert not os.path.exists(dst), "başarısız transkod yarım dosya bırakmamalı"
    assert not list(tmp_path.glob(".tmp-wv-*")), "geçici çıktı temizlenmeli"


def test_basarisiz_transkod_mevcut_turevi_bozmaz(tmp_path):
    """If it wrote DIRECTLY to the destination: a backfill and the worker landing on
    the same file at once would leave a half-corrupt variant. Writing uses a temp
    file + atomic rename."""
    import media
    dst = tmp_path / "var.mp4"
    dst.write_bytes(b"onceki-saglam-turev")
    bozuk = tmp_path / "bozuk.mp4"
    bozuk.write_bytes(b"degil")
    assert media.make_web_variant(str(bozuk), str(dst)) is False
    assert dst.read_bytes() == b"onceki-saglam-turev", "mevcut türev ezilmemeli"


# --- media_store: storing the variant ---

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    import media_store
    return media_store


def test_web_turevi_bulunur_ve_silinir(store):
    p = store.web_path("WEBID1")
    with open(p, "wb") as f:
        f.write(b"turev")
    assert store.find_web("WEBID1") == p
    # when the original is deleted the variant should go too — otherwise a deleted
    # video's variant could still be played via /m/<id> for another 21 days
    store.save_original("WEBID1", b"asil", "video/mp4", "v.mp4")
    assert store.remove("WEBID1") >= 2
    assert store.find_web("WEBID1") is None


def test_web_turevi_gecersiz_idde_yok(store):
    assert store.find_web("../kacis") is None
    assert store.web_path("../kacis") is None


def test_cleanup_web_turevini_de_siler(store):
    import time
    store.save_original("ESKI1", b"x", "video/mp4", "v.mp4")
    p = store.web_path("ESKI1")
    with open(p, "wb") as f:
        f.write(b"turev")
    aged = time.time() - 30 * 86400
    os.utime(store.find_original("ESKI1"), (aged, aged))
    os.utime(p, (aged, aged))
    assert store.cleanup() == 2
    assert store.find_web("ESKI1") is None


# --- /m/<file_id>: variant plays, download gives the original ---

def _kayit(cid, file_id, name="klip.mp4"):
    from extensions import db
    from models_sharing import CardUpload
    u = CardUpload(client_id=cid, week_iso="2026-W31", category="video",
                   file_id=file_id, file_name=name)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def cid(client):
    from conftest import MANAGER, login_as
    from test_session_csrf import csrf_headers
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Türev Müşteri"},
                    headers=csrf_headers(client))
    c = r.get_json()["client"]["id"]
    client.get("/auth/logout")     # endpoint should work without a session
    return c


def test_sayfa_turev_varsa_onu_oynatir(client, cid, monkeypatch, tmp_path):
    import media_store
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    _kayit(cid, "TUREVLI00001")
    media_store.save_original("TUREVLI00001", b"asil-4k", "video/mp4", "klip.mp4")
    with open(media_store.web_path("TUREVLI00001"), "wb") as f:
        f.write(b"turev-1080p")

    html_ = client.get("/m/TUREVLI00001").get_data(as_text=True)
    assert "raw=1&amp;web=1" in html_, "oynatıcı türevi kullanmalı"
    # Download button should go to the ORIGINAL, NOT the variant
    assert "?dl=1" in html_ and "dl=1&amp;web=1" not in html_


def test_turev_yoksa_sayfa_orijinali_oynatir(client, cid, monkeypatch, tmp_path):
    import media_store
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    _kayit(cid, "TUREVSIZ00001")
    media_store.save_original("TUREVSIZ00001", b"asil", "video/mp4", "klip.mp4")
    html_ = client.get("/m/TUREVSIZ00001").get_data(as_text=True)
    assert "web=1" not in html_


def test_web_bayragi_turevi_servis_eder(client, cid, monkeypatch, tmp_path):
    import media_store
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    _kayit(cid, "TUREVLI00002", name="klip.mov")   # original .mov → mime trap
    media_store.save_original("TUREVLI00002", b"asil-mov", "video/quicktime", "klip.mov")
    with open(media_store.web_path("TUREVLI00002"), "wb") as f:
        f.write(b"turev-mp4")

    r = client.get("/m/TUREVLI00002?raw=1&web=1")
    try:
        assert r.status_code == 200
        assert r.get_data() == b"turev-mp4"
        assert r.mimetype == "video/mp4", "türev .mov adından quicktime sanılmamalı"
    finally:
        r.close()          # send_file file handle — closed manually


def test_indirme_daima_orijinali_verir(client, cid, monkeypatch, tmp_path):
    """A videographer's 4K file shouldn't come back as 1080p from the link."""
    import media_store
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    _kayit(cid, "TUREVLI00003")
    media_store.save_original("TUREVLI00003", b"asil-4k-tam-kalite", "video/mp4", "klip.mp4")
    with open(media_store.web_path("TUREVLI00003"), "wb") as f:
        f.write(b"turev")

    r = client.get("/m/TUREVLI00003?dl=1&web=1")      # even if web=1 is given
    try:
        assert r.get_data() == b"asil-4k-tam-kalite"
    finally:
        r.close()


# --- media_worker: the web_variant job ---

def test_worker_turev_uretir(client, monkeypatch, tmp_path):
    import jobqueue
    import media
    import media_store
    import media_worker
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    media_store.save_original("WVJOB1", b"video-baytlari", "video/mp4", "v.mp4")
    monkeypatch.setattr(media, "needs_web_variant", lambda p: True)

    def sahte_transkod(src, dst, timeout=1800):
        with open(dst, "wb") as f:
            f.write(b"uretilmis-turev")
        return True
    monkeypatch.setattr(media, "make_web_variant", sahte_transkod)

    jobqueue.enqueue("web_variant", {"file_id": "WVJOB1"})
    assert media_worker.run_once() is True
    assert media_store.find_web("WVJOB1") is not None
    from models import Job
    assert Job.query.first().result["ok"] is True


def test_worker_uyumlu_videoyu_atlar(client, monkeypatch, tmp_path):
    """CPU shouldn't be wasted on a video that's already h264/1080p."""
    import jobqueue
    import media
    import media_store
    import media_worker
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    media_store.save_original("WVJOB2", b"x", "video/mp4", "v.mp4")
    monkeypatch.setattr(media, "needs_web_variant", lambda p: False)
    monkeypatch.setattr(media, "make_web_variant",
                        lambda *a, **k: pytest.fail("transkod çağrılmamalıydı"))

    jobqueue.enqueue("web_variant", {"file_id": "WVJOB2"})
    assert media_worker.run_once() is True
    from models import Job
    assert "zaten tarayıcı uyumlu" in Job.query.first().result["skipped"]


def test_worker_orijinal_yoksa_atlar(client, monkeypatch, tmp_path):
    import jobqueue
    import media_worker
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    jobqueue.enqueue("web_variant", {"file_id": "YOKBOYLE"})
    assert media_worker.run_once() is True
    from models import Job
    assert "lokal orijinal yok" in Job.query.first().result["skipped"]
