"""media_worker.run_once — error classification (transient network error vs logic error)."""
import drive_gateway as dg
import jobqueue
import media_worker
from extensions import db
from models import Job


def _run_with_error(monkeypatch, exc):
    job_id = jobqueue.enqueue('media', {'share_id': 999}).id
    monkeypatch.setattr(media_worker, 'process', lambda job: (_ for _ in ()).throw(exc))
    assert media_worker.run_once() is True
    db.session.expire_all()
    return db.session.get(Job, job_id)


def test_drive_hatasi_transient_requeue(monkeypatch):
    # Network drop (DriveError) → NOT terminal: gets requeued with backoff.
    job = _run_with_error(monkeypatch, dg.DriveError('BrokenPipeError: [Errno 32] Broken pipe'))
    assert job.status == 'queued'
    assert job.attempts == 1


def test_oserror_transient_requeue(monkeypatch):
    # A raw socket error (OSError class) is also transient.
    job = _run_with_error(monkeypatch, BrokenPipeError(32, 'Broken pipe'))
    assert job.status == 'queued'


def test_mantik_hatasi_terminal(monkeypatch):
    # ValueError (share/file_id missing) → retry is pointless, terminal failed.
    job = _run_with_error(monkeypatch, ValueError('share/file_id yok: 999'))
    assert job.status == 'failed'


# --- file type branch: content decides, NOT `Share.kind` (2026-07-27) -------

MP4_BYTES = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2' + b'\x00' * 64
PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def _share(kind, file_name):
    from models_sharing import Share
    s = Share(client_id=1, week_iso='2026-W31', kind=kind,
              file_id='drive-xyz', file_name=file_name)
    db.session.add(s)
    db.session.commit()
    return s


def _process_share(monkeypatch, share, data, frames=(b'jpeg-kare',)):
    """Runs process() without real Drive/ffmpeg; we read which branch was
    chosen from the returned dict."""
    import media
    monkeypatch.setattr(media_worker.dg, 'download_file', lambda fid: data)
    monkeypatch.setattr(media, 'downscale_image', lambda d, **kw: b'kucuk-jpeg')
    monkeypatch.setattr(media, 'has_audio', lambda p: False)
    monkeypatch.setattr(media, 'extract_frames', lambda p, n: list(frames))
    monkeypatch.setattr(media_worker, '_write_frames', lambda fdir, fr: None)
    # The `type` field also exists on the real `Job` model; `process` branches based
    # on job type (media / web_variant, 2026-08-01) → the fake object must carry it too.
    return media_worker.process(
        type('J', (), {'type': 'media', 'payload': {'share_id': share.id}})())


def test_post_olarak_paylasilan_video_video_kolundan_gecer(monkeypatch):
    """REGRESSION (share 671): kind='post' but the file is .mp4 → does NOT go to PIL.
    The old code used to terminal-fail here with 'cannot identify image file'."""
    s = _share('post', 'rizonr0727.mp4')
    out = _process_share(monkeypatch, s, MP4_BYTES)
    assert out['kind'] == 'video'
    assert out['frame_count'] == 1


def test_gercek_gorsel_hala_gorsel_kolundan_gecer(monkeypatch):
    s = _share('post', 'Kids Home-1.png')
    out = _process_share(monkeypatch, s, PNG_BYTES)
    assert out == {'kind': 'image', 'frame_count': 1}


def test_kind_video_ama_dosya_gorsel_ise_gorsel_islenir(monkeypatch):
    """The reverse direction is also preserved — a wrong `kind` shouldn't call ffmpeg needlessly."""
    s = _share('video', 'aslinda-gorsel.png')
    out = _process_share(monkeypatch, s, PNG_BYTES)
    assert out['kind'] == 'image'


def test_kare_de_transkript_de_yoksa_anlasilir_hata_verir(monkeypatch):
    """If neither exists, the caption guard would wait forever; the job fails visibly instead."""
    import pytest
    s = _share('video', 'bozuk.mp4')
    with pytest.raises(ValueError, match='kare/transkript çıkarılamadı'):
        _process_share(monkeypatch, s, MP4_BYTES, frames=())
