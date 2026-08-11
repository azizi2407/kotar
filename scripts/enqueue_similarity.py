"""Enqueue script for the K9 cross-client similarity check — pushes a SINGLE
`similarity` job to the queue for the target week. systemd `--user` (project
owner) `agency-similarity.timer` calls this (every Tuesday 04:30 — 1 hour
after the brief run); `ai_worker.similarity_handler` picks it up from the queue.

Target week = actual week + 2 — the SAME logic as the brief fan-out
(`enqueue_briefs._target_week_iso` is reused → single source of truth). That
week's generated briefs are compared across clients (the brief run will
already have generated briefs for that week).

Dedup: if an active (queued|running) job already exists for the same week, NO
NEW INSERT happens (`dedup_key=similarity:{week}`) — if the timer + a manual
trigger both fire, one job. Low priority (0 = batch) → doesn't block
interactive caption generation (priority=10).

`run()` is the testable core that writes to the DB; tests call it directly.

Usage:
    venv/bin/python scripts/enqueue_similarity.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_similarity.py --week-iso 2026-W25
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import jobqueue  # noqa: E402
from scripts.enqueue_briefs import _target_week_iso  # noqa: E402  (reuse the +2 logic)


def run(week_iso=None, created_by=None):
    """Creates/returns a `similarity` Job for the target week (defaults to
    actual week + 2, same as the brief run). If the same week is submitted
    twice, dedup_key keeps it to one job (returns the active job). Low priority (0 = batch).

    NOTE: does NOT open an app context — that's the caller's responsibility
    (see `main()`); tests call it directly within conftest's autouse context (see enqueue_ops_digest.py)."""
    target = week_iso or _target_week_iso()
    return jobqueue.enqueue('similarity', {'week_iso': target}, priority=0,
                            dedup_key=f'similarity:{target}', created_by=created_by)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--week-iso', default=None,
                    help="hedef ISO hafta ('YYYY-Www'); verilmezse gerçek hafta + 2")
    ap.add_argument('--created-by', default=None, help='kaydı oluşturan (ör. systemd-timer)')
    return ap


def main():
    args = build_parser().parse_args()
    with app.app_context():
        job = run(week_iso=args.week_iso, created_by=args.created_by)
        jid, jtype, jstatus = job.id, job.type, job.status
    print(f'[enqueue] job id={jid} type={jtype} status={jstatus}')


if __name__ == '__main__':
    main()
