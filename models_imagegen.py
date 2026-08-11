"""Codex image generation jobs (2026-08-10).

Do NOT CONFUSE with `models_sharing.ImageGeneration`: that one is the trace of the
Magnific/Mystic pipeline and uploads the generated image to Drive. This table is the
trace of the SEPARATE pipeline that generates via `codex exec` + `$imagegen` under a
ChatGPT subscription, and keeps the image locally on the server.

Why a separate table: the two pipelines' fields don't overlap (Codex has no
asset_id/result_url, but has provider_run_id/usage/error classification instead),
and having the two pipelines coexist without disturbing each other was a user
decision (2026-08-10). The provider is carried in the `provider` column — when the
official OpenAI Image API is added in the future, the same table is used, the
schema doesn't change.
"""
from extensions import db
from models import iso, utcnow
from models_sharing import JSONB_

# Aspect ratios offered in the panel → generation pixels. 1080x1920 was chosen for
# 9:16; 1024x1792 actually gives 0.571 (not 0.5625) and leaves a visible band on the
# story edge.
ASPECTS = {
    'square_1_1': (1024, 1024),
    'social_post_4_5': (1024, 1280),
    'social_story_9_16': (1080, 1920),
}

# preparing: work directory/references being prepared · running: codex is running
# validating: output being validated · storing: moving to permanent storage
STATUSES = ('queued', 'preparing', 'running', 'validating', 'storing',
            'completed', 'failed', 'cancelled')

# User-visible error classes. 'quota'/'auth'/'consent' are PERMANENT errors —
# retrying is pointless (quota exhausted, session dropped, no client consent, respectively).
ERROR_CODES = ('quota', 'auth', 'timeout', 'invalid_output', 'consent', 'internal')

# In batch generation, two versions of one idea are produced: the text-bearing
# design the brief asks for, and a text-free fallback (AI typography frequently
# fails on Turkish characters).
VARIANTS = ('with_text', 'clean')


class ImageJob(db.Model):
    """The full trace of a single Codex image generation job: what was requested,
    what went to the system, what came out."""
    __tablename__ = 'image_jobs'
    __table_args__ = (db.Index('ix_imagejob_client', 'client_id'),
                      db.Index('ix_imagejob_status', 'status'),
                      db.Index('ix_imagejob_week', 'client_id', 'week_iso'))
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    brief_id = db.Column(db.Integer, db.ForeignKey('weekly_briefs.id'))
    requested_by = db.Column(db.String(64))
    provider = db.Column(db.String(32), nullable=False, default='codex_exec')
    status = db.Column(db.String(16), nullable=False, default='queued')
    original_user_prompt = db.Column(db.Text)   # raw text the user typed
    resolved_prompt = db.Column(db.Text)        # final text sent to the system (auditability)
    aspect_ratio = db.Column(db.String(24), nullable=False, default='social_post_4_5')
    reference_asset_ids = db.Column(JSONB_)     # list of ClientAsset.id
    output_path = db.Column(db.String(512))     # path RELATIVE to storage: <client_id>/<uuid>.png
    output_meta = db.Column(JSONB_)             # {width, height, bytes, sha256, mime}
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    provider_run_id = db.Column(db.String(64))  # Codex thread_id
    started_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    error_code = db.Column(db.String(32))
    error_public = db.Column(db.Text)           # shown to the user
    error_internal = db.Column(db.Text)         # server-side ONLY; does NOT enter to_dict
    usage = db.Column(JSONB_)
    # --- Batch generation fields (2026-08-10). All three are NULLABLE: the
    # single-generation path (POST /generate) doesn't fill these and mustn't break.
    week_iso = db.Column(db.String(16))          # the batch's week; gallery grouping
    brief_idea_index = db.Column(db.Integer)     # index within brief.ideas[] (0-based)
    variant = db.Column(db.String(16))           # with_text | clean; NULL for single generation
    # Translation result (structured English JSON, 2026-08-10). Two purposes: the
    # same idea's second variant reuses this JSON (no second claude call), and the
    # question "what exactly was sent for this image" stays answerable afterward.
    prompt_json = db.Column(JSONB_)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        """The body returned to the panel. `error_internal` and `resolved_prompt`
        are DELIBERATELY absent: the former carries system detail (path, process
        output), the latter contains the entire brand context and is useless in a
        list view."""
        return {
            'id': self.id, 'client_id': self.client_id, 'brief_id': self.brief_id,
            'status': self.status, 'provider': self.provider,
            'aspect_ratio': self.aspect_ratio,
            'original_user_prompt': self.original_user_prompt,
            'output_meta': self.output_meta,
            'has_image': bool(self.output_path),
            'error_code': self.error_code, 'error_public': self.error_public,
            'requested_by': self.requested_by,
            'week_iso': self.week_iso,
            'brief_idea_index': self.brief_idea_index,
            'variant': self.variant,
            'created_at': iso(self.created_at), 'completed_at': iso(self.completed_at),
        }
