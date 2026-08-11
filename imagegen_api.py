"""Codex image generation endpoints (`/api/imagegen/*`) — management only.

These endpoints ONLY enqueue the job and read the result; no long-running operation
runs in the web process (a Codex call takes 1-4 min, which would block the gunicorn worker).

The generated image is NOT served from a PUBLIC route — `/jobs/<id>/image` sits
behind the role gate. There's no approve/reject UI in v1; the approval-gate invariant
is preserved by never linking this pipeline's output to any client-facing surface (spec §10).
"""
from datetime import timedelta

from flask import Blueprint, jsonify, request, send_file

import image_providers
import imagegen_batch
import imagegen_store
import jobqueue
from api import csrf_protect
from extensions import db
from models import AppSetting, Client, ClientAsset, utcnow
from models_imagegen import ASPECTS, VARIANTS, ImageJob
from models_sharing import WeeklyBrief
from sso_client import current_user

bp = Blueprint('imagegen', __name__)
bp.before_request(csrf_protect)  # same CSRF as api (session token)

# We ALSO have quota defense on our side: we hit our own limit before hitting the
# ceiling on ChatGPT's side — so a 'quota' error becomes the exception, not a daily
# routine. With a single worker and ~3 min/generation the theoretical daily ceiling
# is already ~200; these numbers are set to the operation's actual need (40 clients).
DAILY_CLIENT_LIMIT = 20        # per client / day
DAILY_GLOBAL_LIMIT = 60        # whole system / day


def _require_management():
    """The exact same gate as `sharing._require_management` (v1: management-only).

    Image generation sends the client's brand to a third party and spends quota —
    production roles (designer/content_creator) can't enter this pipeline in v1."""
    u = current_user()
    if not u:
        return None, (jsonify(error='no active session'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='you are not authorized for this action'), 403)
    return u, None


def _bakim_kapali():
    """If `AppSetting['codex_image_enabled']` == '0' the pipeline is under maintenance (operator switch)."""
    return AppSetting.get('codex_image_enabled', '1') == '0'


def _gunluk_sayim(client_id=None):
    """Count of ImageJobs opened in the last 24 hours.

    Failures are ALSO COUNTED: every attempt takes from the Codex quota, so the
    limit operates on 'attempts', not 'successful generations'."""
    q = ImageJob.query.filter(ImageJob.created_at >= utcnow() - timedelta(days=1))
    if client_id is not None:
        q = q.filter_by(client_id=client_id)
    return q.count()


@bp.post('/generate')
def imagegen_generate():
    """Enqueue the generation job.

    The KVKK (data-protection) pre-check happens here (fast feedback to the user;
    the ACTUAL gate is in the handler), reference ownership also here (re-checked
    in the handler) — the two layers are deliberate."""
    u, err = _require_management()
    if err:
        return err
    if _bakim_kapali():
        return jsonify(error='Codex image generation is currently under maintenance.'), 503
    data = request.get_json(silent=True) or {}
    c = db.session.get(Client, data.get('client_id'))
    if c is None or c.status == 'deleted':
        return jsonify(error='client not found'), 404
    if _gunluk_sayim(c.id) >= DAILY_CLIENT_LIMIT:
        return jsonify(error=f'Daily generation limit reached for this client '
                             f'({DAILY_CLIENT_LIMIT}). Please try again tomorrow.'), 429
    if _gunluk_sayim() >= DAILY_GLOBAL_LIMIT:
        return jsonify(error=f'Daily total generation limit reached '
                             f'({DAILY_GLOBAL_LIMIT}).'), 429
    if not (c.brand_profile or {}).get('ai_image_consent'):
        return jsonify(error='client has not given AI image consent (data protection). '
                             'The ai_image_consent flag is required on the client record.'), 409
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify(error='prompt cannot be empty'), 400
    aspect = data.get('aspect_ratio') or 'social_post_4_5'
    if aspect not in ASPECTS:
        return jsonify(error='invalid aspect ratio'), 400
    ref_ids = data.get('reference_asset_ids') or []
    if not isinstance(ref_ids, list):
        return jsonify(error='reference_asset_ids must be a list'), 400
    if len(ref_ids) > image_providers.MAX_REFERENCES:
        return jsonify(error=f'at most {image_providers.MAX_REFERENCES} references '
                             f'may be selected'), 400
    if ref_ids:
        sahip = ClientAsset.query.filter(ClientAsset.id.in_(ref_ids),
                                         ClientAsset.client_id == c.id,
                                         ClientAsset.deleted_at.is_(None)).count()
        if sahip != len(set(ref_ids)):
            return jsonify(error='reference image does not belong to this client'), 403

    ij = ImageJob(client_id=c.id, brief_id=data.get('brief_id'),
                  requested_by=u.get('email') or u.get('sub'), provider='codex_exec',
                  original_user_prompt=prompt[:2000], aspect_ratio=aspect,
                  reference_asset_ids=list(ref_ids), status='queued')
    db.session.add(ij)
    db.session.commit()
    # dedup via the ImageJob id: a double-click in the panel doesn't produce a second job.
    job = jobqueue.enqueue('codex_image', {'image_job_id': ij.id}, priority=0,
                           dedup_key=f'codex_image:{ij.id}', created_by=u.get('sub'))
    return jsonify(image_job=ij.to_dict(), job=job.to_dict()), 202


@bp.get('/jobs')
def imagegen_jobs():
    """The client's most recent generations (newest first, max 20)."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id is required'), 400
    rows = (ImageJob.query.filter_by(client_id=client_id)
            .order_by(ImageJob.id.desc()).limit(20).all())
    return jsonify(image_jobs=[r.to_dict() for r in rows])


@bp.get('/jobs/<int:job_id>')
def imagegen_job(job_id):
    """Status of a single job — the panel polls this while waiting for generation."""
    _, err = _require_management()
    if err:
        return err
    ij = db.session.get(ImageJob, job_id)
    if ij is None:
        return jsonify(error='job not found'), 404
    return jsonify(image_job=ij.to_dict())


@bp.get('/jobs/<int:job_id>/image')
def imagegen_image(job_id):
    """Stream the image from behind the role gate. The local store is never exposed
    externally; the file name comes from the DB and is separately validated inside `abs_path`."""
    _, err = _require_management()
    if err:
        return err
    ij = db.session.get(ImageJob, job_id)
    if ij is None or not ij.output_path:
        return jsonify(error='no image'), 404
    p = imagegen_store.abs_path(ij.output_path)
    if not p:
        return jsonify(error='image file not found'), 404
    return send_file(p, mimetype='image/png', max_age=0)


@bp.get('/health')
def imagegen_health():
    """Codex setup and session status + maintenance switch. Does NOT return secrets —
    `health_check` only checks the auth file's EXISTENCE, doesn't read its contents."""
    _, err = _require_management()
    if err:
        return err
    st = image_providers.get_provider('codex_exec').health_check()
    return jsonify(ok=st['ok'], detail=st['detail'], enabled=not _bakim_kapali())


# --- Weekly batch generation (2026-08-10) ---

@bp.post('/batch')
def imagegen_batch_baslat():
    """Start batch generation from the week's approved brief (spec §3).

    Check order: maintenance → client → switch → KVKK → brief → plan → limit.
    Limit is LAST but BEFORE any row is opened: we only know how many jobs the
    batch will need once the plan comes out, and if the limit would be exceeded, NO
    row should be opened (spec §8)."""
    u, err = _require_management()
    if err:
        return err
    if _bakim_kapali():
        return jsonify(error='Codex image generation is currently under maintenance.'), 503
    data = request.get_json(silent=True) or {}
    c = db.session.get(Client, data.get('client_id'))
    if c is None or c.status == 'deleted':
        return jsonify(error='client not found'), 404
    prof = c.brand_profile or {}
    if not prof.get('auto_image_enabled'):
        return jsonify(error='weekly image generation is disabled for this client'), 409
    if not prof.get('ai_image_consent'):
        return jsonify(error='client has not given AI image consent (data protection). '
                             'The ai_image_consent flag is required on the client record.'), 409
    week_iso = (data.get('week_iso') or '').strip()
    if not week_iso:
        return jsonify(error='week_iso is required'), 400
    brief = WeeklyBrief.query.filter_by(client_id=c.id, week_iso=week_iso,
                                        status='approved').first()
    if brief is None:
        return jsonify(error=f'no approved brief for {week_iso}'), 404

    plan = imagegen_batch.parti_plani(brief)
    if not plan['uygun']:
        return jsonify(error='there is no idea to visualize in this brief '
                             '(all are video/reel, or the idea list is empty)',
                       skipped=plan['skipped']), 409

    mevcut = imagegen_batch.mevcut_isler(c.id, week_iso)
    isler = imagegen_batch.uygulanacaklar(plan, mevcut)
    already = [{'index': u_['index'], 'variant': v, 'baslik': u_['baslik'],
                'image_job_id': mevcut[(u_['index'], v)].id}
               for u_ in plan['uygun'] for v in VARIANTS
               if (u_['index'], v) in mevcut
               and mevcut[(u_['index'], v)].status == 'completed']
    logo_missing = imagegen_batch.musteri_logosu(c.id) is None
    if not isler:
        return jsonify(created=[], skipped=plan['skipped'], already=already,
                       logo_missing=logo_missing), 202

    # Limit: applied to the batch TOTAL. No partial generation.
    if _gunluk_sayim(c.id) + len(isler) > DAILY_CLIENT_LIMIT:
        return jsonify(error=f'This batch exceeds the daily client limit '
                             f'({DAILY_CLIENT_LIMIT}). Please try again tomorrow.'), 429
    if _gunluk_sayim() + len(isler) > DAILY_GLOBAL_LIMIT:
        return jsonify(error=f'This batch exceeds the daily total limit '
                             f'({DAILY_GLOBAL_LIMIT}).'), 429

    sonuc = imagegen_batch.parti_uygula(c, brief, isler,
                                        u.get('email') or u.get('sub'))
    return jsonify(created=sonuc['created'], skipped=plan['skipped'],
                   already=already, logo_missing=logo_missing), 202


@bp.get('/batch')
def imagegen_batch_listele():
    """Group the week's jobs by idea (gallery)."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    week_iso = (request.args.get('week_iso') or '').strip()
    if not client_id or not week_iso:
        return jsonify(error='client_id and week_iso are required'), 400
    brief = WeeklyBrief.query.filter_by(client_id=client_id, week_iso=week_iso,
                                        status='approved').first()
    if brief is None:
        return jsonify(groups=[], skipped=[])
    plan = imagegen_batch.parti_plani(brief)
    mevcut = imagegen_batch.mevcut_isler(client_id, week_iso)
    groups = []
    for u_ in plan['uygun']:
        jobs = {v: mevcut[(u_['index'], v)].to_dict()
                for v in VARIANTS if (u_['index'], v) in mevcut}
        groups.append({'index': u_['index'], 'baslik': u_['baslik'], 'jobs': jobs})
    return jsonify(groups=groups, skipped=plan['skipped'])


@bp.get('/weeks')
def imagegen_weeks():
    """Weeks the client has an approved brief for (week selector; newest first).

    `approved` only: a draft brief can't enter generation, listing it in the
    selector would be misleading."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id is required'), 400
    rows = (WeeklyBrief.query
            .filter_by(client_id=client_id, status='approved')
            .order_by(WeeklyBrief.week_iso.desc()).limit(12).all())
    return jsonify(weeks=[{'week_iso': b.week_iso, 'brief_id': b.id,
                           'title': b.title} for b in rows])


@bp.get('/client-settings')
def imagegen_client_settings_oku():
    """The client's two gate flags. The panel reads this on page load; without it,
    the switch would look off on every refresh while being on in the DB, and the
    user wouldn't understand why the button is disabled."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    c = db.session.get(Client, client_id) if client_id else None
    if c is None or c.status == 'deleted':
        return jsonify(error='client not found'), 404
    prof = c.brand_profile or {}
    return jsonify(auto_image_enabled=bool(prof.get('auto_image_enabled')),
                   ai_image_consent=bool(prof.get('ai_image_consent')))


@bp.patch('/client-settings')
def imagegen_client_settings():
    """Write the `auto_image_enabled` switch. `brand_profile` is MERGED — overwriting
    it would erase the other brand fields."""
    _, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    c = db.session.get(Client, data.get('client_id'))
    if c is None or c.status == 'deleted':
        return jsonify(error='client not found'), 404
    if not isinstance(data.get('auto_image_enabled'), bool):
        return jsonify(error='auto_image_enabled must be a boolean'), 400
    bp_ = dict(c.brand_profile) if isinstance(c.brand_profile, dict) else {}
    bp_['auto_image_enabled'] = data['auto_image_enabled']
    c.brand_profile = bp_          # a new dict — this is how SQLAlchemy detects the mutation
    c.updated_at = utcnow()
    db.session.commit()
    return jsonify(auto_image_enabled=bp_['auto_image_enabled'])
