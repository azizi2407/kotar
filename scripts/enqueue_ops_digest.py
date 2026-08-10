"""Ops Digest takip botu enqueue script'i (Faz 4, step 15) — bir systemd --user
timer'ı bunu günde 4× (09/12/15/18) çağırır; kuyruktan `ai_worker.ops_digest_handler` çeker. Düşük priority (0 = batch): interaktif
caption'ı (priority=10) bloklamaz.

Dedup: aynı gün+slot için aktif (queued|running) bir job zaten varsa YENİ INSERT yapılmaz
(`dedup_key=ops_digest:{date}:{slot}`, step 04) — makine kapalıyken biriken (Persistent=true
telafi) ya da timer üst üste tetiklerse tek job → tek rapor (spam-önleme).

`run()` DB'ye yazan test edilebilir çekirdek; testler doğrudan çağırır.

Kullanım:
    venv/bin/python scripts/enqueue_ops_digest.py --created-by systemd-timer
    venv/bin/python scripts/enqueue_ops_digest.py --slot 12 --ai-summary
"""
import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env yüklü olmalı)
import jobqueue  # noqa: E402


def _current_slot(now=None):
    """Şu anki saatin 2 haneli slot etiketi ('09'|'12'|'15'|'18' veya güncel saat).
    Timer tam saatte koştuğundan saat = slot; dedup anahtarının slot bileşenidir."""
    return f'{(now or datetime.now()).hour:02d}'


def run(slot=None, use_ai_summary=False, created_by=None):
    """Bugün + slot için bir `ops_digest` Job'u oluşturur/döner (dedup: gün+slot).

    Düşük priority (0 = batch): interaktif caption'ı (priority=10) bloklamaz.
    dedup_key ile aynı gün+slot iki kez atılırsa tek job kalır (aktif job döner) →
    tek rapor. use_ai_summary=True → handler özet dilini `ai_claude.run` ile derler.

    NOT: app context AÇMAZ — çağıranın sorumluluğu (bkz. `main()`); testler conftest'in
    autouse context'i içinde doğrudan çağırır (bkz. enqueue_special_days.py)."""
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
