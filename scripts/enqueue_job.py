"""Genel enqueue script'i — periyodik AI işlerini (brief/özel gün/Ops Digest/...)
Postgres kuyruğuna atar. systemd `--user` (proje sahibi) timer'ları bunu çağırır;
kuyruktan `ai_worker.py` çeker.

Ekstra resident süreç YOK: her timer tetiklendiğinde bu script bir kez koşar,
bir `Job` satırı ekler ve çıkar (`Type=oneshot` service).

Kullanım:
    venv/bin/python scripts/enqueue_job.py brief
    venv/bin/python scripts/enqueue_job.py ops_digest --payload-json '{"slot": "09:00"}'
    venv/bin/python scripts/enqueue_job.py special_days --created-by systemd-timer

`run()` DB'ye gerçekten yazan test edilebilir çekirdek — testler bunu doğrudan
import edip çağırır (subprocess YOK).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env yüklü olmalı)
import jobqueue  # noqa: E402


def run(job_type, payload_json=None, created_by=None):
    """Verilen tip+payload ile bir `Job` satırı oluşturur, oluşan `Job`'u döner.

    `payload_json`: JSON string ya da None/boş — eksikse payload `{}` olur.
    Geçersiz JSON verilirse `ValueError` fırlatır (net hata, sessiz yutmaz).

    NOT: app context AÇMAZ — çağıranın sorumluluğu (bkz. `main()`). Testler
    zaten conftest'in autouse context'i içinde doğrudan çağırır; burada
    ekstra `with app.app_context()` açılırsa iç context kapanışında
    Flask-SQLAlchemy scoped session'ı teardown edip döndürülen `Job`'u
    detach eder (DetachedInstanceError) — bu yüzden bilerek yok.
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
