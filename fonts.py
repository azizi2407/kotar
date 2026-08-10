"""Font havuzu API'si (2026-08-05) — `/api/fonts`.

Havuz merkezî: her font dosyası tek satır, müşterilere N:N atanır. Dosyalar
**sunucuda** (`data/fonts/<sha256>.<ext>`), Drive'a gitmez — önizleme sayfası her
fontu tarayıcıya indiriyor ve Drive proxy'si 30 fontluk bir sayfayı yavaşlatırdı.

**Doğrulama uzantıya değil İMZAYA bakar.** `/fonts/<id>/file` ucu dosyayı tarayıcıya
`as_attachment=False` ile veriyor; "adı .ttf olan her şey" kabul edilemez.

Yetki: okuma/indirme dört üretim rolü (Marka Rehberi kapısıyla aynı — tasarımcı,
içerikçi ve videograf da fonta bakar), yazma (yükle/sil/ata) **management + designer**.
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

MAX_FONT_BYTES = 10 * 1024 * 1024        # fontlar 100–500 KB; 10 MB bol bir tavan
# Zip sınırları (2026-08-06): font siteleri 1–5 MB arşiv veriyor; tavanlar
# zip-bomb'a karşı. Açılmış toplam boyut sıkıştırılmış boyuttan bağımsız kontrol
# edilir — 50 MB'lık bir arşiv 5 GB'a açılabilir.
ZIP_MAX_BYTES = 50 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 100 * 1024 * 1024
ZIP_MAX_ENTRIES = 200
STORE_DIR = os.environ.get('FONT_STORE_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'fonts')

# Dosya imzaları (ilk 4 bayt). TTF'nin iki geçerli başlangıcı var: sürüm 1.0
# (0x00010000) ve eski Apple 'true'; 'ttcf' koleksiyon dosyasıdır (birden çok
# font içerir, tarayıcı yine oynatır).
_IMZALAR = (
    (b'wOF2', 'woff2'),
    (b'wOFF', 'woff'),
    (b'OTTO', 'otf'),
    (b'\x00\x01\x00\x00', 'ttf'),
    (b'true', 'ttf'),
    (b'ttcf', 'ttf'),
)

# "Montserrat-BoldItalic.ttf" → aile "Montserrat", stil "Bold Italic".
_STIL_SOZCUKLERI = ('Thin', 'ExtraLight', 'UltraLight', 'Light', 'Regular', 'Normal',
                    'Book', 'Medium', 'SemiBold', 'DemiBold', 'Bold', 'ExtraBold',
                    'UltraBold', 'Black', 'Heavy', 'Italic', 'Oblique')


def _format_of(data):
    """Dosya imzasından format; tanınmazsa None."""
    for imza, fmt in _IMZALAR:
        if data[:4] == imza:
            return fmt
    return None


def _stilleri_ayikla(metin):
    """Metinden stil sözcüklerini çıkar. Eşleşenler UZUNDAN KISAYA aranır ve
    bulunan parça metinden düşülür: aksi halde "ExtraBold" içinde "Bold" da
    eşleşir ve stil "Bold ExtraBold" olurdu. Arama case-SENSITIVE — font adları
    CamelCase ve `re.I` "BoldItalic"teki sınırları bozuyor."""
    kalan, bulunan = metin, []
    for s in sorted(_STIL_SOZCUKLERI, key=len, reverse=True):
        if s in kalan:
            bulunan.append(s)
            kalan = kalan.replace(s, '', 1)
    return bulunan


def _tahmin(file_name):
    """Dosya adından (aile, stil) tahmini. fontTools bağımlılığı BİLEREK yok —
    isim kullanıcı tarafından düzeltilebiliyor (`PUT /fonts/<id>`)."""
    kok = re.sub(r'\.(ttf|otf|woff2?|ttc)$', '', file_name or '', flags=re.I)
    # Variable font eksenleri: "Montserrat[wght].ttf", "Inter[opsz,wght].ttf" —
    # köşeli parantez aile adının parçası değil, eksen listesi (2026-08-06).
    degisken = '[' in kok
    kok = re.sub(r'\[[^\]]*\]', '', kok).strip('-_ ')
    parcalar = [p for p in re.split(r'[-_\s]+', kok) if p]
    aile = parcalar[0] if parcalar else kok
    bulunan = _stilleri_ayikla(''.join(parcalar[1:]))
    # Ayırıcı yoksa ("MontserratBold.ttf") stil gövdenin sonundadır.
    if not bulunan:
        for s in sorted(_STIL_SOZCUKLERI, key=len, reverse=True):
            if kok.endswith(s) and len(kok) > len(s):
                bulunan, aile = [s], kok[:-len(s)].strip('-_ ') or kok
                break
    # Çıktı sırası sabit olsun ("Bold Italic", hiçbir zaman "Italic Bold").
    stil = ' '.join(s for s in _STIL_SOZCUKLERI if s in bulunan)
    if degisken:
        # Tek dosya tüm ağırlıkları taşıyor; "Regular" demek yanıltıcı olurdu.
        stil = f'Variable {stil}'.strip()
    return (aile or 'Bilinmeyen'), (stil or 'Regular')


def _path_of(font):
    return os.path.join(STORE_DIR, f'{font.sha256}.{font.format}')


def _require(roles):
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') not in roles:
        return None, (jsonify(error='bu işlem için yetkiniz yok'), 403)
    return u, None


def _silebilir(u, font):
    """Bu kullanıcı bu fontu havuzdan kaldırabilir mi (2026-08-06, proje sahibi).

    Yönetim ayrımsız; **tasarımcı YALNIZ kendi yüklediğini**. Önceden designer da
    her fontu silebiliyordu — 230 fontluk ortak havuzun (Google Fonts içe aktarımı)
    tek tıkla boşaltılabilmesi demekti. Kural TEK yerde: hem uç hem listedeki
    `can_delete` bayrağı buradan okur, ayrışırsa düğme yalan söylerdi.
    """
    if not u:
        return False
    if u.get('role') == 'management':
        return True
    return u.get('role') == 'designer' and font.uploaded_by == u.get('sub')


def _client_map(font_ids):
    """{font_id: [{id, name}]} — TEK sorgu (font başına lazy erişim N+1 olurdu)."""
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
    """Havuzun tamamı; her fontta atandığı müşteriler. Aile+stil sıralı — panel
    aileye göre grupluyor, sıralamayı burada yapmak orada `sort` gerektirmez."""
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
    """Müşteriye atanan fontlar (müşteri medya sayfasının 'İlgili fontlar' bölümü)."""
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
    """Yükleme müşteri sayfasından geldiyse atama aynı istekte yapılır (kullanıcı
    'yükle → sonra ata' iki adımına zorlanmasın). Idempotent."""
    if not client_id or db.session.get(Client, client_id) is None:
        return
    if FontClient.query.filter_by(font_id=font.id, client_id=client_id).first() is None:
        db.session.add(FontClient(font_id=font.id, client_id=client_id, assigned_by=sub))


def _kaydet(data, file_name, sub, family=None, style=None):
    """Tek font dosyasını havuza al. `(font, hata)` döner — hata bir metin.

    Tek dosya ve zip yolları BU fonksiyonu paylaşır: doğrulama, dedup, diske yazma
    ve canlandırma kuralları iki yerde ayrı yazılsaydı zamanla ayrışırdı.
    COMMIT ETMEZ — çağıran (tek dosyada bir, zipte hepsi için bir) commit atar."""
    if not data:
        return None, 'dosya boş'
    if len(data) > MAX_FONT_BYTES:
        return None, 'font dosyası 10 MB sınırını aşıyor'
    fmt = _format_of(data)
    if fmt is None:
        return None, 'geçerli bir font dosyası değil (ttf, otf, woff, woff2)'

    sha = hashlib.sha256(data).hexdigest()
    mevcut = Font.query.filter_by(sha256=sha).order_by(Font.id.desc()).first()
    if mevcut is not None and mevcut.deleted_at is None:
        return mevcut, 'bu font zaten havuzda'

    os.makedirs(STORE_DIR, exist_ok=True)
    yol = os.path.join(STORE_DIR, f'{sha}.{fmt}')
    if not os.path.exists(yol):
        with open(yol, 'wb') as fh:
            fh.write(data)

    aile_t, stil_t = _tahmin(file_name)
    aile = (family or '').strip() or aile_t
    stil = (style or '').strip() or stil_t

    if mevcut is not None:                    # soft-delete edilmişti → canlandır
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
    """Zip içindeki font dosyalarını `[(ad, bytes), …]` olarak çıkar; `(liste, atlanan)`.

    Font siteleri zip'i lisans PDF'i, önizleme JPG'si ve okuma notlarıyla birlikte
    veriyor (örnek: Bigbelow.otf + Bigbelow.ttf + Bigbelow.jpg + More Info.txt +
    Read Me.pdf) — font olmayan her şey atlanır ve **raporlanır**, sessizce yutulmaz.

    Güvenlik: yol BİLGİSİ kullanılmaz (yalnız basename) → zip-slip imkânsız; açılan
    toplam boyut ve dosya sayısı sınırlı → zip bomb erken durur. macOS'un
    `__MACOSX/` ve `._` AppleDouble kayıtları rapora bile girmez (kullanıcı onları
    kendisi koymadı, 'atlandı' listesinde gürültü yaparlar)."""
    fontlar, atlanan, toplam = [], [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist()[:ZIP_MAX_ENTRIES]:
            ad = os.path.basename(info.filename)
            if info.is_dir() or not ad or ad.startswith('._') \
                    or info.filename.startswith('__MACOSX/'):
                continue
            if info.file_size > MAX_FONT_BYTES:
                atlanan.append(f'{ad} (10 MB üstü)')
                continue
            toplam += info.file_size
            if toplam > ZIP_MAX_TOTAL_BYTES:
                atlanan.append('… (arşiv açılmış boyut sınırını aştı)')
                break
            icerik = z.read(info)
            if _format_of(icerik) is None:
                atlanan.append(ad)          # jpg / pdf / txt — beklenen durum
                continue
            fontlar.append((ad, icerik))
    return fontlar, atlanan


@bp.post('/fonts')
def font_upload():
    """Font yükle (multipart: file, family?, style?, client_id?).

    `file` bir **ZIP** olabilir (2026-08-06): içindeki font dosyaları alınır, geri
    kalanı (lisans PDF'i, önizleme görseli, okuma notu) atlanır ve yanıtta
    `skipped` ile raporlanır. Zip yanıtı `{fonts: [...], skipped: [...]}`, tek
    dosya yanıtı `{font: {...}}` — panel ikisini de işler.

    Dedup içerik hash'iyle: aynı dosya silinmemiş bir kayıtta duruyorsa tek dosyada
    409 + mevcut kayıt döner; zipte o dosya `skipped`'a yazılır ve kalanı yüklenir
    (bir zipte hem yeni hem eski font olması normal). Soft-delete edilmiş kayıt
    CANLANDIRILIR — dosya diskte zaten var."""
    u, err = _require(WRITE_ROLES)
    if err:
        return err
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify(error='file zorunlu'), 400
    data = f.read()
    if not data:
        return jsonify(error='dosya boş'), 400
    client_id = request.form.get('client_id', type=int)

    if _zip_mi(data):
        if len(data) > ZIP_MAX_BYTES:
            return jsonify(error='arşiv 50 MB sınırını aşıyor'), 413
        try:
            adaylar, atlanan = _zipten_fontlar(data)
        except (zipfile.BadZipFile, RuntimeError) as e:
            log.warning('zip okunamadı (%s): %s', f.filename, e)
            return jsonify(error='arşiv okunamadı (bozuk veya parola korumalı)'), 400
        if not adaylar:
            return jsonify(error='arşivde font dosyası bulunamadı (ttf, otf, woff, woff2)',
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
    if hata == 'bu font zaten havuzda':
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
    """Aile/stil adını düzelt — tahmin dosya adından geldiği için sık gerekir."""
    _, err = _require(WRITE_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font bulunamadı'), 404
    data = request.get_json(silent=True) or {}
    if 'family' in data:
        aile = (data.get('family') or '').strip()
        if not aile:
            return jsonify(error='aile adı boş olamaz'), 400
        font.family = aile[:160]
    if 'style' in data:
        font.style = ((data.get('style') or '').strip() or 'Regular')[:64]
    db.session.commit()
    cmap = _client_map([font.id])
    return jsonify(font=font.to_dict(clients=cmap.get(font.id, [])))


@bp.delete('/fonts/<int:font_id>')
def font_delete(font_id):
    """Havuzdan kaldır (soft). Disk dosyası KALIR: aynı hash yeniden yüklenirse
    kayıt canlandırılıyor ve indirme masrafı tekrarlanmıyor. Atamalar da kalır —
    font geri gelirse müşteri bağları da geri gelsin.

    Yetki `_silebilir`: yönetim ayrımsız, tasarımcı yalnız kendi yüklediğini."""
    u, err = _require(WRITE_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font bulunamadı'), 404
    if not _silebilir(u, font):
        return jsonify(error='yalnız kendi yüklediğiniz fontu kaldırabilirsiniz'), 403
    font.deleted_at = utcnow()
    db.session.commit()
    return jsonify(ok=True)


@bp.post('/fonts/<int:font_id>/clients')
def font_assign(font_id):
    """{client_id, assigned} — müşteriye ata / atamayı kaldır. Idempotent."""
    u, err = _require(WRITE_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font bulunamadı'), 404
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    if not isinstance(client_id, int) or db.session.get(Client, client_id) is None:
        return jsonify(error='müşteri bulunamadı'), 404

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
        return jsonify(error='font dosyası sunucuda bulunamadı'), 404
    resp = send_file(yol, mimetype=FONT_MIMES.get(font.format, 'application/octet-stream'),
                     as_attachment=indir, download_name=font.file_name,
                     conditional=True)
    # Panel önizlemesi aynı fontu her yeniden çizimde istemesin. `private`: dosya
    # oturumlu uçtan geliyor, paylaşımlı vekilde saklanmamalı.
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@bp.get('/fonts/<int:font_id>/file')
def font_file(font_id):
    """`@font-face` kaynağı — INLINE (as_attachment=False). Bu uç yüzünden yükleme
    tarafında imza doğrulaması zorunlu."""
    _, err = _require(READ_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font bulunamadı'), 404
    return _send(font, indir=False)


@bp.get('/fonts/<int:font_id>/download')
def font_download(font_id):
    _, err = _require(READ_ROLES)
    if err:
        return err
    font = Font.query.filter_by(id=font_id, deleted_at=None).first()
    if font is None:
        return jsonify(error='font bulunamadı'), 404
    return _send(font, indir=True)
