"""Batch image generation from weekly brief ideas (2026-08-10).

Sits on top of the existing Codex pipeline (`codex_image`): this module does
NOT generate anything, it only decides which ideas go into the queue with
which variants and opens `ImageJob` rows. Generation itself is
`ai_worker.codex_image_handler`'s job, and that handler is UNCHANGED for this
feature (only the prompt-builder selection is added).

Why a separate module: `imagegen_api.py` is an HTTP layer; the idea filter and
idempotency rules are pure logic and should be testable without HTTP.
"""
import jobqueue
from extensions import db
from models_imagegen import VARIANTS, ImageJob

# Ideas carrying a video marker are not converted into a single-frame image. The
# `format` field comes back empty in some briefs, so `çekim_tipi` is also scanned.
REEL_ISARETLERI = ('reel', 'video')


def gorsele_uygun(idea):
    """Can this idea be converted into a single-frame image? (NO if reel/video)"""
    if not isinstance(idea, dict):
        return False
    metin = f"{idea.get('format') or ''} {idea.get('çekim_tipi') or ''}".lower()
    return not any(k in metin for k in REEL_ISARETLERI)


def fikir_basligi(idea, index):
    """Human-readable name for use in the gallery and error messages.

    Falls back to `ad` if `başlık` is missing, and to the index number if that's
    missing too — briefs are AI-generated, fields being populated isn't guaranteed."""
    if isinstance(idea, dict):
        for k in ('başlık', 'ad'):
            v = (idea.get(k) or '').strip()
            if v:
                return v
    return f'Fikir {index + 1}'


def parti_plani(brief):
    """Filters the brief's ideas → `{'uygun': [...], 'skipped': [...]}`.

    Writes NOTHING to the DB — plan and apply are kept separate so the caller
    can check limits first. Returns an empty plan if `ideas` is malformed/missing
    (NOT a 500: briefs are AI-generated, their shape can break, and this should
    show the user 'no ideas to visualize in this brief'."""
    ideas = getattr(brief, 'ideas', None)
    if not isinstance(ideas, list):
        return {'uygun': [], 'skipped': []}
    uygun, skipped = [], []
    for i, idea in enumerate(ideas):
        if not isinstance(idea, dict):
            continue        # plain text/number — nothing to show the user
        baslik = fikir_basligi(idea, i)
        if gorsele_uygun(idea):
            uygun.append({'index': i, 'idea': idea, 'baslik': baslik})
        else:
            skipped.append({'index': i, 'baslik': baslik,
                            'sebep': 'video/reel fikri — görsel üretilmedi'})
    return {'uygun': uygun, 'skipped': skipped}


def mevcut_isler(client_id, week_iso):
    """Jobs already opened for that week → `{(brief_idea_index, variant): ImageJob}`.

    The basis of idempotency: if the batch is triggered a second time, `completed`
    ones are skipped, `failed` ones are regenerated (see `uygulanacaklar`)."""
    rows = (ImageJob.query
            .filter(ImageJob.client_id == client_id,
                    ImageJob.week_iso == week_iso,
                    ImageJob.brief_idea_index.isnot(None))
            .all())
    return {(r.brief_idea_index, r.variant): r for r in rows}





def musteri_logosu(client_id):
    """The client's active logo asset id; None if there isn't one.

    User rule (2026-08-10): the brand logo goes along as a reference in every
    image generation. Generation is NOT BLOCKED for a client without a logo —
    29/32 clients have one, locking the pipeline over 3 clients isn't worth it; the panel shows a warning instead."""
    from models import ClientAsset
    row = (ClientAsset.query
           .filter(ClientAsset.client_id == client_id,
                   ClientAsset.kind == 'logo',
                   ClientAsset.deleted_at.is_(None))
           .order_by(ClientAsset.id.desc())
           .first())
    return row.id if row is not None else None

# Batch generation v1 uses a single aspect ratio. Deriving the ratio from
# `çekim_tipi` is v2's job (spec §5); deriving it now would be a fragile guess based on brief text.
BATCH_ASPECT = 'social_post_4_5'

# A completed job is not regenerated; every other status (failed/cancelled/a
# half-finished queued) can be re-queued.
_ATLANACAK_DURUMLAR = ('completed',)


def uygulanacaklar(plan, mevcut):
    """Of the idea × variant combinations in the plan, the ones that will
    ACTUALLY be generated.

    `mevcut` (see `mevcut_isler`) provides idempotency: if a `completed` row
    exists, that combination is skipped; if it's `failed`, the row is reused.
    So pressing the button a second time completes what's left without duplicating what's done."""
    isler = []
    for u in plan['uygun']:
        for variant in VARIANTS:
            var_olan = mevcut.get((u['index'], variant))
            if var_olan is not None and var_olan.status in _ATLANACAK_DURUMLAR:
                continue
            isler.append({'index': u['index'], 'idea': u['idea'],
                          'baslik': u['baslik'], 'variant': variant,
                          'mevcut': var_olan})
    return isler


def parti_uygula(client, brief, isler, requested_by):
    """Open/reset `ImageJob` rows and push `codex_image` jobs to the queue.

    `resolved_prompt` is NOT BUILT HERE: the handler builds it
    (`codex_image_handler`), because the brief can be updated while the job
    waits in the queue, and the brief valid at generation time must be used.
    Whether `brief_idea_index` is populated determines which builder the handler calls.

    The client's logo is added as a reference to every job (user rule
    2026-08-10); if there's no logo, the list stays empty and no logo constraint is written into the prompt."""
    logo_id = musteri_logosu(client.id)
    created = []
    for is_ in isler:
        row = is_['mevcut']
        if row is None:
            row = ImageJob(client_id=client.id, provider='codex_exec')
            db.session.add(row)
        # Clear the old error and output on regeneration — no stale data left in the panel.
        row.brief_id = brief.id
        row.week_iso = brief.week_iso
        row.brief_idea_index = is_['index']
        row.variant = is_['variant']
        row.requested_by = requested_by
        row.aspect_ratio = BATCH_ASPECT
        row.original_user_prompt = is_['baslik'][:2000]
        row.reference_asset_ids = [logo_id] if logo_id else []
        row.status = 'queued'
        row.resolved_prompt = None
        row.prompt_json = None          # refresh the translation too on regeneration
        row.output_path = row.output_meta = None
        row.error_code = row.error_public = row.error_internal = None
        db.session.flush()          # id is needed: the dedup key is derived from it
        created.append(row)
    db.session.commit()

    for row in created:
        jobqueue.enqueue('codex_image', {'image_job_id': row.id}, priority=0,
                         dedup_key=f'codex_image:{row.id}', created_by=requested_by)
    return {'created': [r.to_dict() for r in created]}
