"""Media worker — runs under the project owner's context (Drive download + ffmpeg + whisper service).

Pulls 'media' jobs from the Postgres queue: download the video from Drive → if
it has audio, whisper transcript → write to share.transcript; extract a few
frames → store the first as the video thumbnail (works around Drive's video
thumbnail 404). systemd --user (project owner), Infisical-injected
(GOOGLE_SA_JSON) — for Drive downloads.
"""
import glob
import os
import tempfile
import time

import ai_context
import drive_gateway as dg
import jobqueue
import media
import media_store
from app import app
from extensions import db
from models import Client
from models_sharing import DriveThumbnail, Share

POLL_SECONDS = 5
CLEANUP_INTERVAL = 3600  # while idle, delete expired local copies once an hour
THUMB_WIDTHS = (300, 600)


def _frames_dir(share_id):
    """Shared directory where ai_worker can read frames (data/, gitignored)."""
    d = os.path.join(app.root_path, 'data', 'frames', str(share_id))
    os.makedirs(d, exist_ok=True)
    return d


def _write_frames(fdir, frames):
    """Delete old frames, write new ones as frame{i}.jpg."""
    for old in glob.glob(os.path.join(fdir, '*.jpg')):
        os.remove(old)
    for i, fr in enumerate(frames):
        with open(os.path.join(fdir, f'frame{i}.jpg'), 'wb') as f:
            f.write(fr)


def process_web_variant(job):
    """Produce a browser-compatible 1080p H.264 derivative (into `media_store`'s `web/`).

    WHY A SEPARATE JOB: transcoding a 4K/30s video takes ~1 min of CPU — can't
    run inside the upload request (gunicorn 2 workers × 4 threads, 500 MB uploads
    already occupy a thread). The upload enqueues to `jobqueue`, this produces it
    in the background; until it's ready, `/m/<id>` tries playing the original.

    The original is UNTOUCHED — the "Download" button keeps giving full quality."""
    file_id = (job.payload or {}).get('file_id')
    if not file_id:
        raise ValueError('web_variant: file_id yok')
    if media_store.find_web(file_id):
        return {'skipped': 'türev zaten var', 'file_id': file_id}
    src = media_store.find_original(file_id)
    if not src:
        # Either the 21-day window has expired or the file was deleted; we don't
        # re-download from Drive — once that copy is gone, `/m/` already redirects to Drive anyway.
        return {'skipped': 'lokal orijinal yok', 'file_id': file_id}
    if not media.needs_web_variant(src):
        return {'skipped': 'zaten tarayıcı uyumlu', 'file_id': file_id}
    ok = media.make_web_variant(src, media_store.web_path(file_id))
    return {'ok': ok, 'file_id': file_id,
            'size': os.path.getsize(media_store.web_path(file_id)) if ok else 0}


def process(job):
    if job.type == 'web_variant':
        return process_web_variant(job)
    share_id = (job.payload or {}).get('share_id')
    share = db.session.get(Share, share_id)
    if share is None or not share.file_id:
        raise ValueError(f'share/file_id yok: {share_id}')
    data = dg.download_file(share.file_id)

    # Which path to take is determined from CONTENT — `share.kind` is the
    # PUBLISHING type (post/story/reel), not the file type: a video can be shared
    # as a "post". Trusting `kind` used to break the chain by feeding a .mp4 to
    # PIL and getting "cannot identify image file" (share 671, 2026-07-27).
    # `kind` is only used as a hint when both content AND extension fail to identify it.
    hint = 'video' if share.kind == 'video' else 'image'
    kind = media.resolve_kind(data, share.file_name, fallback=hint)

    # Image share: downscale → store as caption's visual context.
    # No ffprobe/whisper; we write the frame directly from the image.
    if kind == 'image':
        frame = media.downscale_image(data)
        _write_frames(_frames_dir(share.id), [frame])
        return {'kind': 'image', 'frame_count': 1}

    suffix = os.path.splitext(share.file_name or '')[1].lower() or '.mp4'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        audio = media.has_audio(path)
        transcript = ''
        # Transcript is OPTIONAL (2026-07-18): whisper only runs if use_transcript
        # is requested in the job payload. Default off → speed + no unnecessary audio processing.
        use_transcript = bool((job.payload or {}).get('use_transcript'))
        if audio and use_transcript:
            # Vocabulary: this share's client goes FIRST (so it survives if
            # truncated — the video's own brand is the most-repeated name).
            musteri = None
            if share.client_id:
                c = db.session.get(Client, share.client_id)
                musteri = [c.name] if c and c.name else None
            transcript = media.transcribe(
                media.extract_audio(path),
                initial_prompt=ai_context.transcript_vocabulary(extra=musteri))
        frames = media.extract_frames(path, 3)
        if frames:
            # first frame into the thumbnail cache (served instead of the endpoint's video 404)
            for w in THUMB_WIDTHS:
                db.session.merge(DriveThumbnail(
                    file_id=share.file_id, width=w, data=frames[0], mime='image/jpeg'))
            # save all frames to disk (ai_worker's caption handler reads them for visual context)
            _write_frames(_frames_dir(share.id), frames)
        share.transcript = transcript or None
        db.session.commit()
        if not frames and not transcript:
            # Neither frames nor transcript → the caption guard would stay stuck
            # on "waiting for media" forever. Instead of staying silent, a clear
            # error: the job fails, visible in the panel. (If there's a
            # transcript, frames aren't required — the caption can be generated from text.)
            raise ValueError(
                f'videodan kare/transkript çıkarılamadı (share {share.id}, '
                f'{share.file_name}) — bozuk dosya veya desteklenmeyen kodek olabilir')
        return {'kind': 'video', 'has_audio': audio,
                'transcript': transcript, 'frame_count': len(frames)}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def run_once():
    job = jobqueue.claim(['media', 'web_variant'])
    if job is None:
        return False
    try:
        jobqueue.complete(job, process(job))
    except Exception as e:  # noqa: BLE001
        # Network/Drive drops are TRANSIENT: requeue with backoff (a broken pipe
        # used to terminal-fail on the first attempt). Logic errors (e.g.
        # ValueError) stay terminal. OSError also covers requests.RequestException (the whisper POST).
        transient = isinstance(e, (dg.DriveError, OSError))
        jobqueue.fail(job, e, transient=transient)
    return True


def main():
    last_cleanup = 0.0
    with app.app_context():
        print('[media_worker] başladı, kuyruk dinleniyor', flush=True)
        while True:
            try:
                worked = run_once()
            except Exception as e:  # noqa: BLE001
                print(f'[media_worker] döngü hatası: {e}', flush=True)
                worked = False
            if not worked:
                # Idle janitor: clean up local media copies past the 21-day window.
                if time.monotonic() - last_cleanup > CLEANUP_INTERVAL:
                    last_cleanup = time.monotonic()
                    try:
                        n = media_store.cleanup()
                        if n:
                            print(f'[media_worker] lokal medya temizliği: {n} dosya', flush=True)
                    except Exception as e:  # noqa: BLE001
                        print(f'[media_worker] temizlik hatası: {e}', flush=True)
                time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
