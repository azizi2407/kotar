"""Videograf Deposu — `/api/depot/*` [Blueprint: /api/depot].

Videograflar + yönetim için ORTAK serbest dosya alanı: müşteriye ve haftaya bağlı
değil, ekibin çalışma malzemesi (ham çekim, LUT, proje dosyası, referans…) burada
durur. Drive'da `<içerik kökü>/Videograf Deposu` altında TEK DÜZ klasör.

KOTA: ortak **5 GB**, dosya başına **500 MB**. Kota her istekte `SUM(file_size)` ile
ÖLÇÜLÜR, sayaç kolonu YOK — sayaç, Drive yüklemesi ile DB commit'i arasındaki her
çökmede kalıcı drift üretir ve mutabakat işi gerektirirdi. Tablo pratikte ≤~100 satır
(5 GB / ortalama dosya) → agregat sorgu sub-ms.

KONTROL SIRASI (hepsi Drive'a GİTMEDEN önce): boyut ölç → 500 MB aşımı 413 → kota
aşımı 409 → yasak uzantı 400. Eşzamanlı iki yükleme kotayı bir miktar aşabilir
(üst sınır ~(eşzamanlı-1)×500 MB); bu bilinçli tolerans — advisory lock 500 MB'lık
bir upload boyunca tüm depoyu kilitlerdi. Aşım `over_quota` ile görünür kılınır ve
sonraki yüklemeler otomatik reddedilir (kendini toparlar).

RAM: dosya hiçbir noktada tamamen belleğe alınmaz — werkzeug multipart'ı diske
spool'lar, `dg.upload_file` resumable + 16 MB chunk ile akıtır (`num_retries=5`,
kopan chunk kaldığı yerden). `media_store` BİLEREK kullanılmaz: `stage` 500 MB'ı
disk→disk ikinci kez kopyalar, `commit` 5 GB'ı 21 gün diskte ikinci kez tutar — ve
depo dosyalarını hiçbir uç lokal servis etmiyor (Drive kanonik, önizleme/stream yok).

⚠️ Depo dosyalarına yükleme anında 'bağlantıya sahip herkes → okuyabilir' izni
verilir (kullanıcı kararı, 2026-07-25): aksi halde ajans Google hesabında olmayan
personel için kopyalanan link işe yaramaz. Sonuç: **linki bilen herkes indirebilir**
— panelde kalıcı uyarı şeridi var, gizli belge konmamalı.

CSRF `api.csrf_protect` ile paylaşılır (ads.py/client_tracking.py/planning.py deseni).
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
bp.before_request(csrf_protect)  # api ile aynı CSRF (session token)

DEPOT_ROLES = ('management', 'videographer')
DEPOT_FOLDER_NAME = 'Videograf Deposu'

QUOTA_BYTES = 5 * 1024 * 1024 * 1024      # ortak 5 GB
MAX_FILE_BYTES = 500 * 1024 * 1024        # dosya başına 500 MB (sharing.MAX_UPLOAD_BYTES ile AYNI)
NOTE_MAX = 300

# Dosya türü SERBEST, ama çalıştırılabilirler yasak: depo dosyaları bağlantıyla
# herkese açık olduğu için konan bir .exe doğrudan kimlik avı aracına dönüşür.
# Allow-list DEĞİL — gereksinim "serbest dosya".
BLOCKED_EXT = {'exe', 'msi', 'bat', 'cmd', 'com', 'scr', 'pif', 'ps1', 'sh', 'bash',
               'apk', 'jar', 'vbs', 'wsf', 'lnk', 'dll', 'deb', 'rpm'}


def _require_depot():
    """(user, err) — depo yalnız videograf ekibine ve yönetime açık."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in DEPOT_ROLES:
        return None, (jsonify(error='bu bölüm videograf ekibine açıktır'), 403)
    return u, None


# --- kota ----------------------------------------------------------------

def _used_bytes():
    """Silinmemiş dosyaların toplam boyutu — kotanın TEK gerçek kaynağı."""
    return int(db.session.query(
        db.func.coalesce(db.func.sum(DepotFile.file_size), 0)
    ).filter(DepotFile.deleted_at.is_(None)).scalar() or 0)


def _quota():
    used = _used_bytes()
    return {'used': used, 'limit': QUOTA_BYTES, 'remaining': max(0, QUOTA_BYTES - used),
            'pct': round(used * 100 / QUOTA_BYTES, 1) if QUOTA_BYTES else 0,
            'file_limit': MAX_FILE_BYTES, 'over_quota': used > QUOTA_BYTES}


def _mb(n):
    """İnsan-okur boyut (hata mesajlarında)."""
    if n >= 1024 ** 3:
        return f'{n / 1024 ** 3:.1f} GB'.replace('.', ',')
    return f'{n / 1024 ** 2:.0f} MB'


# --- dosya adı / tür -----------------------------------------------------

def _clean_name(raw):
    """Yol ayırıcı ve kontrol karakterlerini temizler; TÜRKÇE harfleri ve kesme
    işaretini KORUR. `werkzeug.secure_filename` kullanılmaz — Türkçe karakterleri
    kırpar, oysa repo başka yerlerde (vg_photo_rename, indirme adı) Türkçe adları
    koruyor. Kesme işareti güvenli: dosya adı Drive'a JSON gövdede gider, `q`
    sorgusunda değil (klasör adımız sabit ve kesme işareti içermiyor)."""
    name = re.sub(r'[\\/\x00-\x1f]', '_', raw or '').strip().strip('.')
    if len(name) > 200:
        stem, dot, ext = name.rpartition('.')
        name = (stem[:200 - len(ext) - 1] + dot + ext) if dot else name[:200]
    return name or 'dosya'


def _blocked_ext(name):
    """Son uzantıya bakar → 'rapor.pdf.exe' de yakalanır."""
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return ext in BLOCKED_EXT


def _depot_folder_id():
    """İçerik kökü altındaki tek düz depo klasörü; yoksa oluşturur (idempotent).

    Klasör id'si cache'lenmez (ne tablo ne AppSetting): TEK klasör var, `ensure_subfolder`
    bul-veya-oluştur ve ~200 ms — saniyeler süren bir upload'ın yanında ölçülemez.
    Cache = bayatlama hatası + global ayar tablosunda gereksiz anahtar.
    (`_resolve_week_folder` tabloda tutuyor çünkü müşteri × 52 klasör var.)"""
    root = (os.environ.get('DRIVE_CONTENT_ROOT_ID') or '').strip()
    if not root:
        return None
    return dg.ensure_subfolder(root, DEPOT_FOLDER_NAME)


def _uploader_names():
    """{sub: ad} — TEK sorgu. Dosya başına lazy erişim YASAK (N+1)."""
    return {u.sub: (u.name or u.email)
            for u in db.session.query(UserRef.sub, UserRef.name, UserRef.email).all()}


# --- uçlar ---------------------------------------------------------------

@bp.get('/files')
@bp.get('/files/')
def depot_files():
    """Depo listesi + kota (tek istekte ikisi — panel iki tur atmasın)."""
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
    """Ucuz kota yoklaması — yükleme diyaloğu listeyi çekmeden tazeler."""
    _, err = _require_depot()
    if err:
        return err
    return jsonify(quota=_quota())


@bp.post('/upload')
def depot_upload():
    """Tek dosya yükle. Çoklu seçim istemcide SIRALI ayrı isteklerle yapılır —
    parti tek gövdede nginx 512 MB tavanına çarpıp 413 alıyordu (47 fotoluk vaka)."""
    u, err = _require_depot()
    if err:
        return err
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='dosya yok'), 400
    name = _clean_name(f.filename)
    if _blocked_ext(name):
        return jsonify(error='Bu dosya türü depoya yüklenemez (çalıştırılabilir dosya).'), 400

    # Boyutu akışı RAM'e almadan ölç (werkzeug büyük gövdeyi diske spool'lar).
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_FILE_BYTES:
        return jsonify(error='Dosya 500 MB sınırını aşıyor.'), 413
    q = _quota()
    if size > q['remaining']:
        # 409, 413 DEĞİL: 413 "bu isteğin gövdesi büyük" demek (Flask'ın kendi
        # handler'ı o mesajı üretiyor); kota bir DURUM çakışması ve panel iki vakayı
        # ayırt edip doğru metni göstermeli.
        return jsonify(error=f'Depo dolu — kalan alan {_mb(q["remaining"])}, '
                             f'dosya {_mb(size)}.', quota=q), 409

    try:
        folder_id = _depot_folder_id()
    except dg.DriveError as e:
        return jsonify(error=f'Depo klasörü oluşturulamadı: {e}'), 502
    if not folder_id:
        # best-effort DEĞİL: client_provision'da Drive hatası müşteri oluşturmayı
        # bloklamaz çünkü müşteri DB'de yaşar; BURADA Drive dosyası ürünün kendisi.
        return jsonify(error='Videograf Deposu için Drive kökü tanımlı değil '
                             '(DRIVE_CONTENT_ROOT_ID).'), 400

    mime = f.mimetype or mimetypes.guess_type(name)[0] or 'application/octet-stream'
    try:
        # AKIŞTAN yükle — media_store kullanılmaz (modül docstring'indeki gerekçe).
        meta = dg.upload_file(folder_id, name, f.stream, mime)
    except dg.DriveError as e:
        log.exception('depo yüklemesi başarısız (dosya=%s boyut=%s)', name, size)
        return jsonify(error=f'Drive yükleme başarısız: {e}'), 502
    try:
        dg.grant_anyone_reader(meta.get('id'))
    except Exception as e:  # noqa: BLE001 — izin en-iyi-çaba, yükleme kritik
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
    # Kota eşiği uyarısı (2026-08-05). Bu 5 GB müşteri içerikleriyle AYNI Drive
    # kotasından yeniyor — tavanda müşteri yüklemeleri de durur, sessiz kalmamalı.
    # Eşik yalnız yükleme anında bakılır (ayrı timer'a gerek yok: depo ancak
    # yükleme ile dolar) ve bildirim tarafında coalesce YOK — `depot_quota` zaten
    # okunmamış bir uyarı varsa tekrar üretilmemeli, o kontrolü eşik yapıyor.
    try:
        if q['pct'] >= 80:
            notifications.notify_depot_quota(q['used'] / 1024 ** 3,
                                             q['limit'] / 1024 ** 3, int(q['pct']))
    except Exception:  # noqa: BLE001 — bildirim en-iyi-çaba, yükleme kritik
        log.exception('depo kota bildirimi başarısız')
    return jsonify(file=row.to_dict(uploader_name=names.get(row.uploaded_by)),
                   quota=q), 201


@bp.delete('/files/<int:row_id>')
def depot_delete(row_id):
    """Dosyayı kaldır: DB'de soft-delete + Drive'da çöp kutusu.

    Depo ORTAK olduğu için silme de ortak — herkes her dosyayı silebilir; kim
    yüklediği ve kim sildiği kayıtta durur.

    Drive silme hata verse bile DB satırı soft-delete EDİLİR (kullanıcının niyeti +
    kota boşalması), yanıt `drive_ok:false` döner. Aksi halde tek bir Drive hıçkırığı
    dosyayı silinemez yapıp kotayı kalıcı meşgul ederdi; `file_id` satırda durduğu
    için elle temizlenebilir."""
    u, err = _require_depot()
    if err:
        return err
    row = DepotFile.query.filter_by(id=row_id, deleted_at=None).first()
    if row is None:
        return jsonify(error='dosya bulunamadı'), 404
    drive_ok = True
    try:
        dg.trash_file(row.file_id)
    except Exception as e:  # noqa: BLE001 — panelden kaldırma her hâlükârda geçerli
        drive_ok = False
        log.exception('depo dosyası Drive çöpüne taşınamadı (%s): %s', row.file_id, e)
    row.deleted_at = utcnow()
    row.deleted_by = u['sub']
    db.session.commit()
    return jsonify(ok=True, drive_ok=drive_ok, quota=_quota())
