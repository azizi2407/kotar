"""Drive Gateway — the single adapter for Google Drive access (Phase 2b).

The old monolith's 4 identity models (SA-ro / SA-write / OAuth-singleton /
user-token) were consolidated behind a single interface; user-token was
effectively dead (the drive.file scope can't write to weekly folders → it
always fell back to OAuth) and was dropped. The remaining 3:

- **SA-readonly**  (drive.readonly): thumbnails, file counts, folder listing.
- **SA-write**     (drive):          creating the folder tree.
- **OAuth-singleton** (drive):       upload + "anyone reader" permission (this
  account owns the folders; the SA has no storage quota).

Credentials come from Infisical env vars (GOOGLE_SA_JSON, GOOGLE_DRIVE_TOKEN_JSON),
never written to disk. Service objects are thread-local (googleapiclient isn't
thread-safe, gunicorn uses gthread). Errors are converted to domain types
(DriveError / DriveAuthError).
"""
import io
import json
import os
import re
import threading

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

RO_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
RW_SCOPE = 'https://www.googleapis.com/auth/drive'
TOKEN_URI = 'https://oauth2.googleapis.com/token'

_local = threading.local()


class DriveError(Exception):
    """Drive is unreachable / an API error occurred."""


class DriveAuthError(DriveError):
    """Credentials are invalid/missing (token revoked, secret missing)."""


def available():
    """Are Drive credentials configured (has Infisical injected them)?"""
    return bool(os.environ.get('GOOGLE_SA_JSON') and os.environ.get('GOOGLE_DRIVE_TOKEN_JSON'))


def _sa_info():
    raw = os.environ.get('GOOGLE_SA_JSON')
    if not raw:
        raise DriveAuthError('GOOGLE_SA_JSON yok (Infisical enjeksiyonu?)')
    info = json.loads(raw)
    if isinstance(info.get('private_key'), str):
        # \n may have stayed literal through the env round-trip — convert to a real newline
        info['private_key'] = info['private_key'].replace('\\n', '\n')
    return info


def _token_info():
    raw = os.environ.get('GOOGLE_DRIVE_TOKEN_JSON')
    if not raw:
        raise DriveAuthError('GOOGLE_DRIVE_TOKEN_JSON yok (Infisical enjeksiyonu?)')
    return json.loads(raw)


def _creds(kind):
    """kind: 'ro' | 'rw' | 'oauth'. Cached thread-local."""
    cache = getattr(_local, 'creds', None)
    if cache is None:
        cache = _local.creds = {}
    if kind not in cache:
        if kind == 'ro':
            cache[kind] = service_account.Credentials.from_service_account_info(
                _sa_info(), scopes=[RO_SCOPE])
        elif kind == 'rw':
            cache[kind] = service_account.Credentials.from_service_account_info(
                _sa_info(), scopes=[RW_SCOPE])
        elif kind == 'oauth':
            t = _token_info()
            cache[kind] = Credentials(
                token=t.get('token'),
                refresh_token=t['refresh_token'],
                token_uri=t.get('token_uri', TOKEN_URI),
                client_id=t['client_id'],
                client_secret=t['client_secret'],
                scopes=t.get('scopes') or [RW_SCOPE])
        else:
            raise ValueError(kind)
    return cache[kind]


def _service(kind):
    cache = getattr(_local, 'svc', None)
    if cache is None:
        cache = _local.svc = {}
    if kind not in cache:
        cache[kind] = build('drive', 'v3', credentials=_creds(kind),
                            cache_discovery=False, static_discovery=True)
    return cache[kind]


def _wrap(e):
    """HttpError/RefreshError → domain error. The exception type is added to the
    message — short socket errors like 'broken pipe' couldn't be diagnosed on their own."""
    status = getattr(getattr(e, 'resp', None), 'status', None)
    if status in (401, 403):
        return DriveAuthError(f'{type(e).__name__}: {e}')
    return DriveError(f'{type(e).__name__}: {e}')


# --- read-only (SA-readonly) ---

def count_files(folder_id):
    """Count of (non-trashed) files in the folder. None if unreachable (distinct from 0)."""
    if not folder_id:
        return None
    try:
        svc = _service('ro')
        total, page = 0, None
        while True:
            resp = svc.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields='nextPageToken, files(id)', pageSize=1000, pageToken=page,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            total += len(resp.get('files', []))
            page = resp.get('nextPageToken')
            if not page:
                return total
    except Exception:
        return None


def list_files(folder_id, media_only=False):
    """Files in the folder (id/name/mimeType/thumbnailLink/createdTime)."""
    if not folder_id:
        return []
    q = f"'{folder_id}' in parents and trashed=false"
    if media_only:
        q += " and (mimeType contains 'image/' or mimeType contains 'video/')"
    try:
        svc = _service('ro')
        out, page = [], None
        while True:
            resp = svc.files().list(
                q=q, orderBy='createdTime',
                fields='nextPageToken, files(id,name,mimeType,createdTime,size)',
                pageSize=1000, pageToken=page,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out.extend(resp.get('files', []))
            page = resp.get('nextPageToken')
            if not page:
                return out
    except (HttpError, Exception) as e:
        raise _wrap(e)


def file_meta(file_id):
    try:
        return _service('ro').files().get(
            fileId=file_id, fields='id,name,mimeType,size,thumbnailLink,createdTime',
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def download_file(file_id):
    """Download the file's raw content as bytes (SA-readonly). For video/audio."""
    import io as _io
    from googleapiclient.http import MediaIoBaseDownload
    try:
        req = _service('ro').files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = _io.BytesIO()
        dl = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            # num_retries: momentary socket/SSL drops are retried per chunk
            # (a broken pipe used to drop the media job on the first attempt).
            _, done = dl.next_chunk(num_retries=5)
        return buf.getvalue()
    except Exception as e:
        raise _wrap(e)


def thumbnail_bytes(file_id, width=400):
    """Returns (data, mime); (None, None) if there's no thumbnail. Caching happens in the API layer."""
    try:
        meta = _service('ro').files().get(
            fileId=file_id, fields='thumbnailLink', supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)
    link = meta.get('thumbnailLink')
    if not link:
        return None, None
    link = re.sub(r'=s\d+', f'=s{width}', link)
    try:
        r = AuthorizedSession(_creds('ro')).get(link, timeout=15)
        r.raise_for_status()
        return r.content, r.headers.get('Content-Type', 'image/jpeg')
    except Exception as e:
        raise DriveError(f'thumbnail indirilemedi: {e}')


# --- write (OAuth-singleton: owns the folders) ---

UPLOAD_CHUNK = 16 * 1024 * 1024  # resumable chunk (must be a multiple of 256 KB)


def upload_file(folder_id, filename, data, mime):
    """Uploads the file to the folder (under the OAuth account's ownership). Returns meta.

    `data`: bytes OR a seekable file-like object. Goes resumable + chunked: RAM
    usage stays bounded by the chunk size; a dropped chunk is retried from where
    it left off via `num_retries` (the old single-POST model lost the entire
    upload on a socket drop for large videos)."""
    stream = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
    try:
        media = MediaIoBaseUpload(stream, mimetype=mime, resumable=True,
                                  chunksize=UPLOAD_CHUNK)
        req = _service('oauth').files().create(
            body={'name': filename, 'parents': [folder_id]},
            media_body=media, fields='id,name,mimeType,size',
            supportsAllDrives=True)
        resp = None
        while resp is None:
            _, resp = req.next_chunk(num_retries=5)
        return resp
    except Exception as e:
        raise _wrap(e)


def grant_anyone_reader(file_id):
    """Grant the file 'anyone with link → reader' permission (for public review)."""
    try:
        _service('oauth').permissions().create(
            fileId=file_id, body={'type': 'anyone', 'role': 'reader'},
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def move_file(file_id, new_parent, old_parent=None):
    """Move the file between folders (not a copy): add to new_parent, remove from
    old_parent. Since the OAuth account is the owner, files().update is enough. Returns meta."""
    try:
        svc = _service('oauth')
        remove = old_parent
        if remove is None:  # if the old parent isn't given, remove the current parents
            meta = svc.files().get(fileId=file_id, fields='parents',
                                   supportsAllDrives=True).execute()
            remove = ','.join(meta.get('parents', []))
        return svc.files().update(
            fileId=file_id, addParents=new_parent, removeParents=remove or None,
            fields='id,name,mimeType,size,parents', supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def trash_file(file_id):
    """Move the file to Drive's TRASH (NOT permanent deletion).

    In the Videographer Storage everyone can delete everyone else's files → a
    wrong click needs to be recoverable; the trash keeps it for 30 days. WARNING:
    while trashed, the file CONTINUES to occupy the account's Drive quota for those 30 days."""
    try:
        return _service('oauth').files().update(
            fileId=file_id, body={'trashed': True}, fields='id,trashed',
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def untrash_file(file_id):
    """Take the file OUT of Drive's TRASH (the reverse of `trash_file`).

    When a work file is restored in the panel, the Drive copy must be restored
    too — otherwise `drive_file_id` looks populated but the file is still in
    trash, Google permanently deletes it after 30 days, and the panel's
    `drive_ok:true` promise becomes false. WARNING: if the file was manually
    permanently deleted on the Drive side after being trashed (30 days passed, or
    someone emptied the trash), this call returns 404 — the caller should treat
    this as BEST-EFFORT, same as `trash_file`."""
    try:
        return _service('oauth').files().update(
            fileId=file_id, body={'trashed': False}, fields='id,trashed',
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def rename_file(file_id, new_name):
    """Rename the file (owned by the OAuth account). Returns updated meta."""
    try:
        return _service('oauth').files().update(
            fileId=file_id, body={'name': new_name},
            fields='id,name', supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def ensure_subfolder(parent_id, name):
    """Find/create the `name` folder under parent, return its id (SA-write)."""
    try:
        svc = _service('rw')
        q = (f"'{parent_id}' in parents and name = '{name}' "
             "and mimeType = 'application/vnd.google-apps.folder' and trashed=false")
        found = svc.files().list(q=q, fields='files(id)', supportsAllDrives=True,
                                 includeItemsFromAllDrives=True).execute().get('files', [])
        if found:
            return found[0]['id']
        created = svc.files().create(
            body={'name': name, 'parents': [parent_id],
                  'mimeType': 'application/vnd.google-apps.folder'},
            fields='id', supportsAllDrives=True).execute()
        return created['id']
    except Exception as e:
        raise _wrap(e)
