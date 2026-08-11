"""Gunicorn config — svc-agency (gthread)."""
import os

bind = os.getenv('GUNICORN_BIND', '127.0.0.1:5030')
workers = int(os.getenv('GUNICORN_WORKERS', '2'))
worker_class = 'gthread'
threads = int(os.getenv('GUNICORN_THREADS', '4'))
preload_app = True
worker_tmp_dir = '/dev/shm'
# Large file uploads (up to 500 MB) are uploaded to Drive synchronously within
# the request → 1200s so the worker doesn't hit a timeout (aligned with nginx
# proxy/body timeouts).
timeout = int(os.getenv('GUNICORN_TIMEOUT', '1200'))
graceful_timeout = 30
keepalive = 5
accesslog = os.getenv('GUNICORN_ACCESS_LOG', '-')
errorlog = os.getenv('GUNICORN_ERROR_LOG', '-')
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')


def post_fork(server, worker):
    """Drop the Postgres connection inherited from the fork out of the pool in the child.

    `preload_app=True` builds the app in the MASTER process; `create_app()` →
    `db.create_all()` at the end of `app.py` opens a real Postgres connection
    there and leaves it in the SQLAlchemy pool. When the master forks, this
    socket is inherited by BOTH workers at once, and the two processes start
    talking on the same TCP socket → the psycopg protocol breaks. This is
    exactly what was seen live after the 2026-08-09 18:07 restart: between
    18:09-18:15, `IndexError: tuple index out of range`, `ResourceClosedError`,
    and finally `server closed the connection unexpectedly` (PATCH
    /api/planning/boards/user:1/items returned 500). Once the socket died and
    dropped from the pool, the error stopped on its own — meaning the symptom
    lasted a few minutes after EVERY restart and then vanished, which is why it
    kept going unnoticed.

    `pool_pre_ping` CANNOT CATCH THIS: the connection isn't dead, it's shared.

    `close=False` is critical — we don't actually close the socket, we only drop
    it from this process's pool; closing it would also tear down the sibling
    worker's still-valid copy of the same page (the approach SQLAlchemy
    recommends for fork).
    """
    from app import app
    from extensions import db
    with app.app_context():
        db.engine.dispose(close=False)
