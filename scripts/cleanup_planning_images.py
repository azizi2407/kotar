#!/usr/bin/env python3
"""Planlama Panosu görsellerinin yetim dosyalarını temizler.

Bir görsel öğesi silinince dosya BİLEREK hemen silinmez: pano Ctrl+Z ile geri
alınabiliyor ve öğe aynı `extra.image.name` ile geri gelebiliyor — dosyayı anında
silmek geri almayı sessizce bozardı. Bunun yerine dosya yetim kalır ve bu script
onu yeterince eskidiğinde alır.

YETİM = hiçbir `planning_items` satırının `extra->'image'->>'name'` değeriyle
eşleşmeyen dosya. Yaş eşiği ayrıca yükleme yarışını kapatır: dosya yüklendikten
sonra öğe PATCH'i saniyeler içinde gelir, 30 gün bunu fazlasıyla kapsar.

Kullanım:
    python scripts/cleanup_planning_images.py                # kuru koşu
    python scripts/cleanup_planning_images.py --apply        # gerçekten sil
    python scripts/cleanup_planning_images.py --days 60 --apply
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env yüklü olmalı)
import planning_images  # noqa: E402
from extensions import db  # noqa: E402
from models_planning import PlanningItem  # noqa: E402

DEFAULT_DAYS = 30


def run(days=DEFAULT_DAYS, apply=False):
    """(silinen, korunan, bayt) döner. `apply=False` hiçbir şeye dokunmaz."""
    root = (os.environ.get('PLANNING_IMAGE_DIR')
            or os.path.join(app.root_path, 'data', 'planning-images'))
    if not os.path.isdir(root):
        return 0, 0, 0

    # Kullanımdaki adlar — TEK sorgu, pano başına küme.
    used = {}
    for board_id, extra in db.session.query(PlanningItem.board_id, PlanningItem.extra).filter(
            PlanningItem.type == 'image').all():
        name = ((extra or {}).get('image') or {}).get('name')
        if name:
            used.setdefault(str(board_id), set()).add(name)

    cutoff = time.time() - days * 86400
    silinen = korunan = bayt = 0
    for board_dir in sorted(os.listdir(root)):
        d = os.path.join(root, board_dir)
        if not os.path.isdir(d):
            continue
        keep = used.get(board_dir, set())
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            # `.tmp-*` yarım yüklemedir: adı hiçbir öğede geçmez, aynı kurala tabi.
            if name in keep:
                korunan += 1
                continue
            st = os.stat(p)
            if st.st_mtime > cutoff:
                korunan += 1           # yetim ama henüz genç (yükleme yarışı)
                continue
            silinen += 1
            bayt += st.st_size
            print(f'  {"SİL " if apply else "SİLİNECEK"} {board_dir}/{name} '
                  f'({st.st_size / 1024:.0f} KB, {(time.time() - st.st_mtime) / 86400:.0f} gün)')
            if apply:
                try:
                    os.remove(p)
                except OSError as e:
                    print(f'  ✗ silinemedi: {e}')
                    silinen -= 1
    return silinen, korunan, bayt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--days', type=int, default=DEFAULT_DAYS,
                    help=f'bu kadar günden eski yetimler silinir (varsayılan {DEFAULT_DAYS})')
    ap.add_argument('--apply', action='store_true', help='gerçekten sil (yoksa kuru koşu)')
    args = ap.parse_args()
    with app.app_context():
        silinen, korunan, bayt = run(days=args.days, apply=args.apply)
    mod = 'SİLİNDİ' if args.apply else 'KURU KOŞU'
    print(f'[planlama-görsel-temizlik] {mod}: {silinen} yetim ({bayt / 1e6:.1f} MB), '
          f'{korunan} korundu (eşik {args.days} gün)')


if __name__ == '__main__':
    main()
