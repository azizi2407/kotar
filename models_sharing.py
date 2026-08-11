"""Sharing Board domain models (Phase 2a core).

The old monolith's two parallel models (new `sharing_shares` + abandoned
`sharing_card_status`) were analyzed: **shares is authoritative**, card_status is
only a W20-W22 historical archive (see LegacyCardStatus). The main axis is
`(client_id, week_iso)`, week_iso = ISO week string "YYYY-Www".

PostgreSQL conversion: status/type fields are native PG enums (fall back to
VARCHAR+CHECK on sqlite), semi-structured fields are JSONB (JSON on sqlite), real
FKs + timestamptz. `legacy_mongo_id` is an idempotent migration key.
"""
from sqlalchemy.dialects.postgresql import JSONB

from extensions import db
from models import iso, utcnow

# JSONB on PG, falls back to JSON in sqlite tests.
# none_as_null=True: Python None → SQL NULL (not the JSON 'null' scalar) → IS NULL
# queries stay consistent, no pointless JSON-null writes.
JSONB_ = JSONB(none_as_null=True).with_variant(db.JSON(none_as_null=True), 'sqlite')

SHARE_KINDS = ('post', 'story', 'video', 'linkedin')
SHARE_STATUSES = ('draft', 'published')
REVISION_KINDS = ('design', 'video')
REVISION_STATUSES = ('open', 'resolved')
SHOOT_STATUSES = ('pending', 'completed')

ShareKind = db.Enum(*SHARE_KINDS, name='share_kind')
ShareStatus = db.Enum(*SHARE_STATUSES, name='share_status')
RevisionKind = db.Enum(*REVISION_KINDS, name='revision_kind')
RevisionStatus = db.Enum(*REVISION_STATUSES, name='revision_status')
ShootStatus = db.Enum(*SHOOT_STATUSES, name='shoot_status')


class Share(db.Model):
    """Authoritative share record — one row = one share."""
    __tablename__ = 'shares'
    __table_args__ = (db.Index('ix_shares_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    kind = db.Column(ShareKind, nullable=False)
    status = db.Column(ShareStatus, nullable=False, default='draft')

    file_id = db.Column(db.String(128))
    file_name = db.Column(db.String(512))
    original_name = db.Column(db.String(512))
    caption_text = db.Column(db.Text)
    hashtag_text = db.Column(db.Text)
    note = db.Column(db.Text)

    published_day_name = db.Column(db.String(32))
    published_at = db.Column(db.DateTime(timezone=True))
    published_by = db.Column(db.String(64))
    planned_date = db.Column(db.Date)
    planned_time = db.Column(db.String(8))

    platforms = db.Column(JSONB_)       # {instagram|story|linkedin: {published_at, url}}
    client_review = db.Column(JSONB_)   # {status: approved|revision_requested, note, at}
    transcript = db.Column(db.Text)     # video audio transcript (media_worker; caption context)
    caption_suggestions = db.Column(JSONB_)  # {captions:[...], hashtags} — last generated (persistent; stays even if the modal closes)
    revision = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
            'kind': self.kind, 'status': self.status,
            'file_id': self.file_id, 'file_name': self.file_name,
            'original_name': self.original_name,
            'caption_text': self.caption_text, 'hashtag_text': self.hashtag_text,
            'note': self.note,
            'published_day_name': self.published_day_name,
            'published_at': iso(self.published_at), 'published_by': self.published_by,
            'planned_date': self.planned_date.isoformat() if self.planned_date else None,
            'planned_time': self.planned_time,
            'platforms': self.platforms or {},
            'client_review': self.client_review,
            'revision': self.revision,
            'has_transcript': bool(self.transcript),
            'caption_suggestions': self.caption_suggestions,
            'created_at': iso(self.created_at), 'created_by': self.created_by,
            'updated_at': iso(self.updated_at), 'updated_by': self.updated_by,
        }


class CardUpload(db.Model):
    """Record of a raw file uploaded to Drive (source for the sharing picker)."""
    __tablename__ = 'card_uploads'
    __table_args__ = (db.Index('ix_uploads_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    card_index = db.Column(db.Integer)
    category = db.Column(db.String(32))
    file_id = db.Column(db.String(128))
    file_name = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger)
    drive_created_time = db.Column(db.String(40))
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True))
    deleted_at = db.Column(db.DateTime(timezone=True))
    request_id = db.Column(db.String(24))
    upload_uuid = db.Column(db.String(64))
    adopted = db.Column(db.Boolean, default=False)
    revision = db.Column(db.Integer, default=0)
    backfilled = db.Column(db.Boolean, default=False)
    moved_at = db.Column(db.DateTime(timezone=True))
    moved_from_week_iso = db.Column(db.String(16))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
            'card_index': self.card_index, 'category': self.category,
            'file_id': self.file_id, 'file_name': self.file_name,
            'mime_type': self.mime_type, 'file_size': self.file_size,
            'uploaded_by': self.uploaded_by, 'uploaded_at': iso(self.uploaded_at),
            'revision': self.revision,
        }


class ReviewLink(db.Model):
    """Non-single-use approval link for the public /review/<token>."""
    __tablename__ = 'review_links'
    __table_args__ = (db.Index('ix_review_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {'id': self.id, 'token': self.token, 'client_id': self.client_id,
                'week_iso': self.week_iso, 'revoked': self.revoked,
                'created_at': iso(self.created_at)}


class RevisionRequest(db.Model):
    """Revision request (design or video). Full flow is Phase 2c; table+status live
    here."""
    __tablename__ = 'revision_requests'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    share_id = db.Column(db.Integer, db.ForeignKey('shares.id'))
    card_index = db.Column(db.Integer)
    kind = db.Column(RevisionKind, nullable=False, default='video')
    status = db.Column(RevisionStatus, nullable=False, default='open')
    category = db.Column(db.String(32))
    note = db.Column(db.Text)
    requested_by = db.Column(db.String(64))
    requested_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    resolved_by = db.Column(db.String(64))
    resolved_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
                'share_id': self.share_id, 'kind': self.kind, 'status': self.status,
                'category': self.category, 'note': self.note,
                'requested_at': iso(self.requested_at)}


class ClientPriority(db.Model):
    """Weekly priority flag (active = cleared_at NULL)."""
    __tablename__ = 'client_priority'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    set_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    set_by = db.Column(db.String(64))
    cleared_at = db.Column(db.DateTime(timezone=True))
    cleared_reason = db.Column(db.String(64))


class SpecialDayEvent(db.Model):
    """Special day/week source. client_id NULL=global, set=client-specific."""
    __tablename__ = 'special_days_events'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    day_name = db.Column(db.String(255))
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)
    type = db.Column(db.String(32))
    date_num = db.Column(db.Integer)
    date_start = db.Column(db.Integer)
    date_end = db.Column(db.Integer)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    # approval gate: server_default = existing/manually-entered records
    # (approved/manual), ORM default = new AI inserts (draft/ai). The two must stay
    # SEPARATE (backward compatibility).
    status = db.Column(db.String(16), nullable=False, server_default='approved', default='draft')
    generated_by = db.Column(db.String(16), nullable=False, server_default='manual', default='ai')

    def to_dict(self):
        return {'id': self.id, 'day_name': self.day_name, 'description': self.description,
                'active': self.active, 'month': self.month, 'year': self.year,
                'type': self.type, 'date_num': self.date_num,
                'date_start': self.date_start, 'date_end': self.date_end,
                'client_id': self.client_id,
                'status': self.status, 'generated_by': self.generated_by}


class SpecialCardStatus(db.Model):
    """Marker that a special day was published in a given week."""
    __tablename__ = 'special_card_status'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_iso', 'event_id'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('special_days_events.id'), nullable=False)
    published_at = db.Column(db.DateTime(timezone=True))
    published_by = db.Column(db.String(64))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
                'event_id': self.event_id, 'published_at': iso(self.published_at)}


class SpecialDaySelection(db.Model):
    """Special days a client selected for a given month (list of event ids)."""
    __tablename__ = 'special_day_selections'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    selected_event_ids = db.Column(JSONB_)   # [new SpecialDayEvent.id, ...]
    token = db.Column(db.String(64))
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)


class CaptionHistory(db.Model):
    """History of selected captions (for reuse)."""
    __tablename__ = 'caption_history'
    __table_args__ = (db.Index('ix_caption_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16))
    card_index = db.Column(db.Integer)
    source = db.Column(db.String(32))
    caption_text = db.Column(db.Text)
    hashtag_text = db.Column(db.Text)
    selected_at = db.Column(db.DateTime(timezone=True))
    selected_by = db.Column(db.String(64))


class ShootTask(db.Model):
    """Videographer's shoot plan task (Kanban card). scheduled_date = day column,
    position = order within the column."""
    __tablename__ = 'shoot_tasks'
    __table_args__ = (db.Index('ix_shoot_date', 'scheduled_date'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))  # nullable = ad-hoc
    title = db.Column(db.String(512))
    content_type = db.Column(db.String(64))
    scheduled_date = db.Column(db.Date)
    start_time = db.Column(db.String(8))
    end_time = db.Column(db.String(8))
    status = db.Column(ShootStatus, nullable=False, default='pending')
    priority = db.Column(db.String(16), nullable=False, default='normal')  # normal|high|urgent
    assigned_to = db.Column(db.String(64))
    assigned_by = db.Column(db.String(64))
    position = db.Column(db.Integer, nullable=False, default=0)
    location_note = db.Column(db.Text)
    equipment = db.Column(JSONB_)
    drive_links = db.Column(JSONB_)
    completed_by = db.Column(db.String(64))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    updated_by = db.Column(db.String(64))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id, 'title': self.title,
            'content_type': self.content_type,
            'scheduled_date': self.scheduled_date.isoformat() if self.scheduled_date else None,
            'start_time': self.start_time, 'end_time': self.end_time,
            'status': self.status, 'priority': self.priority,
            'assigned_to': self.assigned_to, 'position': self.position,
            'location_note': self.location_note, 'equipment': self.equipment,
        }


class VideographerBusinessMark(db.Model):
    """Marker that a client "has a video" for that week."""
    __tablename__ = 'videographer_business_marks'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    has_video = db.Column(db.Boolean, default=False)
    marked_at = db.Column(db.DateTime(timezone=True))
    marked_by = db.Column(db.String(64))


class VideographerPhoto(db.Model):
    """Photo uploaded from a shoot (Drive reference)."""
    __tablename__ = 'videographer_photos'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    shoot_date = db.Column(db.String(32))
    folder_id = db.Column(db.String(128))
    file_id = db.Column(db.String(128))
    file_name = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger)
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True))
    deleted_at = db.Column(db.DateTime(timezone=True))
    # marked when the designer uses this photo (will be linked to the designer
    # board in a future task; for now it's set from the used-mark endpoint).
    # used_at NULL = unused.
    used_at = db.Column(db.DateTime(timezone=True))
    used_by = db.Column(db.String(64))


class DepotFile(db.Model):
    """Videographer Depot (2026-07-25) — a SHARED free-form file area for all
    videographers + management. NOT tied to a client or a week (deliberate: the
    depot is a workspace, not a delivery channel).

    A SINGLE FLAT folder in Drive under `<content root>/Videograf Deposu`; no
    per-person/month subfolders. The quota (shared 5 GB) is measured with
    `SUM(file_size)` on each request (`depot._used_bytes`), NOT with a counter
    column — a counter would drift permanently on every crash between the Drive
    upload and the DB commit and would need a reconciliation job.

    Deletion is TWO-LAYERED: soft-delete in the DB (`deleted_at` → quota frees up
    immediately) + trash in Drive (`dg.trash_file`, recoverable for 30 days). The DB
    row is soft-deleted even if the Drive side errors; otherwise a single Drive
    hiccup would make the file undeletable and keep the quota permanently occupied.

    NOT COPIED to `media_store`: no endpoint serves depot files locally (Drive is
    canonical, no preview/stream) — keeping 5 GB on disk a second time for 21 days
    isn't worth it."""
    __tablename__ = 'depot_files'
    __table_args__ = (db.Index('ix_depot_files_uploaded', 'deleted_at', 'uploaded_at'),)

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.String(128), nullable=False, unique=True)   # Drive file id
    folder_id = db.Column(db.String(128))            # 'Videograf Deposu' folder id (trace)
    file_name = db.Column(db.String(512), nullable=False)
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger, nullable=False)   # quota arithmetic — NEVER NULL
    note = db.Column(db.String(300))                 # optional description
    uploaded_by = db.Column(db.String(64))           # SSO sub
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))

    def to_dict(self, uploader_name=None):
        return {'id': self.id, 'file_id': self.file_id, 'file_name': self.file_name,
                'mime_type': self.mime_type, 'file_size': self.file_size,
                'note': self.note, 'uploaded_by': self.uploaded_by,
                'uploader_name': uploader_name, 'uploaded_at': iso(self.uploaded_at)}


class VideographerIdea(db.Model):
    """AI trend-suggestion card (Phase 5, step 17): `link + reason + shoot idea` for
    the videographer. The `videographer_ideas` handler gathers curated YouTube/Vimeo
    RSS trends (fetched in-handler with Python, OUTSIDE `ai_claude`), wraps them
    with `wrap_untrusted` and feeds them into `ai_claude.run`; generated suggestions
    are written with status='new'. The videographer likes one (added to the shoot
    list as a ShootTask → status='accepted') or skips it (status='skipped')."""
    __tablename__ = 'videographer_ideas'
    __table_args__ = (db.Index('ix_vg_ideas_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    reference_link = db.Column(db.Text)   # link to the trend video that inspired this
    reason = db.Column(db.Text)           # why it fits this client
    shoot_idea = db.Column(db.Text)       # concrete shoot idea
    status = db.Column(db.String(16), nullable=False, default='new')  # new|accepted|skipped
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id,
                'reference_link': self.reference_link, 'reason': self.reason,
                'shoot_idea': self.shoot_idea, 'status': self.status,
                'created_at': iso(self.created_at)}


class ImageGeneration(db.Model):
    """AI image generation trace (Phase 6, step 19). GATE 18 decision
    (faz6-magnific-spike.md): MCP-via-`claude -p` is a NO-GO (a headless subprocess
    can't connect to Magnific) → generation uses the Magnific/Freepik REST API +
    `x-magnific-api-key` with in-handler `requests` (videographer GATE 16 pattern;
    `ai_claude`'s defenses are preserved). Optional prompt refinement is a one-shot
    `ai_claude.run` (MCP disabled). The generated asset lands in the client's Drive
    folder, the row opens with status='pending' (awaiting approval); management
    approves (status='approved') or regenerates. GDPR/KVKK (spike §5): generation
    only happens if the client has given consent
    (Client.brand_profile.ai_image_consent) — without consent, NO image is sent to
    Magnific for that client. The generation trace (which image/client/when) is kept
    here (auditability)."""
    __tablename__ = 'image_generations'
    __table_args__ = (db.Index('ix_imggen_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    brief_id = db.Column(db.Integer, db.ForeignKey('weekly_briefs.id'))  # optional linked approved-brief entry
    prompt = db.Column(db.Text)          # the (possibly refined) prompt actually used for generation
    refs = db.Column(JSONB_)             # list of reference image ids/URLs
    settings = db.Column(JSONB_)         # {type, model, effort, refine, prompt}
    asset_id = db.Column(db.String(128))  # Magnific creation id
    result_url = db.Column(db.Text)       # Magnific asset URL
    drive_file_id = db.Column(db.String(128))
    drive_file_name = db.Column(db.String(512))
    # approval gate: new generation is status='pending' (awaiting approval); management approves/regenerates
    status = db.Column(db.String(16), nullable=False, default='pending')  # pending|approved|rejected
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    reviewed_by = db.Column(db.String(64))
    reviewed_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'brief_id': self.brief_id,
                'prompt': self.prompt, 'refs': self.refs or [], 'settings': self.settings or {},
                'asset_id': self.asset_id, 'result_url': self.result_url,
                'drive_file_id': self.drive_file_id, 'drive_file_name': self.drive_file_name,
                'status': self.status, 'created_at': iso(self.created_at),
                'created_by': self.created_by}


class WeeklyBrief(db.Model):
    """Weekly content brief (client × week). Synced from a vault; content ideas +
    intro + raw markdown. Read by the team (not generation)."""
    __tablename__ = 'weekly_briefs'
    __table_args__ = (db.Index('ix_brief_client_week', 'client_id', 'week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    title = db.Column(db.String(512))
    intro = db.Column(db.Text)
    raw_md = db.Column(db.Text)
    ideas = db.Column(JSONB_)
    frontmatter = db.Column(JSONB_)
    week_notes = db.Column(JSONB_)
    source_path = db.Column(db.String(512))
    # For AI briefs (synced_at NULL), the "most recent" pick falls back to created_at
    # via COALESCE(synced_at, created_at) → created_at must never be empty.
    # default=utcnow: even though the handler also sets it explicitly (step 13),
    # this is a safe default for other insert paths. Import records supply their
    # own created_at (backward compatibility isn't broken).
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    synced_at = db.Column(db.DateTime(timezone=True))
    # APPROVAL GATE REMOVED (2026-07-30, project owner decision: "briefs shouldn't
    # wait for approval after being generated, all of them should be processed
    # directly"). The ORM default used to be 'draft' → every AI brief waited for
    # manual approval and didn't enter the caption context until approved. Now an
    # AI brief is 'approved' the moment it's born → the caption/image flow reads it
    # without waiting.
    # Why removing this is safe: the brief is NOT visible on ANY customer-facing
    # surface (no single reference in review.py / special_days.py / lente.py) —
    # it's team-internal + AI context only. The fix path is not approval but
    # REGENERATION (`brief_handler` force, overwrites the same row).
    # `generated_by` DID NOT CHANGE: the 'ai' vs 'import' distinction is still
    # meaningful (provenance). The approved-only filter on the read side
    # (sharing.brief) deliberately STAYED: drafts aren't generated anymore, but a
    # leftover/restored draft shouldn't slip into the flow by accident.
    status = db.Column(db.String(16), nullable=False, server_default='approved', default='approved')
    generated_by = db.Column(db.String(16), nullable=False, server_default='import', default='ai')

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'week_iso': self.week_iso,
                'title': self.title, 'intro': self.intro, 'raw_md': self.raw_md,
                'ideas': self.ideas or [], 'frontmatter': self.frontmatter or {},
                'week_notes': self.week_notes or {},
                'status': self.status, 'generated_by': self.generated_by,
                'synced_at': iso(self.synced_at)}


class WeeklyCanvas(db.Model):
    """Weekly planning canvas — a matrix of positioned cards (parent_weekly_task).
    Yjs real-time state was DROPPED; cards live in JSONB (position/size/color/title)."""
    __tablename__ = 'weekly_canvas'
    __table_args__ = (db.UniqueConstraint('week_iso'),)
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    week_iso = db.Column(db.String(16), nullable=False)   # board_key ("YYYY-Www")
    year = db.Column(db.Integer)
    week_number = db.Column(db.Integer)
    archived = db.Column(db.Boolean, default=False)
    tasks = db.Column(JSONB_)                              # [{task_id,title,color,position,size,...}]
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    last_modified_by = db.Column(db.String(64))

    def to_dict(self):
        return {'id': self.id, 'week_iso': self.week_iso, 'archived': self.archived,
                'tasks': self.tasks or [], 'updated_at': iso(self.updated_at)}


class DriveThumbnail(db.Model):
    """Persistent Drive thumbnail cache (Postgres, replacing the old Mongo
    drive_thumbnails)."""
    __tablename__ = 'drive_thumbnails'
    file_id = db.Column(db.String(128), primary_key=True)
    width = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.LargeBinary)
    mime = db.Column(db.String(128))
    cached_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class LegacyCardStatus(db.Model):
    """Archive of the abandoned sharing_card_status (W20-W22 caption generation
    history).

    Does NOT participate in the live flow; kept only as raw JSONB to prevent data
    loss.
    """
    __tablename__ = 'legacy_card_status'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    week_iso = db.Column(db.String(16))
    card_index = db.Column(db.Integer)
    raw = db.Column(JSONB_)


class UploadReview(db.Model):
    """The client's **per-upload** decision on the approval page (2026-07-24).

    The approval page no longer shows sharing board shares, but the post/video
    files (`card_uploads`) the designer uploaded that week; the client's
    approve/revise decision is kept here too. `upload_id` is UNIQUE → one (most
    recent) decision per upload; if the client changes their mind, it's overwritten.
    Old `Share.client_review` records are left AS-IS (history isn't disturbed)."""
    __tablename__ = 'card_upload_reviews'
    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('card_uploads.id'),
                          nullable=False, unique=True)
    status = db.Column(db.String(24), nullable=False)  # approved | revision_requested
    note = db.Column(db.Text)
    at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'status': self.status, 'note': self.note, 'at': iso(self.at)}


class ReviewExcludedUpload(db.Model):
    """Upload MANUALLY removed from the approval page (management/designer,
    2026-07-24).

    The client never sees a removed upload; staff opening the same page see it as
    removed and can undo it (non-destructive — the `card_uploads` row is left
    untouched). The scope is per-upload: even if the link is revoked and a new one
    is generated, the decision is preserved."""
    __tablename__ = 'review_excluded_uploads'
    upload_id = db.Column(db.Integer, db.ForeignKey('card_uploads.id'), primary_key=True)
    excluded_by = db.Column(db.String(64))   # SSO sub
    excluded_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class PreApprovalLink(db.Model):
    """Pre-approval link (2026-07-24): the designer generates it, sends it to
    MANAGEMENT.

    The internal counterpart of the client approval link (`ReviewLink`) — same
    (client, week) scope, but only staff can open the page and only management can
    make the decision. The token lives in a separate table;
    `/review/<token>` resolves either token type."""
    __tablename__ = 'pre_approval_links'
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_iso = db.Column(db.String(16), nullable=False)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)


class UploadPreApproval(db.Model):
    """Manager's pre-approval decision (2026-07-24) — per upload.

    Kept SEPARATE from the client's decision (`UploadReview`): one is an internal
    gate, the other is client feedback. `upload_id` is UNIQUE → one/most-recent
    decision per upload.

    **Gradual gate:** if a (client, week) has AT LEAST ONE pre-approval decision,
    the client approval link shows only the `approved` ones; if there's no decision
    at all, the gate is inactive (old weeks and the usual flow aren't broken) — see
    `sharing.review_visible_uploads`."""
    __tablename__ = 'card_upload_pre_approvals'
    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('card_uploads.id'),
                          nullable=False, unique=True)
    status = db.Column(db.String(24), nullable=False)  # approved | revision_requested
    note = db.Column(db.Text)
    decided_by = db.Column(db.String(64))              # SSO sub (manager)
    at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'status': self.status, 'note': self.note, 'at': iso(self.at)}


class ClientApprovalLink(db.Model):
    """Client approval link — a **manually selected** list of uploads (2026-08-06).

    NOT a twin of `ReviewLink`, deliberately a separate flow (project owner
    decision: the old `/review/<token>` flow was left untouched). Two key
    differences:

    1. **Not a (client_id, week_iso) scope, an explicit list:** `upload_ids` is
       frozen at the moment the link is generated. A file uploaded later does NOT
       leak into this link — whoever tells the client "I sent you these" knows
       exactly what they sent. That's also why there's no week boundary: selection
       is made from the sharing board's week ± 1 week window, so files from three
       weeks can end up in a single link.
    2. **Notebook:** the client writes a free-form note (`note`) in the area on the
       right of the page, auto-saved. `note_notified_at` marks that a notification
       was sent on the first write — so a bell doesn't ring on every keystroke.

    Approve/revise decisions are NOT kept separately: they're written to
    `card_upload_reviews` (UploadReview) → the ✅/📝 badges on the sharing board and
    the existing `client_review` notifications work the same way in this flow too.
    """
    __tablename__ = 'client_approval_links'
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    upload_ids = db.Column(JSONB_, nullable=False)   # list of ints, in selection order
    note = db.Column(db.Text)
    note_at = db.Column(db.DateTime(timezone=True))
    note_notified_at = db.Column(db.DateTime(timezone=True))
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {'id': self.id, 'token': self.token, 'client_id': self.client_id,
                'upload_ids': list(self.upload_ids or []), 'note': self.note,
                'note_at': iso(self.note_at), 'revoked': self.revoked,
                'created_at': iso(self.created_at)}
