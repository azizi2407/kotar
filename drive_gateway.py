"""Drive Gateway — Google Drive erişimi için tek adaptör (Faz 2b).

Eski monolitin 4 kimlik modeli (SA-ro / SA-write / OAuth-singleton / user-token)
tek arayüz arkasına alındı; user-token fiilen ölüydü (drive.file scope haftalık
klasörlere yazamıyor → hep OAuth'a düşüyordu), atıldı. Kalan 3:

- **SA-readonly**  (drive.readonly): thumbnail, dosya sayımı, klasör listeleme.
- **SA-write**     (drive):          klasör ağacı oluşturma.
- **OAuth-singleton** (drive):       upload + "anyone reader" izni (klasörlerin
  sahibi bu hesap; SA'nın depolama kotası yok).

Kimlikler Infisical env'inden gelir (GOOGLE_SA_JSON, GOOGLE_DRIVE_TOKEN_JSON),
diske yazılmaz. Servis nesneleri thread-local (googleapiclient thread-safe değil,
gunicorn gthread). Hatalar domain tipine çevrilir (DriveError / DriveAuthError).
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
    """Drive'a erişilemedi / API hatası."""


class DriveAuthError(DriveError):
    """Kimlik geçersiz/eksik (token revoked, secret yok)."""


def available():
    """Drive kimlikleri yapılandırılmış mı (Infisical enjekte etmiş mi)?"""
    return bool(os.environ.get('GOOGLE_SA_JSON') and os.environ.get('GOOGLE_DRIVE_TOKEN_JSON'))


def _sa_info():
    raw = os.environ.get('GOOGLE_SA_JSON')
    if not raw:
        raise DriveAuthError('GOOGLE_SA_JSON yok (Infisical enjeksiyonu?)')
    info = json.loads(raw)
    if isinstance(info.get('private_key'), str):
        # env round-trip'inde \n literal kalmış olabilir — gerçek satır sonuna çevir
        info['private_key'] = info['private_key'].replace('\\n', '\n')
    return info


def _token_info():
    raw = os.environ.get('GOOGLE_DRIVE_TOKEN_JSON')
    if not raw:
        raise DriveAuthError('GOOGLE_DRIVE_TOKEN_JSON yok (Infisical enjeksiyonu?)')
    return json.loads(raw)


def _creds(kind):
    """kind: 'ro' | 'rw' | 'oauth'. Thread-local önbellekli."""
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
    """HttpError/RefreshError → domain hatası. Mesaja istisna tipi eklenir —
    'broken pipe' gibi kısa soket hataları tek başına teşhis edilemiyordu."""
    status = getattr(getattr(e, 'resp', None), 'status', None)
    if status in (401, 403):
        return DriveAuthError(f'{type(e).__name__}: {e}')
    return DriveError(f'{type(e).__name__}: {e}')


# --- salt-okuma (SA-readonly) ---

def count_files(folder_id):
    """Klasördeki (çöp olmayan) dosya sayısı. Erişilemezse None (0'dan farklı)."""
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
    """Klasördeki dosyalar (id/name/mimeType/thumbnailLink/createdTime)."""
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
    """Dosyanın ham içeriğini bytes olarak indir (SA-readonly). Video/ses için."""
    import io as _io
    from googleapiclient.http import MediaIoBaseDownload
    try:
        req = _service('ro').files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = _io.BytesIO()
        dl = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            # num_retries: anlık soket/SSL kopmaları chunk bazında yeniden denenir
            # (Broken pipe media job'ını tek denemede düşürüyordu).
            _, done = dl.next_chunk(num_retries=5)
        return buf.getvalue()
    except Exception as e:
        raise _wrap(e)


def thumbnail_bytes(file_id, width=400):
    """(data, mime) döndürür; thumbnail yoksa (None, None). Cache API katmanında."""
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


# --- yazma (OAuth-singleton: klasörlerin sahibi) ---

UPLOAD_CHUNK = 16 * 1024 * 1024  # resumable chunk (256 KB'nin katı olmalı)


def upload_file(folder_id, filename, data, mime):
    """Dosyayı klasöre yükler (OAuth hesabı sahipliğinde). Meta döndürür.

    `data`: bytes VEYA seek'lenebilir dosya-nesnesi. Resumable + chunk'lı gider:
    RAM kullanımı chunk boyutuyla sınırlı kalır; kopan chunk `num_retries` ile
    kaldığı yerden denenir (tek-POST modeli büyük videolarda soket kopmasıyla
    bütün yüklemeyi kaybediyordu)."""
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
    """Dosyaya 'anyone with link → reader' izni ver (public review için)."""
    try:
        _service('oauth').permissions().create(
            fileId=file_id, body={'type': 'anyone', 'role': 'reader'},
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def move_file(file_id, new_parent, old_parent=None):
    """Dosyayı klasörler arası taşı (kopya değil): new_parent'a ekle, old_parent'tan
    çıkar. Sahip OAuth hesabı olduğu için files().update yeterli. Meta döndürür."""
    try:
        svc = _service('oauth')
        remove = old_parent
        if remove is None:  # eski parent verilmediyse mevcut parent'ları çıkar
            meta = svc.files().get(fileId=file_id, fields='parents',
                                   supportsAllDrives=True).execute()
            remove = ','.join(meta.get('parents', []))
        return svc.files().update(
            fileId=file_id, addParents=new_parent, removeParents=remove or None,
            fields='id,name,mimeType,size,parents', supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def trash_file(file_id):
    """Dosyayı Drive ÇÖP KUTUSUNA taşı (kalıcı silme DEĞİL).

    Videograf Deposu'nda herkes herkesin dosyasını silebiliyor → yanlış tıklamanın
    geri dönüşü olmalı; çöp kutusu 30 gün tutar. UYARI: çöpteki dosya o 30 gün
    boyunca hesabın Drive kotasından yer tutmaya DEVAM eder."""
    try:
        return _service('oauth').files().update(
            fileId=file_id, body={'trashed': True}, fields='id,trashed',
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def untrash_file(file_id):
    """Dosyayı Drive ÇÖP KUTUSUNDAN çıkar (`trash_file`'ın tersi).

    Çalışma dosyası panelden geri alındığında Drive kopyası da geri gelmeli —
    aksi halde `drive_file_id` dolu göründüğü halde dosya hâlâ çöpte durur ve
    30 gün sonra Google onu kalıcı siler, panelin `drive_ok:true` sözü boşa
    çıkar. UYARI: dosya çöpe atıldıktan sonra Drive tarafında elle kalıcı
    silindiyse (30 gün doldu veya biri çöpü boşalttıysa) bu çağrı 404 döner —
    çağıran bunu `trash_file` gibi EN-İYİ-ÇABA olarak ele almalı."""
    try:
        return _service('oauth').files().update(
            fileId=file_id, body={'trashed': False}, fields='id,trashed',
            supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def rename_file(file_id, new_name):
    """Dosyayı yeniden adlandır (sahip OAuth hesabı). Güncel meta döndürür."""
    try:
        return _service('oauth').files().update(
            fileId=file_id, body={'name': new_name},
            fields='id,name', supportsAllDrives=True).execute()
    except Exception as e:
        raise _wrap(e)


def ensure_subfolder(parent_id, name):
    """parent altında `name` klasörünü bul/oluştur, id döndür (SA-write)."""
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
