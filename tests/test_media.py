"""Media helpers (with a real ffmpeg test video) + media_worker (mocked)."""
import os
import subprocess
import tempfile

import pytest

import media
import media_worker

_HAS_FFMPEG = subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0
ffmpeg_only = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not available")


def _make_video(with_audio, seconds=1):
    """Generate an lavfi test video with ffmpeg; returns (path). With/without audio."""
    path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-pix_fmt", "yuv420p", "-shortest", path]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return path


@ffmpeg_only
def test_has_audio_true_false():
    v_audio = _make_video(True)
    v_silent = _make_video(False)
    try:
        assert media.has_audio(v_audio) is True
        assert media.has_audio(v_silent) is False
    finally:
        os.remove(v_audio); os.remove(v_silent)


@ffmpeg_only
def test_extract_frames():
    v = _make_video(False, seconds=2)
    try:
        frames = media.extract_frames(v, 3)
        assert len(frames) >= 1
        assert all(f[:2] == b"\xff\xd8" for f in frames)  # JPEG signature
    finally:
        os.remove(v)


@ffmpeg_only
def test_extract_audio_wav():
    v = _make_video(True)
    try:
        wav = media.extract_audio(v)
        assert wav[:4] == b"RIFF"  # WAV signature
    finally:
        os.remove(v)


# --- media_worker (Drive/ffmpeg/whisper mocked) ---

def test_media_worker_sesli_video(client, monkeypatch):
    import jobqueue
    from extensions import db
    from models import Client
    from models_sharing import DriveThumbnail, Share
    c = Client(name="Vid", status="active")
    db.session.add(c)
    db.session.commit()
    s = Share(client_id=c.id, week_iso="2026-W21", kind="video", status="draft", file_id="VID1")
    db.session.add(s)
    db.session.commit()

    monkeypatch.setattr(dg_download := __import__("drive_gateway"), "download_file", lambda fid: b"fakevideo")
    monkeypatch.setattr(media, "has_audio", lambda p: True)
    monkeypatch.setattr(media, "extract_audio", lambda p: b"wav")
    monkeypatch.setattr(media, "transcribe", lambda b, **k: "merhaba bu videonun sesi")
    monkeypatch.setattr(media, "extract_frames", lambda p, n=3: [b"\xff\xd8jpegframe"])

    # If use_transcript=True is requested, the whisper transcript is extracted and written to the share.
    jobqueue.enqueue("media", {"share_id": s.id, "use_transcript": True})
    assert media_worker.run_once() is True
    db.session.refresh(s)
    assert s.transcript == "merhaba bu videonun sesi"
    # first frame was written to the thumbnail cache (video 404 workaround)
    assert db.session.get(DriveThumbnail, ("VID1", 300)) is not None
    from models import Job
    assert Job.query.first().result["has_audio"] is True


def test_media_worker_video_transkript_opsiyonel_varsayilan_kapali(client, monkeypatch):
    """If use_transcript is not given (default): whisper does NOT run even if there's
    audio, transcript stays None; frames are still extracted (visual context is preserved)."""
    import jobqueue
    from extensions import db
    from models import Client, Job
    from models_sharing import Share
    c = Client(name="Vid2", status="active")
    db.session.add(c)
    db.session.commit()
    s = Share(client_id=c.id, week_iso="2026-W21", kind="video", status="draft", file_id="VID3")
    db.session.add(s)
    db.session.commit()

    monkeypatch.setattr(__import__("drive_gateway"), "download_file", lambda fid: b"fakevideo")
    monkeypatch.setattr(media, "has_audio", lambda p: True)
    called = {"transcribe": False}
    def _tr(b, **k):
        called["transcribe"] = True
        return "OLMAMALI"
    monkeypatch.setattr(media, "extract_audio", lambda p: b"wav")
    monkeypatch.setattr(media, "transcribe", _tr)
    monkeypatch.setattr(media, "extract_frames", lambda p, n=3: [b"\xff\xd8jpegframe"])

    jobqueue.enqueue("media", {"share_id": s.id})  # no use_transcript
    assert media_worker.run_once() is True
    db.session.refresh(s)
    assert s.transcript is None            # transcript was not extracted
    assert called["transcribe"] is False   # whisper was never called
    assert Job.query.first().result["frame_count"] == 1  # frame was still written


def test_downscale_image_genislik_ve_jpeg():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1000), (5, 5, 5)).save(buf, "PNG")
    out = media.downscale_image(buf.getvalue(), max_w=1280)
    assert out[:2] == b"\xff\xd8"  # JPEG
    im = Image.open(io.BytesIO(out))
    assert im.width == 1280 and abs(im.height - 427) <= 1  # aspect ratio preserved


def test_downscale_image_kucukse_buyutmez():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(buf, "PNG")
    out = media.downscale_image(buf.getvalue(), max_w=1280)
    im = Image.open(io.BytesIO(out))
    assert im.width == 400 and im.height == 300  # already small


def test_media_worker_gorsel_kareyi_kaydeder(client, monkeypatch):
    """Image post: download from Drive → downscale → save to frames directory (caption context)."""
    import io
    import glob
    import shutil
    import jobqueue
    from PIL import Image
    from extensions import db
    from models import Client, Job
    from models_sharing import Share
    c = Client(name="Foto", status="active")
    db.session.add(c)
    db.session.commit()
    s = Share(client_id=c.id, week_iso="2026-W21", kind="post", status="draft", file_id="IMG1")
    db.session.add(s)
    db.session.commit()
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), (10, 120, 90)).save(buf, "PNG")
    monkeypatch.setattr(__import__("drive_gateway"), "download_file", lambda fid: buf.getvalue())
    jobqueue.enqueue("media", {"share_id": s.id})
    fdir = os.path.join(media_worker.app.root_path, "data", "frames", str(s.id))
    try:
        assert media_worker.run_once() is True
        frames = glob.glob(os.path.join(fdir, "*.jpg"))
        assert len(frames) == 1
        assert Job.query.first().result["kind"] == "image"
        with open(frames[0], "rb") as f:
            assert f.read(2) == b"\xff\xd8"  # JPEG
    finally:
        shutil.rmtree(fdir, ignore_errors=True)


def test_media_worker_sessiz_video_transcript_yok(client, monkeypatch):
    import jobqueue
    from extensions import db
    from models import Client
    from models_sharing import Share
    c = Client(name="Vid2", status="active")
    db.session.add(c)
    db.session.commit()
    s = Share(client_id=c.id, week_iso="2026-W21", kind="video", status="draft", file_id="VID2")
    db.session.add(s)
    db.session.commit()
    monkeypatch.setattr(__import__("drive_gateway"), "download_file", lambda fid: b"x")
    monkeypatch.setattr(media, "has_audio", lambda p: False)
    # A frame is produced (as in a real video); what's being tested is the ABSENCE of audio.
    # If there's neither a frame nor a transcript, the job now deliberately fails — that
    # path is covered in test_media_worker.py::test_kare_cikmayan_video_anlasilir_hata_verir.
    monkeypatch.setattr(media, "extract_frames", lambda p, n=3: [b"\xff\xd8kare"])
    monkeypatch.setattr(media_worker, "_write_frames", lambda fdir, fr: None)
    jobqueue.enqueue("media", {"share_id": s.id})
    assert media_worker.run_once() is True
    db.session.refresh(s)
    assert s.transcript is None
    from models import Job
    assert Job.query.first().result["has_audio"] is False


# --- transcript vocabulary (2026-08-08) ---------------------------------------
#
# Behavior verified by measurement: when brand/team names are passed to whisper
# as `initial_prompt`, the names in the list come out correctly ("Busto Mahallesi
# hanesi" → "Mavi Nova"). The tests here lock in the vocabulary's GENERATION and
# end-to-end PASS-THROUGH — not transcription quality (that's whisper's job, mocked).

def test_sozluk_musteri_ve_ekip_adlarini_icerir(client):
    import ai_context
    from extensions import db
    from models import Client, UserRef
    db.session.add(Client(name="Mavi Nova"))
    db.session.add(UserRef(sub="s1", email="a@b.c", name="Deniz Yıldız"))
    db.session.commit()
    v = ai_context.transcript_vocabulary()
    assert "Mavi Nova" in v
    assert "Deniz Yıldız" in v
    assert "Kotar" in v          # agency constant — can't be learned from any record


def test_sozluk_silinmis_musteriyi_almaz(client):
    import ai_context
    from extensions import db
    from models import Client, utcnow
    db.session.add(Client(name="Kapanan Marka", deleted_at=utcnow()))
    db.session.commit()
    assert "Kapanan Marka" not in ai_context.transcript_vocabulary()


def test_sozluk_extra_basa_gelir(client):
    """If truncation happens, the video's own client must survive → at the very front."""
    import ai_context
    v = ai_context.transcript_vocabulary(extra=["Öncelikli Marka"])
    assert v.startswith("Öncelikli Marka")


def test_sozluk_tekrari_turkce_duyarli_eler(client):
    import ai_context
    from extensions import db
    from models import Client
    db.session.add(Client(name="IDEAL"))
    db.session.add(Client(name="İdeal"))
    db.session.commit()
    v = ai_context.transcript_vocabulary()
    # 'IDEAL' and 'İdeal' count as the same name (TR casefold) — one should be eliminated.
    assert sum(1 for a in v.rstrip('.').split(', ') if a.casefold() in ('ideal',)) <= 1


def test_sozluk_karakter_sinirini_asmaz(client):
    import ai_context
    from extensions import db
    from models import Client
    for i in range(200):
        db.session.add(Client(name=f"Çok Uzun Marka Adı Numara {i:03d}"))
    db.session.commit()
    v = ai_context.transcript_vocabulary()
    assert len(v) <= ai_context.VOCAB_MAX_CHARS + 1     # +1: trailing period
    # Truncation must not happen in the MIDDLE of a name — each piece must be a complete name.
    assert not v.rstrip('.').split(', ')[-1].endswith(('Numara', 'Marka'))


def test_transcribe_initial_prompt_gonderir(monkeypatch):
    """media.transcribe must pass the vocabulary to the whisper service as a form field."""
    import media
    yakalanan = {}

    class _Yanit:
        def raise_for_status(self): pass
        def json(self): return {"text": "merhaba"}

    def _post(url, files=None, data=None, timeout=None):
        yakalanan.update(data or {})
        return _Yanit()

    monkeypatch.setattr(media.requests, "post", _post)
    media.transcribe(b"ses", initial_prompt="Mavi Nova, Kotar.")
    assert yakalanan["initial_prompt"] == "Mavi Nova, Kotar."


def test_transcribe_sozluksuz_alan_gondermez(monkeypatch):
    """If there's no vocabulary, the field must not be sent at all — the service should
    keep its old behavior (the endpoint is shared: notion-asistan also calls the same
    service)."""
    import media
    yakalanan = {}

    class _Yanit:
        def raise_for_status(self): pass
        def json(self): return {"text": ""}

    monkeypatch.setattr(media.requests, "post",
                        lambda url, files=None, data=None, timeout=None:
                        (yakalanan.update(data or {}), _Yanit())[1])
    media.transcribe(b"ses")
    assert "initial_prompt" not in yakalanan
