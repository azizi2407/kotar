"""Local media store — a 21-day server copy of files uploaded to the panel.

Drive is the canonical source; this is only an acceleration layer (Sharing Board +
the client approval page serve media from here for the first 3 weeks). No
registry: "does it exist on the server?" = does the file exist on disk; age is
measured by file mtime. Directory: originals/ + previews/ under
$MEDIA_STORE_DIR (default <repo>/data/media).
"""
import glob
import io
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid

log = logging.getLogger(__name__)

RETENTION_DAYS = 21
PREVIEW_MAX_PX = 800
_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')      # Drive file_id alphabet
_EXT_RE = re.compile(r'^\.[a-z0-9]{1,8}$')

# The SAME env variable as media.py; we don't import it from there — keeps the
# store layer independent of the processing layer, which also keeps testing light.
FFMPEG = os.environ.get('FFMPEG_BIN', 'ffmpeg')
# `-c copy` remux is I/O bound (150 MB ≈ 0.2s); this cap is only against hangs.
FASTSTART_TIMEOUT = 120
# `+faststart` is meaningful only in an ISO-BMFF container.
FASTSTART_EXTS = ('.mp4', '.mov', '.m4v')


def _dir(kind):
    root = os.environ.get('MEDIA_STORE_DIR') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'data', 'media')
    d = os.path.join(root, kind)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_id(file_id):
    return bool(file_id) and bool(_ID_RE.match(file_id))


def _ext_for(filename, mime):
    ext = os.path.splitext(filename or '')[1].lower()
    if _EXT_RE.match(ext):
        return ext
    return mimetypes.guess_extension(mime or '') or '.bin'


def make_preview(source, max_px=PREVIEW_MAX_PX):
    """Generate a JPEG preview from the image with edges of at most max_px.

    `source`: bytes or a disk path (PIL reads streamed from a path — the whole
    file doesn't go into RAM)."""
    from PIL import Image
    img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    img.thumbnail((max_px, max_px))
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=82)
    return out.getvalue()


def _write_preview(source, file_id):
    """Write the preview best-effort (silently give up on a corrupt image)."""
    try:
        prev = make_preview(source)
        with open(os.path.join(_dir('previews'), file_id + '.jpg'), 'wb') as f:
            f.write(prev)
    except Exception:  # noqa: BLE001 — corrupt image etc.; the original is enough
        log.warning('önizleme üretilemedi: %s', file_id)


def _faststart(path, mime):
    """Move the `moov` atom to the START of the file for video (lossless remux).

    WHY: in mp4s produced by phones/cameras, `moov` (duration, codec, frame index)
    is written at the END of the file — because the size isn't known until the
    recording finishes. The browser needs to read `moov` to start playback; when
    it's at the end, mobile Chrome couldn't start the video (2026-08-01, Android).
    Downloading wasn't affected — it takes bytes sequentially and doesn't wait for
    `moov`; that's exactly why we saw the "downloads but doesn't play" pattern.

    `-c copy`: only the container is rewritten, frames/audio are NOT TOUCHED →
    lossless and fast (150 MB ≈ 0.2s). Also safe (idempotent) on a file that's
    already faststart.

    Best-effort: if ffmpeg is missing/fails, the original stays as-is — the upload
    is critical, not the remux. Returns True on success."""
    if not (mime or '').startswith('video/'):
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext not in FASTSTART_EXTS:
        return False
    # The temp name starts with a DOT → `find_original`'s glob (`<file_id>.*`)
    # NEVER matches it; a half-finished remux can't be accidentally served.
    tmp = os.path.join(os.path.dirname(path), f'.fstmp-{uuid.uuid4().hex}{ext}')
    try:
        r = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-i', path,
             '-c', 'copy', '-movflags', '+faststart', tmp],
            capture_output=True, timeout=FASTSTART_TIMEOUT)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)      # same directory → atomic
            return True
        log.warning('faststart remux başarısız (%s): %s', os.path.basename(path),
                    (r.stderr or b'')[:200].decode('utf-8', 'replace'))
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log.warning('faststart remux koşulamadı (%s): %s', os.path.basename(path), e)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def save_original(file_id, data, mime, filename=None):
    """Write a local copy + (if it's an image) a preview AFTER the Drive upload.

    Best-effort: an error doesn't invalidate the upload, it's only logged."""
    if not _safe_id(file_id):
        return False
    try:
        path = os.path.join(_dir('originals'), file_id + _ext_for(filename, mime))
        with open(path, 'wb') as f:
            f.write(data)
    except OSError:
        log.exception('lokal kopya yazılamadı: %s', file_id)
        return False
    if (mime or '').startswith('image/'):
        _write_preview(data, file_id)
    else:
        _faststart(path, mime)
    return True


# --- Stream-based path (large files: disk-to-disk without loading into RAM) ---

def stage(stream, chunk=8 * 1024 * 1024):
    """Copy the request stream into a TEMPORARY file under originals/; return its path.

    The Drive upload is done from this file (seekable); `commit` is called on
    success, `discard` on error. Returns None if it can't be written (the caller
    continues from the stream)."""
    tmp = os.path.join(_dir('originals'), f'.tmp-{uuid.uuid4().hex}')
    try:
        with open(tmp, 'wb') as f:
            shutil.copyfileobj(stream, f, chunk)
        return tmp
    except OSError:
        log.exception('geçici medya dosyası yazılamadı')
        discard(tmp)
        return None


def commit(tmp_path, file_id, mime, filename=None):
    """Drive succeeded → move the temp file to its permanent name, generate a preview if it's an image."""
    if not tmp_path:
        return False
    if not _safe_id(file_id):
        discard(tmp_path)
        return False
    final = os.path.join(_dir('originals'), file_id + _ext_for(filename, mime))
    try:
        os.replace(tmp_path, final)
    except OSError:
        log.exception('lokal kopya kalıcılaştırılamadı: %s', file_id)
        discard(tmp_path)
        return False
    if (mime or '').startswith('image/'):
        _write_preview(final, file_id)
    else:
        # Videos come through this path (stream-based); the `/m/<id>` page playing
        # on mobile depends on this — see `_faststart`.
        _faststart(final, mime)
    return True


def discard(tmp_path):
    """Delete the temp file (silent if missing)."""
    if not tmp_path:
        return
    try:
        os.remove(tmp_path)
    except OSError:
        pass


def find_original(file_id):
    """Disk path of the original; None if missing/invalid id."""
    if not _safe_id(file_id):
        return None
    hits = glob.glob(os.path.join(_dir('originals'), file_id + '.*'))
    return hits[0] if hits else None


def find_preview(file_id):
    if not _safe_id(file_id):
        return None
    p = os.path.join(_dir('previews'), file_id + '.jpg')
    return p if os.path.exists(p) else None


def has_original(file_id):
    return find_original(file_id) is not None


def web_path(file_id):
    """Target path of the web variant (`web/<file_id>.mp4`); None for an invalid id.

    The variant is ALWAYS .mp4/H.264 (`media.make_web_variant`), so the extension
    is fixed — no need for `find_original`'s glob."""
    if not _safe_id(file_id):
        return None
    return os.path.join(_dir('web'), file_id + '.mp4')


def find_web(file_id):
    """Path of the web variant; None if missing.

    The variant is generated for videos whose original doesn't play in the browser
    (4K/HEVC/10-bit); the `/m/<file_id>` page plays IT, the "Download" button gives
    the original."""
    p = web_path(file_id)
    return p if p and os.path.exists(p) else None


def remove(file_id):
    """IMMEDIATELY delete a file's local copies (original + preview + web variant).

    `cleanup` runs by age; this is single and instant — so a permanently-deleted
    upload doesn't stay accessible on disk and via `/m/<file_id>` for 21 days
    (2026-07-31 videographer "Delete" button). Returns the number of deleted
    files; 0 if none (not an error)."""
    removed = 0
    for path in (find_original(file_id), find_preview(file_id), find_web(file_id)):
        if not path:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            log.exception('lokal kopya silinemedi: %s', path)
    return removed


def cleanup(max_age_days=RETENTION_DAYS):
    """Delete local copies that have expired (by mtime); returns the number deleted.

    Half-finished `.tmp-*` files (leftovers from a crashed request) are deleted
    once older than 1 day."""
    now = time.time()
    cutoff = now - max_age_days * 86400
    tmp_cutoff = now - 86400
    removed = 0
    # 'web': variants are also subject to the same 21-day window as the original —
    # keeping the variant once the original is gone is pointless (it's regenerated
    # from the Drive source).
    for kind in ('originals', 'previews', 'web'):
        d = _dir(kind)
        for name in os.listdir(d):
            p = os.path.join(d, name)
            limit = tmp_cutoff if name.startswith('.tmp-') else cutoff
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < limit:
                    os.remove(p)
                    removed += 1
            except OSError:
                log.exception('temizlik silemedi: %s', p)
    return removed
