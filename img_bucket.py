"""img-bucket — admin image store (from the old monolith).

Admins upload images; they're written under `data/img-bucket/` and served via the
public `/img/<name>` URL (for embedding on external sites). No schema/migration
(filesystem); old images stay on the old server until cutover. The storage directory
is gitignored.
"""
import os

from flask import current_app
from PIL import Image
from werkzeug.utils import secure_filename

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
MAX_BYTES = 50 * 1024 * 1024            # 50 MB/file
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB folder


def bucket_dir():
    d = os.environ.get('IMG_BUCKET_DIR') or os.path.join(current_app.root_path, 'data', 'img-bucket')
    os.makedirs(d, exist_ok=True)
    return d


def ext_ok(name):
    return '.' in name and name.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def public_url(name):
    return current_app.config['AGENCY_BASE_URL'].rstrip('/') + '/img/' + name


def _unique(directory, filename):
    """On collision return name_1.jpg, name_2.jpg ..."""
    base, ext = os.path.splitext(filename)
    candidate, i = filename, 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f'{base}_{i}{ext}'
        i += 1
    return candidate


def _total_bytes(directory):
    total = 0
    for name in os.listdir(directory):
        p = os.path.join(directory, name)
        if os.path.isfile(p) and ext_ok(name):
            total += os.path.getsize(p)
    return total


def list_images():
    d = bucket_dir()
    files = []
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if not os.path.isfile(p) or not ext_ok(name):
            continue
        st = os.stat(p)
        files.append({'name': name, 'url': public_url(name),
                      'size': st.st_size, 'mtime': int(st.st_mtime)})
    files.sort(key=lambda f: f['mtime'], reverse=True)
    return {'files': files, 'usage': {'used': sum(f['size'] for f in files),
                                      'total': MAX_TOTAL_BYTES}}


def save_uploads(files):
    d = bucket_dir()
    saved, errors = [], []
    total = _total_bytes(d)
    for f in files:
        if not f or not f.filename:
            continue
        if not ext_ok(f.filename):
            errors.append(f'{f.filename}: tip izinli değil')
            continue
        safe = secure_filename(f.filename)
        if not safe or not ext_ok(safe):
            errors.append(f'{f.filename}: geçersiz ad')
            continue
        name = _unique(d, safe)
        path = os.path.join(d, name)
        f.save(path)
        size = os.path.getsize(path)
        if size > MAX_BYTES:
            os.remove(path)
            errors.append(f'{f.filename}: 50MB sınırını aştı')
            continue
        if total + size > MAX_TOTAL_BYTES:
            os.remove(path)
            errors.append(f'{f.filename}: 4GB klasör sınırı dolu')
            continue
        total += size
        saved.append({'name': name, 'url': public_url(name)})
    return saved, errors


def _valid_flat_name(name):
    return bool(name) and name == os.path.basename(name) and ext_ok(name)


def delete_image(name):
    if not _valid_flat_name(name):
        return False, 'geçersiz ad'
    path = os.path.join(bucket_dir(), name)
    if not os.path.isfile(path):
        return False, 'dosya yok'
    os.remove(path)
    return True, None


def convert_webp(name):
    """Convert the image to .webp at the same resolution, delete the original. (new_name, error)."""
    if not _valid_flat_name(name):
        return None, 'geçersiz ad'
    ext = name.rsplit('.', 1)[1].lower()
    if ext == 'webp':
        return None, 'zaten webp'
    if ext == 'svg':
        return None, 'svg dönüştürülemez (vektör)'
    d = bucket_dir()
    src = os.path.join(d, name)
    if not os.path.isfile(src):
        return None, 'dosya yok'
    new_name = _unique(d, os.path.splitext(name)[0] + '.webp')
    dst = os.path.join(d, new_name)
    try:
        img = Image.open(src)
        img.save(dst, 'WEBP', quality=90)
    except Exception as e:
        if os.path.exists(dst):
            os.remove(dst)
        return None, f'dönüştürülemedi: {e}'
    os.remove(src)
    return new_name, None
