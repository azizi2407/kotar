"""Shared rate limit — Postgres fixed-window (shared across workers).

Redis is out + the `limits` library doesn't support Postgres; hence a simple,
atomic fixed-window counter. `hit(bucket, max, window)` → whether it was allowed (bool).
Guards public endpoints (review/special-days) against abuse.
"""
import time

from extensions import db
from models import RateWindow


def hit(bucket, max_count, window_seconds):
    """Count a request; return True if it hasn't exceeded max_count within the window."""
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
    else:  # sqlite (tests)
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = (sqlite_insert(RateWindow)
                .values(bucket=bucket, window_start=window, count=1)
                .on_conflict_do_update(
                    index_elements=['bucket', 'window_start'],
                    set_={'count': RateWindow.count + 1}))
        db.session.execute(stmt)
        count = db.session.query(RateWindow.count).filter_by(
            bucket=bucket, window_start=window).scalar()

    # Clean up this bucket's old windows (keep the table from bloating)
    db.session.query(RateWindow).filter(
        RateWindow.bucket == bucket, RateWindow.window_start < window).delete()
    db.session.commit()
    return count <= max_count
