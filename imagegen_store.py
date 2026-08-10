"""Codex çıktısının doğrulanması ve kalıcı lokal deposu.

Neden Drive değil: bu hattın çıktısı panel içi bir taslaktır, ekip indirip kullanır;
her üretimde Drive OAuth turu gecikme ve kota demek (kota zaten %85 dolu — bkz.
planning_images.py'deki aynı gerekçe). Neden `img_bucket` değil: orası `/img/<ad>`
ile PUBLIC servis eder; müşteri marka görselleri public URL'e konamaz. Neden
`media_store` değil: orası 21 günlük önbellek (Drive kanonik), burada kanonik kopya
bu dosyanın kendisidir.

Yerleşim: `data/codex-images/<client_id>/<uuid>.png` — dizinin müşteri başına
ayrılması yetkilendirmeyi bedavaya getirir (servis ucu rol kapısından geçer,
dosya adı tahmin edilse bile yol müşteri kimliğini taşır).
"""
import hashlib
import io
import os
import uuid

from flask import current_app
from PIL import Image, UnidentifiedImageError

MIN_BYTES = 1024                    # 1 KB altı: yarım/boş dosya
MAX_BYTES = 25 * 1024 * 1024        # 25 MB üstü: üretim değil, kaza
NAME_LEN = 32                       # uuid4().hex


class OutputError(Exception):
    """Çıktı doğrulama hatası — iş `error_code='invalid_output'` ile kapanır."""


def root_dir(create=False):
    d = (os.environ.get('CODEX_IMAGE_DIR')
         or os.path.join(current_app.root_path, 'data', 'codex-images'))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def validate(path, workdir):
    """Codex'in ürettiği dosyayı doğrula → `{width, height, bytes, sha256, mime}`.

    Codex'in "başarılı" demesine GÜVENİLMEZ (spec §7). Sıra önemli: yol → varlık →
    boyut → içerik. Yol kontrolü `realpath` ile yapılır; iş dizini içinde duran ama
    dışarıyı gösteren bir symlink aksi halde /etc/passwd'i "çıktı" diye geçirirdi."""
    real = os.path.realpath(path)
    root = os.path.realpath(workdir)
    if not (real == root or real.startswith(root + os.sep)):
        raise OutputError('çıktı iş dizininin dışında')
    if not os.path.isfile(real):
        raise OutputError('çıktı dosyası oluşmadı')
    size = os.path.getsize(real)
    if size < MIN_BYTES:
        raise OutputError('çıktı dosyası boş ya da yarım')
    if size > MAX_BYTES:
        raise OutputError(f'çıktı {MAX_BYTES // (1024 * 1024)} MB sınırını aşıyor')
    with open(real, 'rb') as f:
        data = f.read()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise OutputError('çıktı bir görsel değil ya da bozuk') from e
    if (img.format or '').upper() != 'PNG':
        raise OutputError(f'beklenmeyen görsel türü: {img.format or "bilinmiyor"}')
    return {'width': img.width, 'height': img.height, 'bytes': size,
            'sha256': hashlib.sha256(data).hexdigest(), 'mime': 'image/png'}


def store(client_id, src_path):
    """Doğrulanmış dosyayı kalıcı depoya taşı → depoya GÖRELİ yol.

    PIL ile yeniden kaydedilir: metadata (EXIF/yorum/tEXt) taşınmaz. Maliyeti düşük,
    garantisi net — üretim aracının gömdüğü hiçbir alan diske geçmez."""
    d = os.path.join(root_dir(create=True), str(client_id))
    os.makedirs(d, exist_ok=True)
    name = uuid.uuid4().hex + '.png'
    final = os.path.join(d, name)
    tmp = os.path.join(d, '.tmp-' + name)
    with Image.open(src_path) as img:
        img.load()
        # Yeni bir tuvale kopyalamak `info` sözlüğünü (EXIF/tEXt/yorum) arkada bırakır;
        # `img.copy()` onları taşırdı. `getdata` KULLANILMAZ — Pillow 14'te kalkıyor.
        temiz = Image.new(img.mode, img.size)
        temiz.paste(img)
        temiz.save(tmp, format='PNG')
    # Önce geçici ada yaz, sonra rename: yarım dosya asla kalıcı adı almasın.
    os.replace(tmp, final)
    # GRUP YAZILABİLİR: dosyayı worker (proje sahibi) yazar, Flask (svc-agency) okur/siler;
    # ikisi de `appdev` grubunda ama varsayılan umask (022) grubu salt-okur bırakır.
    try:
        os.chmod(final, 0o664)
        os.chmod(d, 0o775)
    except OSError:
        pass                        # izin ayarı en-iyi-çaba; dosya yine geçerli
    os.remove(src_path)             # efemer kopya bırakma
    return f'{client_id}/{name}'


def abs_path(rel):
    """Göreli yolu servis edilecek mutlak yola çevir; geçersiz/yoksa None.

    `rel` DB'den gelir, ama yine de doğrulanır: bozuk ya da elle düzenlenmiş bir
    satır dosya sistemi gezintisine dönüşmemeli."""
    if not isinstance(rel, str) or '..' in rel or rel.startswith('/'):
        return None
    parts = rel.split('/')
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    stem, ext = os.path.splitext(parts[1])
    if ext != '.png' or len(stem) != NAME_LEN or not all(c in '0123456789abcdef' for c in stem):
        return None
    p = os.path.join(root_dir(), rel)
    return p if os.path.isfile(p) else None
