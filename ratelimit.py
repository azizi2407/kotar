"""Paylaşımlı hız sınırı — Postgres fixed-window (worker'lar arası ortak).

Redis eleniyor + `limits` kütüphanesi Postgres desteklemiyor; bu yüzden basit,
atomik bir fixed-window sayaç. `hit(bucket, max, window)` → izin verildi mi (bool).
Public uçların (review/special-days) kötüye kullanımına karşı.
"""
import time

from extensions import db
from models import RateWindow


def hit(bucket, max_count, window_seconds):
    """Bir istek say; pencere içinde max_count'u aşmadıysa True döndür."""
    now = int(time.time())
    window = now - (now % window_seconds)
    dialect = db.session.get_bind().dialect.name

    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = (pg_insert(RateWindow)
                .values(bucket=bucket, window_start=window, count=1)
                .on_conflict_do_update(
                    index_elements=['bucket', 'window_start'],
                    set_={'count': RateWindow.count + 1})
                .returning(RateWindow.count))
        count = db.session.execute(stmt).scalar()
    else:  # sqlite (test)
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = (sqlite_insert(RateWindow)
                .values(bucket=bucket, window_start=window, count=1)
                .on_conflict_do_update(
                    index_elements=['bucket', 'window_start'],
                    set_={'count': RateWindow.count + 1}))
        db.session.execute(stmt)
        count = db.session.query(RateWindow.count).filter_by(
            bucket=bucket, window_start=window).scalar()

    # Bu bucket'ın eski pencerelerini temizle (tablo şişmesin)
    db.session.query(RateWindow).filter(
        RateWindow.bucket == bucket, RateWindow.window_start < window).delete()
    db.session.commit()
    return count <= max_count
