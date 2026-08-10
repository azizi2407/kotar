"""Aylık rapor API'si (/api/reports) + public rapor sayfası (/rapor/<token>).

Hesaplama `aylik_rapor.py`'de (proje sahibi'in masaüstü aracından taşınan katman); burada
yalnız web tarafı var: yükleme → geçici dizin → üretim → DB, ve görüntüleme uçları.

**Yükleme neden diske yazılıyor:** taşınan katmanın tamamı dosya YOLU ile çalışıyor
(`auto_match_csv_files(klasör)`, `preflight_bulk(ana_klasör)`). Bellekte tutup
imzaları değiştirmek, aylardır gerçek veriyle sınanmış eşleştirme mantığına
dokunmak demekti. Yüklenenler `tempfile.TemporaryDirectory` içinde kalır ve istek
biter bitmez silinir — kalıcı olarak saklanan tek şey hesaplanmış rapor verisidir.
"""
import base64
import logging
import os
import re
import secrets
import subprocess
import tempfile

from flask import Blueprint, Response, abort, jsonify, request, send_file

import aylik_rapor as ar
import ratelimit
from api import csrf_protect
from extensions import db
from models import Client, utcnow
from models_reports import MonthlyReport
from sso_client import current_user

bp = Blueprint('reports', __name__)
bp.before_request(csrf_protect)

log = logging.getLogger(__name__)

# Yükleme sınırları. CSV'ler pratikte 5–500 KB; tavanlar kaza/kötüye kullanım için.
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 600                      # 60 müşteri × ~9 CSV
PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

_LOGO_DATA_URI = None


def _logo_data_uri():
    """Logoyu base64 data URI olarak döndür (bir kez okunur, süreç ömrü boyunca).

    Şablon `src="logo.png"` diyor. Ayrı bir dosya olarak servis etmek yerine gömmek,
    üretilen HTML'i TEK DOSYA yapıyor: aynı çıktı panelde de, public linkte de,
    Chromium'un PDF'e bastığı `file://` sayfasında da eksiksiz görünüyor."""
    global _LOGO_DATA_URI
    if _LOGO_DATA_URI is None:
        try:
            with open(ar._LOGO_PATH, 'rb') as f:
                _LOGO_DATA_URI = 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
        except OSError:
            _LOGO_DATA_URI = ''      # logo yoksa rapor yine üretilsin
    return _LOGO_DATA_URI


def _require_management():
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='yetkiniz yok'), 403)
    return u, None


def _guvenli_ad(ad):
    """Yüklenen yol parçasını tek bir güvenli dosya/klasör adına indirger.

    Yalnız `basename` alınır ve ayraç/gizli-dosya kalıpları temizlenir → yüklenen
    `webkitRelativePath` ne olursa olsun geçici dizinin dışına yazılamaz
    (zip-slip'in multipart karşılığı)."""
    ad = os.path.basename((ad or '').replace('\\', '/').strip())
    ad = ad.replace('\x00', '').lstrip('.')
    return ad[:120]


def _yukle_ve_dagit(kok):
    """İsteğin dosyalarını `kok/<müşteri>/<dosya>.csv` olarak yaz; müşteri adlarını döndür.

    Müşteri adı `paths[i]` (tarayıcının `webkitRelativePath`'i) içindeki İLK klasör
    adından gelir — masaüstü aracının "her alt klasör bir müşteri" davranışının
    birebir karşılığı. Klasör bilgisi yoksa (kullanıcı dosyaları tek tek seçtiyse)
    `client_name` alanı kullanılır: tek müşterilik akış.
    """
    dosyalar = request.files.getlist('files')
    yollar = request.form.getlist('paths')
    tekil_ad = _guvenli_ad(request.form.get('client_name') or '') or 'Rapor'
    if not dosyalar:
        return None, 'dosya yüklenmedi'
    if len(dosyalar) > MAX_FILES:
        return None, f'en fazla {MAX_FILES} dosya yüklenebilir'

    toplam = 0
    yazilan = 0
    for i, f in enumerate(dosyalar):
        ad = _guvenli_ad(f.filename)
        if not ad.lower().endswith('.csv'):
            continue                                  # CSV dışı sessizce atlanır
        rel = yollar[i] if i < len(yollar) else ''
        parcalar = [p for p in (rel or '').replace('\\', '/').split('/') if p not in ('', '.', '..')]
        # webkitRelativePath: "<seçilen klasör>/<müşteri>/<dosya>" ya da "<müşteri>/<dosya>"
        musteri = _guvenli_ad(parcalar[-2]) if len(parcalar) >= 2 else tekil_ad
        musteri = musteri or tekil_ad
        hedef_dir = os.path.join(kok, musteri)
        os.makedirs(hedef_dir, exist_ok=True)
        veri = f.read(MAX_FILE_BYTES + 1)
        if len(veri) > MAX_FILE_BYTES:
            return None, f'{ad}: dosya çok büyük (>10 MB)'
        toplam += len(veri)
        if toplam > MAX_TOTAL_BYTES:
            return None, 'toplam yükleme boyutu sınırı aşıldı'
        with open(os.path.join(hedef_dir, ad), 'wb') as out:
            out.write(veri)
        yazilan += 1

    if not yazilan:
        return None, 'CSV dosyası bulunamadı'
    return sorted(os.listdir(kok)), None


def _client_eslestir(ad):
    """Rapor adını panel müşterisiyle eşleştir (Türkçe-duyarlı, gevşek).

    Eşleşme ZORUNLU DEĞİL: tutmazsa rapor `client_id` olmadan kaydedilir. Amaç
    sonradan müşteri sayfasından raporlara erişebilmek, üretimi engellemek değil."""
    hedef = ar._normalize(ad or '')
    if not hedef:
        return None
    for c in Client.query.all():
        if ar._normalize(c.name) == hedef:
            return c.id
    return None


@bp.post('/generate')
def generate():
    """CSV'leri al, rapor(lar) üret ve kaydet. Tek müşteri de toplu da aynı yol."""
    u, err = _require_management()
    if err:
        return err
    period = (request.form.get('period') or '').strip()
    if not PERIOD_RE.match(period):
        return jsonify(error='geçerli bir dönem seçin (YYYY-AA)'), 400

    with tempfile.TemporaryDirectory(prefix='rapor-') as kok:
        musteriler, hata = _yukle_ve_dagit(kok)
        if hata:
            return jsonify(error=hata), 400
        # `preflight_bulk` her müşteri klasörünü tarar, eşleştirir, veriyi üretir ve
        # üretemediklerinin SEBEBİNİ döndürür — sessizce atlamak, eksik CSV'yi fark
        # etmeden "raporu aldım" sanmaya yol açardı.
        try:
            sonuclar = ar.preflight_bulk(kok)
        except Exception as e:                        # noqa: BLE001
            log.exception('rapor üretimi çöktü')
            return jsonify(error=f'rapor üretilemedi: {type(e).__name__}'), 500

        uretilen, atlanan = [], []
        for s in sonuclar:
            if s.get('skip') or not s.get('data'):
                atlanan.append({'client_name': s['name'],
                                'reason': s.get('skip') or 'veri üretilemedi',
                                'missing': s.get('missing', [])})
                continue
            cid = _client_eslestir(s['name'])
            # Aynı (müşteri, dönem) tekrar üretilirse ÜZERİNE yazılır: kullanıcı
            # eksik CSV'yi tamamlayıp yeniden yüklediğinde iki çelişkili rapor
            # kalmamalı. Paylaşım token'ı korunur — dağıtılmış link kırılmasın.
            mevcut = MonthlyReport.query.filter_by(client_name=s['name'], period=period).first()
            rapor = mevcut or MonthlyReport(client_name=s['name'], period=period)
            rapor.client_id = cid
            rapor.data = s['data']
            rapor.warnings = s.get('warnings') or []
            rapor.created_by = u['sub']
            rapor.created_at = utcnow()
            if mevcut is None:
                db.session.add(rapor)
            uretilen.append(rapor)
        db.session.commit()

    return jsonify(reports=[r.to_dict(ozet=True) for r in uretilen],
                   skipped=atlanan, found_clients=musteriler)


@bp.get('')
def reports_list():
    u, err = _require_management()
    if err:
        return err
    q = MonthlyReport.query
    if request.args.get('period'):
        q = q.filter_by(period=request.args['period'])
    if request.args.get('client_id'):
        try:
            q = q.filter_by(client_id=int(request.args['client_id']))
        except ValueError:
            return jsonify(error='client_id sayı olmalı'), 400
    rows = q.order_by(MonthlyReport.period.desc(), MonthlyReport.client_name).all()
    return jsonify(reports=[r.to_dict(ozet=True) for r in rows])


@bp.get('/periods')
def periods():
    """Rapor bulunan dönemler (filtre açılırı)."""
    _, err = _require_management()
    if err:
        return err
    rows = (db.session.query(MonthlyReport.period, db.func.count(MonthlyReport.id))
            .group_by(MonthlyReport.period)
            .order_by(MonthlyReport.period.desc()).all())
    return jsonify(periods=[{'period': p, 'count': n} for p, n in rows])


@bp.get('/<int:report_id>')
def report_get(report_id):
    _, err = _require_management()
    if err:
        return err
    r = db.session.get(MonthlyReport, report_id)
    if r is None:
        return jsonify(error='rapor bulunamadı'), 404
    return jsonify(report=r.to_dict())


def _rapor_html(rapor):
    """Rapor verisinden tek dosyalık HTML üret (şablon + veri + gömülü logo).

    Saklanmaz, her istekte üretilir: şablon iyileştirildiğinde eski raporlar da
    yeni görünümü alır ve kayıtlar 50 KB'lık HTML kopyalarıyla şişmez."""
    with tempfile.TemporaryDirectory(prefix='raporhtml-') as d:
        yol = os.path.join(d, 'rapor.html')
        veri = dict(rapor.data or {})
        veri.setdefault('client_name', rapor.client_name)
        # export_to_html tuple listesi bekliyor (masaüstü tarafında öyle üretiliyor);
        # JSONB'den dönerken listeler [başlık, değer] hâline gelmiş olur.
        for anahtar in ('top_posts', 'top_stories'):
            veri[anahtar] = [tuple(x) if isinstance(x, (list, tuple)) else x
                             for x in (veri.get(anahtar) or [])]
        ar.export_to_html(veri, yol)
        with open(yol, encoding='utf-8') as f:
            html = f.read()
    return html.replace('src="logo.png"', f'src="{_logo_data_uri()}"')


@bp.get('/<int:report_id>/html')
def report_html(report_id):
    _, err = _require_management()
    if err:
        return err
    r = db.session.get(MonthlyReport, report_id)
    if r is None:
        abort(404)
    return Response(_rapor_html(r), mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow'})


def _pdf_uret(rapor):
    """HTML'i Chromium ile PDF'e bas. Dosya yolunu döndürür (çağıran temizler)."""
    d = tempfile.mkdtemp(prefix='raporpdf-')
    html_yol = os.path.join(d, 'rapor.html')
    pdf_yol = os.path.join(d, 'rapor.pdf')
    with open(html_yol, 'w', encoding='utf-8') as f:
        f.write(_rapor_html(rapor))
    ar.export_to_pdf(html_yol, pdf_yol)
    return d, pdf_yol


def _pdf_adi(rapor):
    ad = re.sub(r'[^\w\s.-]', '', rapor.client_name, flags=re.UNICODE).strip() or 'rapor'
    return f'{ad} - {rapor.period} Aylik Rapor.pdf'


@bp.get('/<int:report_id>/pdf')
def report_pdf(report_id):
    _, err = _require_management()
    if err:
        return err
    r = db.session.get(MonthlyReport, report_id)
    if r is None:
        abort(404)
    import shutil
    try:
        d, pdf_yol = _pdf_uret(r)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        log.warning('PDF üretilemedi (rapor %s): %s', report_id, e)
        return jsonify(error='PDF üretilemedi'), 502
    try:
        with open(pdf_yol, 'rb') as f:
            veri = f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return Response(veri, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename*=UTF-8\'\'{_pdf_adi(r).replace(" ", "%20")}'})


@bp.post('/<int:report_id>/share')
def report_share(report_id):
    """Public link üret / iptal et. `{shared: bool}`."""
    _, err = _require_management()
    if err:
        return err
    r = db.session.get(MonthlyReport, report_id)
    if r is None:
        return jsonify(error='rapor bulunamadı'), 404
    paylas = bool((request.get_json(silent=True) or {}).get('shared', True))
    if paylas:
        # Token BİR KEZ üretilir: iptal edip yeniden açınca aynı adres dönsün diye
        # değil — tersine, iptal `revoked` ile yapılır ve yeniden açmak eski linki
        # canlandırır. Müşteriye gönderilen adresi her seferinde değiştirmek,
        # "linkin çalışmıyor mu?" yazışmasına yol açardı.
        if not r.token:
            r.token = secrets.token_urlsafe(32)
        r.revoked = False
    else:
        r.revoked = True
    db.session.commit()
    return jsonify(report=r.to_dict(ozet=True))


@bp.delete('/<int:report_id>')
def report_delete(report_id):
    _, err = _require_management()
    if err:
        return err
    r = db.session.get(MonthlyReport, report_id)
    if r is None:
        return jsonify(error='rapor bulunamadı'), 404
    db.session.delete(r)
    db.session.commit()
    return jsonify(ok=True)


# --- public rapor sayfası ---------------------------------------------------

public_bp = Blueprint('report_public', __name__)


@public_bp.get('/rapor/<token>')
def public_report(token):
    """Müşteriye gönderilen rapor linki. Auth YOK: token'ın kendisi yetkidir."""
    if not ratelimit.hit(f'rapor:{token}', 120, 60):
        abort(429)
    r = MonthlyReport.query.filter_by(token=token, revoked=False).first()
    if r is None:
        abort(404)
    return Response(_rapor_html(r), mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow'})


@public_bp.get('/rapor/<token>/pdf')
def public_report_pdf(token):
    if not ratelimit.hit(f'raporpdf:{token}', 20, 60):
        abort(429)
    r = MonthlyReport.query.filter_by(token=token, revoked=False).first()
    if r is None:
        abort(404)
    import shutil
    try:
        d, pdf_yol = _pdf_uret(r)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        log.warning('public PDF üretilemedi (rapor %s): %s', r.id, e)
        abort(502)
    try:
        with open(pdf_yol, 'rb') as f:
            veri = f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return Response(veri, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename*=UTF-8\'\'{_pdf_adi(r).replace(" ", "%20")}'})
