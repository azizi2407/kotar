"""Araç uçları (/api/tools) — image_splitter vb. Ekip araçları (yazma değil,
işlem). CSRF api ile paylaşımlı. Görsel işleme image_tools.py'de (test edilebilir).
"""
import base64
import io
import zipfile

from flask import Blueprint, jsonify, request

import image_tools
import img_bucket
from api import csrf_protect
from sso_client import current_user

bp = Blueprint('tools', __name__)
bp.before_request(csrf_protect)

TOOL_ROLES = {'management', 'designer', 'content_creator', 'videographer'}


def _require_management():
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='yetkiniz yok'), 403)
    return u, None


@bp.post('/image-split')
def image_split():
    u = current_user()
    if not u:
        return jsonify(error='oturum yok'), 401
    if u.get('role') not in TOOL_ROLES:
        return jsonify(error='yetkiniz yok'), 403
    f = request.files.get('image')
    if not f or not f.filename:
        return jsonify(error='görsel yok'), 400
    reels = request.form.get('mode') == 'reels'
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'jpg'
    try:
        parts = image_tools.split_wide_bytes(f.read(), ext, reels=reels)
    except image_tools.ImageToolError as e:
        return jsonify(error=str(e)), 400
    pieces = [{
        'name': name,
        'is_cover': reels and i == 1,  # orta parça = video kapağı
        'data_url': f'data:{mime};base64,' + base64.b64encode(data).decode(),
    } for i, (name, data, mime) in enumerate(parts)]
    # Tek tıkla hepsi: parçaları zip'le (STORED — görseller zaten sıkışık).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
        for name, data, _ in parts:
            zf.writestr(name, data)
    base = f.filename.rsplit('.', 1)[0] or 'gorsel'
    return jsonify(
        pieces=pieces,
        zip_name=f'{base}-parcalar.zip',
        zip_data_url='data:application/zip;base64,' + base64.b64encode(buf.getvalue()).decode())


# --- img-bucket (yönetici resim deposu, management-only) ---

@bp.get('/img-bucket/list')
def bucket_list():
    _, err = _require_management()
    if err:
        return err
    return jsonify(img_bucket.list_images())


@bp.post('/img-bucket/upload')
def bucket_upload():
    _, err = _require_management()
    if err:
        return err
    saved, errors = img_bucket.save_uploads(request.files.getlist('files'))
    return jsonify(saved=saved, errors=errors)


@bp.post('/img-bucket/delete')
def bucket_delete():
    _, err = _require_management()
    if err:
        return err
    name = (request.get_json(silent=True) or {}).get('name', '')
    ok, msg = img_bucket.delete_image(name)
    if not ok:
        return jsonify(error=msg), (404 if msg == 'dosya yok' else 400)
    return jsonify(ok=True)


@bp.post('/img-bucket/convert')
def bucket_convert():
    _, err = _require_management()
    if err:
        return err
    name = (request.get_json(silent=True) or {}).get('name', '')
    new_name, msg = img_bucket.convert_webp(name)
    if msg:
        return jsonify(error=msg), (404 if msg == 'dosya yok' else 400)
    return jsonify(ok=True, name=new_name, url=img_bucket.public_url(new_name))
