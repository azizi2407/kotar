"""Lokal medya deposu — panele yüklenen dosyaların 21 günlük sunucu kopyası.

Drive kanonik kaynaktır; burası yalnız hızlandırma katmanıdır (Sharing Board +
müşteri onay sayfası ilk 3 hafta medyayı buradan servis eder). Kayıt defteri
yok: "sunucuda var mı?" = dosya diskte var mı; süre ölçütü dosya mtime'ı.
Dizin: $MEDIA_STORE_DIR (varsayılan <repo>/data/media) altında originals/ + previews/.
"""
import glob
import io
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid

log = logging.getLogger(__name__)

RETENTION_DAYS = 21
PREVIEW_MAX_PX = 800
_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')      # Drive file_id alfabesi
_EXT_RE = re.compile(r'^\.[a-z0-9]{1,8}$')

# media.py ile AYNI env değişkeni; oradan import etmiyoruz — depo katmanının
# işleme katmanına bağımlı olmaması testi de hafif tutuyor.
FFMPEG = os.environ.get('FFMPEG_BIN', 'ffmpeg')
# `-c copy` remux I/O bağımlı (150 MB ≈ 0.2 sn); bu tavan yalnız takılmaya karşı.
FASTSTART_TIMEOUT = 120
# `+faststart` yalnız ISO-BMFF konteynerinde anlamlı.
FASTSTART_EXTS = ('.mp4', '.mov', '.m4v')


def _dir(kind):
    root = os.environ.get('MEDIA_STORE_DIR') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'data', 'media')
    d = os.path.join(root, kind)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_id(file_id):
    return bool(file_id) and bool(_ID_RE.match(file_id))


def _ext_for(filename, mime):
    ext = os.path.splitext(filename or '')[1].lower()
    if _EXT_RE.match(ext):
        return ext
    return mimetypes.guess_extension(mime or '') or '.bin'


def make_preview(source, max_px=PREVIEW_MAX_PX):
    """Görselden en fazla max_px kenarlı JPEG önizleme üret.

    `source`: bytes veya disk yolu (PIL yoldan akışlı okur — RAM'e tüm dosya girmez)."""
    from PIL import Image
    img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    img.thumbnail((max_px, max_px))
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=82)
    return out.getvalue()


def _write_preview(source, file_id):
    """Önizlemeyi best-effort yaz (bozuk görselde sessizce vazgeç)."""
    try:
        prev = make_preview(source)
        with open(os.path.join(_dir('previews'), file_id + '.jpg'), 'wb') as f:
            f.write(prev)
    except Exception:  # noqa: BLE001 — bozuk görsel vb.; orijinal yeterli
        log.warning('önizleme üretilemedi: %s', file_id)


def _faststart(path, mime):
    """Videoda `moov` atom'unu dosyanın BAŞINA taşı (kayıpsız remux).

    NEDEN: telefon/kamera çıktısı mp4'lerde `moov` (süre, codec, kare indeksi)
    dosyanın SONUNDA yazılır — kaydı bitirmeden boyutu bilinmediği için. Tarayıcı
    oynatmaya başlamak için `moov`'u okumak zorunda; sonda olunca mobil Chrome
    videoyu başlatamıyordu (2026-08-01, Android). İndirme etkilenmiyordu — o
    baytları sırayla alır, `moov`'u beklemez; "iniyor ama oynamıyor" tablosu
    tam olarak bundandı.

    `-c copy`: yalnız konteyner yeniden yazılır, kare/ses DOKUNULMAZ → kayıpsız
    ve hızlı (150 MB ≈ 0.2 sn). Zaten önde olan dosyada da güvenli (idempotent).

    Best-effort: ffmpeg yoksa/çözemezse orijinal olduğu gibi kalır — yükleme
    kritik, remux değil. Başarıysa True."""
    if not (mime or '').startswith('video/'):
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext not in FASTSTART_EXTS:
        return False
    # Geçici ad NOKTA ile başlar → `find_original` glob'u (`<file_id>.*`) onu
    # ASLA yakalamaz; yarım kalmış remux yanlışlıkla servis edilemez.
    tmp = os.path.join(os.path.dirname(path), f'.fstmp-{uuid.uuid4().hex}{ext}')
    try:
        r = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-i', path,
             '-c', 'copy', '-movflags', '+faststart', tmp],
            capture_output=True, timeout=FASTSTART_TIMEOUT)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)      # aynı dizin → atomik
            return True
        log.warning('faststart remux başarısız (%s): %s', os.path.basename(path),
                    (r.stderr or b'')[:200].decode('utf-8', 'replace'))
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        log.warning('faststart remux koşulamadı (%s): %s', os.path.basename(path), e)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def save_original(file_id, data, mime, filename=None):
    """Drive yüklemesi SONRASI lokal kopya + (görselse) önizleme yaz.

    Best-effort: hata yüklemeyi geçersiz kılmaz, yalnız log'lanır."""
    if not _safe_id(file_id):
        return False
    try:
        path = os.path.join(_dir('originals'), file_id + _ext_for(filename, mime))
        with open(path, 'wb') as f:
            f.write(data)
    except OSError:
        log.exception('lokal kopya yazılamadı: %s', file_id)
        return False
    if (mime or '').startswith('image/'):
        _write_preview(data, file_id)
    else:
        _faststart(path, mime)
    return True


# --- Akış tabanlı yol (büyük dosyalar: RAM'e almadan diskten diske) ---

def stage(stream, chunk=8 * 1024 * 1024):
    """İstek akışını originals/ altına GEÇİCİ dosyaya kopyala; yolunu döndür.

    Drive yüklemesi bu dosyadan (seek'lenebilir) yapılır; başarıda `commit`,
    hatada `discard` çağrılır. Yazılamazsa None (çağıran akıştan devam eder)."""
    tmp = os.path.join(_dir('originals'), f'.tmp-{uuid.uuid4().hex}')
    try:
        with open(tmp, 'wb') as f:
            shutil.copyfileobj(stream, f, chunk)
        return tmp
    except OSError:
        log.exception('geçici medya dosyası yazılamadı')
        discard(tmp)
        return None


def commit(tmp_path, file_id, mime, filename=None):
    """Drive başarılı → geçiciyi kalıcı adına al, görselse önizleme üret."""
    if not tmp_path:
        return False
    if not _safe_id(file_id):
        discard(tmp_path)
        return False
    final = os.path.join(_dir('originals'), file_id + _ext_for(filename, mime))
    try:
        os.replace(tmp_path, final)
    except OSError:
        log.exception('lokal kopya kalıcılaştırılamadı: %s', file_id)
        discard(tmp_path)
        return False
    if (mime or '').startswith('image/'):
        _write_preview(final, file_id)
    else:
        # Videolar bu yoldan gelir (akış tabanlı); `/m/<id>` sayfasının mobilde
        # oynayabilmesi buna bağlı — bkz. `_faststart`.
        _faststart(final, mime)
    return True


def discard(tmp_path):
    """Geçici dosyayı sil (yoksa sessiz)."""
    if not tmp_path:
        return
    try:
        os.remove(tmp_path)
    except OSError:
        pass


def find_original(file_id):
    """Orijinalin disk yolu; yoksa/geçersiz id'de None."""
    if not _safe_id(file_id):
        return None
    hits = glob.glob(os.path.join(_dir('originals'), file_id + '.*'))
    return hits[0] if hits else None


def find_preview(file_id):
    if not _safe_id(file_id):
        return None
    p = os.path.join(_dir('previews'), file_id + '.jpg')
    return p if os.path.exists(p) else None


def has_original(file_id):
    return find_original(file_id) is not None


def web_path(file_id):
    """Web türevinin hedef yolu (`web/<file_id>.mp4`); geçersiz id'de None.

    Türev DAİMA .mp4/H.264'tür (`media.make_web_variant`), o yüzden uzantı sabit —
    `find_original`'ın glob'una gerek yok."""
    if not _safe_id(file_id):
        return None
    return os.path.join(_dir('web'), file_id + '.mp4')


def find_web(file_id):
    """Web türevinin yolu; yoksa None.

    Türev, orijinali tarayıcıda oynamayan videolar için üretilir (4K/HEVC/10-bit);
    `/m/<file_id>` sayfası ONU oynatır, "İndir" düğmesi orijinali verir."""
    p = web_path(file_id)
    return p if p and os.path.exists(p) else None


def remove(file_id):
    """Bir dosyanın lokal kopyalarını (orijinal + önizleme + web türevi) HEMEN sil.

    `cleanup` yaşa göre çalışır; bu ise tekil ve anlıktır — kalıcı silinen bir
    yükleme 21 gün boyunca diskte ve `/m/<file_id>` üzerinden erişilebilir
    kalmasın diye (2026-07-31 videograf "Sil" düğmesi). Silinen dosya sayısını
    döner; yoksa 0 (hata değil)."""
    removed = 0
    for path in (find_original(file_id), find_preview(file_id), find_web(file_id)):
        if not path:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            log.exception('lokal kopya silinemedi: %s', path)
    return removed


def cleanup(max_age_days=RETENTION_DAYS):
    """Süresi (mtime) dolan lokal kopyaları sil; silinen dosya sayısını döner.

    Yarım kalmış `.tmp-*` dosyaları (çöken istek artığı) 1 günden eskiyse silinir."""
    now = time.time()
    cutoff = now - max_age_days * 86400
    tmp_cutoff = now - 86400
    removed = 0
    # 'web': türevler de orijinalle aynı 21 günlük pencereye tabi — orijinal
    # gidince türevi tutmanın anlamı yok (kaynağı Drive'dan yeniden üretilir).
    for kind in ('originals', 'previews', 'web'):
        d = _dir(kind)
        for name in os.listdir(d):
            p = os.path.join(d, name)
            limit = tmp_cutoff if name.startswith('.tmp-') else cutoff
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < limit:
                    os.remove(p)
                    removed += 1
            except OSError:
                log.exception('temizlik silemedi: %s', p)
    return removed
