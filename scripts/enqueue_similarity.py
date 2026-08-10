"""K9 müşteriler-arası benzerlik kontrolü enqueue script'i — hedef hafta için TEK
`similarity` job'u kuyruğa atar. systemd `--user` (proje sahibi) `agency-similarity.timer` bunu
çağırır (her Salı 04:30 — brief run'ından 1 saat sonra); kuyruktan `ai_worker.similarity_handler` çeker.

Hedef hafta = gerçek hafta + 2 — brief fan-out ile AYNI mantık (`enqueue_briefs._target_week_iso`
yeniden kullanılır → tek kaynak). O haftanın üretilmiş brief'leri müşteriler arası
karşılaştırılır (brief run bu hafta için brief'leri zaten üretmiş olur).

Dedup: aynı hafta için aktif (queued|running) bir job zaten varsa YENİ INSERT yapılmaz
(`dedup_key=similarity:{week}`) — timer + elle-tetik üst üste basarsa tek job. Düşük
priority (0 = batch) → interaktif caption'ı (priority=10) bloklamaz.

`run()` DB'ye yazan test edilebilir çekirdek; testler doğrudan çağırır.

Kullanım:
    venv/bin/python scripts/enqueue_similarity.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_similarity.py --week-iso 2026-W25
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env yüklü olmalı)
import jobqueue  # noqa: E402
from scripts.enqueue_briefs import _target_week_iso  # noqa: E402  (+2 mantığı yeniden kullan)


def run(week_iso=None, created_by=None):
    """Hedef hafta (yoksa gerçek hafta + 2, brief run'la aynı) için bir `similarity`
    Job'u oluşturur/döner. dedup_key ile aynı hafta iki kez atılırsa tek job kalır
    (aktif job döner). Düşük priority (0 = batch).

    NOT: app context AÇMAZ — çağıranın sorumluluğu (bkz. `main()`); testler conftest'in
    autouse context'i içinde doğrudan çağırır (bkz. enqueue_ops_digest.py)."""
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
