"""Media işleme yardımcıları — ffprobe/ffmpeg + whisper servisi çağrısı.

Video için: ses akışı var mı (ffprobe) → varsa whisper transkripti; farklı
sürelerden birkaç kare (ffmpeg) → caption görsel bağlamı + video thumbnail.
media_worker (proje sahibi) çağırır. Gerçek dosya sistemi işlemleri (test edilebilir
kısımlar saf; ffmpeg entegrasyon testiyle).
"""
import io
import json
import logging
import os
import subprocess
import tempfile
import uuid

import requests
from PIL import Image

log = logging.getLogger(__name__)

FFMPEG = os.environ.get('FFMPEG_BIN', 'ffmpeg')
FFPROBE = os.environ.get('FFPROBE_BIN', 'ffprobe')
WHISPER_URL = os.environ.get('WHISPER_URL', 'http://127.0.0.1:5051')

# --- dosya türü tespiti (2026-07-27) ---------------------------------------
# `Share.kind` YAYIN türüdür (post/story/reel/linkedin), dosya türü DEĞİL — bir
# video pekâlâ "post" olarak paylaşılır (Instagram'da olağan). media_worker
# eskiden `kind != 'video'` görünce dosyayı PIL'e veriyordu; `kind='post'` olan
# bir .mp4 "cannot identify image file" ile 3 denemede de çöktü (job 233,
# share 671) ve caption zinciri o paylaşımda hiç ilerlemedi. Artık karar
# İÇERİKTEN veriliyor: bayt imzası yalan söylemez.

# ISO-BMFF (`ftyp`) hem MP4 hem HEIC/AVIF tarafından kullanılır → brand'a bakılır.
_ISOBMFF_IMAGE_BRANDS = frozenset(
    (b'heic', b'heix', b'heim', b'heis', b'hevc', b'hevx',
     b'mif1', b'msf1', b'avif', b'avis'))

_VIDEO_EXTS = frozenset(
    ('.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.flv', '.wmv',
     '.mpg', '.mpeg', '.3gp', '.ts', '.mts'))
_IMAGE_EXTS = frozenset(
    ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff',
     '.heic', '.heif', '.avif'))


def sniff_kind(data):
    """Bayt imzasından 'video' | 'image'; tanınmazsa None.

    Dosya ADINA ve `Share.kind`'a göre daha güvenilir: ad değiştirilmiş ya da
    yayın türü farklı seçilmiş olabilir, içerik olduğu gibi durur."""
    head = bytes(data[:16])
    if head[:3] == b'\xff\xd8\xff':                       return 'image'   # JPEG
    if head[:8] == b'\x89PNG\r\n\x1a\n':                  return 'image'   # PNG
    if head[:4] in (b'GIF8',):                            return 'image'
    if head[:2] == b'BM':                                 return 'image'   # BMP
    if head[:4] in (b'II*\x00', b'MM\x00*'):              return 'image'   # TIFF
    if head[:4] == b'RIFF':                               # WEBP / AVI aynı konteyner
        tag = bytes(data[8:12])
        return 'image' if tag == b'WEBP' else ('video' if tag == b'AVI ' else None)
    if head[4:8] == b'ftyp':                              # ISO-BMFF: MP4/MOV vs HEIC/AVIF
        return 'image' if bytes(data[8:12]) in _ISOBMFF_IMAGE_BRANDS else 'video'
    if head[:4] == b'\x1a\x45\xdf\xa3':                   return 'video'   # Matroska/WebM
    if head[:3] == b'FLV':                                return 'video'
    if head[:4] in (b'\x00\x00\x01\xba', b'\x00\x00\x01\xb3'):  return 'video'  # MPEG-PS/ES
    if head[:1] == b'\x47':                               return 'video'   # MPEG-TS
    return None


def kind_from_name(filename):
    """Dosya adı uzantısından 'video' | 'image'; tanınmazsa None (sniff yedeği)."""
    ext = os.path.splitext(filename or '')[1].lower()
    if ext in _VIDEO_EXTS:
        return 'video'
    if ext in _IMAGE_EXTS:
        return 'image'
    return None


def resolve_kind(data, filename=None, fallback=None):
    """Medya türü — TEK KARAR NOKTASI: içerik imzası → uzantı → çağıranın tahmini.

    `fallback` yalnız ikisi de tanımazsa kullanılır; None ise 'image' varsayılır
    (eski davranış: görsel yolu). media_worker ve testler bunu paylaşır."""
    return sniff_kind(data) or kind_from_name(filename) or fallback or 'image'


def has_audio(path):
    """Videoda ses akışı var mı (ffprobe)."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-select_streams', 'a', '-show_entries',
             'stream=codec_type', '-of', 'json', path],
            capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or '{}')
        return bool(data.get('streams'))
    except Exception:
        return False


def duration_seconds(path):
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', path], capture_output=True, text=True, timeout=60)
        return float(json.loads(out.stdout or '{}').get('format', {}).get('duration', 0))
    except Exception:
        return 0.0


# --- Web uyumlu türev (2026-08-01) ---
#
# Telefon videoları 4K/60fps HEVC Main 10 (10-bit) geliyor; Android Chrome bunu
# açamıyor — oynatmaya basınca hiç görüntü vermeden kapanıyor. Aynı cihaz 1080p
# HEVC'yi oynatıyor, yani sorun codec değil PROFİLİN ağırlığı. Yine de kapsamı
# "h264 + 8-bit + ≤1080p" beyaz listesi yapıyoruz: Firefox HEVC'yi hiç açmaz ve
# bu linkler ajans dışına, bilmediğimiz tarayıcılara gidiyor.
WEB_MAX_EDGE = 1920          # türevin uzun kenarı (dikeyde 1080x1920)
WEB_CRF = 23                 # görsel olarak kayıpsıza yakın, makul boyut
WEB_THREADS = 2              # 4 çekirdekli sunucu — panel yanıt vermeye devam etsin


def video_profile(path):
    """Videonun `(codec, width, height, pix_fmt)` profili; okunamazsa None."""
    try:
        out = subprocess.run(
            [FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
             'stream=codec_name,width,height,pix_fmt', '-of', 'json', path],
            capture_output=True, text=True, timeout=60)
        streams = json.loads(out.stdout or '{}').get('streams') or []
        if not streams:
            return None
        s = streams[0]
        if not s.get('width') or not s.get('height'):
            return None
        return (s.get('codec_name'), int(s['width']), int(s['height']),
                s.get('pix_fmt') or '')
    except Exception:  # noqa: BLE001 — profil okunamazsa "gerekmiyor" sayılır
        return None


def needs_web_variant(path):
    """Bu video tarayıcı için türev gerektiriyor mu?

    Beyaz liste: H.264 + 8-bit + uzun kenar ≤1920 ise DOKUNMA. Geri kalan her şey
    (HEVC, 10-bit, 4K) türev ister. Profil okunamıyorsa False — tahminle pahalı
    transkod başlatmayız."""
    prof = video_profile(path)
    if prof is None:
        return False
    codec, w, h, pix = prof
    if codec != 'h264':
        return True
    if not pix.startswith('yuv420p') or pix != 'yuv420p':
        return True              # 10-bit / 4:2:2 / 4:4:4 → tarayıcı desteği yok
    return max(w, h) > WEB_MAX_EDGE


def make_web_variant(src, dst, timeout=1800):
    """`src`'ten tarayıcı uyumlu 1080p H.264 türev üret → `dst`. Başarıysa True.

    En-boy oranı korunur (uzun kenar `WEB_MAX_EDGE`'e iner, kısa kenar çift sayıya
    yuvarlanır — H.264 tek boyut kabul etmez). `+faststart` ile moov başa alınır;
    onsuz türev de mobilde başlamazdı (bkz. `media_store._faststart`).

    Best-effort: hata durumunda yarım çıktı bırakılmaz ve False döner — çağıran
    orijinali servis etmeye devam eder."""
    # `scale`: uzun kenarı sınırla, oranı koru, ikisini de çift sayıya yuvarla.
    # `-2` = "oranı koru, 2'nin katına yuvarla"; min() büyütmeyi engeller
    # (zaten küçük video yukarı ölçeklenmemeli).
    vf = (f"scale='if(gt(iw,ih),min({WEB_MAX_EDGE},iw),-2)'"
          f":'if(gt(iw,ih),-2,min({WEB_MAX_EDGE},ih))'")
    # Hedefe DOĞRUDAN yazmıyoruz: geri dolum scripti ile worker aynı dosyaya denk
    # gelirse iki ffmpeg aynı çıktıyı ezer ve yarısı bozuk bir türev kalırdı.
    # Geçici ad `.tmp-` ile başlar → `media_store.cleanup`'ın bayat-tmp kuralı
    # (1 gün) yarım kalmış çıktıyı zaten toplar.
    tmp = os.path.join(os.path.dirname(dst) or '.',
                       f'.tmp-wv-{uuid.uuid4().hex}.mp4')
    try:
        r = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-i', src,
             '-vf', vf, '-c:v', 'libx264', '-profile:v', 'high', '-pix_fmt', 'yuv420p',
             '-crf', str(WEB_CRF), '-preset', 'veryfast', '-threads', str(WEB_THREADS),
             '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', tmp],
            capture_output=True, timeout=timeout)
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, dst)          # aynı dizin → atomik
            return True
        log.warning('web türevi üretilemedi (%s): %s', os.path.basename(src),
                    (r.stderr or b'')[:200].decode('utf-8', 'replace'))
    except Exception as e:  # noqa: BLE001 — timeout/OSError; orijinal yeterli
        log.warning('web türevi koşulamadı (%s): %s', os.path.basename(src), e)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


def extract_frames(path, count=3):
    """Videodan `count` kare (jpeg bytes) — süre boyunca eşit aralıklarla."""
    dur = duration_seconds(path)
    if dur <= 0:
        return []
    # kenarları atla: %10 ile %90 arası eşit noktalar
    points = [dur * (0.1 + 0.8 * i / max(1, count - 1)) for i in range(count)] if count > 1 else [dur / 2]
    frames = []
    for ts in points:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tf:
            out_path = tf.name
        try:
            subprocess.run(
                [FFMPEG, '-y', '-ss', f'{ts:.2f}', '-i', path, '-frames:v', '1',
                 '-vf', 'scale=640:-1', '-q:v', '3', out_path],
                capture_output=True, timeout=120)
            if os.path.getsize(out_path) > 0:
                with open(out_path, 'rb') as f:
                    frames.append(f.read())
        except Exception:
            pass
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
    return frames


def extract_audio(path):
    """Videodan mono 16kHz wav (bytes) — whisper için hafif."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
        out_path = tf.name
    try:
        subprocess.run(
            [FFMPEG, '-y', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
             '-f', 'wav', out_path], capture_output=True, timeout=300)
        with open(out_path, 'rb') as f:
            return f.read()
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def downscale_image(data, max_w=1280):
    """Görsel bytes → en fazla max_w genişlikte JPEG bytes (caption görsel bağlamı,
    token/boyut için hafif). Küçükse büyütmez; RGBA/P beyaz zemine düzleştirilir."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    if img.width > max_w:
        h = round(img.height * max_w / img.width)
        img = img.resize((max_w, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def transcribe(audio_bytes, filename='audio.wav', language='tr', timeout=600,
               initial_prompt=None):
    """Whisper servisine gönder → transkript metni.

    `initial_prompt`: özel ad sözlüğü (marka/ekip adları). Whisper bunu önceki
    bağlam sayıp geçen adlara yaklaşır — ajans markalarını doğru yazdırmanın en
    ucuz yolu (bkz. `ai_context.transcript_vocabulary`). Sözlüğü BURADA üretmiyoruz:
    `media` saf medya yardımcısı, DB'ye bakmaz; çağıran verir."""
    data = {'language': language}
    if initial_prompt:
        data['initial_prompt'] = initial_prompt
    r = requests.post(f'{WHISPER_URL}/transcribe',
                      files={'file': (filename, audio_bytes)},
                      data=data, timeout=timeout)
    r.raise_for_status()
    return r.json().get('text', '')
