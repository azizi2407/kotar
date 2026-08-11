"""Validation and persistent local storage of Codex output.

Why not Drive: this pipeline's output is an in-panel draft that the team downloads and
uses; a Drive OAuth round trip on every generation means latency and quota (quota is
already 85% full — see the same rationale in planning_images.py). Why not `img_bucket`:
that serves PUBLICLY via `/img/<name>`; client brand images can't be placed at a public
URL. Why not `media_store`: that's a 21-day cache (Drive is canonical), here the
canonical copy is this file itself.

Layout: `data/codex-images/<client_id>/<uuid>.png` — splitting the directory per
client gets authorization for free (the serving endpoint goes through the role gate,
and even if the filename is guessed, the path still carries the client identity).
"""
import hashlib
import io
import os
import uuid

from flask import current_app
from PIL import Image, UnidentifiedImageError

MIN_BYTES = 1024                    # under 1 KB: half-written/empty file
MAX_BYTES = 25 * 1024 * 1024        # over 25 MB: not a real generation, an accident
NAME_LEN = 32                       # uuid4().hex


class OutputError(Exception):
    """Output validation error — the job closes with `error_code='invalid_output'`."""


def root_dir(create=False):
    d = (os.environ.get('CODEX_IMAGE_DIR')
         or os.path.join(current_app.root_path, 'data', 'codex-images'))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def validate(path, workdir):
    """Validate the file Codex produced → `{width, height, bytes, sha256, mime}`.

    Codex saying "success" is NOT TRUSTED (spec §7). Order matters: path → existence →
    size → content. The path check uses `realpath`; otherwise a symlink that sits inside
    the work directory but points outside it would let /etc/passwd pass as "output"."""
    real = os.path.realpath(path)
    root = os.path.realpath(workdir)
    if not (real == root or real.startswith(root + os.sep)):
        raise OutputError('çıktı iş dizininin dışında')
    if not os.path.isfile(real):
        raise OutputError('çıktı dosyası oluşmadı')
    size = os.path.getsize(real)
    if size < MIN_BYTES:
        raise OutputError('çıktı dosyası boş ya da yarım')
    if size > MAX_BYTES:
        raise OutputError(f'çıktı {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor')
    with open(real, 'rb') as f:
        data = f.read()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise OutputError('çıktı bir görsel değil ya da bozuk') from e
    if (img.format or '').upper() != 'PNG':
        raise OutputError(f'beklenmeyen görsel türü: {img.format or "bilinmiyor"}')
    return {'width': img.width, 'height': img.height, 'bytes': size,
            'sha256': hashlib.sha256(data).hexdigest(), 'mime': 'image/png'}


def store(client_id, src_path):
    """Move the validated file into permanent storage → path RELATIVE to the store.

    Re-saved with PIL: metadata (EXIF/comment/tEXt) is not carried over. Cost is low,
    the guarantee is clear — nothing the generation tool embedded reaches disk."""
    d = os.path.join(root_dir(create=True), str(client_id))
    os.makedirs(d, exist_ok=True)
    name = uuid.uuid4().hex + '.png'
    final = os.path.join(d, name)
    tmp = os.path.join(d, '.tmp-' + name)
    with Image.open(src_path) as img:
        img.load()
        # Copying onto a fresh canvas leaves the `info` dict (EXIF/tEXt/comment) behind;
        # `img.copy()` would have carried them over. `getdata` is NOT used — removed in Pillow 14.
        temiz = Image.new(img.mode, img.size)
        temiz.paste(img)
        temiz.save(tmp, format='PNG')
    # Write to a temp name first, then rename: a half-written file must never get the final name.
    os.replace(tmp, final)
    # GROUP WRITABLE: the worker (project owner) writes the file, Flask (svc-agency) reads/deletes it;
    # both are in the `appdev` group but the default umask (022) leaves the group read-only.
    try:
        os.chmod(final, 0o664)
        os.chmod(d, 0o775)
    except OSError:
        pass                        # permission tweak is best-effort; the file is still valid
    os.remove(src_path)             # don't leave the ephemeral copy behind
    return f'{client_id}/{name}'


def abs_path(rel):
    """Convert the relative path into an absolute path to serve; None if invalid/missing.

    `rel` comes from the DB, but is still validated: a corrupted or hand-edited
    row must not turn into filesystem traversal."""
    if not isinstance(rel, str) or '..' in rel or rel.startswith('/'):
        return None
    parts = rel.split('/')
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    stem, ext = os.path.splitext(parts[1])
    if ext != '.png' or len(stem) != NAME_LEN or not all(c in '0123456789abcdef' for c in stem):
        return None
    p = os.path.join(root_dir(), rel)
    return p if os.path.isfile(p) else None
