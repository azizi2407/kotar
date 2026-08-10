"""Özel gün botu enqueue script'i (Faz 3, step 11) — sonraki ay için `special_days`
job'unu düşük öncelikle kuyruğa atar. systemd `--user` (proje sahibi) `agency-special-days.timer`
bunu çağırır (ayın 3. Pzt 03:30); kuyruktan `ai_worker.special_days_handler` çeker.

Dedup: aynı ay için aktif (queued|running) bir job zaten varsa YENİ INSERT yapılmaz
(`dedup_key=special_days:{year}-{month}`) — timer + elle-tetik üst üste basarsa mükerrer
job olmaz. `run()` DB'ye yazan test edilebilir çekirdek; testler doğrudan çağırır.

Kullanım:
    venv/bin/python scripts/enqueue_special_days.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_special_days.py --month 6 --year 2026
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env yüklü olmalı)
import ai_worker  # noqa: E402
import jobqueue  # noqa: E402


def run(month=None, year=None, created_by=None):
    """Verilen ay (yoksa sonraki ay) için bir `special_days` Job'u oluşturur/döner.

    Düşük priority (0 = batch): interaktif caption'ı (priority=10) bloklamaz.
    dedup_key ile aynı ay iki kez atılırsa tek job kalır (aktif job döner).

    NOT: app context AÇMAZ — çağıranın sorumluluğu (bkz. `main()`); testler
    conftest'in autouse context'i içinde doğrudan çağırır (bkz. enqueue_job.py).
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
