"""Design working-file API (2026-08-07) — `/api/design-files`.

Designers' source files (`.psd`, `.ai`, `.indd`, `.aep`, packaged zips) live
here per client, VERSIONED.

**File is CANONICAL on the server, Drive holds a COPY.** Download authorization
can only be enforced while the file is ours: the depot (`depot.py`) grants
'anyone with the link can view' on its files, which is unacceptable for a
client's source file. The Drive copy exists for backup and sharing outside the
agency; its upload is BEST-EFFORT (if it fails, `drive_file_id` stays NULL and
the endpoint still returns 201).

**Quota is 2 GB per client, MEASURED via `SUM(file_size)` on every request** —
no counter column (same rationale as `depot.py`: a counter would drift
permanently on every crash between upload and commit).

CSRF is shared via `api.csrf_protect` (same pattern as `depot.py`/`ads.py`).
"""
import glob
import logging
import mimetypes
import os
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, send_file
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import drive_gateway as dg
import sha_store
from api import csrf_protect
# Blocked extension list and name sanitization are SHARED with the depot: if
# the two lists diverge, one of them becomes unsafe. Deliberately imported,
# not copied.
from depot import BLOCKED_EXT, _clean_name
from extensions import db
from models import Client, UserRef, utcnow
from models_design_files import DesignFile, DesignFileVersion
from sso_client import current_user

log = logging.getLogger('agency.design_files')

bp = Blueprint('design_files', __name__)
bp.before_request(csrf_protect)

DESIGN_ROLES = ('management', 'designer')

MAX_FILE_BYTES = 1024 * 1024 * 1024          # single file 1 GB (nginx cap is 1100m)
# WARNING: if this value EXCEEDS app.config['MAX_CONTENT_LENGTH'] (app.py), the
# body never reaches the view and hits Flask's global 413 instead — the check
# below (`_dosya_kontrol`) never runs (a bug that actually happened, see
# app.py). Guard: tests/test_design_files.py::test_max_file_bytes_uygulama_tavanini_asmiyor.
QUOTA_BYTES = 2 * 1024 * 1024 * 1024         # 2 GB per client
NOTE_MAX = 300
TITLE_MAX = 200
TAG_MAX = 40
TAGS_MAX = 12

STORE_DIR = os.environ.get('DESIGN_FILES_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'design-files')

DRIVE_FOLDER_NAME = 'Çalışma Dosyaları'

# nginx `X-Accel-Redirect` mode: nginx (NOT Flask) streams the file — so a 1 GB
# download doesn't tie up a gunicorn thread for minutes (2 workers x 4
# threads). When off (tests, `flask run`), falls back to `send_file`.
XACCEL = (os.environ.get('DESIGN_FILES_XACCEL') or '').strip() == '1'
XACCEL_PREFIX = '/_dsg/'


# --- authorization ------------------------------------------------------------------

def _require():
    """(user, err) — working files are open only to the design team and management."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not authenticated'), 401)
    if u.get('role') not in DESIGN_ROLES:
        return None, (jsonify(error='this section is only open to the design team'), 403)
    return u, None


def _silebilir(u, row):
    """Management can delete anything; a designer can delete ONLY what they
    uploaded (same pattern as fonts' `_silebilir`). The rule lives in ONE
    place — both the delete endpoint and the list's `can_delete` read from
    here, so they can't diverge and make the button lie.

    `row`: DesignFile (uses `created_by`) or DesignFileVersion (`uploaded_by`)."""
    if not u:
        return False
    if u.get('role') == 'management':
        return True
    sahibi = getattr(row, 'uploaded_by', None) or getattr(row, 'created_by', None)
    return u.get('role') == 'designer' and sahibi == u.get('sub')


def _musteri_veya_404(client_id):
    c = db.session.get(Client, client_id)
    if c is None or c.deleted_at is not None:
        return None, (jsonify(error='client not found'), 404)
    return c, None


def _uploader_names():
    return {u.sub: (u.name or u.email) for u in UserRef.query.all()}


# --- quota -------------------------------------------------------------------

def _kota(client_id):
    """Space used by the client — sum of non-deleted versions. NO counter
    column; the aggregate query (tens to hundreds of rows per client) is
    sub-ms."""
    used = int(db.session.query(
        db.func.coalesce(db.func.sum(DesignFileVersion.file_size), 0))
        .join(DesignFile, DesignFile.id == DesignFileVersion.file_id)
        .filter(DesignFile.client_id == client_id,
                DesignFile.deleted_at.is_(None),
                DesignFileVersion.deleted_at.is_(None))
        .scalar() or 0)
    kalan = max(0, QUOTA_BYTES - used)
    return {'used': used, 'limit': QUOTA_BYTES, 'remaining': kalan,
            'pct': round(used * 100.0 / QUOTA_BYTES, 1) if QUOTA_BYTES else 0.0}


def _cop_boyutu(client_id):
    """Total size in the trash — ALL deleted versions of deleted files, plus
    individually deleted versions of files that are still alive, all summed
    together. `DesignFileVersion.deleted_at IS NOT NULL` covers both cases
    (deleting a file stamps the same timestamp onto all its versions), so no
    separate file-status distinction is needed. A SINGLE aggregate query —
    same rationale as `_kota`, NO summing in a Python loop."""
    toplam = int(db.session.query(
        db.func.coalesce(db.func.sum(DesignFileVersion.file_size), 0))
        .join(DesignFile, DesignFile.id == DesignFileVersion.file_id)
        .filter(DesignFile.client_id == client_id,
                DesignFileVersion.deleted_at.isnot(None))
        .scalar() or 0)
    return toplam


# --- listing --------------------------------------------------------------

def _guncel_surumler(file_ids, silinmisler_dahil=False):
    """{file_id: (current_version, version_count)} — A SINGLE query.

    A separate `MAX(version_no)` query per file would be N+1; instead we pull
    all versions in one go and fold in Python (row count per client is
    two digits).

    `silinmisler_dahil=True`: for DELETED files in the trash — ALL of these
    files' versions are deleted (`file_delete` stamps the same timestamp on
    all of them), so the default filter (`deleted_at IS NULL`) would always
    return empty here. Existing callers (the live list) don't pass the
    parameter, so their behavior is unchanged — keeping this in one place
    instead of a separate twin function closes off the risk of the two
    queries drifting apart over time."""
    if not file_ids:
        return {}
    q = DesignFileVersion.query.filter(DesignFileVersion.file_id.in_(file_ids))
    if not silinmisler_dahil:
        q = q.filter(DesignFileVersion.deleted_at.is_(None))
    rows = q.order_by(DesignFileVersion.file_id, DesignFileVersion.version_no.desc()).all()
    out = {}
    for v in rows:
        guncel, adet = out.get(v.file_id, (None, 0))
        out[v.file_id] = (guncel or v, adet + 1)   # first one wins = highest version
    return out


@bp.get('/client/<int:client_id>')
def client_files(client_id):
    """Client's working files + quota (single response — spare the panel a second round trip)."""
    u, err = _require()
    if err:
        return err
    _, cerr = _musteri_veya_404(client_id)
    if cerr:
        return cerr
    rows = (DesignFile.query
            .filter_by(client_id=client_id, deleted_at=None)
            .order_by(DesignFile.title, DesignFile.id).all())
    guncel = _guncel_surumler([f.id for f in rows])
    names = _uploader_names()
    files = []
    for f in rows:
        v, adet = guncel.get(f.id, (None, 0))
        files.append(f.to_dict(
            current=v.to_dict(uploader_name=names.get(v.uploaded_by),
                              can_delete=_silebilir(u, v)) if v else None,
            version_count=adet,
            uploader_name=names.get(f.created_by),
            can_delete=_silebilir(u, f)))
    return jsonify(files=files, quota=_kota(client_id))


# --- disk -------------------------------------------------------------------

def _uzanti(file_name):
    """Final extension — shared `sha_store` implementation (see that module's docstring)."""
    return sha_store.uzanti(file_name)


def _yol(sha256, file_name):
    """`data/design-files/<sha[:2]>/<sha>.<ext>` — a two-character prefix
    directory, so tens of thousands of files don't pile up in a single
    directory (fonts uses a flat directory; files there are KB-scale)."""
    return sha_store.yol(STORE_DIR, sha256, file_name)


def _olcu(stream):
    """Size — WITHOUT loading the stream into RAM (werkzeug spools large bodies to disk)."""
    stream.seek(0, 2)
    n = stream.tell()
    stream.seek(0)
    return n


# --- concurrency: sha lock (TOCTOU) ---------------------------------------
#
# Race: if the target file ALREADY EXISTS on disk, `_diske_yaz` deletes the
# temp file without writing (dedup), and the new `DesignFileVersion` row only
# becomes visible when the request commits. `_purge_disk_dedup`, on the other
# hand, does its count in ITS OWN (separate) transaction, AFTER the purge's DB
# delete. The following order was possible:
#   upload dedup-skips (file already on disk) -> purge counts (can't see the
#   upload's not-yet-committed new row, finds 0) -> purge does `os.unlink` ->
#   upload commits -> the new row now points to a file that's NOT ON DISK
#   (download breaks, the canonical copy is permanently gone).
#
# Fix: `pg_advisory_xact_lock` for the same `sha256` — TRANSACTION scoped,
# released AUTOMATICALLY on commit/rollback (no manual "unlock" call needed).
# The upload side takes the lock inside `_diske_yaz`, IMMEDIATELY BEFORE
# checking whether the target file exists; the request's (view function's)
# first `db.session.commit()` both makes the new row visible and releases the
# lock — exactly the ordering we want. The purge side takes the same lock
# BEFORE counting (`_purge_disk_dedup`). Either possible order gives a
# consistent result: if the upload locks first, the purge waits until the row
# is committed and visible, and then counts >=1 (does NOT delete the file);
# if the purge locks first, the upload waits until the purge clears the disk
# and releases the lock, and then does its `os.path.exists` check against the
# UP-TO-DATE disk state (the file is gone now -> dedup doesn't skip, rewrites
# the content).
#
# Key: instead of deriving an int from the sha256 in Python, Postgres's own
# `hashtext()` is used — the SAME pattern exists elsewhere in the repo, in
# `jobqueue.py`'s `enqueue` dedup lock; the string goes straight to the
# database, no extra bit-conversion code. To avoid collisions the key is
# namespaced with a `design-files:sha:` prefix (otherwise `hashtext` could
# collide with a literal key string from another module sharing the same
# 32-bit space).
#
# sqlite (tests) has NO advisory lock — silently skipped via a dialect check;
# in a single-process, single-connection sqlite test this race can't arise
# anyway (see the docstring of
# `tests/test_design_files.py::test_sha_kilidi_yalniz_postgreste_cagrilir` —
# it spells out exactly what the test does and doesn't prove).
#
# How long the lock is held on the upload side: the sha is only known AFTER
# the stream has been FULLY read and hashed, so the lock is taken AFTER the
# large-file I/O (the `stream.read` loop) — the time spent reading/writing a
# 1 GB upload stays OUTSIDE the lock. The lock only covers the (small, fast)
# window from the existence check through `os.replace` up to the DB row's
# commit. If another request is waiting on the same sha, it's blocked only
# for this window — since the same sha means the same content, this is rare
# and correct behavior.
def _sha_kilidi(sha):
    """Take a transaction-scoped advisory lock for `sha256` — Postgres only.

    Read the block comment above: this function is the ONE place that closes
    the TOCTOU race between upload (`_diske_yaz`) and purge
    (`_purge_disk_dedup`); both call it (the lock logic is consolidated here
    to remove the risk of the two sites drifting apart)."""
    if db.session.get_bind().dialect.name != 'postgresql':
        return
    db.session.execute(
        text('SELECT pg_advisory_xact_lock(hashtext(:k))'),
        {'k': f'design-files:sha:{sha}'})


def _diske_yaz(stream, file_name):
    """Write the stream to disk + sha256; `(sha256, size)`. Atomicity, dedup,
    and the 0644 mode live in the shared `sha_store` — shared with
    `voice_notes` so the 0600 bug (2026-08-08) doesn't need to be fixed
    separately in two places.

    `on_hashed=_sha_kilidi`: so that `_sha_kilidi` is called right after the
    sha is computed, BEFORE checking whether the target exists — keeping the
    lock ordering that closes the TOCTOU race EXACTLY the same as before it
    was delegated to `sha_store` (read the block comment above together with
    `_purge_disk_dedup`)."""
    return sha_store.yaz(stream, STORE_DIR, file_name, on_hashed=_sha_kilidi)


# --- Drive (best-effort) ----------------------------------------------------

def _drive_kopyala(client, version, path):
    """Copy the file into 'Çalışma Dosyaları' under the client's Drive root.

    BEST-EFFORT: failure doesn't break the upload, `drive_file_id` stays NULL
    and the panel shows a 'not copied to Drive' badge. The user is unaffected
    since the canonical file is on disk (the reverse was true in the depot —
    there the Drive file WAS the product itself). SETS `version.drive_file_id`,
    does NOT commit."""
    from sharing import _extract_folder_id
    try:
        if not dg.available():
            return False
        root = _extract_folder_id(client.drive_meta)
        if not root:
            return False
        folder = dg.ensure_subfolder(root, DRIVE_FOLDER_NAME)
        with open(path, 'rb') as fh:
            meta = dg.upload_file(folder, version.file_name, fh,
                                  version.mime_type or 'application/octet-stream')
        version.drive_file_id = meta.get('id')
        return bool(version.drive_file_id)
    except Exception as e:  # noqa: BLE001 — the Drive copy is not the product itself
        log.warning('çalışma dosyası Drive kopyası başarısız (%s): %s',
                    version.file_name, e)
        return False


# --- form helpers ------------------------------------------------------

def _etiketler(raw):
    """Turn the JSON array from the form into a cleaned tag list.

    Blanks are dropped, values are trimmed, cut to TAG_MAX, and limited to
    TAGS_MAX. Duplicate elimination uses the Turkish-aware
    `client_tracking._fold()` ('Şablon' and 'şablon' are the same tag) — NOT
    plain `casefold()`: `'İ'.casefold()` doesn't produce a plain 'i', it
    produces a string with a combining dot ('İstanbul'/'istanbul' would count
    as different tags), which is why the repo uses the shared `_fold` that
    normalizes Turkish letters first. The display keeps the case the user
    ENTERED."""
    import json

    from client_tracking import _fold
    if not raw:
        return []
    try:
        veri = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(veri, list):
        return []
    out, gorulen = [], set()
    for t in veri:
        if not isinstance(t, str):
            continue
        t = t.strip()[:TAG_MAX]
        if not t:
            continue
        k = _fold(t)
        if k in gorulen:
            continue
        gorulen.add(k)
        out.append(t)
        if len(out) >= TAGS_MAX:
            break
    return out


def _dosya_kontrol(client_id, f):
    """(name, size, mime, err) — the check order BEFORE touching Drive/disk:
    measure size -> over 1 GB is 413 -> over quota is 409 -> blocked
    extension is 400 (depot pattern)."""
    if f is None or not f.filename:
        return None, 0, None, (jsonify(error='no file provided'), 400)
    ad = _clean_name(f.filename)
    boyut = _olcu(f.stream)
    if boyut > MAX_FILE_BYTES:
        return None, 0, None, (jsonify(
            error='File exceeds the 1 GB limit.'), 413)
    q = _kota(client_id)
    if boyut > q['remaining']:
        return None, 0, None, (jsonify(
            error=f'Client storage is full — {_mb(q["remaining"])} remaining, '
                  f'file is {_mb(boyut)}.', quota=q), 409)
    if _blocked_ext(ad):
        return None, 0, None, (jsonify(
            error='This file type cannot be uploaded (executable file).'), 400)
    mime = f.mimetype or mimetypes.guess_type(ad)[0] or 'application/octet-stream'
    # DesignFileVersion.mime_type is String(120) — if the client's Content-Type
    # exceeds it (some browser/extension combos send long/parameterized
    # values), the Postgres commit blows up with a DataError; by then the file
    # is already written to disk and gets orphaned. Truncating here covers both
    # the new-file (v1) and new-version paths at once — both go through this
    # function.
    mime = mime[:120]
    return ad, boyut, mime, None


def _blocked_ext(name):
    return _uzanti(name) in BLOCKED_EXT


def _mb(n):
    return f'{n / 1024 / 1024:.0f} MB'


def _tek_dosya_json(f, u):
    """A single file's (with its current version) panel dict — used in the upload response."""
    guncel = _guncel_surumler([f.id])
    v, adet = guncel.get(f.id, (None, 0))
    names = _uploader_names()
    return f.to_dict(
        current=v.to_dict(uploader_name=names.get(v.uploaded_by),
                          can_delete=_silebilir(u, v)) if v else None,
        version_count=adet, uploader_name=names.get(f.created_by),
        can_delete=_silebilir(u, f))


# --- upload ----------------------------------------------------------------

@bp.post('/client/<int:client_id>')
def client_file_create(client_id):
    """New working file + v1 (multipart: file, title, tags?, note?)."""
    u, err = _require()
    if err:
        return err
    c, cerr = _musteri_veya_404(client_id)
    if cerr:
        return cerr
    title = (request.form.get('title') or '').strip()[:TITLE_MAX]
    if not title:
        return jsonify(error='title is required'), 400
    f = request.files.get('file')
    ad, boyut, mime, ferr = _dosya_kontrol(client_id, f)
    if ferr:
        return ferr

    sha, _ = _diske_yaz(f.stream, ad)
    row = DesignFile(client_id=client_id, title=title,
                     tags=_etiketler(request.form.get('tags')),
                     created_by=u['sub'], created_at=utcnow())
    db.session.add(row)
    db.session.flush()
    v = DesignFileVersion(
        file_id=row.id, version_no=1, sha256=sha, file_name=ad, mime_type=mime,
        file_size=boyut, note=(request.form.get('note') or '').strip()[:NOTE_MAX] or None,
        uploaded_by=u['sub'], uploaded_at=utcnow())
    db.session.add(v)
    # DB commit BEFORE Drive: the canonical file is on disk, so even if Drive
    # lags the user can see and download their file.
    db.session.commit()
    drive_ok = _drive_kopyala(c, v, _yol(sha, ad))
    db.session.commit()
    return jsonify(file=_tek_dosya_json(row, u), drive_ok=drive_ok,
                   quota=_kota(client_id)), 201


# --- versioning ----------------------------------------------------------

def _dosya_veya_404(file_id):
    f = db.session.get(DesignFile, file_id)
    if f is None or f.deleted_at is not None:
        return None, (jsonify(error='file not found'), 404)
    return f, None


@bp.get('/<int:file_id>/versions')
def file_versions(file_id):
    """Version history — newest to oldest (panel shows the same order in its dropdown)."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    # If the client is archived (soft-deleted), the file row remains but the
    # history shouldn't show — same check as `client_files`/`file_version_create`.
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    rows = (DesignFileVersion.query
            .filter_by(file_id=f.id, deleted_at=None)
            .order_by(DesignFileVersion.version_no.desc()).all())
    names = _uploader_names()
    return jsonify(versions=[v.to_dict(uploader_name=names.get(v.uploaded_by),
                                       can_delete=_silebilir(u, v))
                             for v in rows])


@bp.post('/<int:file_id>/versions')
def file_version_create(file_id):
    """Upload a new version (multipart: file, note?). Version number is MAX+1.

    Race: if two designers upload at the same time, MAX+1 comes out the same
    for both, and UNIQUE(file_id, version_no) rejects the second one -> 409.
    Automatically retrying (MAX+2) is DELIBERATELY not done: the user would
    end up overwriting the other's version without seeing it."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    c, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    up = request.files.get('file')
    ad, boyut, mime, uerr = _dosya_kontrol(f.client_id, up)
    if uerr:
        return uerr

    # Deliberately MAX(version_no) WITHOUT applying the `deleted_at` filter:
    # if a deleted version's number got reused, past notes/download records
    # would misleadingly point to two different pieces of content (the
    # "current" filter is kept separate, see `_guncel_surumler`).
    sonraki = 1 + int(db.session.query(
        db.func.coalesce(db.func.max(DesignFileVersion.version_no), 0))
        .filter(DesignFileVersion.file_id == f.id).scalar() or 0)
    sha, _ = _diske_yaz(up.stream, ad)
    v = DesignFileVersion(
        file_id=f.id, version_no=sonraki, sha256=sha, file_name=ad, mime_type=mime,
        file_size=boyut, note=(request.form.get('note') or '').strip()[:NOTE_MAX] or None,
        uploaded_by=u['sub'], uploaded_at=utcnow())
    db.session.add(v)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error='Someone else uploaded a new version in the meantime — '
                             'refresh the page and try again.'), 409
    drive_ok = _drive_kopyala(c, v, _yol(sha, ad))
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u), drive_ok=drive_ok,
                   quota=_kota(f.client_id)), 201


# --- download ------------------------------------------------------------

@bp.get('/versions/<int:version_id>/download')
def version_download(version_id):
    """Download the version. Authorization is checked HERE; streaming (in
    nginx mode) happens in nginx.

    No Drive link is given: the Drive copy has restricted permissions and is
    useless to a designer without an agency Google account; the file is
    already canonical on our side."""
    _, err = _require()
    if err:
        return err
    v = db.session.get(DesignFileVersion, version_id)
    if v is None or v.deleted_at is not None:
        return jsonify(error='version not found'), 404
    f, ferr = _dosya_veya_404(v.file_id)
    if ferr:
        return ferr
    # If the client is archived (soft-deleted), the file row remains but must
    # not be downloadable — same rule as `file_versions`/`file_version_create`
    # (flagged in review).
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    yol = _yol(v.sha256, v.file_name)
    if not os.path.exists(yol):
        # DB row exists, disk file doesn't: manually deleted, or a migration
        # mishap. A clear state instead of a 500 — panel shows "file not found
        # on server".
        log.error('çalışma dosyası diskte yok: version=%s sha=%s', v.id, v.sha256)
        return jsonify(error='file not found on server'), 410

    if not XACCEL:
        # No nginx (tests / `flask run`) -> Flask serves the file directly.
        return send_file(yol, mimetype=v.mime_type or 'application/octet-stream',
                         as_attachment=True, download_name=v.file_name,
                         conditional=True)

    resp = Response(status=200)
    resp.headers['X-Accel-Redirect'] = (
        XACCEL_PREFIX + f'{v.sha256[:2]}/{v.sha256}.{_uzanti(v.file_name)}')
    resp.headers['Content-Type'] = v.mime_type or 'application/octet-stream'
    # The name on disk is a hash — let the user download the ORIGINAL name
    # (Turkish characters as UTF-8).
    resp.headers['Content-Disposition'] = (
        "attachment; filename*=UTF-8''" + quote(v.file_name or f'dosya-{v.id}'))
    return resp


# --- editing and deletion -------------------------------------------------

@bp.patch('/<int:file_id>')
def file_patch(file_id):
    """Edit title and tags. Does not touch the file's content."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    # If the client is archived (soft-deleted), the file row remains but must
    # not be editable — same rule as `file_versions`/`version_download`.
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    veri = request.get_json(silent=True) or {}
    if 'title' in veri:
        ham_title = veri.get('title')
        # A non-string body like `{"title": 5}` would raise AttributeError in
        # `.strip()` and fall through to a 500 — a clear 400 here instead.
        if ham_title is not None and not isinstance(ham_title, str):
            return jsonify(error='title must be text'), 400
        t = (ham_title or '').strip()[:TITLE_MAX]
        if not t:
            return jsonify(error='title cannot be empty'), 400
        f.title = t
    if 'tags' in veri:
        import json
        ham_tags = veri.get('tags')
        # A non-array body like `{"tags": "şablon"}` would silently return []
        # from `_etiketler` and delete ALL of the file's tags — a clear 400
        # instead of a 200.
        if ham_tags is not None and not isinstance(ham_tags, list):
            return jsonify(error='tags must be a list'), 400
        f.tags = _etiketler(json.dumps(ham_tags or []))
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u))


def _drive_cope(version):
    """Move the Drive copy to trash (recoverable for 30 days). BEST-EFFORT:
    a single Drive hiccup shouldn't make the panel record undeletable (depot pattern)."""
    if not version.drive_file_id:
        return True
    try:
        dg.trash_file(version.drive_file_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning('Drive kopyası çöpe atılamadı (%s): %s', version.drive_file_id, e)
        return False


def _drive_geri_al(version):
    """Take the Drive copy out of trash (opposite of `_drive_cope`).
    BEST-EFFORT: since the canonical file remains on the server, a Drive
    error must NOT BLOCK the restore — it just returns `drive_ok` False, and
    the panel can show that the Drive copy is still in trash (and will be
    permanently deleted after 30 days)."""
    if not version.drive_file_id:
        return True
    try:
        dg.untrash_file(version.drive_file_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning('Drive kopyası çöpten çıkarılamadı (%s): %s', version.drive_file_id, e)
        return False


@bp.delete('/<int:file_id>')
def file_delete(file_id):
    """Soft-delete the file and all its versions; move Drive copies to trash.

    Disk file STAYS: another row could point at the same sha256, and
    soft-delete must be reversible (fonts pattern)."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    # If the client is archived, the file already doesn't show, but the
    # endpoint can be called directly — same rule here too (consistent with
    # `file_versions`).
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if not _silebilir(u, f):
        return jsonify(error='only the uploader or management can delete'), 403
    simdi = utcnow()
    drive_ok = True
    for v in DesignFileVersion.query.filter_by(file_id=f.id, deleted_at=None).all():
        drive_ok = _drive_cope(v) and drive_ok
        v.deleted_at = simdi
        v.deleted_by = u['sub']
    f.deleted_at = simdi
    f.deleted_by = u['sub']
    db.session.commit()
    return jsonify(ok=True, drive_ok=drive_ok, quota=_kota(f.client_id))


@bp.delete('/versions/<int:version_id>')
def version_delete(version_id):
    """Remove a single version. The last remaining version cannot be deleted —
    the file becoming an empty shell with no content at all is meaningless to
    the user; whoever wants to delete the file should delete the file
    (`DELETE /<file_id>`)."""
    u, err = _require()
    if err:
        return err
    v = db.session.get(DesignFileVersion, version_id)
    if v is None or v.deleted_at is not None:
        return jsonify(error='version not found'), 404
    f, ferr = _dosya_veya_404(v.file_id)
    if ferr:
        return ferr
    # Same consistency rule: an archived client's version shouldn't be deletable.
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if not _silebilir(u, v):
        return jsonify(error='only the uploader or management can delete'), 403
    kalan = DesignFileVersion.query.filter_by(file_id=f.id, deleted_at=None).count()
    if kalan <= 1:
        return jsonify(error='The last version cannot be deleted — delete the entire file instead.'), 400
    drive_ok = _drive_cope(v)
    v.deleted_at = utcnow()
    v.deleted_by = u['sub']
    db.session.commit()
    return jsonify(ok=True, drive_ok=drive_ok, quota=_kota(f.client_id))


# --- trash: restore ---------------------------------------------------
#
# Soft-delete used to be a one-way gate by itself (`deleted_at` gets written,
# no endpoint to reverse it) — the trash turns it into an approval queue:
# everything deleted sits here, and management either restores it or purges
# it permanently (project owner's decision, 2026-08-08, see
# `.superpowers/sdd/2026-08-07-tasarim-calisma-dosyalari/
# followup-silme-brief.md`). A SEPARATE request/approval state machine was
# NOT built: the trigger already lives in the existing authorization matrix
# via `_silebilir`/`management`.


@bp.get('/client/<int:client_id>/trash')
def client_trash(client_id):
    """Trash: deleted FILES + individually deleted VERSIONS of files that are
    still alive (two separate arrays — one for file-level restore/purge, the
    other version-level). Permanent deletion is NOT DONE here, only viewing +
    flags (see `file_purge_request`)."""
    u, err = _require()
    if err:
        return err
    _, cerr = _musteri_veya_404(client_id)
    if cerr:
        return cerr
    names = _uploader_names()
    yonetim = u.get('role') == 'management'

    dosyalar = (DesignFile.query
                .filter(DesignFile.client_id == client_id,
                        DesignFile.deleted_at.isnot(None))
                .order_by(DesignFile.deleted_at.desc()).all())
    # Deleted files' versions are deleted too — without `silinmisler_dahil=True`
    # `_guncel_surumler` would always return empty for these rows (the live
    # list's `deleted_at IS NULL` filter matches nothing here).
    guncel = _guncel_surumler([f.id for f in dosyalar], silinmisler_dahil=True)
    files = []
    for f in dosyalar:
        v, adet = guncel.get(f.id, (None, 0))
        files.append(f.to_dict(
            current=v.to_dict(uploader_name=names.get(v.uploaded_by),
                              can_delete=_silebilir(u, v)) if v else None,
            version_count=adet,
            uploader_name=names.get(f.created_by),
            can_delete=_silebilir(u, f),
            can_restore=_silebilir(u, f),
            can_purge=yonetim,
            deleter_name=names.get(f.deleted_by),
            purge_requester_name=names.get(f.purge_requested_by)))

    versiyonlar = (DesignFileVersion.query
                   .join(DesignFile, DesignFile.id == DesignFileVersion.file_id)
                   .filter(DesignFile.client_id == client_id,
                           DesignFile.deleted_at.is_(None),
                           DesignFileVersion.deleted_at.isnot(None))
                   .order_by(DesignFileVersion.deleted_at.desc()).all())
    versions = [v.to_dict(
        uploader_name=names.get(v.uploaded_by),
        can_delete=_silebilir(u, v),
        can_restore=_silebilir(u, v),
        can_purge=yonetim,
        deleter_name=names.get(v.deleted_by))
        for v in versiyonlar]
    return jsonify(files=files, versions=versions, trash_bytes=_cop_boyutu(client_id))


@bp.post('/<int:file_id>/restore')
def file_restore(file_id):
    """Restore the file from trash.

    **Scope — match by `deleted_at` timestamp:** ALONG WITH the file, only
    versions with `deleted_at == f.deleted_at` get restored; versions deleted
    INDIVIDUALLY earlier (carrying a different, older timestamp) stay
    deleted. This matching is reliable because `file_delete` stamps the SAME
    `utcnow()` on all active versions — otherwise there'd be a surprise like
    "I deleted v2, then deleted and restored the file, and v2 came back too."""
    u, err = _require()
    if err:
        return err
    f = db.session.get(DesignFile, file_id)
    if f is None:
        return jsonify(error='file not found'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is None:
        return jsonify(error='file is not in the trash'), 400
    if not _silebilir(u, f):
        return jsonify(error='only the uploader or management can restore'), 403
    damga = f.deleted_at
    f.deleted_at = None
    f.deleted_by = None
    f.purge_requested_at = None
    f.purge_requested_by = None
    surumler = (DesignFileVersion.query
                .filter(DesignFileVersion.file_id == f.id,
                        DesignFileVersion.deleted_at == damga).all())
    drive_ok = True
    for v in surumler:
        drive_ok = _drive_geri_al(v) and drive_ok
        v.deleted_at = None
        v.deleted_by = None
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u), drive_ok=drive_ok, quota=_kota(f.client_id))


@bp.post('/versions/<int:version_id>/restore')
def version_restore(version_id):
    """Restore a single version from trash. Rejected (400) if the file
    ITSELF is in trash — in that case the restore must happen at the file
    level (`file_restore`), otherwise it would produce an inconsistent
    intermediate state, like a 'live' version attached to a file that doesn't
    show in the live list."""
    u, err = _require()
    if err:
        return err
    v = db.session.get(DesignFileVersion, version_id)
    if v is None:
        return jsonify(error='version not found'), 404
    f = db.session.get(DesignFile, v.file_id)
    if f is None:
        return jsonify(error='file not found'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is not None:
        return jsonify(error='the file itself is in the trash — restore the file first'), 400
    if v.deleted_at is None:
        return jsonify(error='version is not in the trash'), 400
    if not _silebilir(u, v):
        return jsonify(error='only the uploader or management can restore'), 403
    drive_ok = _drive_geri_al(v)
    v.deleted_at = None
    v.deleted_by = None
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u), drive_ok=drive_ok, quota=_kota(f.client_id))


@bp.post('/<int:file_id>/purge-request')
def file_purge_request(file_id):
    """A designer's 'purge this permanently' flag — set/clear. DELETES
    NOTHING, just leaves a warning badge visible to management in the trash.
    Authorization is via `_require()` for the whole design team (not just the
    owner) — requesting is a lighter action than deleting."""
    u, err = _require()
    if err:
        return err
    f = db.session.get(DesignFile, file_id)
    if f is None:
        return jsonify(error='file not found'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is None:
        return jsonify(error='can only be requested for a file that is in the trash'), 400
    veri = request.get_json(silent=True) or {}
    istendi = bool(veri.get('requested'))
    if istendi:
        f.purge_requested_at = utcnow()
        f.purge_requested_by = u['sub']
    else:
        f.purge_requested_at = None
        f.purge_requested_by = None
    db.session.commit()
    return jsonify(ok=True, requested=istendi)


def _purge_disk_dedup(sha):
    """Should ALL disk paths for a given `sha256` STAY or be DELETED — deletes
    only if NO OTHER `DesignFileVersion` row (including deleted ones, since
    those are still restorable) points at the same content (dedup). An
    unguarded `os.unlink` would kill another client's still-live file.
    BEST-EFFORT: if it fails, the DB purge is NOT ROLLED BACK, the caller
    just gets `disk_ok:false`.

    **Extension leakage:** the same bytes might have been uploaded under
    different names/extensions (`logo.psd` and `logo.ai` with identical
    content) — since `_yol` includes the extension, TWO separate files exist
    on disk (`<sha>.psd`, `<sha>.ai`). Rather than a single `file_name`'s
    path, we look at ALL paths for that sha (`<sha[:2]>/<sha>.*` pattern,
    `glob`). This is safe: the count is already sha-scoped (extension-
    independent), and if it's 0 no row remains — however many files match the
    pattern, all of them are orphaned.

    **Lock:** `_sha_kilidi` is taken BEFORE counting — the lock that closes
    the TOCTOU race (read together with `_diske_yaz`, see the block comment
    above). After the count (whatever it finds), `db.session.commit()`
    releases the lock right away; since this function doesn't change any
    data outside a read-only SELECT, the commit is safe — it's only there to
    release the advisory lock (same pattern as jobqueue.py's `enqueue`)."""
    _sha_kilidi(sha)
    if DesignFileVersion.query.filter_by(sha256=sha).count():
        db.session.commit()   # release the lock (no changes in this transaction)
        return True
    desen = os.path.join(STORE_DIR, sha[:2], f'{sha}.*')
    ok = True
    for yol in glob.glob(desen):
        try:
            os.unlink(yol)
        except OSError as e:
            log.warning('çalışma dosyası diskten kalıcı silinemedi (%s): %s', yol, e)
            ok = False
    db.session.commit()       # release the lock
    return ok


@bp.delete('/<int:file_id>/purge')
def file_purge(file_id):
    """PERMANENTLY delete the file and ALL its versions. Management only,
    only a file already in trash, only with `{"confirm": true}` in the body —
    unrecoverable.

    **FK order:** `DesignFileVersion` rows first, then `DesignFile` (lesson
    from `card_uploads` deletion — reverse order raises an FK error).

    **No permanent deletion in Drive:** the copy was already moved to Drive
    trash at soft-delete time (`_drive_cope`, recoverable for 30 days), and
    `drive_gateway` has no permanent-delete function — a deliberate choice by
    the repo (same rationale as `sharing.py`). Drive cleans it up on its own
    30-day schedule."""
    u, err = _require()
    if err:
        return err
    if u.get('role') != 'management':
        return jsonify(error='permanent deletion can only be done by management'), 403
    f = db.session.get(DesignFile, file_id)
    if f is None:
        return jsonify(error='file not found'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is None:
        return jsonify(error='only a file already in the trash can be permanently deleted'), 400
    veri = request.get_json(silent=True) or {}
    if veri.get('confirm') is not True:
        return jsonify(error='permanent deletion requires confirmation ({"confirm": true})'), 400

    versiyonlar = DesignFileVersion.query.filter_by(file_id=f.id).all()
    # sha256s are collected BEFORE the commit — accessing ORM objects after
    # their rows are deleted risks a DetachedInstanceError. `set`: two versions
    # of the same file could've been re-uploaded with identical content (same
    # sha), this weeds out the repeated lock/query on the Python side upfront.
    diskteki = {v.sha256 for v in versiyonlar}
    client_id = f.client_id
    DesignFileVersion.query.filter_by(file_id=f.id).delete(synchronize_session=False)
    db.session.delete(f)
    db.session.commit()

    disk_ok = True
    for sha in diskteki:
        disk_ok = _purge_disk_dedup(sha) and disk_ok
    return jsonify(ok=True, disk_ok=disk_ok, quota=_kota(client_id))


@bp.delete('/versions/<int:version_id>/purge')
def version_purge(version_id):
    """PERMANENTLY delete a single version. Management only, only a version
    already in trash, only with `{"confirm": true}`. No permanent deletion in
    Drive (same rationale as `file_purge`).

    **Gate: the file itself must not be in trash.** Unlike `version_restore`,
    this endpoint used to check only `v.deleted_at` — purging versions one by
    one while the file itself (`f.deleted_at`) is in trash could leave the
    `design_files` row with ZERO versions (a shell file that violates
    `version_delete`'s "last version can't be deleted" rule; if later
    restored, the live list would show `current: null`). The SAME gate as
    `version_restore` is applied here too."""
    u, err = _require()
    if err:
        return err
    if u.get('role') != 'management':
        return jsonify(error='permanent deletion can only be done by management'), 403
    v = db.session.get(DesignFileVersion, version_id)
    if v is None:
        return jsonify(error='version not found'), 404
    f = db.session.get(DesignFile, v.file_id)
    if f is None:
        return jsonify(error='file not found'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is not None:
        return jsonify(error='the file itself is in the trash — permanently delete '
                             'the entire file, not individual versions'), 400
    if v.deleted_at is None:
        return jsonify(error='only a version already in the trash can be permanently deleted'), 400
    veri = request.get_json(silent=True) or {}
    if veri.get('confirm') is not True:
        return jsonify(error='permanent deletion requires confirmation ({"confirm": true})'), 400

    sha = v.sha256
    db.session.delete(v)
    db.session.commit()
    disk_ok = _purge_disk_dedup(sha)
    return jsonify(ok=True, disk_ok=disk_ok, quota=_kota(f.client_id))
