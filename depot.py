"""Videographer Depot — `/api/depot/*` [Blueprint: /api/depot].

A SHARED free-form file space for videographers + management: not tied to a
client or week, this is where the team's working material lives (raw
footage, LUTs, project files, references...). A SINGLE flat folder in Drive
under `<content root>/Videograf Deposu`.

QUOTA: shared **5 GB**, **500 MB** per file. Quota is MEASURED via
`SUM(file_size)` on every request, NO counter column — a counter would drift
permanently on every crash between the Drive upload and the DB commit, and
would require a reconciliation job. In practice the table has ≤~100 rows
(5 GB / average file size) -> the aggregate query is sub-ms.

CHECK ORDER (all BEFORE touching Drive): measure size -> over 500 MB is 413
-> over quota is 409 -> blocked extension is 400. Two simultaneous uploads
can push the quota somewhat over (upper bound ~(concurrent-1)x500 MB); this
is a deliberate tolerance — an advisory lock would lock the whole depot for
the duration of a 500 MB upload. The overage is made visible via
`over_quota` and subsequent uploads are automatically rejected (self-heals).

RAM: the file is never fully loaded into memory at any point — werkzeug
spools the multipart body to disk, `dg.upload_file` streams it resumably in
16 MB chunks (`num_retries=5`, a dropped chunk resumes where it left off).
`media_store` is DELIBERATELY not used: `stage` would copy the 500 MB
disk-to-disk a second time, `commit` would keep the 5 GB on disk a second
time for 21 days — and no endpoint serves depot files locally anyway (Drive
is canonical, no preview/stream).

WARNING: depot files get 'anyone with the link -> can view' permission at
upload time (user's decision, 2026-07-25): otherwise the copied link is
useless to staff without an agency Google account. Consequence: **anyone who
knows the link can download it** — there's a permanent warning banner in the
panel, no confidential documents should be put here.

CSRF is shared via `api.csrf_protect` (same pattern as
ads.py/client_tracking.py/planning.py).
"""
import logging
import mimetypes
import os
import re

from flask import Blueprint, jsonify, request

import drive_gateway as dg
import notifications
from api import csrf_protect
from extensions import db
from models import UserRef, utcnow
from models_sharing import DepotFile
from sso_client import current_user

log = logging.getLogger(__name__)

bp = Blueprint('depot', __name__)
bp.before_request(csrf_protect)  # same CSRF as api (session token)

DEPOT_ROLES = ('management', 'videographer')
DEPOT_FOLDER_NAME = 'Videograf Deposu'

QUOTA_BYTES = 5 * 1024 * 1024 * 1024      # shared 5 GB
MAX_FILE_BYTES = 500 * 1024 * 1024        # 500 MB per file (SAME as sharing.MAX_UPLOAD_BYTES)
NOTE_MAX = 300

# File type is UNRESTRICTED, but executables are blocked: since depot files
# are publicly link-accessible, a planted .exe turns directly into a
# phishing tool. NOT an allow-list — the requirement is "free-form files".
BLOCKED_EXT = {'exe', 'msi', 'bat', 'cmd', 'com', 'scr', 'pif', 'ps1', 'sh', 'bash',
               'apk', 'jar', 'vbs', 'wsf', 'lnk', 'dll', 'deb', 'rpm'}


def _require_depot():
    """(user, err) — the depot is open only to the videographer team and management."""
    u = current_user()
    if not u:
        return None, (jsonify(error='not authenticated'), 401)
    if u.get('role') not in DEPOT_ROLES:
        return None, (jsonify(error='this section is only open to the videographer team'), 403)
    return u, None


# --- quota ----------------------------------------------------------------

def _used_bytes():
    """Total size of non-deleted files — the ONE source of truth for quota."""
    return int(db.session.query(
        db.func.coalesce(db.func.sum(DepotFile.file_size), 0)
    ).filter(DepotFile.deleted_at.is_(None)).scalar() or 0)


def _quota():
    used = _used_bytes()
    return {'used': used, 'limit': QUOTA_BYTES, 'remaining': max(0, QUOTA_BYTES - used),
            'pct': round(used * 100 / QUOTA_BYTES, 1) if QUOTA_BYTES else 0,
            'file_limit': MAX_FILE_BYTES, 'over_quota': used > QUOTA_BYTES}


def _mb(n):
    """Human-readable size (used in error messages)."""
    if n >= 1024 ** 3:
        return f'{n / 1024 ** 3:.1f} GB'.replace('.', ',')
    return f'{n / 1024 ** 2:.0f} MB'


# --- file name / type -----------------------------------------------------

def _clean_name(raw):
    """Strips path separators and control characters; KEEPS Turkish letters
    and the apostrophe. `werkzeug.secure_filename` is not used — it strips
    Turkish characters, whereas the repo preserves Turkish names elsewhere
    (vg_photo_rename, download name). The apostrophe is safe: the file name
    travels to Drive in the JSON body, not in the `q` query (our folder name
    is fixed and contains no apostrophe)."""
    name = re.sub(r'[\\/\x00-\x1f]', '_', raw or '').strip().strip('.')
    if len(name) > 200:
        stem, dot, ext = name.rpartition('.')
        name = (stem[:200 - len(ext) - 1] + dot + ext) if dot else name[:200]
    return name or 'dosya'


def _blocked_ext(name):
    """Checks the final extension -> 'rapor.pdf.exe' gets caught too."""
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return ext in BLOCKED_EXT


def _depot_folder_id():
    """The single flat depot folder under the content root; creates it if
    missing (idempotent).

    The folder id is NOT cached (neither a table nor AppSetting): there's a
    SINGLE folder, `ensure_subfolder`'s find-or-create takes ~200 ms —
    negligible next to an upload that takes seconds. Caching would mean
    staleness risk + an unnecessary key in the global settings table.
    (`_resolve_week_folder` does keep it in a table because there's a
    client x 52 matrix of folders.)"""
    root = (os.environ.get('DRIVE_CONTENT_ROOT_ID') or '').strip()
    if not root:
        return None
    return dg.ensure_subfolder(root, DEPOT_FOLDER_NAME)


def _uploader_names():
    """{sub: name} — A SINGLE query. Lazy per-file access is FORBIDDEN (N+1)."""
    return {u.sub: (u.name or u.email)
            for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}


# --- endpoints ---------------------------------------------------------------

@bp.get('/files')
@bp.get('/files/')
def depot_files():
    """Depot list + quota (both in a single request — spare the panel a second round trip)."""
    _, err = _require_depot()
    if err:
        return err
    q = DepotFile.query.filter(DepotFile.deleted_at.is_(None))
    term = (request.args.get('q') or '').strip()
    if term:
        q = q.filter(DepotFile.file_name.ilike(f'%{term}%'))
    rows = q.order_by(DepotFile.uploaded_at.desc().nullslast(), DepotFile.id.desc()).all()
    names = _uploader_names()
    return jsonify(files=[r.to_dict(uploader_name=names.get(r.uploaded_by)) for r in rows],
                   quota=_quota())


@bp.get('/quota')
def depot_quota():
    """Cheap quota poll — the upload dialog refreshes without fetching the whole list."""
    _, err = _require_depot()
    if err:
        return err
    return jsonify(quota=_quota())


@bp.post('/upload')
def depot_upload():
    """Upload a single file. Multi-select is done client-side as SEQUENTIAL
    separate requests — batching into a single body was hitting nginx's
    512 MB cap and getting a 413 (the 47-photo case)."""
    u, err = _require_depot()
    if err:
        return err
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='no file provided'), 400
    name = _clean_name(f.filename)
    if _blocked_ext(name):
        return jsonify(error='This file type cannot be uploaded to the depot (executable file).'), 400

    # Measure size without loading the stream into RAM (werkzeug spools large bodies to disk).
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_FILE_BYTES:
        return jsonify(error='File exceeds the 500 MB limit.'), 413
    q = _quota()
    if size > q['remaining']:
        # 409, NOT 413: 413 means "this request's body is too large" (Flask's
        # own handler produces that message); quota is a STATE conflict, and
        # the panel needs to tell the two cases apart and show the right text.
        return jsonify(error=f'Storage is full — {_mb(q["remaining"])} remaining, '
                             f'file is {_mb(size)}.', quota=q), 409

    try:
        folder_id = _depot_folder_id()
    except dg.DriveError as e:
        return jsonify(error=f'Could not create the depot folder: {e}'), 502
    if not folder_id:
        # NOT best-effort: in client_provision a Drive error doesn't block
        # client creation because the client lives in the DB; HERE the Drive
        # file is the product itself.
        return jsonify(error='No Drive root defined for the Videographer Depot '
                             '(DRIVE_CONTENT_ROOT_ID).'), 400

    mime = f.mimetype or mimetypes.guess_type(name)[0] or 'application/octet-stream'
    try:
        # Upload FROM THE STREAM — media_store is not used (rationale in the
        # module docstring).
        meta = dg.upload_file(folder_id, name, f.stream, mime)
    except dg.DriveError as e:
        log.exception('depo yüklemesi başarısız (dosya=%s boyut=%s)', name, size)
        return jsonify(error=f'Drive upload failed: {e}'), 502
    try:
        dg.grant_anyone_reader(meta.get('id'))
    except Exception as e:  # noqa: BLE001 — permission is best-effort, the upload is critical
        log.warning('depo dosyasına izin verilemedi (%s): %s', meta.get('id'), e)

    row = DepotFile(
        file_id=meta.get('id'), folder_id=folder_id,
        file_name=meta.get('name') or name,
        mime_type=meta.get('mimeType') or mime,
        file_size=int(meta['size']) if str(meta.get('size') or '').isdigit() else size,
        note=((request.form.get('note') or '').strip()[:NOTE_MAX] or None),
        uploaded_by=u['sub'], uploaded_at=utcnow())
    db.session.add(row)
    db.session.commit()
    names = _uploader_names()
    q = _quota()
    # Quota threshold warning (2026-08-05). This 5 GB eats into the SAME
    # Drive quota as client content — at the cap, client uploads stop too,
    # so this shouldn't stay silent. The threshold is only checked at upload
    # time (no separate timer needed: the depot only fills up via uploads),
    # and there's NO coalescing on the notification side — `depot_quota`
    # shouldn't be regenerated if there's already an unread warning; the
    # threshold itself provides that check.
    try:
        if q['pct'] >= 80:
            notifications.notify_depot_quota(q['used'] / 1024 ** 3,
                                             q['limit'] / 1024 ** 3, int(q['pct']))
    except Exception:  # noqa: BLE001 — notification is best-effort, the upload is critical
        log.exception('depo kota bildirimi başarısız')
    return jsonify(file=row.to_dict(uploader_name=names.get(row.uploaded_by)),
                   quota=q), 201


@bp.delete('/files/<int:row_id>')
def depot_delete(row_id):
    """Remove a file: soft-delete in the DB + move to trash in Drive.

    Since the depot is SHARED, deletion is shared too — anyone can delete
    any file; who uploaded and who deleted it stays on record.

    The DB row is soft-deleted EVEN IF the Drive deletion errors (the
    user's intent + freeing up quota), the response returns `drive_ok:false`.
    Otherwise a single Drive hiccup would make a file permanently
    undeletable and keep the quota occupied forever; since `file_id` stays
    on the row it can be cleaned up manually."""
    u, err = _require_depot()
    if err:
        return err
    row = DepotFile.query.filter_by(id=row_id, deleted_at=None).first()
    if row is None:
        return jsonify(error='file not found'), 404
    drive_ok = True
    try:
        dg.trash_file(row.file_id)
    except Exception as e:  # noqa: BLE001 — removal from the panel is valid either way
        drive_ok = False
        log.exception('depo dosyası Drive çöpüne taşınamadı (%s): %s', row.file_id, e)
    row.deleted_at = utcnow()
    row.deleted_by = u['sub']
    db.session.commit()
    return jsonify(ok=True, drive_ok=drive_ok, quota=_quota())
