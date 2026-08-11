"""Weekly brief fan-out enqueue script (Phase 2, step 13) — assigns a SEPARATE
`brief` job to every ACTIVE client for the target week (NOT a single job loop →
partial failure isolation: if one client's brief blows up, the others keep going).
A systemd timer calls this (every Tuesday 03:30); `ai_worker.brief_handler` pulls
from the queue and processes each job for a single client.

Dedup: each client gets ITS OWN key
(`dedup_key_fn=lambda cid: f'brief:{cid}:{week}'`) — if an active (queued|running)
job already exists for the same client+week, no new INSERT happens (if the timer
and a manual panel trigger overlap, only one job per client remains). Low priority
(0 = batch) → doesn't block interactive captions (priority=10).

`run()` is the testable core that writes to the DB; tests call it directly.

Usage:
    venv/bin/python scripts/enqueue_briefs.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_briefs.py --week-iso 2026-W25
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import jobqueue  # noqa: E402
from models import Client  # noqa: E402


def _target_week_iso():
    """Target ISO week = current week + 2 ('YYYY-Www'). Fixed +2 tempo (K1):
    generates two weeks ahead for team planning. (The old 'current week' was
    removed — aligned with the vault routine's +2 target; agency is now the sole
    producer.)"""
    y, w, _ = (date.today() + timedelta(days=14)).isocalendar()
    return f'{y}-W{w:02d}'


def run(week_iso=None, created_by=None):
    """Fans out a `brief` Job to every ACTIVE, BRIEF-ENABLED client for the target
    week (current week + 2 if not given); returns the list of created/existing
    (dedup) Jobs. Clients with `brief_enabled=False` (brief-disabled) are skipped.

    NOTE: does NOT open an app context — that's the caller's responsibility (see
    `main()`); tests call it directly inside conftest's autouse context (see
    enqueue_special_days.py)."""
    target = week_iso or _target_week_iso()
    client_ids = [c.id for c in
                  Client.query.filter_by(status='active', brief_enabled=True)
                  .order_by(Client.id).all()]
    return jobqueue.enqueue_per_client(
        'brief', client_ids,
        payload_fn=lambda cid: {'client_id': cid, 'week_iso': target},
        priority=0,
        dedup_key_fn=lambda cid: f'brief:{cid}:{target}',
        created_by=created_by)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--week-iso', default=None,
                    help="hedef ISO hafta ('YYYY-Www'); verilmezse gerçek hafta + 2")
    ap.add_argument('--created-by', default=None, help='kaydı oluşturan (ör. systemd-timer)')
    return ap


def main():
    args = build_parser().parse_args()
    with app.app_context():
        jobs = run(week_iso=args.week_iso, created_by=args.created_by)
        summary = [(j.id, j.payload.get('client_id'), j.status) for j in jobs]
    print(f'[enqueue] {len(summary)} brief job: {summary}')


if __name__ == '__main__':
    main()
