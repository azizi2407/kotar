"""Planlama Panosu görselleri — pano başına lokal dosya deposu.

Kullanıcı panoya pano'dan (clipboard) görsel yapıştırır, dosya sürükler ya da
sağ tık → "Görsel ekle" der; dosya SUNUCUDA saklanır ve `planning_items`'ta
`type='image'` bir öğe ona işaret eder.

Neden `img_bucket` değil: o depo `/img/<ad>` ile **public** servis eder (harici
sitelere gömmek için). Pano içeriği iç veridir — public URL'e konamaz. Neden
`media_store` değil: orası 21 GÜNLÜK önbellek (Drive kanonik), pano görselinin
kanonik kopyası yoktur, silinirse öğe boşa düşer. Neden Drive değil: pano
görselleri küçük ve çok sayıda; Drive OAuth kotası zaten %85 dolu ve her
yapıştırmada ağ turu gecikme demek.

Yerleşim: `data/planning-images/<board_id>/<uuid>.<ext>` — dizinin pano başına
ayrılması yetkilendirmeyi bedavaya getirir: servis ucu `_board_access(key)`'den
geçer, dosya adı tahmin edilse bile başka panonun görseline erişilemez.
"""
import io
import os
import uuid

from flask import current_app
from PIL import Image, UnidentifiedImageError

# Kabul edilen türler — PIL'in tanıdığı ve tarayıcının gösterebildiği kesişim.
# SVG BİLEREK YOK: metin tabanlı, script taşıyabilir ve panoda inline render
# edilecek (img_bucket'ta kabul edilir çünkü orası harici gömme içindir).
ALLOWED = {'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif', 'WEBP': '.webp'}

MAX_BYTES = 10 * 1024 * 1024              # 10 MB/dosya — ekran görüntüsü için bol
MAX_BOARD_BYTES = 500 * 1024 * 1024       # 500 MB/pano
MAX_EDGE = 2000                           # bundan büyük kenar küçültülür
NAME_LEN = 32                             # uuid4().hex


class ImageError(Exception):
    """Kullanıcıya gösterilecek doğrulama hatası (400)."""


def board_dir(board_id, create=False):
    root = (os.environ.get('PLANNING_IMAGE_DIR')
            or os.path.join(current_app.root_path, 'data', 'planning-images'))
    d = os.path.join(root, str(board_id))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def safe_name(name):
    """`<32 hex>.<ext>` biçimini doğrular — path traversal'a kapalı.

    Ad istemciden gelir (öğenin `extra.image.name`'i); tek karakter bile
    kaçmasına izin verilmez, aksi halde `../../etc/passwd` okunabilirdi."""
    if not isinstance(name, str) or len(name) > NAME_LEN + 8:
        return None
    stem, ext = os.path.splitext(name)
    if len(stem) != NAME_LEN or not all(c in '0123456789abcdef' for c in stem):
        return None
    if ext.lower() not in set(ALLOWED.values()):
        return None
    return stem + ext.lower()


def board_bytes(board_id):
    """Panonun kullandığı toplam disk — sayaç kolonu YOK (depot deseni).

    Sayaç, dosya yazımı ile DB commit'i arasındaki her çökmede kalıcı drift
    üretirdi; dizin taraması pano başına birkaç yüz dosyada ihmal edilebilir."""
    d = board_dir(board_id)
    if not os.path.isdir(d):
        return 0
    return sum(os.path.getsize(os.path.join(d, n)) for n in os.listdir(d)
               if os.path.isfile(os.path.join(d, n)))


def store(board_id, data):
    """Ham bytes → diske yazılmış görsel. `{name, width, height, size}` döner.

    Doğrulama SIRASI önemli: boyut → içerik → kota. Kota kontrolü en sonda ama
    yazmadan ÖNCE; büyük dosyayı önce diske koyup sonra silmek gereksiz I/O."""
    if not data:
        raise ImageError('boş dosya')
    if len(data) > MAX_BYTES:
        raise ImageError(f'görsel {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageError('dosya bir görsel değil ya da bozuk') from e
    fmt = (img.format or '').upper()
    if fmt not in ALLOWED:
        raise ImageError(f'desteklenmeyen görsel türü: {fmt or "bilinmiyor"}')

    # Çok büyük görseli panoda tam çözünürlükte tutmanın karşılığı yok; küçültme
    # animasyonlu GIF'i ilk kareye indirirdi → GIF olduğu gibi saklanır.
    payload, w, h = data, img.width, img.height
    if fmt != 'GIF' and max(img.width, img.height) > MAX_EDGE:
        img = img.copy()
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        buf = io.BytesIO()
        # PNG'de şeffaflık korunur; JPEG'e çevirmek beyaz zemin basardı.
        img.save(buf, format=fmt, **({'quality': 88} if fmt == 'JPEG' else {}))
        payload, w, h = buf.getvalue(), img.width, img.height

    if board_bytes(board_id) + len(payload) > MAX_BOARD_BYTES:
        raise ImageError('pano görsel alanı dolu (500 MB) — eski görselleri silin')

    name = uuid.uuid4().hex + ALLOWED[fmt]
    d = board_dir(board_id, create=True)
    tmp = os.path.join(d, '.tmp-' + name)
    # Önce geçici ada yaz, sonra rename: yarım dosya asla kalıcı adı almasın
    # (istemci o adı öğeye yazdıktan sonra bozuk görsel servis edilirdi).
    with open(tmp, 'wb') as f:
        f.write(payload)
    final = os.path.join(d, name)
    os.replace(tmp, final)
    # GRUP YAZILABİLİR: dosyayı Flask (svc-agency) yazar, temizlik timer'ı
    # `proje sahibi` olarak koşar; ikisi de `appdev` grubunda ve `data/` setgid'li, ama
    # varsayılan umask (022) grubu salt-okur bırakır → janitor silemezdi.
    try:
        os.chmod(final, 0o664)
        os.chmod(d, 0o775)
    except OSError:
        pass                     # izin ayarı en-iyi-çaba; yükleme yine geçerli
    return {'name': name, 'width': w, 'height': h, 'size': len(payload)}


def path_of(board_id, name):
    """Servis edilecek dosyanın tam yolu; ad geçersiz/dosya yoksa None."""
    safe = safe_name(name)
    if not safe:
        return None
    p = os.path.join(board_dir(board_id), safe)
    return p if os.path.isfile(p) else None
