"""Content-addressed disk store (2026-08-09) — atomic write under the sha256 name.

`design_files` (working files) and `voice_notes` (voice notes) SHARE this
module. Writing them separately would have caused drift: the "mkstemp
produces 0600 → nginx (www-data) can't read the file, every download 403s"
bug found in production on 2026-08-08 would have had to be fixed in two
places separately.

Layout is `<store_dir>/<sha[:2]>/<sha>.<ext>`: a two-character prefix
directory so tens of thousands of files don't pile up in a single directory.
"""
import hashlib
import os
import tempfile

# Read block: 8 MB — 128 iterations for a 1 GB file, only one block stays in RAM.
CHUNK = 8 * 1024 * 1024
# 0644: owner writes, everyone reads. nginx reads the file as www-data via
# X-Accel; mkstemp's default is 0600 and `os.replace` PRESERVES it.
FILE_MODE = 0o644


def uzanti(file_name):
    """The last extension: lowercase, no dot, alphanumeric only, ≤12 chars. A
    file with no extension becomes 'bin' — so the disk name always stays in
    the `<sha>.<ext>` shape."""
    ext = os.path.splitext(file_name or '')[1].lstrip('.').lower()
    return ''.join(ch for ch in ext if ch.isalnum())[:12] or 'bin'


def yol(store_dir, sha256, file_name):
    """This content's canonical disk path."""
    return os.path.join(store_dir, sha256[:2], f'{sha256}.{uzanti(file_name)}')


def yaz(stream, store_dir, file_name, on_hashed=None):
    """Write the stream to disk, computing sha256 in the SAME pass; returns `(sha256, size)`.

    First written to a temp file, then moved to the canonical path via
    `os.replace`: a half-written file never appears at the canonical path (so
    a download waiting on the same hash never reads partial content). If the
    target already exists (dedup), the temp file is deleted — but its mode is
    still repaired: a 0600 file restored from a backup would otherwise stay
    permanently unreadable, and a new upload wouldn't fix it.

    `on_hashed`: an optional callback (`on_hashed(sha)`) invoked RIGHT AFTER
    sha256 is computed, BEFORE checking whether the target exists. For callers
    that need a synchronization point after hashing but before writing (e.g.
    `design_files._sha_kilidi` — a Postgres advisory lock that closes the
    TOCTOU race between upload and purge; this hook was added so the ordering
    that existed before delegating to this module is preserved EXACTLY)."""
    os.makedirs(store_dir, exist_ok=True)
    h = hashlib.sha256()
    boyut = 0
    fd, gecici = tempfile.mkstemp(dir=store_dir, suffix='.part')
    try:
        with os.fdopen(fd, 'wb') as out:
            while True:
                parca = stream.read(CHUNK)
                if not parca:
                    break
                h.update(parca)
                boyut += len(parca)
                out.write(parca)
        sha = h.hexdigest()
        if on_hashed is not None:
            on_hashed(sha)
        hedef = yol(store_dir, sha, file_name)
        os.makedirs(os.path.dirname(hedef), exist_ok=True)
        if os.path.exists(hedef):
            os.unlink(gecici)
        else:
            os.replace(gecici, hedef)
        os.chmod(hedef, FILE_MODE)
        return sha, boyut
    except Exception:
        if os.path.exists(gecici):
            os.unlink(gecici)
        raise
