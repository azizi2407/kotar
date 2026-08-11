"""Shared whisper transcription service (127.0.0.1:5051).

Whisper (faster-whisper, existing HF cache model — no download) in a single
place. The agency media worker + notion-assistant send audio/video via POST
/transcribe → transcript. The model is loaded ON DEMAND.

**RAM management = systemd SOCKET-ACTIVATION.** If no request arrives for
IDLE_UNLOAD_SECONDS, the process shuts down ENTIRELY (SIGTERM to the gunicorn
master) → the ~900MB model + OpenMP threads + interpreter are returned to the
OS. systemd holds the socket; it restarts the service on the next connection
(cold start: gunicorn ~1s + model ~a few seconds; client timeouts are set
generously to match). Note: trying in-process `_model=None` did NOT return RAM
to the OS (glibc arena retention + CTranslate2 pool), so we switched to process-level recycling instead.

Running it: systemd socket-activation (whisper-service.socket → .service).
Fallback (manual): gunicorn -w 1 -b 127.0.0.1:5051 --timeout 600 whisper_service:app
"""
import os
import signal
import tempfile
import threading
import time

from flask import Flask, jsonify, request

MODEL_NAME = os.environ.get('WHISPER_MODEL', 'medium')
COMPUTE = os.environ.get('WHISPER_COMPUTE', 'int8')
IDLE_UNLOAD_SECONDS = int(os.environ.get('WHISPER_IDLE_UNLOAD', '300'))
# VAD: never feeds silence into the model. Measured on this machine — 117s of
# Turkish speech on turbo/int8 takes 42.8s without VAD, 29.7s with VAD. It also
# cuts off hallucinations that appear during silence ("Subtitle M.K.", "Thanks
# for watching"). Default is ON; turn it off via env if needed.
VAD_FILTER = (os.environ.get('WHISPER_VAD', '1').strip() != '0')
# `initial_prompt` comes from the caller (this service is shared, it doesn't
# know the agency DB). The upper bound is defensive: faster-whisper already
# truncates to 224 tokens, this truncation only prevents an unbounded body from being loaded into memory.
PROMPT_MAX_CHARS = 2000

app = Flask(__name__)

_model = None
_last_use = time.monotonic()  # grant the full idle window at startup (no instant shutdown)
_lock = threading.Lock()


def _get_model():
    global _model, _last_use
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME, device='cpu', compute_type=COMPUTE,
                                  local_files_only=True)  # from cache, no download
        _last_use = time.monotonic()
        return _model


def _idle_reaper():
    """Shuts down the ENTIRE process once the idle window elapses; the systemd
    socket restarts it on the next connection. This way RAM fully returns to the OS while idle."""
    while True:
        time.sleep(30)
        with _lock:
            idle_for = time.monotonic() - _last_use
        if idle_for > IDLE_UNLOAD_SECONDS:
            app.logger.info(
                'whisper %d sn boşta — süreç kapanıyor (socket-activation yeniden başlatır)',
                int(idle_for))
            # SIGTERM to the gunicorn master → the whole service shuts down, RAM
            # returns to the OS, systemd restarts on the next request to the socket.
            # WARNING: when this process IS the gunicorn MASTER (parent = systemd
            # --user), os.getppid() returns the USER MANAGER; killing it blindly
            # would kill the project owner's ENTIRE session (notion, beszel,
            # workers, claude). So verify the target: if the parent is gunicorn
            # we're a worker → target the master; otherwise we ARE the master →
            # target ourselves. NEVER send a signal to the systemd --user manager.
            parent = os.getppid()
            try:
                with open(f'/proc/{parent}/comm') as fh:
                    parent_is_gunicorn = fh.read().strip() == 'gunicorn'
            except OSError:
                parent_is_gunicorn = False
            os.kill(parent if parent_is_gunicorn else os.getpid(), signal.SIGTERM)
            return


threading.Thread(target=_idle_reaper, daemon=True).start()


@app.get('/health')
def health():
    return jsonify(status='ok', loaded=_model is not None)


@app.post('/transcribe')
def transcribe():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='no file'), 400
    language = request.form.get('language') or 'tr'
    # Proper-name vocabulary (2026-08-08). Whisper treats this as "prior
    # context" and gravitates toward the names in it: measured "Busto Mahallesi
    # hanesi" → "Gusto Mare", "Molo Pantarya" → "Mall of Antalya" (3/3
    # reproducible, +0.3s). TRADEOFF: proper names NOT in the list can get
    # slightly mangled (measured "Gökhan Namlı" → "Gök Anlamlı") — accepted
    # deliberately since the agency's own brand/team names win out. If
    # empty/missing, behavior is unchanged from before.
    initial_prompt = (request.form.get('initial_prompt') or '').strip()[:PROMPT_MAX_CHARS] or None
    suffix = os.path.splitext(f.filename)[1] or '.bin'
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        f.save(tmp.name)
        tmp.close()
        model = _get_model()
        segments, info = model.transcribe(tmp.name, language=language,
                                          vad_filter=VAD_FILTER,
                                          initial_prompt=initial_prompt)
        text = ' '.join(seg.text.strip() for seg in segments).strip()
        global _last_use
        _last_use = time.monotonic()
        return jsonify(text=text, language=info.language,
                       duration=round(info.duration, 1))
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f'transcription error: {e}'), 500
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
