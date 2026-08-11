"""media_store — local media store unit tests (tmp_path isolation)."""
import os
import time

import pytest


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    import media_store
    return media_store


def _jpeg_bytes(w=1200, h=900):
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (w, h), (10, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()


def test_save_ve_bul_original(store):
    ok = store.save_original("Abc-123_x", b"videodata", "video/mp4", "klip.mp4")
    assert ok is True
    path = store.find_original("Abc-123_x")
    assert path and path.endswith("Abc-123_x.mp4")
    with open(path, "rb") as f:
        assert f.read() == b"videodata"
    assert store.has_original("Abc-123_x") is True
    assert store.find_preview("Abc-123_x") is None  # video: no preview is generated


def test_gorsel_kaydinda_preview_uretilir(store):
    data = _jpeg_bytes()
    assert store.save_original("IMG1", data, "image/jpeg", "foto.jpg") is True
    prev = store.find_preview("IMG1")
    assert prev and prev.endswith("IMG1.jpg")
    from PIL import Image
    with Image.open(prev) as img:
        assert max(img.size) <= 800  # ~800px preview


def test_bozuk_gorsel_previewsuz_ama_original_kalir(store):
    # PIL can't open it → no preview, but the original should still have been written (best-effort)
    assert store.save_original("BROKEN", b"bu-gorsel-degil", "image/jpeg", "x.jpg") is True
    assert store.has_original("BROKEN") is True
    assert store.find_preview("BROKEN") is None


def test_gecersiz_file_id_reddedilir(store):
    assert store.save_original("../etc/passwd", b"x", "image/jpeg") is False
    assert store.find_original("../../x") is None
    assert store.find_preview("a/b") is None
    assert store.has_original("") is False


def test_uzanti_mimetan_turetilir(store):
    # If there's no reliable extension in the filename, derive it from mime
    store.save_original("NOEXT", b"x", "video/mp4", "uzantisiz")
    path = store.find_original("NOEXT")
    assert path and path.endswith(".mp4")


def test_cleanup_21_gunu_gecenleri_siler(store):
    store.save_original("OLD1", _jpeg_bytes(), "image/jpeg", "eski.jpg")
    store.save_original("NEW1", b"yeni", "video/mp4", "yeni.mp4")
    old_path = store.find_original("OLD1")
    old_prev = store.find_preview("OLD1")
    aged = time.time() - 22 * 86400
    os.utime(old_path, (aged, aged))
    os.utime(old_prev, (aged, aged))
    removed = store.cleanup(max_age_days=21)
    assert removed == 2  # original + preview
    assert store.find_original("OLD1") is None
    assert store.find_preview("OLD1") is None
    assert store.has_original("NEW1") is True


def test_cleanup_idempotent(store):
    store.save_original("X1", b"a", "video/mp4", "a.mp4")
    aged = time.time() - 30 * 86400
    os.utime(store.find_original("X1"), (aged, aged))
    assert store.cleanup() == 1
    assert store.cleanup() == 0  # second call silently returns 0


# --- Stream-based path (stage/commit/discard) ---

def test_stage_commit_akisi(store):
    from io import BytesIO
    tmp = store.stage(BytesIO(b"buyuk-video-icerigi"))
    assert tmp and os.path.exists(tmp)
    assert store.commit(tmp, "STREAM1", "video/mp4", "v.mp4") is True
    assert not os.path.exists(tmp)  # temp file was moved to the permanent name
    path = store.find_original("STREAM1")
    assert path and path.endswith("STREAM1.mp4")
    with open(path, "rb") as f:
        assert f.read() == b"buyuk-video-icerigi"


def test_commit_gorselde_preview_uretir(store):
    from io import BytesIO
    tmp = store.stage(BytesIO(_jpeg_bytes()))
    assert store.commit(tmp, "STREAMIMG", "image/jpeg", "f.jpg") is True
    assert store.find_preview("STREAMIMG") is not None


def test_discard_gecici_siler(store):
    from io import BytesIO
    tmp = store.stage(BytesIO(b"x"))
    store.discard(tmp)
    assert not os.path.exists(tmp)
    store.discard(None)  # None is safe
    assert store.commit(None, "X", "video/mp4") is False  # path for stage failure


def test_cleanup_bayat_tmp_siler(store):
    from io import BytesIO
    tmp = store.stage(BytesIO(b"yarim-kalmis"))
    aged = time.time() - 2 * 86400  # 2 days: hasn't hit 21 days, but has hit the tmp threshold (1 day)
    os.utime(tmp, (aged, aged))
    store.save_original("TAZE", b"t", "video/mp4", "t.mp4")  # fresh file must not be deleted
    assert store.cleanup() == 1
    assert not os.path.exists(tmp)
    assert store.has_original("TAZE") is True


# --- faststart (moving the moov atom to the front) ---
#
# WHY: in mp4s output by cameras/phones, the `moov` atom sits at the END of the file.
# The browser has to read `moov` before it can start playback → on mobile the video
# "just won't start" (2026-08-01, Android/Chrome; downloading worked because
# downloading doesn't wait for moov). `-movflags +faststart` moves it to the front, LOSSLESSLY.

def _atom_sirasi(path):
    """Return the file's top-level atom names in order (ftyp/moov/mdat...)."""
    import struct
    out = []
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        off = 0
        while off < size and len(out) < 12:
            f.seek(off)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            box, typ = struct.unpack(">I4s", hdr)
            if box == 1:
                box = struct.unpack(">Q", f.read(8))[0]
            elif box == 0:
                box = size - off
            if box <= 0:
                break
            out.append(typ.decode("latin1"))
            off += box
    return out


def _mp4_moov_sonda(path, saniye=1):
    """Produce a small mp4 with ffmpeg. In the default output, moov is at the END."""
    import subprocess
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={saniye}:size=64x64:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True)
    return path


ffmpeg_gerekli = pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="ffmpeg yok")


@ffmpeg_gerekli
def test_video_commit_moovu_basa_alir(store, tmp_path):
    from io import BytesIO
    kaynak = _mp4_moov_sonda(tmp_path / "kaynak.mp4")
    assert _atom_sirasi(kaynak).index("moov") > _atom_sirasi(kaynak).index("mdat")

    with open(kaynak, "rb") as fh:
        tmp = store.stage(BytesIO(fh.read()))
    assert store.commit(tmp, "FASTSTART1", "video/mp4", "v.mp4") is True

    sira = _atom_sirasi(store.find_original("FASTSTART1"))
    assert sira.index("moov") < sira.index("mdat"), f"moov hâlâ geride: {sira}"


@ffmpeg_gerekli
def test_faststart_videoyu_bozmaz(store, tmp_path):
    """Remux must be lossless: duration and codec are preserved, file stays playable."""
    import json
    import subprocess
    from io import BytesIO

    def probe(p):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name:format=duration", "-of", "json", str(p)],
            capture_output=True, text=True, check=True)
        d = json.loads(r.stdout)
        return (d["streams"][0]["codec_name"], round(float(d["format"]["duration"]), 1))

    kaynak = _mp4_moov_sonda(tmp_path / "k2.mp4", saniye=2)
    onceki = probe(kaynak)
    with open(kaynak, "rb") as fh:
        tmp = store.stage(BytesIO(fh.read()))
    store.commit(tmp, "FASTSTART2", "video/mp4", "v.mp4")
    assert probe(store.find_original("FASTSTART2")) == onceki


@ffmpeg_gerekli
def test_faststart_idempotent(store, tmp_path):
    """A file that's already at the front doesn't get corrupted even if remuxed a second time."""
    from io import BytesIO
    kaynak = _mp4_moov_sonda(tmp_path / "k3.mp4")
    with open(kaynak, "rb") as fh:
        tmp = store.stage(BytesIO(fh.read()))
    store.commit(tmp, "FS3", "video/mp4", "v.mp4")
    birinci = store.find_original("FS3")
    with open(birinci, "rb") as fh:
        veri = fh.read()
    tmp2 = store.stage(BytesIO(veri))
    store.commit(tmp2, "FS4", "video/mp4", "v.mp4")
    sira = _atom_sirasi(store.find_original("FS4"))
    assert sira.index("moov") < sira.index("mdat")


def test_video_olmayan_icerik_oldugu_gibi_kalir(store):
    """If ffmpeg can't decode it, the original is preserved — remux is best-effort, upload is critical."""
    from io import BytesIO
    tmp = store.stage(BytesIO(b"bu-mp4-degil"))
    assert store.commit(tmp, "BOZUKVID", "video/mp4", "v.mp4") is True
    with open(store.find_original("BOZUKVID"), "rb") as f:
        assert f.read() == b"bu-mp4-degil"


def test_gorsel_remux_edilmez(store):
    """Only video/* gets remuxed; image bytes must stay exactly as-is."""
    from io import BytesIO
    data = _jpeg_bytes()
    tmp = store.stage(BytesIO(data))
    store.commit(tmp, "IMGNOREMUX", "image/jpeg", "f.jpg")
    with open(store.find_original("IMGNOREMUX"), "rb") as f:
        assert f.read() == data
