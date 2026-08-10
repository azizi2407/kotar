"""Gunicorn config — svc-agency (gthread)."""
import os

bind = os.getenv('GUNICORN_BIND', '127.0.0.1:5030')
workers = int(os.getenv('GUNICORN_WORKERS', '2'))
worker_class = 'gthread'
threads = int(os.getenv('GUNICORN_THREADS', '4'))
preload_app = True
worker_tmp_dir = '/dev/shm'
# Büyük dosya yüklemeleri (500 MB'a kadar) istek-içinde Drive'a senkron yüklenir →
# worker'ın timeout'a takılmaması için 1200 sn (nginx proxy/body timeout'larıyla hizalı).
timeout = int(os.getenv('GUNICORN_TIMEOUT', '1200'))
graceful_timeout = 30
keepalive = 5
accesslog = os.getenv('GUNICORN_ACCESS_LOG', '-')
errorlog = os.getenv('GUNICORN_ERROR_LOG', '-')
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')


def post_fork(server, worker):
    """Fork'tan miras kalan Postgres bağlantısını çocukta havuzdan düşür.

    `preload_app=True` uygulamayı MASTER süreçte kuruyor; `app.py`'nin sonundaki
    `create_app()` → `db.create_all()` orada gerçek bir Postgres bağlantısı açıp
    SQLAlchemy havuzunda bırakıyor. Master fork edince bu soket İKİ worker'a
    birden miras kalıyor ve iki süreç aynı TCP soketinde konuşmaya başlıyor →
    psycopg protokolü bozuluyor. Canlıda 2026-08-09 18:07 restart'ından sonra
    tam olarak bu görüldü: 18:09-18:15 arası `IndexError: tuple index out of
    range`, `ResourceClosedError`, sonunda `server closed the connection
    unexpectedly` (PATCH /api/planning/boards/user:1/items 500 döndü). Soket
    ölüp havuzdan düşünce hata kendiliğinden durdu — yani belirti HER restart'ın
    ardından birkaç dakika sürüp kayboluyordu, bu yüzden gözden kaçıyordu.

    `pool_pre_ping` bunu YAKALAYAMAZ: bağlantı ölü değil, paylaşılmış.

    `close=False` kritik — soketi gerçekten kapatmayız, yalnız bu sürecin
    havuzundan bırakırız; kapatsaydık kardeş worker'ın hâlâ geçerli sayfa
    kopyasını da yıkardık (SQLAlchemy'nin fork için önerdiği yol).
    """
    from app import app
    from extensions import db
    with app.app_context():
        db.engine.dispose(close=False)
