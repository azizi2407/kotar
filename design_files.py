"""Tasarım çalışma dosyaları API'si (2026-08-07) — `/api/design-files`.

Tasarımcıların kaynak dosyaları (`.psd`, `.ai`, `.indd`, `.aep`, paket zip'leri)
müşteri bazında, SÜRÜMLÜ olarak burada durur.

**Dosya sunucuda KANONİK, Drive'da KOPYA.** İndirme yetkisi ancak dosya bizdeyken
korunabilir: depo (`depot.py`) dosyalarına 'bağlantıya sahip herkes okuyabilir'
izni veriyor, müşteri kaynak dosyası için bu kabul edilemez. Drive kopyası yedek
ve ajans dışı paylaşım için var, yüklemesi EN-İYİ-ÇABA (patlarsa `drive_file_id`
NULL kalır, uç yine 201 döner).

**Kota müşteri başına 2 GB, her istekte `SUM(file_size)` ile ÖLÇÜLÜR** — sayaç
kolonu yok (`depot.py` ile aynı gerekçe: sayaç, yükleme ile commit arasındaki her
çökmede kalıcı drift üretir).

CSRF `api.csrf_protect` ile paylaşılır (`depot.py`/`ads.py` deseni).
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
# Yasak uzantı listesi ve ad temizliği depo ile ORTAK: iki liste ayrışırsa biri
# güvensiz kalır. Bilerek kopyalanmadı, import edildi.
from depot import BLOCKED_EXT, _clean_name
from extensions import db
from models import Client, UserRef, utcnow
from models_design_files import DesignFile, DesignFileVersion
from sso_client import current_user

log = logging.getLogger('agency.design_files')

bp = Blueprint('design_files', __name__)
bp.before_request(csrf_protect)

DESIGN_ROLES = ('management', 'designer')

MAX_FILE_BYTES = 1024 * 1024 * 1024          # tek dosya 1 GB (nginx tavanı 1100m)
# UYARI: bu değer app.config['MAX_CONTENT_LENGTH']'i (app.py) AŞARSA gövde
# view'a hiç ulaşmadan Flask'ın global 413'üne takılır — aşağıdaki kontrol sırası
# (`_dosya_kontrol`) hiçbir zaman çalışmaz (yaşanmış hata, bkz. app.py). Muhafız:
# tests/test_design_files.py::test_max_file_bytes_uygulama_tavanini_asmiyor.
QUOTA_BYTES = 2 * 1024 * 1024 * 1024         # müşteri başına 2 GB
NOTE_MAX = 300
TITLE_MAX = 200
TAG_MAX = 40
TAGS_MAX = 12

STORE_DIR = os.environ.get('DESIGN_FILES_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'design-files')

DRIVE_FOLDER_NAME = 'Çalışma Dosyaları'

# nginx `X-Accel-Redirect` modu: dosyayı Flask DEĞİL nginx akıtır — 1 GB'lık bir
# indirme bir gunicorn thread'ini dakikalarca tutmasın (2 worker × 4 thread).
# Kapalıyken (test, `flask run`) `send_file`'a düşer.
XACCEL = (os.environ.get('DESIGN_FILES_XACCEL') or '').strip() == '1'
XACCEL_PREFIX = '/_dsg/'


# --- yetki ------------------------------------------------------------------

def _require():
    """(user, err) — çalışma dosyaları yalnız tasarım ekibine ve yönetime açık."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in DESIGN_ROLES:
        return None, (jsonify(error='bu bölüm tasarım ekibine açıktır'), 403)
    return u, None


def _silebilir(u, row):
    """Yönetim ayrımsız; tasarımcı YALNIZ kendi yüklediğini (fonts `_silebilir`
    deseni). Kural TEK yerde: hem silme ucu hem listedeki `can_delete` buradan
    okur — ayrışırsa düğme yalan söyler.

    `row`: DesignFile (o zaman `created_by`) veya DesignFileVersion (`uploaded_by`)."""
    if not u:
        return False
    if u.get('role') == 'management':
        return True
    sahibi = getattr(row, 'uploaded_by', None) or getattr(row, 'created_by', None)
    return u.get('role') == 'designer' and sahibi == u.get('sub')


def _musteri_veya_404(client_id):
    c = db.session.get(Client, client_id)
    if c is None or c.deleted_at is not None:
        return None, (jsonify(error='müşteri bulunamadı'), 404)
    return c, None


def _uploader_names():
    return {u.sub: (u.name or u.email) for u in UserRef.query.all()}


# --- kota -------------------------------------------------------------------

def _kota(client_id):
    """Müşterinin kullandığı alan — silinmemiş sürümlerin toplamı. Sayaç kolonu
    YOK; agregat sorgu (müşteri başına on-yüz satır) sub-ms."""
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
    """Çöp kutusundaki toplam boyut — silinmiş dosyaların TÜM silinmiş
    sürümleri + hâlâ yaşayan dosyaların tekil silinmiş sürümleri, hepsi
    birden. `DesignFileVersion.deleted_at IS NOT NULL` her iki durumu da
    kapsıyor (dosya silinince altındaki tüm sürümler de aynı damgayla
    silinir), ayrı bir dosya-durumu ayrımına gerek yok. TEK agregat sorgu —
    `_kota` ile aynı gerekçe, Python'da döngüyle toplama YOK."""
    toplam = int(db.session.query(
        db.func.coalesce(db.func.sum(DesignFileVersion.file_size), 0))
        .join(DesignFile, DesignFile.id == DesignFileVersion.file_id)
        .filter(DesignFile.client_id == client_id,
                DesignFileVersion.deleted_at.isnot(None))
        .scalar() or 0)
    return toplam


# --- listeleme --------------------------------------------------------------

def _guncel_surumler(file_ids, silinmisler_dahil=False):
    """{file_id: (guncel_surum, surum_adedi)} — TEK sorgu.

    Dosya başına ayrı `MAX(version_no)` sorgusu N+1 olurdu; tüm sürümleri bir
    kerede çekip Python'da katlıyoruz (müşteri başına satır sayısı iki haneli).

    `silinmisler_dahil=True`: çöp kutusundaki DOSYALAR için — bu dosyaların
    sürümlerinin TAMAMI silinmiş durumda (`file_delete` hepsine aynı damgayı
    basar), varsayılan filtre (`deleted_at IS NULL`) burada her zaman boş
    döner. Var olan çağıranlar (canlı liste) parametreyi vermediği için
    davranışları değişmez — ayrı bir ikiz fonksiyon yerine tek yerde tutmak
    iki sorgunun zamanla ayrışma riskini kapatıyor."""
    if not file_ids:
        return {}
    q = DesignFileVersion.query.filter(DesignFileVersion.file_id.in_(file_ids))
    if not silinmisler_dahil:
        q = q.filter(DesignFileVersion.deleted_at.is_(None))
    rows = q.order_by(DesignFileVersion.file_id, DesignFileVersion.version_no.desc()).all()
    out = {}
    for v in rows:
        guncel, adet = out.get(v.file_id, (None, 0))
        out[v.file_id] = (guncel or v, adet + 1)   # ilk gelen = en yüksek sürüm
    return out


@bp.get('/client/<int:client_id>')
def client_files(client_id):
    """Müşterinin çalışma dosyaları + kota (tek yanıt — panel iki tur atmasın)."""
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
    """Son uzantı — ortak `sha_store` uygulaması (bkz. o modülün docstring'i)."""
    return sha_store.uzanti(file_name)


def _yol(sha256, file_name):
    """`data/design-files/<sha[:2]>/<sha>.<ext>` — iki harfli ön ek dizini, tek
    dizinde on binlerce dosya birikmesin (fonts düz dizin kullanıyor; orada
    dosyalar KB ölçeğinde)."""
    return sha_store.yol(STORE_DIR, sha256, file_name)


def _olcu(stream):
    """Boyut — akışı RAM'e ALMADAN (werkzeug büyük gövdeyi diske spool'lar)."""
    stream.seek(0, 2)
    n = stream.tell()
    stream.seek(0)
    return n


# --- eşzamanlılık: sha kilidi (TOCTOU) ---------------------------------------
#
# Yarış: `_diske_yaz` hedef dosya diskte VARSA geçici dosyayı silip hiç yazmaz
# (dedup) ve yeni `DesignFileVersion` satırı ancak isteğin commit'inde görünür
# olur. `_purge_disk_dedup` ise sayımı KENDİ (ayrı) transaction'ında, purge'ün
# DB silmesinden SONRA yapar. Şu sıra mümkündü:
#   yükleme dedup-atlar (dosya zaten diskte) → purge sayar (yüklemenin henüz
#   commit OLMAMIŞ yeni satırını göremez, 0 bulur) → purge `os.unlink` → yükleme
#   commit eder → yeni satır artık DİSKTE OLMAYAN bir dosyaya işaret eder
#   (indirme kırılır, kanonik kopya kalıcı gitmiştir).
#
# Çözüm: aynı `sha256` için `pg_advisory_xact_lock` — TRANSACTION kapsamlı,
# commit/rollback'te KENDİLİĞİNDEN bırakılır (elle "unlock" çağrısı gerekmez).
# Yükleme tarafı kilidi `_diske_yaz` içinde, hedef dosyanın var olup olmadığına
# bakmadan HEMEN ÖNCE alır; isteğin (view fonksiyonunun) ilk `db.session.commit()`'i
# hem yeni satırı görünür yapar hem kilidi bırakır — tam istediğimiz sıralama.
# Purge tarafı aynı kilidi sayımdan ÖNCE alır (`_purge_disk_dedup`). İki olası
# sıra da tutarlı sonuç verir: yükleme önce kilitlerse purge, satır commit'lenip
# görünür olana kadar bekler ve artık ≥1 sayar (dosyayı SİLMEZ); purge önce
# kilitlerse yükleme, purge diski temizleyip kilidi bırakana kadar bekler ve
# `os.path.exists` kontrolünü GÜNCEL diskle yapar (dosya artık yok → dedup
# atlamaz, içeriği yeniden yazar).
#
# Anahtar: Python'da sha256'dan int türetmek yerine Postgres'in kendi
# `hashtext()`'i kullanılıyor — repo'da AYNI desen `jobqueue.py`nin `enqueue`
# dedup kilidinde de var, string doğrudan veritabanına gidiyor, ekstra
# bit-çevirme kodu yok. Çakışmayı önlemek için anahtar `design-files:sha:`
# önekiyle ad alanı ayrılıyor (aksi halde `hashtext` aynı 32-bit uzayı
# paylaşan başka bir modülün literal anahtar string'iyle çakışabilirdi).
#
# sqlite'ta (testler) advisory lock YOK — dialect kontrolüyle sessizce atlanır;
# tek süreçli, tek bağlantılı sqlite testinde bu yarış zaten kurulamıyor (bkz.
# `tests/test_design_files.py::test_sha_kilidi_yalniz_postgreste_cagrilir`
# docstring'i — testin ne kanıtlayıp ne kanıtlamadığı orada açık yazılı).
#
# Kilidin yükleme tarafında tuttuğu süre: sha ancak akış TAMAMEN okunup
# hashlendikten SONRA bilinir, yani kilit büyük dosya G/Ç'sinden (`stream.read`
# döngüsü) SONRA alınır — 1 GB'lık bir yüklemenin okunma/diske yazılma süresi
# kilidin DIŞINDA kalır. Kilit yalnız var-olma kontrolü + `os.replace` + DB
# satırının commit'ine kadarki (küçük, hızlı) pencereyi kapsar. Aynı sha'yı
# bekleyen başka bir istek varsa yalnız bu pencere kadar bloke olur — aynı
# sha = aynı içerik olduğu için bu nadir ve doğru bir davranıştır.
def _sha_kilidi(sha):
    """`sha256` için transaction ömürlü advisory lock al — yalnız Postgres'te.

    Yukarıdaki blok yorumunu oku: bu fonksiyon yükleme (`_diske_yaz`) ve purge
    (`_purge_disk_dedup`) arasındaki TOCTOU yarışını kapatan TEK yer, iki yerde
    de bu çağrılır (ayrışma riskini kapatmak için kilit mantığı burada toplandı)."""
    if db.session.get_bind().dialect.name != 'postgresql':
        return
    db.session.execute(
        text('SELECT pg_advisory_xact_lock(hashtext(:k))'),
        {'k': f'design-files:sha:{sha}'})


def _diske_yaz(stream, file_name):
    """Akışı diske yaz + sha256; `(sha256, boyut)`. Atomiklik, dedup ve 0644
    modu ortak `sha_store`'ta — `voice_notes` ile paylaşılır ki 0600 hatası
    (2026-08-08) iki yerde ayrı ayrı düzeltilmek zorunda kalmasın.

    `on_hashed=_sha_kilidi`: sha hesaplandıktan hemen sonra, hedefin var olup
    olmadığına bakılmadan ÖNCE `_sha_kilidi` çağrılsın diye — TOCTOU yarışını
    kapatan kilidin sırası `sha_store`'a devredilmeden ÖNCEKİYLE BİREBİR aynı
    kalsın (yukarıdaki blok yorumu, `_purge_disk_dedup` ile birlikte okunmalı)."""
    return sha_store.yaz(stream, STORE_DIR, file_name, on_hashed=_sha_kilidi)


# --- Drive (en-iyi-çaba) ----------------------------------------------------

def _drive_kopyala(client, version, path):
    """Dosyayı müşterinin Drive kökü altındaki 'Çalışma Dosyaları'na kopyala.

    EN-İYİ-ÇABA: başarısızlık yüklemeyi bozmaz, `drive_file_id` NULL kalır ve
    panelde 'Drive'a kopyalanmadı' rozeti çıkar. Kanonik dosya diskte olduğu için
    kullanıcı bundan etkilenmez (depo'da tersi doğruydu — orada Drive dosyası
    ürünün kendisiydi). `version.drive_file_id`'yi SET EDER, commit ETMEZ."""
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
    except Exception as e:  # noqa: BLE001 — Drive kopyası ürünün kendisi değil
        log.warning('çalışma dosyası Drive kopyası başarısız (%s): %s',
                    version.file_name, e)
        return False


# --- form yardımcıları ------------------------------------------------------

def _etiketler(raw):
    """Form'daki JSON diziyi temizlenmiş etiket listesine çevir.

    Boşlar atılır, kırpılır, TAG_MAX'a kesilir, TAGS_MAX ile sınırlanır. Tekrar
    ayıklaması Türkçe-duyarlı `client_tracking._fold()` ile yapılır ('Şablon' ve
    'şablon' aynı etiket) — çıplak `casefold()` DEĞİL: `'İ'.casefold()` normal
    'i' değil birleşik nokta içeren bir dizge üretir ('İstanbul'/'istanbul' farklı
    etiket sayılır), repo bu yüzden TR harflerini önce sadeleştiren ortak `_fold`'u
    kullanıyor. Görüntülemede kullanıcının YAZDIĞI hâl korunur."""
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
    """(ad, boyut, mime, err) — Drive'a/diske GİTMEDEN önceki kontrol sırası:
    boyut ölç → 1 GB aşımı 413 → kota aşımı 409 → yasak uzantı 400 (depo deseni)."""
    if f is None or not f.filename:
        return None, 0, None, (jsonify(error='dosya yok'), 400)
    ad = _clean_name(f.filename)
    boyut = _olcu(f.stream)
    if boyut > MAX_FILE_BYTES:
        return None, 0, None, (jsonify(
            error='Dosya 1 GB sınırını aşıyor.'), 413)
    q = _kota(client_id)
    if boyut > q['remaining']:
        return None, 0, None, (jsonify(
            error=f'Müşteri alanı dolu — kalan {_mb(q["remaining"])}, '
                  f'dosya {_mb(boyut)}.', quota=q), 409)
    if _blocked_ext(ad):
        return None, 0, None, (jsonify(
            error='Bu dosya türü yüklenemez (çalıştırılabilir dosya).'), 400)
    mime = f.mimetype or mimetypes.guess_type(ad)[0] or 'application/octet-stream'
    # DesignFileVersion.mime_type String(120) — istemciden gelen Content-Type
    # bunu aşarsa (bazı tarayıcı/eklenti kombinasyonları uzun/parametreli değer
    # yollayabiliyor) Postgres commit'i DataError ile patlar; dosya diske zaten
    # yazılmış olur ve öksüz kalır. Burada kırpmak hem yeni dosya (v1) hem yeni
    # sürüm yolunu birden kapatır — ikisi de bu fonksiyona uğrar.
    mime = mime[:120]
    return ad, boyut, mime, None


def _blocked_ext(name):
    return _uzanti(name) in BLOCKED_EXT


def _mb(n):
    return f'{n / 1024 / 1024:.0f} MB'


def _tek_dosya_json(f, u):
    """Tek dosyanın (güncel sürümüyle) panel sözlüğü — yükleme yanıtında kullanılır."""
    guncel = _guncel_surumler([f.id])
    v, adet = guncel.get(f.id, (None, 0))
    names = _uploader_names()
    return f.to_dict(
        current=v.to_dict(uploader_name=names.get(v.uploaded_by),
                          can_delete=_silebilir(u, v)) if v else None,
        version_count=adet, uploader_name=names.get(f.created_by),
        can_delete=_silebilir(u, f))


# --- yükleme ----------------------------------------------------------------

@bp.post('/client/<int:client_id>')
def client_file_create(client_id):
    """Yeni çalışma dosyası + v1 (multipart: file, title, tags?, note?)."""
    u, err = _require()
    if err:
        return err
    c, cerr = _musteri_veya_404(client_id)
    if cerr:
        return cerr
    title = (request.form.get('title') or '').strip()[:TITLE_MAX]
    if not title:
        return jsonify(error='başlık zorunlu'), 400
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
    # DB commit'i Drive'dan ÖNCE: kanonik dosya diskte, Drive gecikse de kullanıcı
    # dosyasını görür ve indirir.
    db.session.commit()
    drive_ok = _drive_kopyala(c, v, _yol(sha, ad))
    db.session.commit()
    return jsonify(file=_tek_dosya_json(row, u), drive_ok=drive_ok,
                   quota=_kota(client_id)), 201


# --- sürümleme ----------------------------------------------------------

def _dosya_veya_404(file_id):
    f = db.session.get(DesignFile, file_id)
    if f is None or f.deleted_at is not None:
        return None, (jsonify(error='dosya bulunamadı'), 404)
    return f, None


@bp.get('/<int:file_id>/versions')
def file_versions(file_id):
    """Sürüm geçmişi — yeniden eskiye (panel açılır listede aynı sırayı gösterir)."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    # Müşteri arşivlenmişse (soft-delete) dosya satırı kalır ama geçmişi
    # görünmemeli — `client_files`/`file_version_create` ile aynı kontrol.
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
    """Yeni sürüm yükle (multipart: file, note?). Sürüm numarası MAX+1.

    Yarış: iki tasarımcı aynı anda yüklerse MAX+1 ikisinde de aynı çıkar ve
    UNIQUE(file_id, version_no) ikincisini reddeder → 409. Otomatik yeniden
    denemek (MAX+2) BİLEREK yapılmıyor: kullanıcı diğerinin sürümünü görmeden
    üstüne yazmış olurdu."""
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

    # Bilerek `deleted_at` süzgeci UYGULANMADAN MAX(version_no): silinmiş bir
    # sürümün numarası yeniden kullanılırsa geçmişteki not/indirme kaydı
    # yanıltıcı biçimde iki farklı içeriğe işaret eder ("güncel" süzgeci ayrı
    # tutuluyor, bkz. `_guncel_surumler`).
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
        return jsonify(error='Bu sırada başkası yeni sürüm yükledi — '
                             'sayfayı tazeleyip tekrar dene.'), 409
    drive_ok = _drive_kopyala(c, v, _yol(sha, ad))
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u), drive_ok=drive_ok,
                   quota=_kota(f.client_id)), 201


# --- indirme ------------------------------------------------------------

@bp.get('/versions/<int:version_id>/download')
def version_download(version_id):
    """Sürümü indir. Yetki BURADA doğrulanır, akıtma (nginx modunda) nginx'te.

    Drive linki verilmiyor: Drive kopyası kısıtlı izinli, ajans Google hesabında
    olmayan tasarımcı için işe yaramaz; dosya zaten bizde kanonik."""
    _, err = _require()
    if err:
        return err
    v = db.session.get(DesignFileVersion, version_id)
    if v is None or v.deleted_at is not None:
        return jsonify(error='sürüm bulunamadı'), 404
    f, ferr = _dosya_veya_404(v.file_id)
    if ferr:
        return ferr
    # Müşteri arşivlenmişse (soft-delete) dosya satırı kalır ama indirilememeli —
    # `file_versions`/`file_version_create` ile aynı kural (denetimde işaretlendi).
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    yol = _yol(v.sha256, v.file_name)
    if not os.path.exists(yol):
        # DB satırı var, disk dosyası yok: elle silinmiş ya da göç kazası.
        # 500 yerine anlaşılır bir durum — panelde "dosya sunucuda bulunamadı".
        log.error('çalışma dosyası diskte yok: version=%s sha=%s', v.id, v.sha256)
        return jsonify(error='dosya sunucuda bulunamadı'), 410

    if not XACCEL:
        # nginx yok (test / `flask run`) → dosyayı Flask servis eder.
        return send_file(yol, mimetype=v.mime_type or 'application/octet-stream',
                         as_attachment=True, download_name=v.file_name,
                         conditional=True)

    resp = Response(status=200)
    resp.headers['X-Accel-Redirect'] = (
        XACCEL_PREFIX + f'{v.sha256[:2]}/{v.sha256}.{_uzanti(v.file_name)}')
    resp.headers['Content-Type'] = v.mime_type or 'application/octet-stream'
    # Diskteki ad hash — kullanıcı ÖZGÜN adı indirsin (Türkçe karakterler UTF-8).
    resp.headers['Content-Disposition'] = (
        "attachment; filename*=UTF-8''" + quote(v.file_name or f'dosya-{v.id}'))
    return resp


# --- düzenleme ve silme -------------------------------------------------

@bp.patch('/<int:file_id>')
def file_patch(file_id):
    """Başlık ve etiketleri düzenle. Dosya içeriğine dokunmaz."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    # Müşteri arşivlenmişse (soft-delete) dosya satırı kalır ama düzenlenememeli —
    # `file_versions`/`version_download` ile aynı kural.
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    veri = request.get_json(silent=True) or {}
    if 'title' in veri:
        ham_title = veri.get('title')
        # `{"title": 5}` gibi string-olmayan bir gövde `.strip()`'te AttributeError
        # fırlatıp 500'e düşerdi — burada anlaşılır 400.
        if ham_title is not None and not isinstance(ham_title, str):
            return jsonify(error='başlık metin olmalı'), 400
        t = (ham_title or '').strip()[:TITLE_MAX]
        if not t:
            return jsonify(error='başlık boş olamaz'), 400
        f.title = t
    if 'tags' in veri:
        import json
        ham_tags = veri.get('tags')
        # `{"tags": "şablon"}` gibi dizi-olmayan bir gövde `_etiketler`'de sessizce
        # [] döner ve dosyanın TÜM etiketlerini siler — 200 yerine anlaşılır 400.
        if ham_tags is not None and not isinstance(ham_tags, list):
            return jsonify(error='etiketler liste olmalı'), 400
        f.tags = _etiketler(json.dumps(ham_tags or []))
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u))


def _drive_cope(version):
    """Drive kopyasını çöp kutusuna at (30 gün geri alınabilir). EN-İYİ-ÇABA:
    tek Drive hıçkırığı panel kaydını silinemez yapmamalı (depo deseni)."""
    if not version.drive_file_id:
        return True
    try:
        dg.trash_file(version.drive_file_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning('Drive kopyası çöpe atılamadı (%s): %s', version.drive_file_id, e)
        return False


def _drive_geri_al(version):
    """Drive kopyasını çöp kutusundan çıkar (`_drive_cope`'un tersi). EN-İYİ-ÇABA:
    kanonik dosya sunucuda durduğu için Drive hatası geri almayı BLOKLAMAMALI,
    yalnız `drive_ok` False döner ve panel Drive kopyasının hâlâ çöpte olduğunu
    (30 gün sonra kalıcı silineceğini) gösterebilir."""
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
    """Dosyayı ve tüm sürümlerini soft-delete et; Drive kopyaları çöp kutusuna.

    Disk dosyası KALIR: aynı sha256'ya başka bir satır işaret ediyor olabilir ve
    soft-delete geri alınabilir olmalı (fonts deseni)."""
    u, err = _require()
    if err:
        return err
    f, ferr = _dosya_veya_404(file_id)
    if ferr:
        return ferr
    # Müşteri arşivlenmişse dosya zaten görünmüyor demektir ama uç doğrudan
    # çağrılabilir — aynı kural burada da (`file_versions` ile tutarlı).
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if not _silebilir(u, f):
        return jsonify(error='yalnız yükleyen veya yönetim silebilir'), 403
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
    """Tek sürümü kaldır. Son kalan sürüm silinemez — dosyanın hiç içeriği olmayan
    bir kabuğa dönüşmesi kullanıcı için anlamsız; dosyayı silmek isteyen dosyayı
    silsin (`DELETE /<file_id>`)."""
    u, err = _require()
    if err:
        return err
    v = db.session.get(DesignFileVersion, version_id)
    if v is None or v.deleted_at is not None:
        return jsonify(error='sürüm bulunamadı'), 404
    f, ferr = _dosya_veya_404(v.file_id)
    if ferr:
        return ferr
    # Aynı tutarlılık kuralı: arşivlenmiş müşterinin sürümü silinmemeli.
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if not _silebilir(u, v):
        return jsonify(error='yalnız yükleyen veya yönetim silebilir'), 403
    kalan = DesignFileVersion.query.filter_by(file_id=f.id, deleted_at=None).count()
    if kalan <= 1:
        return jsonify(error='Son sürüm silinemez — dosyanın tamamını sil.'), 400
    drive_ok = _drive_cope(v)
    v.deleted_at = utcnow()
    v.deleted_by = u['sub']
    db.session.commit()
    return jsonify(ok=True, drive_ok=drive_ok, quota=_kota(f.client_id))


# --- çöp kutusu: geri alma ---------------------------------------------------
#
# Soft-delete tek başına tek yönlü bir kapıydı (`deleted_at` yazılır, geri
# döndürecek uç yoktu) — çöp kutusu bunu bir onay kuyruğuna çeviriyor: silinen
# her şey burada durur, yönetim ya geri alır ya kalıcı siler (proje sahibi kararı,
# 2026-08-08, bkz. `.superpowers/sdd/2026-08-07-tasarim-calisma-dosyalari/
# followup-silme-brief.md`). Ayrı bir istek/onay durum makinesi KURULMADI:
# tetik zaten `_silebilir`/`management` ile var olan yetki matrisinde.


@bp.get('/client/<int:client_id>/trash')
def client_trash(client_id):
    """Çöp kutusu: silinmiş DOSYALAR + hâlâ yaşayan dosyaların tekil silinmiş
    SÜRÜMLERİ (iki ayrı dizi — biri dosya bazlı geri alma/kalıcı silme, diğeri
    sürüm bazlı). Kalıcı silme burada YAPILMAZ, yalnız görüntüleme + işaretler
    (bkz. `file_purge_request`)."""
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
    # Silinmiş dosyaların sürümleri de silinmiş — `silinmisler_dahil=True`
    # olmadan `_guncel_surumler` bu satırlarda hep boş dönerdi (canlı listenin
    # `deleted_at IS NULL` filtresi burada hiçbir şeyle eşleşmez).
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
    """Dosyayı çöp kutusundan geri al.

    **Kapsam — `deleted_at` damgasıyla eşleşme:** dosyayla BİRLİKTE yalnız
    `deleted_at == f.deleted_at` olan sürümler döner; daha önce TEK TEK
    silinmiş sürümler (farklı, daha eski bir damga taşıyorlar) silinmiş kalır.
    `file_delete` tüm aktif sürümlere AYNI `utcnow()` damgasını bastığı için bu
    eşleşme güvenilir — aksi halde 'v2'yi sildim, sonra dosyayı silip geri
    aldım, v2 de geri geldi' sürprizi olurdu."""
    u, err = _require()
    if err:
        return err
    f = db.session.get(DesignFile, file_id)
    if f is None:
        return jsonify(error='dosya bulunamadı'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is None:
        return jsonify(error='dosya zaten çöp kutusunda değil'), 400
    if not _silebilir(u, f):
        return jsonify(error='yalnız yükleyen veya yönetim geri alabilir'), 403
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
    """Tek sürümü çöp kutusundan geri al. Dosyanın KENDİSİ çöp kutusundaysa
    reddedilir (400) — o durumda geri alma dosya seviyesinden yapılır
    (`file_restore`), aksi halde canlı listede görünmeyen bir dosyaya bağlı
    'canlı' bir sürüm gibi tutarsız bir ara durum doğardı."""
    u, err = _require()
    if err:
        return err
    v = db.session.get(DesignFileVersion, version_id)
    if v is None:
        return jsonify(error='sürüm bulunamadı'), 404
    f = db.session.get(DesignFile, v.file_id)
    if f is None:
        return jsonify(error='dosya bulunamadı'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is not None:
        return jsonify(error='dosyanın kendisi çöp kutusunda — önce dosyayı geri al'), 400
    if v.deleted_at is None:
        return jsonify(error='sürüm zaten çöp kutusunda değil'), 400
    if not _silebilir(u, v):
        return jsonify(error='yalnız yükleyen veya yönetim geri alabilir'), 403
    drive_ok = _drive_geri_al(v)
    v.deleted_at = None
    v.deleted_by = None
    db.session.commit()
    return jsonify(file=_tek_dosya_json(f, u), drive_ok=drive_ok, quota=_kota(f.client_id))


@bp.post('/<int:file_id>/purge-request')
def file_purge_request(file_id):
    """Tasarımcının 'kalıcı silinsin' işareti — koy/kaldır. HİÇBİR ŞEYİ SİLMEZ,
    yalnız yönetime çöp kutusunda görünen bir uyarı rozeti bırakır. Yetki
    `_require()` ile tüm tasarım ekibi (yalnız sahip değil) — talep etmek
    silmekten daha hafif bir eylem."""
    u, err = _require()
    if err:
        return err
    f = db.session.get(DesignFile, file_id)
    if f is None:
        return jsonify(error='dosya bulunamadı'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is None:
        return jsonify(error='yalnız çöp kutusundaki dosya için talep edilebilir'), 400
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
    """`sha256`'ya ait TÜM disk yolları DURSUN mu SİLİNSİN mi — aynı içeriğe
    (dedup) işaret eden BAŞKA hiçbir `DesignFileVersion` satırı (silinmiş
    dahil, onlar da geri alınabilir) kalmadıysa siler. Kontrolsüz `os.unlink`
    başka bir müşterinin hâlâ canlı dosyasını öldürürdü. EN-İYİ-ÇABA: patlarsa
    DB purge'ü GERİ ALINMAZ, çağıran `disk_ok:false` döner.

    **Uzantı sızıntısı:** aynı bayt'lar farklı ad/uzantıyla yüklenmiş olabilir
    (`logo.psd` ve `logo.ai` aynı içerik) — `_yol` uzantı içerdiği için diskte
    İKİ ayrı dosya oluşur (`<sha>.psd`, `<sha>.ai`). Tek bir `file_name`'in
    yoluna değil, o sha'ya ait TÜM yollara (`<sha[:2]>/<sha>.*` deseni, `glob`)
    bakılır. Bu güvenli: sayım zaten sha-kapsamlı (uzantıdan bağımsız), 0 ise
    hiçbir satır kalmamıştır — desen ne kadar dosyaya uyarsa hepsi öksüzdür.

    **Kilit:** sayımdan ÖNCE `_sha_kilidi` alınır — TOCTOU yarışını kapatan
    kilit (`_diske_yaz` ile birlikte okunmalı, bkz. yukarıdaki blok yorumu).
    Sayımdan sonra (ne bulunursa bulunsun) `db.session.commit()` ile kilit
    hemen bırakılır; bu fonksiyon salt-okunur bir SELECT dışında veri
    değiştirmediği için commit güvenli — yalnızca advisory lock'u serbest
    bırakma amaçlı (jobqueue.py `enqueue` ile aynı desen)."""
    _sha_kilidi(sha)
    if DesignFileVersion.query.filter_by(sha256=sha).count():
        db.session.commit()   # kilidi bırak (bu transaction'da değişiklik yok)
        return True
    desen = os.path.join(STORE_DIR, sha[:2], f'{sha}.*')
    ok = True
    for yol in glob.glob(desen):
        try:
            os.unlink(yol)
        except OSError as e:
            log.warning('çalışma dosyası diskten kalıcı silinemedi (%s): %s', yol, e)
            ok = False
    db.session.commit()       # kilidi bırak
    return ok


@bp.delete('/<int:file_id>/purge')
def file_purge(file_id):
    """Dosyayı ve TÜM sürümlerini KALICI sil. Yalnız yönetim, yalnız çöp
    kutusundaki dosya, yalnız gövdede `{"confirm": true}` ile — geri alınamaz.

    **FK sırası:** önce `DesignFileVersion` satırları, sonra `DesignFile`
    (`card_uploads` silme dersi — ters sırada FK hatası alınır).

    **Drive'da kalıcı silme YAPILMAZ:** kopya soft-delete anında zaten Drive
    çöp kutusuna atıldı (`_drive_cope`, 30 gün geri alınabilir) ve
    `drive_gateway`'de kalıcı silme fonksiyonu yok — repo bunu bilinçli tercih
    etmedi (`sharing.py` aynı gerekçe). Drive kendi 30 günde temizler."""
    u, err = _require()
    if err:
        return err
    if u.get('role') != 'management':
        return jsonify(error='kalıcı silme yalnız yönetimde yapılabilir'), 403
    f = db.session.get(DesignFile, file_id)
    if f is None:
        return jsonify(error='dosya bulunamadı'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is None:
        return jsonify(error='yalnız çöp kutusundaki dosya kalıcı silinebilir'), 400
    veri = request.get_json(silent=True) or {}
    if veri.get('confirm') is not True:
        return jsonify(error='kalıcı silme onayı gerekli ({"confirm": true})'), 400

    versiyonlar = DesignFileVersion.query.filter_by(file_id=f.id).all()
    # sha256'lar commit'ten ÖNCE toplanır — satırlar silindikten sonra ORM
    # nesnelerine erişmek DetachedInstanceError riski taşır. `set`: aynı dosyanın
    # iki sürümü aynı içerikle yeniden yüklenmiş olabilir (aynı sha), tekrar eden
    # kilit/sorguyu Python tarafında baştan eler.
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
    """Tek sürümü KALICI sil. Yalnız yönetim, yalnız çöp kutusundaki sürüm,
    yalnız `{"confirm": true}` ile. Drive kalıcı silme YAPILMAZ (`file_purge`
    ile aynı gerekçe).

    **Kapı: dosyanın kendisi çöpte olamaz.** `version_restore`'un aksine bu uç
    eskiden yalnız `v.deleted_at`'a bakıyordu — dosyanın kendisi (`f.deleted_at`)
    çöpteyken sürümleri tek tek purge etmek `design_files` satırını SIFIR
    sürümle bırakabiliyordu (`version_delete`'in "son sürüm silinemez" kararına
    aykırı bir kabuk dosya; sonradan restore edilirse canlı listede
    `current: null` görünür). `version_restore` ile AYNI kapı burada da var."""
    u, err = _require()
    if err:
        return err
    if u.get('role') != 'management':
        return jsonify(error='kalıcı silme yalnız yönetimde yapılabilir'), 403
    v = db.session.get(DesignFileVersion, version_id)
    if v is None:
        return jsonify(error='sürüm bulunamadı'), 404
    f = db.session.get(DesignFile, v.file_id)
    if f is None:
        return jsonify(error='dosya bulunamadı'), 404
    _, cerr = _musteri_veya_404(f.client_id)
    if cerr:
        return cerr
    if f.deleted_at is not None:
        return jsonify(error='dosyanın kendisi çöp kutusunda — sürümü tek tek '
                             'değil, dosyanın tamamını kalıcı sil'), 400
    if v.deleted_at is None:
        return jsonify(error='yalnız çöp kutusundaki sürüm kalıcı silinebilir'), 400
    veri = request.get_json(silent=True) or {}
    if veri.get('confirm') is not True:
        return jsonify(error='kalıcı silme onayı gerekli ({"confirm": true})'), 400

    sha = v.sha256
    db.session.delete(v)
    db.session.commit()
    disk_ok = _purge_disk_dedup(sha)
    return jsonify(ok=True, disk_ok=disk_ok, quota=_kota(f.client_id))
