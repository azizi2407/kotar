"""Magnific credit refresh enqueue script — queues the `magnific_credits` job
(deduped; a new one isn't opened if one is already active). Timer: agency-magnific-credits.timer,
twice a day (09:00 and 14:00 Istanbul time). Handler: ai_worker.magnific_credits_handler (free MCP
account_balance → AppSetting['magnific_credits'] cache; the panel top-bar badge reads it).

Usage:
    venv/bin/python scripts/enqueue_magnific_credits.py --created-by systemd-timer
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobqueue  # noqa: E402
from app import app  # noqa: E402


def run(created_by=None):
    """Enqueues a single `magnific_credits` job (dedup: returns the active one if there is one).
    NOTE: does NOT open an app context — that's the caller's responsibility (see enqueue_briefs.py)."""
    return jobqueue.enqueue('magnific_credits', {}, priority=0,
                            dedup_key='magnific_credits', created_by=created_by)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--created-by', default=None, help='kaydı oluşturan (ör. systemd-timer)')
    return ap


def main():
    args = build_parser().parse_args()
    with app.app_context():
        job = run(created_by=args.created_by)
        # NOTE: job attributes must be read INSIDE the context (DetachedInstanceError outside it).
        summary = f'{job.id} ({job.status})'
    print(f'[enqueue] magnific_credits job: {summary}')


if __name__ == '__main__':
    main()
