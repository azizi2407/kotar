"""Magnific kredi tazeleme enqueue script'i — `magnific_credits` job'unu kuyruğa atar
(dedup'lu; aktif varsa yenisi açılmaz). Timer: agency-magnific-credits.timer, günde 2 kez
(İstanbul 09:00 ve 14:00). Handler: ai_worker.magnific_credits_handler (ücretsiz MCP
account_balance → AppSetting['magnific_credits'] cache; panel üst bar rozeti okur).

Kullanım:
    venv/bin/python scripts/enqueue_magnific_credits.py --created-by systemd-timer
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobqueue  # noqa: E402
from app import app  # noqa: E402


def run(created_by=None):
    """Tek `magnific_credits` job'u enqueue eder (dedup: aktif varsa onu döndürür).
    NOT: app context AÇMAZ — çağıranın sorumluluğu (bkz. enqueue_briefs.py)."""
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
        # NOT: job attribute'ları context İÇİNDE okunmalı (dışarıda DetachedInstanceError).
        summary = f'{job.id} ({job.status})'
    print(f'[enqueue] magnific_credits job: {summary}')


if __name__ == '__main__':
    main()
