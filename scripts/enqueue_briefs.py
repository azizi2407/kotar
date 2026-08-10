"""Haftalık brief fan-out enqueue script'i (Faz 2, step 13) — hedef hafta için
AKTİF her müşteriye AYRI bir `brief` job'u atar (tek job döngüsü DEĞİL → kısmi hata
izolasyonu: bir müşterinin brief'i patlarsa diğerleri sürer). Bir systemd timer'ı
bunu çağırır (her Salı 03:30); kuyruktan `ai_worker.brief_handler` her job'u tek
müşteri için işler.

Dedup: her müşteri KENDİ anahtarını alır (`dedup_key_fn=lambda cid: f'brief:{cid}:{week}'`)
— aynı müşteri+hafta için aktif (queued|running) job varsa yeni INSERT yapılmaz (timer +
panel elle-tetik üst üste basarsa müşteri-başı tek job kalır). Düşük priority (0 = batch) →
interaktif caption'ı (priority=10) bloklamaz.

`run()` DB'ye yazan test edilebilir çekirdek; testler doğrudan çağırır.

Kullanım:
    venv/bin/python scripts/enqueue_briefs.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_briefs.py --week-iso 2026-W25
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env yüklü olmalı)
import jobqueue  # noqa: E402
from models import Client  # noqa: E402


def _target_week_iso():
    """Hedef ISO hafta = gerçek hafta + 2 ('YYYY-Www'). Sabit +2 tempo (K1): ekip
    planlama için iki hafta önden üretir. (Eski 'içinde bulunulan hafta' kaldırıldı —
    vault rutininin +2 hedefiyle hizalı; artık tek üretici agency.)"""
    y, w, _ = (date.today() + timedelta(days=14)).isocalendar()
    return f'{y}-W{w:02d}'


def run(week_iso=None, created_by=None):
    """Hedef hafta (yoksa gerçek hafta + 2) için AKTİF ve BRIEF-AÇIK her müşteriye bir
    `brief` Job'u fan-out eder; yaratılan/var olan (dedup) Job listesini döndürür.
    `brief_enabled=False` müşteriler (brief-pasif) atlanır.

    NOT: app context AÇMAZ — çağıranın sorumluluğu (bkz. `main()`); testler conftest'in
    autouse context'i içinde doğrudan çağırır (bkz. enqueue_special_days.py)."""
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
