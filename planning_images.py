"""Planning Board images — a local file store per board.

The user pastes an image onto the board from the clipboard, drags a file, or
right-clicks → "Add image"; the file is stored ON THE SERVER and an item with
`type='image'` in `planning_items` points to it.

Why not `img_bucket`: that store serves **publicly** via `/img/<name>` (for
embedding on external sites). Board content is internal data — it can't be put
on a public URL. Why not `media_store`: that's a 21-DAY cache (Drive is
canonical), a board image has no canonical copy, and if deleted the item is left
dangling. Why not Drive: board images are small and numerous; the Drive OAuth
quota is already 85% full and every paste would mean a network round trip delay.

Layout: `data/planning-images/<board_id>/<uuid>.<ext>` — splitting the directory
per board gets authorization for free: the serving endpoint goes through
`_board_access(key)`, so even a guessed file name can't reach another board's image.
"""
import io
import os
import uuid

from flask import current_app
from PIL import Image, UnidentifiedImageError

# Accepted types — the intersection of what PIL recognizes and the browser can display.
# SVG IS DELIBERATELY EXCLUDED: it's text-based, can carry scripts, and would be
# rendered inline on the board (it's accepted in img_bucket because that's for
# external embedding).
ALLOWED = {'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif', 'WEBP': '.webp'}

MAX_BYTES = 10 * 1024 * 1024              # 10 MB/file — plenty for a screenshot
MAX_BOARD_BYTES = 500 * 1024 * 1024       # 500 MB/board
MAX_EDGE = 2000                           # edges larger than this are downscaled
NAME_LEN = 32                             # uuid4().hex


class ImageError(Exception):
    """Validation error to show the user (400)."""


def board_dir(board_id, create=False):
    root = (os.environ.get('PLANNING_IMAGE_DIR')
            or os.path.join(current_app.root_path, 'data', 'planning-images'))
    d = os.path.join(root, str(board_id))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def safe_name(name):
    """Validates the `<32 hex>.<ext>` format — closed against path traversal.

    The name comes from the client (the item's `extra.image.name`); not even a
    single stray character is allowed through, otherwise `../../etc/passwd` could
    be read."""
    if not isinstance(name, str) or len(name) > NAME_LEN + 8:
        return None
    stem, ext = os.path.splitext(name)
    if len(stem) != NAME_LEN or not all(c in '0123456789abcdef' for c in stem):
        return None
    if ext.lower() not in set(ALLOWED.values()):
        return None
    return stem + ext.lower()


def board_bytes(board_id):
    """Total disk used by the board — NO counter column (depot pattern).

    A counter would produce permanent drift on every crash between the file write
    and the DB commit; a directory scan is negligible at a few hundred files per board."""
    d = board_dir(board_id)
    if not os.path.isdir(d):
        return 0
    return sum(os.path.getsize(os.path.join(d, n)) for n in os.listdir(d)
               if os.path.isfile(os.path.join(d, n)))


def store(board_id, data):
    """Raw bytes → image written to disk. Returns `{name, width, height, size}`.

    Validation ORDER matters: size → content → quota. The quota check is last but
    BEFORE writing; putting a large file on disk first and deleting it later is
    needless I/O."""
    if not data:
        raise ImageError('boş dosya')
    if len(data) > MAX_BYTES:
        raise ImageError(f'görsel {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageError('dosya bir görsel değil ya da bozuk') from e
    fmt = (img.format or '').upper()
    if fmt not in ALLOWED:
        raise ImageError(f'desteklenmeyen görsel türü: {fmt or "bilinmiyor"}')

    # There's no benefit to keeping a very large image at full resolution on the
    # board; downscaling would reduce an animated GIF to its first frame → GIFs
    # are stored as-is.
    payload, w, h = data, img.width, img.height
    if fmt != 'GIF' and max(img.width, img.height) > MAX_EDGE:
        img = img.copy()
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        buf = io.BytesIO()
        # Transparency is preserved for PNG; converting to JPEG would stamp a white background.
        img.save(buf, format=fmt, **({'quality': 88} if fmt == 'JPEG' else {}))
        payload, w, h = buf.getvalue(), img.width, img.height

    if board_bytes(board_id) + len(payload) > MAX_BOARD_BYTES:
        raise ImageError('pano görsel alanı dolu (500 MB) — eski görselleri silin')

    name = uuid.uuid4().hex + ALLOWED[fmt]
    d = board_dir(board_id, create=True)
    tmp = os.path.join(d, '.tmp-' + name)
    # Write to a temp name first, then rename: a half-written file should never
    # get the permanent name (the client would be served a corrupt image after
    # writing that name onto the item).
    with open(tmp, 'wb') as f:
        f.write(payload)
    final = os.path.join(d, name)
    os.replace(tmp, final)
    # GROUP-WRITABLE: the file is written by Flask (svc-agency), the cleanup timer
    # runs as `project owner`; both are in the `appdev` group and `data/` is setgid,
    # but the default umask (022) leaves the group read-only → the janitor
    # couldn't delete it.
    try:
        os.chmod(final, 0o664)
        os.chmod(d, 0o775)
    except OSError:
        pass                     # permission setting is best-effort; the upload is still valid
    return {'name': name, 'width': w, 'height': h, 'size': len(payload)}


def path_of(board_id, name):
    """Full path of the file to serve; None if the name is invalid/the file is missing."""
    safe = safe_name(name)
    if not safe:
        return None
    p = os.path.join(board_dir(board_id), safe)
    return p if os.path.isfile(p) else None
