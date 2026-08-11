"""Voice note API (2026-08-09) — `/api/voice-notes`.

Management speaks from the browser, the audio is uploaded here, a `voice_note`
job enters the queue; `ai_worker` produces a transcript and a structured note
via whisper + Haiku.

**Why a queue (not sync):** for resilience, not speed. The job continues even if
the browser closes, `jobqueue` retries on error, the worker is monitored from a
single point. A 2-minute note finishes in ~30s (2026-08-08 measurement).

**Ownership is ABSOLUTE:** a voice note is a personal draft space, not a shared
channel. Even the management role CANNOT see SOMEONE ELSE's note, and the
endpoints return 404 (not 403 — not even the note's existence should leak).

The board-write endpoint is NOT HERE: the panel calls the existing
`PATCH /api/planning/boards/<key>/items` endpoint (conflict/version logic lives
there, keeping it in one place).

CSRF is shared via `api.csrf_protect` (same pattern as `depot.py`/`design_files.py`).
"""
import logging
import os
import subprocess

from flask import Blueprint, jsonify, request, send_file

import jobqueue
import sha_store
from api import csrf_protect
from extensions import db
from models import utcnow
from models_voice_notes import VoiceNote, normalize_structured
from sso_client import current_user

log = logging.getLogger('agency.voice_notes')

bp = Blueprint('voice_notes', __name__)
bp.before_request(csrf_protect)

VOICE_ROLES = ('management',)

MAX_AUDIO_BYTES = 100 * 1024 * 1024      # ≈90 min of opus — accident protection
MAX_DURATION_SEC = 3600
# Browser MediaRecorder output: Chrome webm/opus, Safari mp4. ogg/wav/m4a also
# accepted — the user may later upload audio from another source.
ALLOWED_EXT = {'webm', 'mp4', 'm4a', 'ogg', 'oga', 'wav', 'mp3'}

STORE_DIR = os.environ.get('VOICE_NOTES_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'voice-notes')

FFPROBE = os.environ.get('FFPROBE_BIN', 'ffprobe')


# --- authorization ------------------------------------------------------------------

def _require():
    """(user, err) — voice notes are only open to management."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not signed in'), 401)
    if u.get('role') not in VOICE_ROLES:
        return None, (jsonify(error='this section is open to management only'), 403)
    return u, None


def _not_veya_404(note_id, u):
    """Ownership is enforced HERE: someone else's note says 'not found'."""
    n = db.session.get(VoiceNote, note_id)
    if n is None or n.deleted_at is not None or n.owner_sub != u.get('sub'):
        return None, (jsonify(error='note not found'), 404)
    return n, None


# --- helpers ------------------------------------------------------------

def _sure_sn(yol):
    """Audio duration (seconds, int) — None if it can't be read. Duration is
    COSMETIC: the upload should proceed even if ffprobe fails (the user shouldn't
    lose their recording)."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', yol],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return int(float(out)) if out else None
    except Exception:  # noqa: BLE001 — None if there's no duration, the flow continues
        return None


def _yol(n):
    return sha_store.yol(STORE_DIR, n.audio_sha256, f'x.{n.audio_ext}')


# --- endpoints ------------------------------------------------------------------

@bp.get('')
def notes_list():
    """My own notes, newest to oldest. Transcript is NOT RETURNED (the list is
    polled every 3s, must stay light)."""
    u, err = _require()
    if err:
        return err
    rows = (VoiceNote.query
            .filter_by(owner_sub=u['sub'], deleted_at=None)
            .order_by(VoiceNote.created_at.desc(), VoiceNote.id.desc())
            .limit(100).all())
    return jsonify(notes=[n.to_dict() for n in rows])


@bp.get('/<int:note_id>')
def note_get(note_id):
    """A single note — full body. The panel polls this while processing."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    return jsonify(note=n.to_dict(full=True))


@bp.post('')
def note_create():
    """Upload audio → write to disk → enqueue a `voice_note` job (multipart: audio)."""
    u, err = _require()
    if err:
        return err
    f = request.files.get('audio')
    if f is None or not f.filename:
        return jsonify(error='no audio file'), 400
    ext = sha_store.uzanti(f.filename)
    if ext not in ALLOWED_EXT:
        return jsonify(error='not an audio file (webm, mp4, m4a, ogg, wav, mp3)'), 400

    # Measure the size WITHOUT loading the stream into RAM (werkzeug spools large bodies to disk).
    f.stream.seek(0, 2)
    boyut = f.stream.tell()
    f.stream.seek(0)
    if boyut == 0:
        return jsonify(error='audio file is empty'), 400
    if boyut > MAX_AUDIO_BYTES:
        return jsonify(error='Recording exceeds the 100 MB limit.'), 413

    sha, boyut = sha_store.yaz(f.stream, STORE_DIR, f.filename)
    yol = sha_store.yol(STORE_DIR, sha, f.filename)
    sure = _sure_sn(yol)
    if sure is not None and sure > MAX_DURATION_SEC:
        # The file STAYS on disk (content-addressed, another note may be using the
        # same sha) — only the record isn't opened.
        return jsonify(error='Recording is too long (60 minutes max).'), 413

    n = VoiceNote(owner_sub=u['sub'], audio_sha256=sha, audio_ext=ext,
                  mime_type=(f.mimetype or '')[:120] or None,
                  file_size=boyut, duration_sec=sure, status='queued',
                  created_at=utcnow())
    db.session.add(n)
    db.session.commit()
    # priority=10: same band as caption — the user is waiting on screen.
    jobqueue.enqueue('voice_note', {'note_id': n.id}, priority=10,
                     created_by=u['sub'])
    return jsonify(note=n.to_dict(full=True)), 201


@bp.get('/<int:note_id>/audio')
def note_audio(note_id):
    """Serve the audio — small file, no need for X-Accel."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    yol = _yol(n)
    if not os.path.exists(yol):
        log.error('sesli not diskte yok: note=%s sha=%s', n.id, n.audio_sha256)
        return jsonify(error='audio file not found on server'), 410
    return send_file(yol, mimetype=n.mime_type or 'application/octet-stream',
                     conditional=True)


@bp.patch('/<int:note_id>')
def note_patch(note_id):
    """Saves `structured` as edited in the panel, and the task keys pushed to the
    board. No other field is accepted."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    veri = request.get_json(silent=True) or {}
    if 'structured' in veri:
        # The panel body is also treated as UNTRUSTED — goes through the same normalize.
        n.structured = normalize_structured(veri.get('structured'))
    if 'pushed_item_keys' in veri:
        gelen = veri.get('pushed_item_keys')
        gelen = gelen if isinstance(gelen, list) else []
        # ACCUMULATES: a second push shouldn't erase the previous one.
        mevcut = list(n.pushed_item_keys or [])
        for k in gelen:
            if isinstance(k, str) and k and k not in mevcut:
                mevcut.append(k)
        n.pushed_item_keys = mevcut[:200]
    db.session.commit()
    return jsonify(note=n.to_dict(full=True))


@bp.delete('/<int:note_id>')
def note_delete(note_id):
    """Soft-delete. The audio file STAYS on disk: another row in the content-
    addressed store may point to the same sha (same rationale as `design_files`)."""
    u, err = _require()
    if err:
        return err
    n, nerr = _not_veya_404(note_id, u)
    if nerr:
        return nerr
    n.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)
