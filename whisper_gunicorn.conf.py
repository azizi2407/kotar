"""Gunicorn config — whisper-service (socket-activated, single worker).

**Why a SEPARATE file:** `whisper-service.service` runs with
`WorkingDirectory=/srv/apps/agency` and its ExecStart had NO `-c`. When `-c`
isn't given, Gunicorn silently loads the `gunicorn.conf.py` in the cwd →
whisper was inheriting the panel's (`agency.service`) config. An accidental
coupling: every gunicorn setting added for the panel also leaked into
whisper. When `post_fork` (drops the DB pool on fork) was added to the panel
on 2026-08-09, the whisper worker's `from app import app` failed to boot with
`KeyError: 'SECRET_KEY'` and the service dropped to `failed` (transcription
stopped entirely). The config is now given explicitly via `-c`; the two
services are independent of each other.

The values are an EXACT copy of the old effective settings that were being
inherited by accident — this file exists to pin the behavior, not to change it.
"""
import os

# `-b` is only a fallback for manual runs: if a systemd socket (LISTEN_FDS)
# exists, gunicorn inherits it and this bind is ignored.
bind = os.getenv('WHISPER_BIND', '127.0.0.1:5051')
workers = 1                 # the model runs in a single process; a second worker would double the RAM
worker_class = 'gthread'
threads = int(os.getenv('WHISPER_THREADS', '4'))
# Transcription takes a while (117s of audio ≈ 30s of CPU); the worker shouldn't hit its timeout.
timeout = int(os.getenv('WHISPER_TIMEOUT', '600'))
# The idle-reaper thread starts when `whisper_service` is imported; with
# preload it runs in this master and shuts the process down after
# `WHISPER_IDLE_UNLOAD` (the socket restarts it on the next request). This is
# the existing behavior, preserved as-is.
preload_app = True
worker_tmp_dir = '/dev/shm'
graceful_timeout = 30
keepalive = 5
accesslog = os.getenv('WHISPER_ACCESS_LOG', '-')
errorlog = os.getenv('WHISPER_ERROR_LOG', '-')
loglevel = os.getenv('WHISPER_LOG_LEVEL', 'info')
