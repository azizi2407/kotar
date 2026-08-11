"""Generic enqueue script — pushes periodic AI jobs (brief/special day/Ops
Digest/...) onto the Postgres queue. systemd `--user` (project owner)
timers call this; `ai_worker.py` pulls from the queue.

NO extra resident process: whenever a timer fires, this script runs once,
adds a `Job` row, and exits (`Type=oneshot` service).

Usage:
    venv/bin/python scripts/enqueue_job.py brief
    venv/bin/python scripts/enqueue_job.py ops_digest --payload-json '{"slot": "09:00"}'
    venv/bin/python scripts/enqueue_job.py special_days --created-by systemd-timer

`run()` is the testable core that actually writes to the DB — tests import
and call it directly (NO subprocess).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import jobqueue  # noqa: E402


def run(job_type, payload_json=None, created_by=None):
    """Creates a `Job` row with the given type+payload, returns the created `Job`.

    `payload_json`: a JSON string, or None/empty — if missing, payload becomes `{}`.
    Raises `ValueError` on invalid JSON (a clear error, not silently swallowed).

    NOTE: does NOT open an app context — that's the caller's responsibility
    (see `main()`). Tests already call this directly inside conftest's
    autouse context; opening an extra `with app.app_context()` here would,
    on the inner context's exit, tear down the Flask-SQLAlchemy scoped
    session and detach the returned `Job` (DetachedInstanceError) — which is
    why it's deliberately absent.
    """
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as e:
            raise ValueError(f'--payload-json geçerli JSON değil: {e}') from e
    else:
        payload = {}

    return jobqueue.enqueue(job_type, payload, created_by=created_by)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('type', help="job tipi (ör. brief, special_days, ops_digest)")
    ap.add_argument('--payload-json', default=None,
                     help='JSON string payload (verilmezse boş {} kullanılır)')
    ap.add_argument('--created-by', default=None,
                     help="kaydı oluşturan (ör. systemd-timer, cli)")
    return ap


def main():
    args = build_parser().parse_args()
    try:
        with app.app_context():
            job = run(args.type, payload_json=args.payload_json, created_by=args.created_by)
            job_id, job_type, job_status = job.id, job.type, job.status
    except ValueError as e:
        print(f'[hata] {e}', file=sys.stderr)
        sys.exit(1)
    print(f'[enqueue] job id={job_id} type={job_type} status={job_status}')


if __name__ == '__main__':
    main()
