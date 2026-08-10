#!/usr/bin/env python3
"""Günlük hatırlatıcılar — yarının özel günleri + bugün biten reklamlar (2026-08-05).

`agency-reminders.timer` sabah bir kez çağırır. Enqueue script'lerinden farkı: iş
kuyruğuna job atmaz, bildirimi DOĞRUDAN üretir — AI/Drive gerektirmeyen saf DB
sorguları, worker turunu beklemenin anlamı yok.

**Sessizlik kuralı:** yarın özel gün yoksa veya bugün biten reklam yoksa **hiçbir
bildirim üretilmez** (proje sahibi isteği). Her sabah "bugün bir şey yok" diyen bir çan,
bir süre sonra hiç okunmayan bir çandır.

İdempotans: aynı gün ikinci kez koşulursa `notifications`'taki okunmamış aynı
başlıklı kayıt yeniden üretilmez (başlık tarihi taşır → per-gün dedup).

Kullanım:
    python scripts/daily_reminders.py            # kuru koşu (ne gönderileceğini yazar)
    python scripts/daily_reminders.py --apply    # bildirimleri üret
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
import notifications  # noqa: E402
from extensions import db  # noqa: E402
from models import AdCampaign, Client, Notification  # noqa: E402
from models_sharing import SpecialDayEvent  # noqa: E402

AYLAR = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz',
         'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def _tarih_metni(d):
    return f'{d.day} {AYLAR[d.month]}'


def yarinin_ozel_gunleri(hedef):
    """Hedef tarihe düşen özel günlerin adları.

    Kapsam onaylı + aktif kayıtlar: taslak (AI'ın ürettiği, yönetimin onaylamadığı)
    günler hatırlatılmaz — onay kapısı özellikle bunun için var. Tek-gün (`date_num`)
    ve aralık (`date_start`–`date_end`) kayıtlarının ikisi de değerlendirilir."""
    rows = (SpecialDayEvent.query
            .filter(SpecialDayEvent.month == hedef.month,
                    SpecialDayEvent.active.is_(True),
                    SpecialDayEvent.status == 'approved')
            .filter(db.or_(SpecialDayEvent.year == hedef.year,
                           SpecialDayEvent.year.is_(None)))
            .all())
    adlar = []
    for e in rows:
        if e.date_num == hedef.day:
            adlar.append(e.day_name)
        elif (e.date_start is not None and e.date_end is not None
              and e.date_start <= hedef.day <= e.date_end):
            adlar.append(e.day_name)
    # Aynı gün adı hem global hem müşteriye özel kayıtla gelebilir → tekilleştir.
    return sorted({a for a in adlar if a})


def biten_reklamlar(bugun):
    """Bugün biten, silinmemiş kampanyalar. `status` süzgeci YOK: 'planned' kalmış
    ama bitiş tarihi gelmiş bir kampanya da ilgilenilmesi gereken bir durumdur."""
    return (AdCampaign.query
            .filter(AdCampaign.end_date == bugun,
                    AdCampaign.deleted_at.is_(None))
            .join(Client, Client.id == AdCampaign.client_id)
            .filter(Client.deleted_at.is_(None))
            .all())


def _zaten_var(baslik):
    """Aynı başlıkla okunmamış bildirim var mı (per-gün dedup — başlık tarihi taşır)."""
    return (Notification.query
            .filter_by(title=baslik, read_at=None)
            .first()) is not None


def main():
    ap = argparse.ArgumentParser(description='Günlük hatırlatıcı bildirimleri')
    ap.add_argument('--apply', action='store_true', help='gerçekten gönder')
    ap.add_argument('--date', help='bugünü ezer (YYYY-MM-DD, test için)')
    args = ap.parse_args()

    bugun = date.fromisoformat(args.date) if args.date else date.today()
    yarin = bugun + timedelta(days=1)

    with app.app_context():
        gunler = yarinin_ozel_gunleri(yarin)
        kampanyalar = biten_reklamlar(bugun)

        if not gunler and not kampanyalar:
            print('Bildirilecek bir şey yok — sessiz geçildi.')
            return 0

        if gunler:
            baslik = f'Yarın: {_tarih_metni(yarin)}'
            if _zaten_var(baslik):
                print(f'ATLA  özel günler: "{baslik}" için okunmamış bildirim var')
            else:
                print(f'{"GÖNDER" if args.apply else "KURU"}  özel günler ({len(gunler)}): '
                      f'{", ".join(gunler)}')
                if args.apply:
                    notifications.notify_special_day_soon(gunler, _tarih_metni(yarin))

        for k in kampanyalar:
            ad = k.title or 'Reklam'
            print(f'{"GÖNDER" if args.apply else "KURU"}  reklam bitiyor: '
                  f'client={k.client_id} "{ad}"')
            if args.apply:
                notifications.notify_ad_ending(k.client_id, ad, k.end_date.isoformat())

        print(f'\nBitti — özel gün {len(gunler)}, biten reklam {len(kampanyalar)}'
              f'{"" if args.apply else " (KURU KOŞU)"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
