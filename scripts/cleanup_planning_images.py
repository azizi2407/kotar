#!/usr/bin/env python3
"""Cleans up orphan files among Planning Board images.

When an image item is deleted, the file is DELIBERATELY NOT deleted right away:
the board can be undone with Ctrl+Z and the item can come back with the same
`extra.image.name` — deleting the file immediately would silently break undo.
Instead the file is left orphaned, and this script picks it up once it's old enough.

ORPHAN = a file that doesn't match the `extra->'image'->>'name'` value of any
`planning_items` row. The age threshold also closes the upload race: the item
PATCH arrives within seconds of the file being uploaded, 30 days covers that
with plenty of margin.

Usage:
    python scripts/cleanup_planning_images.py                # dry run
    python scripts/cleanup_planning_images.py --apply        # actually delete
    python scripts/cleanup_planning_images.py --days 60 --apply
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (env must be loaded)
import planning_images  # noqa: E402
from extensions import db  # noqa: E402
from models_planning import PlanningItem  # noqa: E402

DEFAULT_DAYS = 30


def run(days=DEFAULT_DAYS, apply=False):
    """Returns (deleted, kept, bytes). `apply=False` touches nothing."""
    root = (os.environ.get('PLANNING_IMAGE_DIR')
            or os.path.join(app.root_path, 'data', 'planning-images'))
    if not os.path.isdir(root):
        return 0, 0, 0

    # Names in use — a SINGLE query, a set per board.
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
            # `.tmp-*` is a half-finished upload: its name doesn't appear in any item, subject to the same rule.
            if name in keep:
                korunan += 1
                continue
            st = os.stat(p)
            if st.st_mtime > cutoff:
                korunan += 1           # orphan but still young (upload race)
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
