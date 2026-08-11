"""Media processing helpers — ffprobe/ffmpeg + whisper service calls.

For video: is there an audio stream (ffprobe) → if so, a whisper transcript; a few
frames from different timestamps (ffmpeg) → caption image context + video
thumbnail. Called by media_worker (project owner). Real filesystem operations (the
testable parts are pure; covered by ffmpeg integration tests).
"""
import io
import json
import logging
import os
import subprocess
import tempfile
import uuid

import requests
from PIL import Image

log = logging.getLogger(__name__)

FFMPEG = os.environ.get('FFMPEG_BIN', 'ffmpeg')
FFPROBE = os.environ.get('FFPROBE_BIN', 'ffprobe')
WHISPER_URL = os.environ.get('WHISPER_URL', 'http://127.0.0.1:5051')

# --- file type detection (2026-07-27) ---------------------------------------
# `Share.kind` is the POST type (post/story/reel/linkedin), NOT the file type — a
# video can perfectly well be shared as a "post" (normal on Instagram).
# media_worker used to hand the file to PIL whenever it saw `kind != 'video'`; a
# .mp4 with `kind='post'` crashed with "cannot identify image file" on all 3
# attempts (job 233, share 671) and the caption chain never progressed for that
# share. Now the decision is made FROM THE CONTENT: the byte signature doesn't lie.

# ISO-BMFF (`ftyp`) is used by both MP4 and HEIC/AVIF → the brand is checked.
_ISOBMFF_IMAGE_BRANDS = frozenset(
    (b'heic', b'heix', b'heim', b'heis', b'hevc', b'hevx',
     b'mif1', b'msf1', b'avif', b'avis'))

_VIDEO_EXTS = frozenset(
    ('.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.flv', '.wmv',
     '.mpg', '.mpeg', '.3gp', '.ts', '.mts'))
_IMAGE_EXTS = frozenset(
    ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff',
     '.heic', '.heif', '.avif'))


def sniff_kind(data):
    """'video' | 'image' from the byte signature; None if unrecognized.

    More reliable than the file NAME or `Share.kind`: the name may have been
    changed, or a different post type may have been chosen, but the content stays
    as it is."""
    head = bytes(data[:16])
    if head[:3] == b'\xff\xd8\xff':                       return 'image'   # JPEG
    if head[:8] == b'\x89PNG\r\n\x1a\n':                  return 'image'   # PNG
    if head[:4] in (b'GIF8',):                            return 'image'
    if head[:2] == b'BM':                                 return 'image'   # BMP
    if head[:4] in (b'II*\x00', b'MM\x00*'):              return 'image'   # TIFF
    if head[:4] == b'RIFF':                               # WEBP / AVI share the same container
        tag = bytes(data[8:12])
        return 'image' if tag == b'WEBP' else ('video' if tag == b'AVI ' else None)
    if head[4:8] == b'ftyp':                              # ISO-BMFF: MP4/MOV vs HEIC/AVIF
        return 'image' if bytes(data[8:12]) in _ISOBMFF_IMAGE_BRANDS else 'video'
    if head[:4] == b'\x1a\x45\xdf\xa3':                   return 'video'   # Matroska/WebM
    if head[:3] == b'FLV':                                return 'video'
    if head[:4] in (b'\x00\x00\x01\xba', b'\x00\x00\x01\xb3'):  return 'video'  # MPEG-PS/ES
    if head[:1] == b'\x47':                               return 'video'   # MPEG-TS
    return None


def kind_from_name(filename):
    """'video' | 'image' from the file name's extension; None if unrecognized
    (sniff fallback)."""
    ext = os.path.splitext(filename or '')[1].lower()
    if ext in _VIDEO_EXTS:
        return 'video'
    if ext in _IMAGE_EXTS:
        return 'image'
    return None


def resolve_kind(data, filename=None, fallback=None):
    """Media type — SINGLE DECISION POINT: content signature → extension → caller's
    guess.

    `fallback` is used only if neither recognizes it; if None, 'image' is assumed
    (old behavior: the image path). media_worker and the tests share this."""
    return sniff_kind(data) or kind_from_name(filename) or fallback or 'image'


def has_audio(path):
    """Is there an audio stream in the video (ffprobe)."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-select_streams', 'a', '-show_entries',
             'stream=codec_type', '-of', 'json', path],
            capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or '{}')
        return bool(data.get('streams'))
    except Exception:
        return False


def duration_seconds(path):
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', path], capture_output=True, text=True, timeout=60)
        return float(json.loads(out.stdout or '{}').get('format', {}).get('duration', 0))
    except Exception:
        return 0.0


# --- Web-compatible variant (2026-08-01) ---
#
# Phone videos come in as 4K/60fps HEVC Main 10 (10-bit); Android Chrome can't
# open it — it closes with no picture at all when you hit play. The same device
# plays 1080p HEVC fine, so the problem isn't the codec, it's the PROFILE's
# weight. Still, we scope it as an "h264 + 8-bit + ≤1080p" whitelist: Firefox
# never opens HEVC at all, and these links go outside the agency, to browsers we
# don't know.
WEB_MAX_EDGE = 1920          # long edge of the variant (1080x1920 in portrait)
WEB_CRF = 23                 # visually near-lossless, reasonable size
WEB_THREADS = 2              # 4-core server — the panel should keep responding


def video_profile(path):
    """The video's `(codec, width, height, pix_fmt)` profile; None if unreadable."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
             'stream=codec_name,width,height,pix_fmt', '-of', 'json', path],
            capture_output=True, text=True, timeout=60)
        streams = json.loads(out.stdout or '{}').get('streams') or []
        if not streams:
            return None
        s = streams[0]
        if not s.get('width') or not s.get('height'):
            return None
        return (s.get('codec_name'), int(s['width']), int(s['height']),
                s.get('pix_fmt') or '')
    except Exception:  # noqa: BLE001 — if the profile can't be read, treat it as "not needed"
        return None


def needs_web_variant(path):
    """Does this video need a browser-compatible variant?

    Whitelist: if it's H.264 + 8-bit + long edge ≤1920, DON'T TOUCH IT. Everything
    else (HEVC, 10-bit, 4K) needs a variant. If the profile can't be read, False —
    we don't start an expensive transcode on a guess."""
    prof = video_profile(path)
    if prof is None:
        return False
    codec, w, h, pix = prof
    if codec != 'h264':
        return True
    if not pix.startswith('yuv420p') or pix != 'yuv420p':
        return True              # 10-bit / 4:2:2 / 4:4:4 → no browser support
    return max(w, h) > WEB_MAX_EDGE


def make_web_variant(src, dst, timeout=1800):
    """Produce a browser-compatible 1080p H.264 variant from `src` → `dst`. True on
    success.

    The aspect ratio is preserved (the long edge is capped to `WEB_MAX_EDGE`, the
    short edge is rounded to an even number — H.264 doesn't accept an odd
    dimension). `+faststart` moves moov to the front; without it the variant
    wouldn't even start on mobile (see `media_store._faststart`).

    Best-effort: no half-written output is left behind on error, and False is
    returned — the caller keeps serving the original."""
    # `scale`: cap the long edge, preserve the ratio, round both to an even number.
    # `-2` = "preserve the ratio, round to a multiple of 2"; min() prevents
    # upscaling (an already-small video shouldn't be scaled up).
    vf = (f"scale='if(gt(iw,ih),min({WEB_MAX_EDGE},iw),-2)'"
          f":'if(gt(iw,ih),-2,min({WEB_MAX_EDGE},ih))'")
    # We don't write DIRECTLY to the destination: if the backfill script and the
    # worker land on the same file, two ffmpeg processes would overwrite the same
    # output and leave a half-broken variant behind. The temp name starts with
    # `.tmp-` → `media_store.cleanup`'s stale-tmp rule (1 day) already sweeps up
    # any leftover half-finished output.
    tmp = os.path.join(os.path.dirname(dst) or '.',
                       f'.tmp-wv-{uuid.uuid4().hex}.mp4')
    try:
        r = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-i', src,
             '-vf', vf, '-c:v', 'libx264', '-profile:v', 'high', '-pix_fmt', 'yuv420p',
             '-crf', str(WEB_CRF), '-preset', 'veryfast', '-threads', str(WEB_THREADS),
             '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', tmp],
            capture_output=True, timeout=timeout)
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, dst)          # same directory → atomic
            return True
        log.warning('web türevi üretilemedi (%s): %s', os.path.basename(src),
                    (r.stderr or b'')[:200].decode('utf-8', 'replace'))
    except Exception as e:  # noqa: BLE001 — timeout/OSError; the original is good enough
        log.warning('web türevi koşulamadı (%s): %s', os.path.basename(src), e)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def extract_frames(path, count=3):
    """`count` frames from the video (jpeg bytes) — evenly spaced across the duration."""
    dur = duration_seconds(path)
    if dur <= 0:
        return []
    # skip the edges: evenly spaced points between 10% and 90%
    points = [dur * (0.1 + 0.8 * i / max(1, count - 1)) for i in range(count)] if count > 1 else [dur / 2]
    frames = []
    for ts in points:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tf:
            out_path = tf.name
        try:
            subprocess.run(
                [FFMPEG, '-y', '-ss', f'{ts:.2f}', '-i', path, '-frames:v', '1',
                 '-vf', 'scale=640:-1', '-q:v', '3', out_path],
                capture_output=True, timeout=120)
            if os.path.getsize(out_path) > 0:
                with open(out_path, 'rb') as f:
                    frames.append(f.read())
        except Exception:
            pass
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
    return frames


def extract_audio(path):
    """Mono 16kHz wav from the video (bytes) — light for whisper."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
        out_path = tf.name
    try:
        subprocess.run(
            [FFMPEG, '-y', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
             '-f', 'wav', out_path], capture_output=True, timeout=300)
        with open(out_path, 'rb') as f:
            return f.read()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def downscale_image(data, max_w=1280):
    """Image bytes → JPEG bytes at most max_w wide (caption image context, light
    for token/size). Doesn't upscale if already small; RGBA/P is flattened onto a
    white background."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    if img.width > max_w:
        h = round(img.height * max_w / img.width)
        img = img.resize((max_w, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def transcribe(audio_bytes, filename='audio.wav', language='tr', timeout=600,
               initial_prompt=None):
    """Send to the Whisper service → transcript text.

    `initial_prompt`: a proper-name vocabulary (brand/team names). Whisper treats
    this as previous context and biases toward names that appear in it — the
    cheapest way to get agency brand names spelled correctly (see
    `ai_context.transcript_vocabulary`). We don't build the vocabulary HERE:
    `media` is a pure media helper, it doesn't touch the DB; the caller provides it."""
    data = {'language': language}
    if initial_prompt:
        data['initial_prompt'] = initial_prompt
    r = requests.post(f'{WHISPER_URL}/transcribe',
                      files={'file': (filename, audio_bytes)},
                      data=data, timeout=timeout)
    r.raise_for_status()
    return r.json().get('text', '')
