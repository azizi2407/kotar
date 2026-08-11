"""Enqueue script for the Ops Digest tracking bot (Phase 4, step 15) — a
systemd --user timer calls this 4x a day (09/12/15/18); `ai_worker.ops_digest_handler`
picks it up from the queue. Low priority (0 = batch): doesn't block interactive
caption generation (priority=10).

Dedup: if an active (queued|running) job already exists for the same
day+slot, NO NEW INSERT happens (`dedup_key=ops_digest:{date}:{slot}`, step
04) — whether it's catch-up runs piling up while the machine was off
(Persistent=true) or the timer firing repeatedly, one job → one report (spam prevention).

`run()` is the testable core that writes to the DB; tests call it directly.

Usage:
    venv/bin/python scripts/enqueue_ops_digest.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_ops_digest.py --slot 12 --ai-summary
"""
import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import jobqueue  # noqa: E402


def _current_slot(now=None):
    """The current hour's 2-digit slot label ('09'|'12'|'15'|'18', or the
    current hour). Since the timer runs on the hour, hour = slot; this is the dedup key's slot component."""
    return f'{(now or datetime.now()).hour:02d}'


def run(slot=None, use_ai_summary=False, created_by=None):
    """Creates/returns an `ops_digest` Job for today + slot (dedup: day+slot).

    Low priority (0 = batch): doesn't block interactive caption generation
    (priority=10). If the same day+slot is submitted twice, dedup_key keeps it
    to one job (returns the active job) → one report. use_ai_summary=True → the
    handler composes the summary text via `ai_claude.run`.

    NOTE: does NOT open an app context — that's the caller's responsibility
    (see `main()`); tests call it directly within conftest's autouse context (see enqueue_special_days.py)."""
    slot = slot or _current_slot()
    today = date.today().isoformat()
    payload = {'date': today, 'slot': slot}
    if use_ai_summary:
        payload['use_ai_summary'] = True
    return jobqueue.enqueue('ops_digest', payload, priority=0,
                            dedup_key=f'ops_digest:{today}:{slot}', created_by=created_by)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--slot', default=None,
                    help='slot etiketi (ör. 09); verilmezse güncel saat')
    ap.add_argument('--ai-summary', action='store_true',
                    help='özet dilini ai_claude.run ile derle (kota harcar; varsayılan kapalı)')
    ap.add_argument('--created-by', default=None, help='kaydı oluşturan (ör. systemd-timer)')
    return ap


def main():
    args = build_parser().parse_args()
    with app.app_context():
        job = run(slot=args.slot, use_ai_summary=args.ai_summary, created_by=args.created_by)
        jid, jtype, jstatus = job.id, job.type, job.status
    print(f'[enqueue] job id={jid} type={jtype} status={jstatus}')


if __name__ == '__main__':
    main()
