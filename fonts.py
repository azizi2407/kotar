"""Font pool API (2026-08-05) — `/api/fonts`.

The pool is centralized: each font file is a single row, assigned to clients N:N.
Files live **on the server** (`data/fonts/<sha256>.<ext>`), not on Drive — the preview
page downloads every font to the browser, and a Drive proxy would slow down a
30-font page.

**Validation checks the SIGNATURE, not the extension.** The `/fonts/<id>/file`
endpoint serves the file to the browser with `as_attachment=False`; "anything named
.ttf" can't be accepted.

Authorization: read/download is open to the four production roles (same as the Brand
Guide gate — designer, content_creator and videographer also look at fonts), write
(upload/delete/assign) is **management + designer**.
"""
import hashlib
import io
import logging
import os
import re
import zipfile

from flask import Blueprint, jsonify, request, send_file

from api import csrf_protect
from extensions import db
from models import Client, UserRef, utcnow
from models_fonts import FONT_MIMES, Font, FontClient
from sso_client import current_user

log = logging.getLogger('agency.fonts')

bp = Blueprint('fonts', __name__)
bp.before_request(csrf_protect)

READ_ROLES = ('management', 'designer', 'content_creator', 'videographer')
WRITE_ROLES = ('management', 'designer')

MAX_FONT_BYTES = 10 * 1024 * 1024        # fonts are 100–500 KB; 10 MB is a generous ceiling
# Zip limits (2026-08-06): font sites give 1–5 MB archives; the ceilings guard
# against zip-bombs. Total extracted size is checked independently of compressed
# size — a 50 MB archive can extract to 5 GB.
ZIP_MAX_BYTES = 50 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 100 * 1024 * 1024
ZIP_MAX_ENTRIES = 200
STORE_DIR = os.environ.get('FONT_STORE_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'fonts')

# File signatures (first 4 bytes). TTF has two valid headers: version 1.0
# (0x00010000) and the old Apple 'true'; 'ttcf' is a collection file (contains
# multiple fonts, the browser still plays it).
_IMZALAR = (
    (b'wOF2', 'woff2'),
    (b'wOFF', 'woff'),
    (b'OTTO', 'otf'),
    (b'\x00\x01\x00\x00', 'ttf'),
    (b'true', 'ttf'),
    (b'ttcf', 'ttf'),
)

# "Montserrat-BoldItalic.ttf" → family "Montserrat", style "Bold Italic".
_STIL_SOZCUKLERI = ('Thin', 'ExtraLight', 'UltraLight', 'Light', 'Regular', 'Normal',
                    'Book', 'Medium', 'SemiBold', 'DemiBold', 'Bold', 'ExtraBold',
                    'UltraBold', 'Black', 'Heavy', 'Italic', 'Oblique')


def _format_of(data):
    """Format from the file signature; None if unrecognized."""
    for imza, fmt in _IMZALAR:
        if data[:4] == imza:
            return fmt
    return None


def _stilleri_ayikla(metin):
    """Extract style words from text. Matches are searched LONGEST TO SHORTEST, and
    the matched piece is removed from the text: otherwise "Bold" would also match
    inside "ExtraBold" and the style would end up "Bold ExtraBold". The search is
    case-SENSITIVE — font names are CamelCase and `re.I` breaks the boundaries in
    "BoldItalic"."""
    kalan, bulunan = metin, []
    for s in sorted(_STIL_SOZCUKLERI, key=len, reverse=True):
        if s in kalan:
            bulunan.append(s)
            kalan = kalan.replace(s, '', 1)
    return bulunan


def _tahmin(file_name):
    """Guess (family, style) from the file name. fontTools dependency is DELIBERATELY
    absent — the name can be corrected by the user (`PUT /fonts/<id>`)."""
    kok = re.sub(r'\.(ttf|otf|woff2?|ttc)$', '', file_name or '', flags=re.I)
    # Variable font axes: "Montserrat[wght].ttf", "Inter[opsz,wght].ttf" — the
    # square brackets aren't part of the family name, they're the axis list (2026-08-06).
    degisken = '[' in kok
    kok = re.sub(r'\[[^\]]*\]', '', kok).strip('-_ ')
    parcalar = [p for p in re.split(r'[-_\s]+', kok) if p]
    aile = parcalar[0] if parcalar else kok
    bulunan = _stilleri_ayikla(''.join(parcalar[1:]))
    # If there's no separator ("MontserratBold.ttf") the style is at the end of the stem.
    if not bulunan:
        for s in sorted(_STIL_SOZCUKLERI, key=len, reverse=True):
            if kok.endswith(s) and len(kok) > len(s):
                bulunan, aile = [s], kok[:-len(s)].strip('-_ ') or kok
                break
    # Keep the output order fixed ("Bold Italic", never "Italic Bold").
    stil = ' '.join(s for s in _STIL_SOZCUKLERI if s in bulunan)
    if degisken:
        # A single file carries all weights; calling it "Regular" would be misleading.
        stil = f'Variable {stil}'.strip()
    return (aile or 'Bilinmeyen'), (stil or 'Regular')


def _path_of(font):
    return os.path.join(STORE_DIR, f'{font.sha256}.{font.format}')


def _require(roles):
    u = current_user()
    if not u:
        return None, (jsonify(error='not authenticated'), 401)
    if u.get('role') not in roles:
        return None, (jsonify(error='you are not authorized for this action'), 403)
    return u, None


def _silebilir(u, font):
    """Can this user remove this font from the pool (2026-08-06, project owner).

    Management can remove any; **designer can remove ONLY what they uploaded**.
    Previously designer could delete any font — meaning the shared 230-font pool
    (imported from Google Fonts) could be wiped out with one click. The rule lives
    in ONE place: both the endpoint and the list's `can_delete` flag read from here,
    so the button never lies by diverging.
    """
    if not u:
        return False
    if u.get('role') == 'management':
        return True
    return u.get('role') == 'designer' and font.uploaded_by == u.get('sub')


def _client_map(font_ids):
    """{font_id: [{id, name}]} — ONE query (lazy access per font would be N+1)."""
    if not font_ids:
        return {}
    rows = (db.session.query(FontClient.font_id, Client.id, Client.name)
            .join(Client, Client.id == FontClient.client_id)
            .filter(FontClient.font_id.in_(font_ids), Client.deleted_at.is_(None))
            .all())
    out = {}
    for font_id, cid, name in rows:
        out.setdefault(font_id, []).append({'id': cid, 'name': name})
    return out


def _uploader_names():
    return {u.sub: (u.name or u.email) for u in UserRef.query.all()}


@bp.get('/fonts')
def fonts_list():
    """The entire pool; each font with its assigned clients. Sorted by family+style —
    the panel groups by family, doing the sort here means it doesn't need `sort` there."""
    _, err = _require(READ_ROLES)
    if err:
        return err
    u = current_user()
    rows = (Font.query.filter_by(deleted_at=None)
            .order_by(Font.family, Font.style, Font.id).all())
    cmap = _client_map([f.id for f in rows])
    names = _uploader_names()
    return jsonify(fonts=[f.to_dict(clients=cmap.get(f.id, []),
                                    uploader_name=names.get(f.uploaded_by),
                                    can_delete=_silebilir(u, f))
                          for f in rows])


@bp.get('/clients/<int:client_id>/fonts')
def client_fonts(client_id):
    """Fonts assigned to the client (the 'Related fonts' section on the client media page)."""
    _, err = _require(READ_ROLES)
    if err:
        return err
    rows = (Font.query.join(FontClient, FontClient.font_id == Font.id)
            .filter(FontClient.client_id == client_id, Font.deleted_at.is_(None))
            .order_by(Font.family, Font.style).all())
    names = _uploader_names()
    return jsonify(fonts=[f.to_dict(uploader_name=names.get(f.uploaded_by))
                          for f in rows])


def _ata(font, client_id, sub):
    """If the upload came from the client page, assignment happens in the same
    request (so the user isn't forced through the two-step 'upload → then assign'). Idempotent."""
    if not client_id or db.session.get(Client, client_id) is None:
        return
    if FontClient.query.filter_by(font_id=font.id, client_id=client_id).first() is None:
        db.session.add(FontClient(font_id=font.id, client_id=client_id, assigned_by=sub))


def _kaydet(data, file_name, sub, family=None, style=None):
    """Take a single font file into the pool. Returns `(font, error)` — error is text.

    The single-file and zip paths SHARE this function: if validation, dedup,
    disk write and revival rules were written separately in two places they'd
    drift apart over time. Does NOT COMMIT — the caller commits (once for a single
    file, once for all of a zip)."""
    if not data:
        return None, 'file is empty'
    if len(data) > MAX_FONT_BYTES:
        return None, 'font file exceeds the 10 MB limit'
    fmt = _format_of(data)
    if fmt is None:
        return None, 'not a valid font file (ttf, otf, woff, woff2)'

    sha = hashlib.sha256(data).hexdigest()
    mevcut = Font.query.filter_by(sha256=sha).order_by(Font.id.desc()).first()
    if mevcut is not None and mevcut.deleted_at is None:
        return mevcut, 'this font is already in the pool'

    os.makedirs(STORE_DIR, exist_ok=True)
    yol = os.path.join(STORE_DIR, f'{sha}.{fmt}')
    if not os.path.exists(yol):
        with open(yol, 'wb') as fh:
            fh.write(data)

    aile_t, stil_t = _tahmin(file_name)
    aile = (family or '').strip() or aile_t
    stil = (style or '').strip() or stil_t

    if mevcut is not None:                    # had been soft-deleted → revive it
        mevcut.deleted_at = None
        mevcut.family, mevcut.style = aile, stil
        mevcut.uploaded_by, mevcut.uploaded_at = sub, utcnow()
        font = mevcut
    else:
        font = Font(family=aile, style=stil, file_name=file_name, sha256=sha,
                    format=fmt, file_size=len(data), uploaded_by=sub,
                    uploaded_at=utcnow())
        db.session.add(font)
    db.session.flush()
    return font, None


def _zip_mi(data):
    return data[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')


def _zipten_fontlar(data):
    """Extract the font files inside a zip as `[(name, bytes), …]`; returns `(list, skipped)`.

    Font sites ship the zip together with a license PDF, a preview JPG and readme
    notes (example: Bigbelow.otf + Bigbelow.ttf + Bigbelow.jpg + More Info.txt +
    Read Me.pdf) — everything that isn't a font is skipped and **reported**, not
    silently swallowed.

    Security: path INFORMATION is never used (only the basename) → zip-slip is
    impossible; extracted total size and file count are limited → a zip bomb stops
    early. macOS's `__MACOSX/` and `._` AppleDouble entries don't even make it into
    the report (the user didn't put them there themselves, they'd just be noise in
    the 'skipped' list)."""
    fontlar, atlanan, toplam = [], [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist()[:ZIP_MAX_ENTRIES]:
            ad = os.path.basename(info.filename)
            if info.is_dir() or not ad or ad.startswith('._') \
                    or info.filename.startswith('__MACOSX/'):
                continue
            if info.file_size > MAX_FONT_BYTES:
                atlanan.append(f'{ad} (over 10 MB)')
                continue
            toplam += info.file_size
            if toplam > ZIP_MAX_TOTAL_BYTES:
                atlanan.append('… (archive exceeded the extracted size limit)')
                break
            icerik = z.read(info)
            if _format_of(icerik) is None:
                atlanan.append(ad)          # jpg / pdf / txt — expected case
                continue
            fontlar.append((ad, icerik))
    return fontlar, atlanan


@bp.post('/fonts')
def font_upload():
    """Upload a font (multipart: file, family?, style?, client_id?).

    `file` can be a **ZIP** (2026-08-06): the font files inside are taken, the rest
    (license PDF, preview image, readme note) is skipped and reported in the
    response as `skipped`. Zip response is `{fonts: [...], skipped: [...]}`, single
    file response is `{font: {...}}` — the panel handles both.

    Dedup by content hash: if the same file already exists as a non-deleted record,
    a single-file upload returns 409 + the existing record; in a zip that file is
    written to `skipped` and the rest is uploaded (having both a new and an existing
    font in one zip is normal). A soft-deleted record is REVIVED — the file already
    exists on disk."""
    u, err = _require(WRITE_ROLES)
    if err:
        return err
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify(error='file is required'), 400
    data = f.read()
    if not data:
        return jsonify(error='file is empty'), 400
    client_id = request.form.get('client_id', type=int)

    if _zip_mi(data):
        if len(data) > ZIP_MAX_BYTES:
            return jsonify(error='archive exceeds the 50 MB limit'), 413
        try:
            adaylar, atlanan = _zipten_fontlar(data)
        except (zipfile.BadZipFile, RuntimeError) as e:
            log.warning('zip okunamadı (%s): %s', f.filename, e)
            return jsonify(error='could not read the archive (corrupt or password protected)'), 400
        if not adaylar:
            return jsonify(error='no font file found in the archive (ttf, otf, woff, woff2)',
                           skipped=atlanan), 400
        eklenen = []
        for ad, icerik in adaylar:
            font, hata = _kaydet(icerik, ad, u['sub'])
            if hata:
                atlanan.append(f'{ad} ({hata})')
                continue
            _ata(font, client_id, u['sub'])
            eklenen.append(font)
        db.session.commit()
        cmap = _client_map([x.id for x in eklenen])
        return jsonify(fonts=[x.to_dict(clients=cmap.get(x.id, [])) for x in eklenen],
                       skipped=atlanan), 201

    font, hata = _kaydet(data, f.filename, u['sub'],
                         request.form.get('family'), request.form.get('style'))
    if hata == 'this font is already in the pool':
        cmap = _client_map([font.id])
        return jsonify(error=hata, font=font.to_dict(clients=cmap.get(font.id, []))), 409
    if hata:
        return jsonify(error=hata), (413 if '10 MB' in hata else 400)
    _ata(font, client_id, u['sub'])
    db.session.commit()
    cmap = _client_map([font.id])
    return jsonify(font=font.to_dict(clients=cmap.get(font.id, []))), 201


@bp.put('/fonts/<int:font_id>')
def font_update(font_id):
    """Correct the family/style name — needed often since the guess comes from the file name."""
    _, err = _require(WRITE_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font not found'), 404
    data = request.get_json(silent=True) or {}
    if 'family' in data:
        aile = (data.get('family') or '').strip()
        if not aile:
            return jsonify(error='family name cannot be empty'), 400
        font.family = aile[:160]
    if 'style' in data:
        font.style = ((data.get('style') or '').strip() or 'Regular')[:64]
    db.session.commit()
    cmap = _client_map([font.id])
    return jsonify(font=font.to_dict(clients=cmap.get(font.id, [])))


@bp.delete('/fonts/<int:font_id>')
def font_delete(font_id):
    """Remove from the pool (soft). The disk file STAYS: if the same hash is
    uploaded again, the record is revived and the download cost isn't repeated.
    Assignments also stay — if the font comes back, the client links should come
    back with it.

    Authorization via `_silebilir`: management can remove any, designer only what they uploaded."""
    u, err = _require(WRITE_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font not found'), 404
    if not _silebilir(u, font):
        return jsonify(error='you can only remove fonts you uploaded yourself'), 403
    font.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.post('/fonts/<int:font_id>/clients')
def font_assign(font_id):
    """{client_id, assigned} — assign to client / remove assignment. Idempotent."""
    u, err = _require(WRITE_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font not found'), 404
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    if not isinstance(client_id, int) or db.session.get(Client, client_id) is None:
        return jsonify(error='client not found'), 404

    row = FontClient.query.filter_by(font_id=font_id, client_id=client_id).first()
    if data.get('assigned', True):
        if row is None:
            db.session.add(FontClient(font_id=font_id, client_id=client_id,
                                      assigned_by=u['sub']))
    elif row is not None:
        db.session.delete(row)
    db.session.commit()
    cmap = _client_map([font_id])
    return jsonify(font=font.to_dict(clients=cmap.get(font_id, [])))


def _send(font, indir):
    yol = _path_of(font)
    if not os.path.exists(yol):
        log.warning('font dosyası diskte yok: %s', yol)
        return jsonify(error='font file not found on server'), 404
    resp = send_file(yol, mimetype=FONT_MIMES.get(font.format, 'application/octet-stream'),
                     as_attachment=indir, download_name=font.file_name,
                     conditional=True)
    # So the panel preview doesn't re-request the same font on every redraw. `private`:
    # the file comes from an authenticated endpoint, must not be cached in a shared proxy.
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@bp.get('/fonts/<int:font_id>/file')
def font_file(font_id):
    """`@font-face` source — INLINE (as_attachment=False). Because of this endpoint,
    signature validation is mandatory on the upload side."""
    _, err = _require(READ_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font not found'), 404
    return _send(font, indir=False)


@bp.get('/fonts/<int:font_id>/download')
def font_download(font_id):
    _, err = _require(READ_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font not found'), 404
    return _send(font, indir=True)
