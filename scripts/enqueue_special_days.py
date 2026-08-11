"""Special-day bot enqueue script (Phase 3, step 11) — pushes the next
month's `special_days` job onto the queue at low priority. systemd `--user`
(project owner) `agency-special-days.timer` calls this (3rd Monday of the
month, 03:30); `ai_worker.special_days_handler` pulls it from the queue.

Dedup: if an active (queued|running) job for the same month already exists,
NO new INSERT happens (`dedup_key=special_days:{year}-{month}`) — so a timer
+ manual trigger overlapping doesn't produce a duplicate job. `run()` is the
testable core that writes to the DB; tests call it directly.

Usage:
    venv/bin/python scripts/enqueue_special_days.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_special_days.py --month 6 --year 2026
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import ai_worker  # noqa: E402
import jobqueue  # noqa: E402


def run(month=None, year=None, created_by=None):
    """Creates/returns a `special_days` Job for the given month (or next month if unspecified).

    Low priority (0 = batch): doesn't block interactive captioning (priority=10).
    If the same month is enqueued twice via dedup_key, only one job remains
    (the active job is returned).

    NOTE: does NOT open an app context — that's the caller's responsibility
    (see `main()`); tests call this directly inside conftest's autouse
    context (see enqueue_job.py).
    """
    if not month or not year:
        month, year = ai_worker._next_month()
    month, year = int(month), int(year)
    return jobqueue.enqueue('special_days', {'month': month, 'year': year},
                            priority=0, dedup_key=f'special_days:{year}-{month}',
                            created_by=created_by)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--month', type=int, default=None, help='ay (1-12); verilmezse sonraki ay')
    ap.add_argument('--year', type=int, default=None, help='yıl; verilmezse sonraki ayın yılı')
    ap.add_argument('--created-by', default=None, help='kaydı oluşturan (ör. systemd-timer)')
    return ap


def main():
    args = build_parser().parse_args()
    with app.app_context():
        job = run(month=args.month, year=args.year, created_by=args.created_by)
        jid, jtype, jstatus = job.id, job.type, job.status
    print(f'[enqueue] job id={jid} type={jtype} status={jstatus}')


if __name__ == '__main__':
    main()
