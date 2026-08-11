"""Monthly report API (/api/reports) + public report page (/rapor/<token>).

Calculation lives in `aylik_rapor.py` (the layer ported from the project owner's
desktop tool); this file has only the web side: upload → temp directory →
generation → DB, and the viewing endpoints.

**Why the upload is written to disk:** the ported layer works entirely with file
PATHS (`auto_match_csv_files(folder)`, `preflight_bulk(root_folder)`). Keeping it
in memory and changing the signatures would mean touching matching logic that's
been tested for months against real data. Uploads stay inside a
`tempfile.TemporaryDirectory` and are deleted as soon as the request ends — the
only thing kept permanently is the computed report data.
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

# Upload limits. CSVs are practically 5-500 KB; the caps guard against accidents/abuse.
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 600                      # 60 clients × ~9 CSV
PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

_LOGO_DATA_URI = None


def _logo_data_uri():
    """Return the logo as a base64 data URI (read once, for the life of the process).

    The template says `src="logo.png"`. Embedding it instead of serving it as a
    separate file makes the generated HTML a SINGLE FILE: the same output renders
    fully in the panel, on the public link, and on the `file://` page Chromium
    prints to PDF."""
    global _LOGO_DATA_URI
    if _LOGO_DATA_URI is None:
        try:
            with open(ar._LOGO_PATH, 'rb') as f:
                _LOGO_DATA_URI = 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
        except OSError:
            _LOGO_DATA_URI = ''      # generate the report anyway if there's no logo
    return _LOGO_DATA_URI


def _require_management():
    u = current_user()
    if not u:
        return None, (jsonify(error='no active session'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='you are not authorized'), 403)
    return u, None


def _guvenli_ad(ad):
    """Reduces an uploaded path segment to a single safe file/folder name.

    Only the `basename` is taken and separator/hidden-file patterns are stripped →
    no matter what the uploaded `webkitRelativePath` is, nothing can be written
    outside the temp directory (the multipart equivalent of zip-slip)."""
    ad = os.path.basename((ad or '').replace('\\', '/').strip())
    ad = ad.replace('\x00', '').lstrip('.')
    return ad[:120]


def _yukle_ve_dagit(kok):
    """Write the request's files as `kok/<client>/<file>.csv`; return the client names.

    The client name comes from the FIRST folder name in `paths[i]` (the browser's
    `webkitRelativePath`) — the exact counterpart of the desktop tool's "every
    subfolder is a client" behavior. If there's no folder info (the user selected
    files one by one), the `client_name` field is used: single-client flow.
    """
    dosyalar = request.files.getlist('files')
    yollar = request.form.getlist('paths')
    tekil_ad = _guvenli_ad(request.form.get('client_name') or '') or 'Rapor'
    if not dosyalar:
        return None, 'no file uploaded'
    if len(dosyalar) > MAX_FILES:
        return None, f'at most {MAX_FILES} files may be uploaded'

    toplam = 0
    yazilan = 0
    for i, f in enumerate(dosyalar):
        ad = _guvenli_ad(f.filename)
        if not ad.lower().endswith('.csv'):
            continue                                  # non-CSV files are silently skipped
        rel = yollar[i] if i < len(yollar) else ''
        parcalar = [p for p in (rel or '').replace('\\', '/').split('/') if p not in ('', '.', '..')]
        # webkitRelativePath: "<selected folder>/<client>/<file>" or "<client>/<file>"
        musteri = _guvenli_ad(parcalar[-2]) if len(parcalar) >= 2 else tekil_ad
        musteri = musteri or tekil_ad
        hedef_dir = os.path.join(kok, musteri)
        os.makedirs(hedef_dir, exist_ok=True)
        veri = f.read(MAX_FILE_BYTES + 1)
        if len(veri) > MAX_FILE_BYTES:
            return None, f'{ad}: file too large (>10 MB)'
        toplam += len(veri)
        if toplam > MAX_TOTAL_BYTES:
            return None, 'total upload size limit exceeded'
        with open(os.path.join(hedef_dir, ad), 'wb') as out:
            out.write(veri)
        yazilan += 1

    if not yazilan:
        return None, 'no CSV file found'
    return sorted(os.listdir(kok)), None


def _client_eslestir(ad):
    """Matches the report name against a panel client (Turkish-aware, loose).

    Matching is NOT MANDATORY: the report is saved without a `client_id` if it
    doesn't match. The goal is to later reach reports from the client page, not to
    block generation."""
    hedef = ar._normalize(ad or '')
    if not hedef:
        return None
    for c in Client.query.all():
        if ar._normalize(c.name) == hedef:
            return c.id
    return None


@bp.post('/generate')
def generate():
    """Take the CSVs, generate report(s) and save. Same path for both single-client and bulk."""
    u, err = _require_management()
    if err:
        return err
    period = (request.form.get('period') or '').strip()
    if not PERIOD_RE.match(period):
        return jsonify(error='select a valid period (YYYY-MM)'), 400

    with tempfile.TemporaryDirectory(prefix='rapor-') as kok:
        musteriler, hata = _yukle_ve_dagit(kok)
        if hata:
            return jsonify(error=hata), 400
        # `preflight_bulk` scans every client folder, matches it, generates the data,
        # and returns the REASON for the ones it couldn't generate — silently
        # skipping would lead to thinking "I got the report" without noticing a
        # missing CSV.
        try:
            sonuclar = ar.preflight_bulk(kok)
        except Exception as e:                        # noqa: BLE001
            log.exception('rapor üretimi çöktü')
            return jsonify(error=f'report generation failed: {type(e).__name__}'), 500

        uretilen, atlanan = [], []
        for s in sonuclar:
            if s.get('skip') or not s.get('data'):
                atlanan.append({'client_name': s['name'],
                                'reason': s.get('skip') or 'data could not be generated',
                                'missing': s.get('missing', [])})
                continue
            cid = _client_eslestir(s['name'])
            # If the same (client, period) is regenerated, it's OVERWRITTEN: once
            # the user completes the missing CSV and re-uploads, there shouldn't be
            # two conflicting reports. The share token is preserved — so a
            # distributed link doesn't break.
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
            return jsonify(error='client_id must be a number'), 400
    rows = q.order_by(MonthlyReport.period.desc(), MonthlyReport.client_name).all()
    return jsonify(reports=[r.to_dict(ozet=True) for r in rows])


@bp.get('/periods')
def periods():
    """Periods that have reports (for the filter dropdown)."""
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
        return jsonify(error='report not found'), 404
    return jsonify(report=r.to_dict())


def _rapor_html(rapor):
    """Generate single-file HTML from the report data (template + data + embedded logo).

    Not stored, generated on every request: when the template is improved, old
    reports also get the new look, and records don't bloat with 50 KB HTML copies."""
    with tempfile.TemporaryDirectory(prefix='raporhtml-') as d:
        yol = os.path.join(d, 'rapor.html')
        veri = dict(rapor.data or {})
        veri.setdefault('client_name', rapor.client_name)
        # export_to_html expects a list of tuples (that's how it's generated on the
        # desktop side); coming back from JSONB, the lists become [title, value].
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
    """Print the HTML to PDF with Chromium. Returns the file path (caller cleans up)."""
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
        return jsonify(error='failed to generate PDF'), 502
    try:
        with open(pdf_yol, 'rb') as f:
            veri = f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return Response(veri, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename*=UTF-8\'\'{_pdf_adi(r).replace(" ", "%20")}'})


@bp.post('/<int:report_id>/share')
def report_share(report_id):
    """Generate / revoke the public link. `{shared: bool}`."""
    _, err = _require_management()
    if err:
        return err
    r = db.session.get(MonthlyReport, report_id)
    if r is None:
        return jsonify(error='report not found'), 404
    paylas = bool((request.get_json(silent=True) or {}).get('shared', True))
    if paylas:
        # The token is generated ONCE: not so that revoking and re-enabling returns
        # the same address by design — rather, revoking is done via `revoked`, and
        # re-enabling revives the old link. Changing the address sent to the client
        # every time would trigger "is the link broken?" back-and-forth.
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
        return jsonify(error='report not found'), 404
    db.session.delete(r)
    db.session.commit()
    return jsonify(ok=True)


# --- public report page ---------------------------------------------------

public_bp = Blueprint('report_public', __name__)


@public_bp.get('/rapor/<token>')
def public_report(token):
    """The report link sent to the client. NO auth: the token itself is the authorization."""
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
