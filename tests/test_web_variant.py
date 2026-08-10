"""Web uyumlu video türevi — tarayıcıda oynamayan yüklemeler için (2026-08-01).

NEDEN VAR: telefonla çekilen videolar 4K/60fps **HEVC Main 10** (10-bit) olarak
geliyor. Android Chrome bunu açamıyor — oynatmaya basınca hiç görüntü vermeden
kapanıyor (decoder reddi). 1080p HEVC aynı cihazda oynuyor, yani sorun codec'in
kendisi değil PROFİLİN ağırlığı; ama Firefox HEVC'yi hiç desteklemediği için
kapsam "h264 + 8-bit + ≤1080p olmayan her video" olarak seçildi (beyaz liste).

Orijinal DOKUNULMAZ: `/m/<id>` sayfası türevi oynatır, "İndir" düğmesi hep tam
kaliteli orijinali verir.
"""
import os
import shutil
import subprocess

import pytest

ffmpeg_gerekli = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe yok")


def _video(path, w=64, h=64, codec="libx264", pix="yuv420p", saniye=1):
    """Test videosu üret (ffmpeg lavfi kaynağı)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={saniye}:size={w}x{h}:rate=10",
         "-c:v", codec, "-pix_fmt", pix, str(path)],
        check=True)
    return str(path)


# --- media.needs_web_variant: kimin türevi gerekiyor ---

@ffmpeg_gerekli
def test_h264_1080p_alti_turev_gerektirmez(tmp_path):
    import media
    p = _video(tmp_path / "ok.mp4", w=320, h=240)
    assert media.needs_web_variant(p) is False


@ffmpeg_gerekli
def test_4k_turev_gerektirir(tmp_path):
    """Uzun kenar 1920'yi aşıyorsa küçültülmeli — asıl şikâyet buydu."""
    import media
    p = _video(tmp_path / "4k.mp4", w=2160, h=3840)
    assert media.needs_web_variant(p) is True


@ffmpeg_gerekli
def test_hevc_turev_gerektirir(tmp_path):
    """1080p bile olsa: Firefox HEVC'yi hiç oynatmaz."""
    import media
    if not _encoder_var("libx265"):
        pytest.skip("libx265 yok")
    p = _video(tmp_path / "hevc.mp4", codec="libx265")
    assert media.needs_web_variant(p) is True


@ffmpeg_gerekli
def test_10bit_turev_gerektirir(tmp_path):
    """10-bit H.264'ün tarayıcı desteği yok."""
    import media
    p = _video(tmp_path / "10bit.mp4", pix="yuv420p10le")
    assert media.needs_web_variant(p) is True


def test_okunamayan_dosya_turev_gerektirmez(tmp_path):
    """ffprobe çözemiyorsa dokunma — tahminle transkod başlatma."""
    import media
    p = tmp_path / "bozuk.mp4"
    p.write_bytes(b"bu-video-degil")
    assert media.needs_web_variant(str(p)) is False


def _encoder_var(name):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True)
    return name in out.stdout


# --- media.make_web_variant: türevin kendisi ---

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
    # faststart: moov, mdat'tan önce
    with open(dst, "rb") as f:
        bas = f.read(4096)
    assert bas.index(b"moov") < bas.index(b"mdat") if b"mdat" in bas else True


@ffmpeg_gerekli
def test_turev_en_boy_oranini_korur(tmp_path):
    import media
    src = _video(tmp_path / "genis.mp4", w=3840, h=2160)  # yatay 4K
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
    """Hedefe DOĞRUDAN yazılsaydı: geri dolum ile worker aynı dosyaya denk
    gelince yarısı bozuk türev kalırdı. Yazım geçici dosya + atomik rename."""
    import media
    dst = tmp_path / "var.mp4"
    dst.write_bytes(b"onceki-saglam-turev")
    bozuk = tmp_path / "bozuk.mp4"
    bozuk.write_bytes(b"degil")
    assert media.make_web_variant(str(bozuk), str(dst)) is False
    assert dst.read_bytes() == b"onceki-saglam-turev", "mevcut türev ezilmemeli"


# --- media_store: türevin saklanması ---

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
    # orijinal silinince türev de gitmeli — yoksa silinen videonun türevi
    # /m/<id> üzerinden 21 gün daha oynatılabilirdi
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


# --- /m/<file_id>: türev oynatılır, indirme orijinali verir ---

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
    client.get("/auth/logout")     # uç oturumsuz çalışmalı
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
    # İndir düğmesi türeve DEĞİL orijinale gitmeli
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
    _kayit(cid, "TUREVLI00002", name="klip.mov")   # orijinal .mov → mime tuzağı
    media_store.save_original("TUREVLI00002", b"asil-mov", "video/quicktime", "klip.mov")
    with open(media_store.web_path("TUREVLI00002"), "wb") as f:
        f.write(b"turev-mp4")

    r = client.get("/m/TUREVLI00002?raw=1&web=1")
    try:
        assert r.status_code == 200
        assert r.get_data() == b"turev-mp4"
        assert r.mimetype == "video/mp4", "türev .mov adından quicktime sanılmamalı"
    finally:
        r.close()          # send_file dosya tanıtıcısı — elle kapatılır


def test_indirme_daima_orijinali_verir(client, cid, monkeypatch, tmp_path):
    """4K çeken videografın dosyası linkten 1080p dönmemeli."""
    import media_store
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    _kayit(cid, "TUREVLI00003")
    media_store.save_original("TUREVLI00003", b"asil-4k-tam-kalite", "video/mp4", "klip.mp4")
    with open(media_store.web_path("TUREVLI00003"), "wb") as f:
        f.write(b"turev")

    r = client.get("/m/TUREVLI00003?dl=1&web=1")      # web=1 verilse bile
    try:
        assert r.get_data() == b"asil-4k-tam-kalite"
    finally:
        r.close()


# --- media_worker: web_variant işi ---

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
    """Zaten h264/1080p olan videoya boşuna CPU harcanmamalı."""
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
