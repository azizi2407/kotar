#!/usr/bin/env python3
"""Daily reminders — tomorrow's special days + ads ending today (2026-08-05).

`agency-reminders.timer` calls this once every morning. Unlike the enqueue
scripts, it doesn't push a job onto the queue, it produces the notification
DIRECTLY — plain DB queries that need no AI/Drive, so there's no point
waiting for a worker cycle.

**Silence rule:** if there's no special day tomorrow and no ad ending today,
**no notification is produced at all** (project owner's request). A bell that
rings "nothing today" every morning eventually becomes a bell nobody reads.

Idempotency: if run a second time on the same day, the existing unread record
with the same title in `notifications` is not regenerated (the title carries
the date → per-day dedup).

Usage:
    python scripts/daily_reminders.py            # dry run (prints what would be sent)
    python scripts/daily_reminders.py --apply    # produce the notifications
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

AYLAR = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July',
         'August', 'September', 'October', 'November', 'December']


def _tarih_metni(d):
    return f'{d.day} {AYLAR[d.month]}'


def yarinin_ozel_gunleri(hedef):
    """Names of special days falling on the target date.

    Scope is approved + active records: draft days (AI-generated, not yet
    approved by management) are not reminded — the approval gate exists
    specifically for this. Both single-day (`date_num`) and range
    (`date_start`–`date_end`) records are evaluated."""
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
    # The same day name can come in via both a global and a client-specific record → dedupe.
    return sorted({a for a in adlar if a})


def biten_reklamlar(bugun):
    """Non-deleted campaigns ending today. NO `status` filter: a campaign stuck
    on 'planned' whose end date has arrived is also a situation worth attention."""
    return (AdCampaign.query
            .filter(AdCampaign.end_date == bugun,
                    AdCampaign.deleted_at.is_(None))
            .join(Client, Client.id == AdCampaign.client_id)
            .filter(Client.deleted_at.is_(None))
            .all())


def _zaten_var(baslik):
    """Is there an unread notification with the same title (per-day dedup — the title carries the date)."""
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
