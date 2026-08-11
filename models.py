"""Agency domain models — clients module + users_ref projection.

Identity lives in SSO; the `user_id`/`*_by` fields here hold the SSO `sub` (string).
users_ref is a lightweight SSO projection for displaying name/role (upserted at
login, seeded during migration) — it is NOT the source of truth.

On records migrated from the old Mongo, `legacy_mongo_id` is the idempotent import key.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from extensions import db

# none_as_null=True: Python None → SQL NULL (not the JSON 'null' scalar).
# The TypeEngine instance can be shared across columns.
JSON_ = db.JSON(none_as_null=True)


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat() if dt else None


class UserRef(db.Model):
    __tablename__ = 'users_ref'
    sub = db.Column(db.String(64), primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255))
    role = db.Column(db.String(32), nullable=False, default='pending')
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'sub': self.sub, 'email': self.email, 'name': self.name, 'role': self.role}


def upsert_user_ref(claims):
    """Insert/update the users_ref row from JWT claims (commit is the caller's job)."""
    ref = db.session.get(UserRef, str(claims['sub']))
    if ref is None:
        ref = UserRef(sub=str(claims['sub']))
        db.session.add(ref)
    ref.email = claims['email']
    ref.name = claims.get('name')
    ref.role = claims.get('role', 'pending')
    return ref


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    legacy_mongo_id = db.Column(db.String(24), unique=True)
    name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='active')  # active | deleted
    sector = db.Column(db.String(255))
    notes = db.Column(db.Text)
    client_email = db.Column(db.String(255))
    instagram_url = db.Column(db.String(512))
    google_drive_url = db.Column(db.String(512))
    special_days_token = db.Column(db.String(128))
    sharing_playbook = db.Column(JSON_)   # Sharing Board input (could get its own table in Phase 2)
    drive_meta = db.Column(JSON_)         # root folder links + occasional video/photo folders
    brand_profile = db.Column(JSON_)      # brand context for AI flows + client "Settings" authority:
                                          # brand_voice, target_audience, forbidden, cta, guide_md,
                                          # color_palette, content_mix, content_pillars, hashtags,
                                          # posting_days, ideas_per_week (all optional)
    caption_settings = db.Column(JSON_)   # per-client caption generation defaults (Phase 1b): model, tone,
                                          # emoji_limit, hashtag_count, lang, use_brief, char_limit (optional)
    # brief on/off (Phase 3): False → routine/catch-up won't auto-generate briefs for
    # this client (brief-inactive). server_default=true: existing rows stay on after the ALTER.
    brief_enabled = db.Column(db.Boolean, nullable=False,
                              server_default=text('true'), default=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True))
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))
    deleted_reason = db.Column(db.Text)
    restored_at = db.Column(db.DateTime(timezone=True))
    restored_by = db.Column(db.String(64))

    contract = db.relationship('ClientContract', uselist=False, cascade='all, delete-orphan',
                               backref='client')
    contacts = db.relationship('ClientContact', cascade='all, delete-orphan', backref='client',
                               order_by='ClientContact.id')
    locations = db.relationship('ClientLocation', cascade='all, delete-orphan', backref='client',
                                order_by='ClientLocation.id')
    team_assignments = db.relationship('ClientTeamAssignment', cascade='all, delete-orphan',
                                       backref='client')
    week_folders = db.relationship('ClientWeekFolder', cascade='all, delete-orphan',
                                   backref='client', order_by='ClientWeekFolder.week_number')

    def to_dict(self, full=False, sensitive=True):
        """`sensitive=False` → commercial and contact fields are COMPLETELY dropped from the response.

        Production roles (designer/content_creator/videographer) can read the client
        record in the brand guide context (2026-07-27, Brand Guide page); VAT/fee
        (`contract`), client contact info (`client_email`, `contacts`), addresses
        (`locations`), internal notes and the public page token aren't theirs to see.
        The field isn't hidden, it's simply NOT included — the panel reading
        `undefined` doesn't need to distinguish "no permission" from "empty".
        The role decision is made in one place, in api.py `_client_json()`."""
        d = {
            'id': self.id, 'name': self.name, 'status': self.status,
            'sector': self.sector,
            'instagram_url': self.instagram_url,
            'brief_enabled': self.brief_enabled,
            'created_at': iso(self.created_at), 'created_by': self.created_by,
            'updated_at': iso(self.updated_at), 'updated_by': self.updated_by,
            'team_assignments': {a.role_slot: a.user_id for a in self.team_assignments},
        }
        if sensitive:
            d['client_email'] = self.client_email
        if full:
            d.update({
                'google_drive_url': self.google_drive_url,
                'sharing_playbook': self.sharing_playbook,
                'drive_meta': self.drive_meta,
                'deleted_at': iso(self.deleted_at), 'deleted_by': self.deleted_by,
                'deleted_reason': self.deleted_reason,
                'restored_at': iso(self.restored_at), 'restored_by': self.restored_by,
                'week_folders': [w.to_dict() for w in self.week_folders],
            })
            if sensitive:
                d.update({
                    'notes': self.notes,
                    'special_days_token': self.special_days_token,
                    'contract': self.contract.to_dict() if self.contract else None,
                    'contacts': [c.to_dict() for c in self.contacts],
                    'locations': [l.to_dict() for l in self.locations],
                })
        return d


class ClientContract(db.Model):
    """Contract terms (1:1): agreement + shoot + financial fields."""
    __tablename__ = 'client_contracts'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, unique=True)
    weekly_content_count = db.Column(db.Integer)
    post_count = db.Column(db.Integer)
    story_count = db.Column(db.Integer)
    content_plan = db.Column(db.String(255))
    content_types = db.Column(JSON_)
    special_sharing_types = db.Column(JSON_)
    description = db.Column(db.Text)
    vat_rate = db.Column(db.Float)
    fee_effective_date = db.Column(db.String(32))
    video_shooting_enabled = db.Column(db.Boolean)
    weekly_video_count = db.Column(db.Integer)
    photo_shooting_enabled = db.Column(db.Boolean)
    weekly_photo_count = db.Column(db.Integer)
    drone_usage = db.Column(db.Boolean)
    location_notes = db.Column(db.Text)

    FIELDS = ('weekly_content_count', 'post_count', 'story_count', 'content_plan',
              'content_types', 'special_sharing_types', 'description', 'vat_rate',
              'fee_effective_date', 'video_shooting_enabled', 'weekly_video_count',
              'photo_shooting_enabled', 'weekly_photo_count', 'drone_usage', 'location_notes')

    def to_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}


class ClientContact(db.Model):
    __tablename__ = 'client_contacts'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(64))
    notes = db.Column(db.Text)

    def to_dict(self):
        return {'name': self.name, 'email': self.email, 'phone': self.phone, 'notes': self.notes}


class ClientLocation(db.Model):
    __tablename__ = 'client_locations'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    name = db.Column(db.String(255))
    address = db.Column(db.Text)

    def to_dict(self):
        return {'name': self.name, 'address': self.address}


class ClientTeamAssignment(db.Model):
    """Fixed role slots: videographer_shoot / videographer_edit / content_creator / designer /
    manager (the "My Clients" marker on the management board; only the assigned manager sets it)."""
    __tablename__ = 'client_team_assignments'
    __table_args__ = (db.UniqueConstraint('client_id', 'role_slot'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    role_slot = db.Column(db.String(32), nullable=False)
    user_id = db.Column(db.String(64), nullable=False)  # SSO sub


class UserHiddenClient(db.Model):
    """Personal "don't show this client on this page" preference (2026-07-25).

    The EXISTENCE of the row = hidden. Soft-delete is DELIBERATELY absent — same
    rationale as `client_tracking_entries`: it occupies the UNIQUE slot, and
    unhiding by rewriting the same row raises IntegrityError. "Unhide" = DELETE the row.

    WHY NOT a single-row JSON array: the lesson `planning.py` taught — a model that
    rewrites a single column wholesale silently loses one write when two tabs/two
    requests toggle at the same time (read-modify-write). The row model makes each
    toggle atomic, makes the UNIQUE constraint guard the race, and turns the filter
    into a single `IN` query.

    WHY NOT `AppSetting`: that's a GLOBAL key/value store (`key` String(64));
    keying it as `vg_hidden:<sub>` breaks the semantics, risks overflow on a long sub,
    and turns the value into an unqueryable JSON blob. WHY NOT a `users_ref` column:
    users_ref is an SSO projection (overwritten at login) + a new column would need
    a manual ALTER in prod.

    `scope`: if another page (e.g. designer board) wants hiding in the future, the
    same table serves it with a different scope instead of opening a second table."""
    __tablename__ = 'user_hidden_clients'
    __table_args__ = (
        db.UniqueConstraint('owner_sub', 'scope', 'client_id',
                            name='uq_hidden_owner_scope_client'),
        db.Index('ix_hidden_owner_scope', 'owner_sub', 'scope'),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_sub = db.Column(db.String(64), nullable=False)   # SSO sub — NOT an FK (identity lives in SSO)
    scope = db.Column(db.String(32), nullable=False, default='videographer_upload')
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class ClientAsset(db.Model):
    """Client brand images: logo + fixed standard images (e.g. product labels).
    Selected as a reference in AI image generation; the file sits in the 'Brand Images'
    subfolder on Drive. kind='logo' has ONE active row per client (a new one
    soft-deletes the old one); kind='standard' is multiple."""
    __tablename__ = 'client_assets'
    __table_args__ = (db.Index('ix_client_assets_client', 'client_id'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    kind = db.Column(db.String(16), nullable=False)  # logo | standard
    file_id = db.Column(db.String(128), nullable=False)
    file_name = db.Column(db.String(512))
    mime_type = db.Column(db.String(128))
    file_size = db.Column(db.BigInteger)
    label = db.Column(db.String(256))  # display name in the panel (e.g. "White cheese label")
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'kind': self.kind,
                'file_id': self.file_id, 'file_name': self.file_name,
                'mime_type': self.mime_type, 'file_size': self.file_size,
                'label': self.label,
                'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None}


class ClientWeekFolder(db.Model):
    """Per-week Drive folders (replacing the old week_folders.{1..52} map)."""
    __tablename__ = 'client_week_folders'
    __table_args__ = (db.UniqueConstraint('client_id', 'week_number'),)
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    week_number = db.Column(db.Integer, nullable=False)
    folder_id = db.Column(db.String(128))
    name = db.Column(db.String(255))
    link = db.Column(db.String(512))

    def to_dict(self):
        return {'week_number': self.week_number, 'folder_id': self.folder_id,
                'name': self.name, 'link': self.link}


class Notification(db.Model):
    """In-panel notification. Ops Digest (Phase 4) and revision/approval notifications
    flow from this table; the panel bell reads it. **2026-08-05:** ntfy came back but
    NOT in place of this table — the `severity` field determines which rows also go
    to the phone (decision in `notify_rules.should_push_ntfy`, delivery in `ntfy_gateway`)."""
    __tablename__ = 'notifications'
    __table_args__ = (db.Index('ix_notifications_recipient', 'recipient_sub', 'read_at'),)
    id = db.Column(db.Integer, primary_key=True)
    recipient_sub = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    # kritik | normal | bilgi — default comes from the catalog (notifications.CATALOG).
    # server_default: so existing rows don't stay NULL after the ALTER (2026-08-05).
    severity = db.Column(db.String(16), nullable=False, default='normal',
                         server_default='normal')
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text)
    link = db.Column(db.String(512))
    read_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {'id': self.id, 'kind': self.kind, 'severity': self.severity,
                'title': self.title, 'body': self.body,
                'link': self.link, 'read_at': iso(self.read_at),
                'created_at': iso(self.created_at)}


class NotificationPref(db.Model):
    """Per-person notification preference (2026-08-05). If there's NO record, nothing
    goes to the phone — ntfy is deliberately OPT-IN (the in-panel bell already works for everyone).

    `ntfy_topic` is a random 32-hex string: in ntfy, read access relies on the topic
    name's secrecy (writes are token-based, see ntfy_gateway), so the topic must not
    be guessable and is only shown to its owner."""
    __tablename__ = 'notification_prefs'
    user_sub = db.Column(db.String(64), primary_key=True)
    ntfy_topic = db.Column(db.String(64), nullable=False)
    ntfy_enabled = db.Column(db.Boolean, nullable=False, default=False)
    min_severity = db.Column(db.String(16), nullable=False, default='kritik')
    # Quiet-hours range (0-23, wrapping range is fine: 22→08). NULL = none.
    quiet_start = db.Column(db.Integer)
    quiet_end = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'ntfy_topic': self.ntfy_topic, 'ntfy_enabled': self.ntfy_enabled,
                'min_severity': self.min_severity,
                'quiet_start': self.quiet_start, 'quiet_end': self.quiet_end}


class RateWindow(db.Model):
    """Shared fixed-window rate limit counter (Postgres/sqlite; shared across
    workers — replaces the old in-memory per-worker one). bucket e.g. 'review:<token>'."""
    __tablename__ = 'rate_limits'
    bucket = db.Column(db.String(160), primary_key=True)
    window_start = db.Column(db.Integer, primary_key=True)  # unix window start
    count = db.Column(db.Integer, nullable=False, default=0)


class Job(db.Model):
    """Async job queue (Postgres SKIP LOCKED). type:
    caption|media|special_days|brief|ops_digest|videographer_ideas|image_gen|codex_image|similarity|magnific_credits|prompt_examples|prompt_convert. The worker
    (running as the project owner) claims, processes, and writes the result. The panel polls. priority: a higher number
    is claimed first (interactive caption=10, batch=0)."""
    __tablename__ = 'jobs'
    __table_args__ = (db.Index('ix_jobs_status_type', 'status', 'type'),)
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='queued')  # queued|running|done|failed
    priority = db.Column(db.Integer, nullable=False, server_default='0', default=0)  # higher=claimed first
    payload = db.Column(JSON_)
    result = db.Column(JSON_)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    claimed_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    available_at = db.Column(db.DateTime(timezone=True), nullable=True)  # backoff/retry: not claimed before this moment

    def to_dict(self):
        return {'id': self.id, 'type': self.type, 'status': self.status,
                'result': self.result, 'created_at': iso(self.created_at),
                'finished_at': iso(self.finished_at)}


class AppSetting(db.Model):
    """Simple global key/value settings store (editable from the panel). First use:
    `caption_global_rules` — caption/hashtag rules shared across all clients
    (seeded from a vault snapshot, see scripts/seed_brand_profiles.py)."""
    __tablename__ = 'app_settings'
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @classmethod
    def get(cls, key, default=None):
        row = db.session.get(cls, key)
        return row.value if row is not None else default

    @classmethod
    def set(cls, key, value):
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value = value
        return row


class AiUsage(db.Model):
    """Per-call token/cost trace for `claude -p` (Server Settings token panel).

    `ai_claude.run` writes one row per successful call (via usage_sink).
    Does NOT contain secrets/prompts — counters only. `source` = job type (attribution)."""
    __tablename__ = 'ai_usage'
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)
    model = db.Column(db.String(64))
    source = db.Column(db.String(32))
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    cache_read_tokens = db.Column(db.Integer, nullable=False, default=0)
    cache_creation_tokens = db.Column(db.Integer, nullable=False, default=0)
    cost_usd = db.Column(db.Float, nullable=False, default=0.0)


class AdCampaign(db.Model):
    """Ad tracking (2026-07-24) — ad spends per client: date range, amount spent (₺),
    platform, status, result metrics and notes.

    Only `management` sees/edits it (financial data). Deletion is SOFT (`deleted_at`) —
    record history is preserved, dropped from lists. Amount is `Numeric(12,2)`: kept
    with cent precision (we don't want Float rounding errors); `to_dict` converts to
    float (Decimal can't be serialized to JSON)."""
    __tablename__ = 'ad_campaigns'
    __table_args__ = (db.Index('ix_ad_campaigns_client_start', 'client_id', 'start_date'),)

    PLATFORMS = ('meta', 'google', 'tiktok', 'other')
    STATUSES = ('planned', 'active', 'finished')

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    title = db.Column(db.String(255))
    platform = db.Column(db.String(16), nullable=False, default='meta')
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)                      # NULL = ongoing / single day
    amount_spent = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    status = db.Column(db.String(16), nullable=False, default='active')
    reach = db.Column(db.Integer)                      # reach (entered manually)
    clicks = db.Column(db.Integer)                     # clicks (entered manually)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.String(64))              # SSO sub
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    client = db.relationship('Client', backref=db.backref('ad_campaigns', lazy='select'))

    def to_dict(self):
        return {
            'id': self.id, 'client_id': self.client_id,
            'client_name': self.client.name if self.client else None,
            'title': self.title, 'platform': self.platform,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'amount_spent': float(self.amount_spent or 0),
            'status': self.status, 'reach': self.reach, 'clicks': self.clicks,
            'notes': self.notes,
            'created_at': iso(self.created_at), 'updated_at': iso(self.updated_at),
        }


class TrackingItem(db.Model):
    """Client Tracking item catalog (2026-07-25) — the MANAGEABLE definition of work
    items the agency can sell to a client (add/edit/reorder from the panel; no code changes).

    TWO SEPARATE deactivation mechanisms, deliberately:
      * active=False → hides it from the catalog but EXISTING client records
        (client_tracking_entries) stay put; "we don't sell this anymore but keep the history".
      * deleted_at   → soft-delete; not listed anywhere, row is preserved.

    `key` is only populated on SEEDED items (seed idempotency key); it's NULL for
    ones added from the panel. There's NO DB unique on `name` — a partial index
    (WHERE deleted_at IS NULL) would diverge between Postgres/sqlite; uniqueness is
    enforced at the endpoint via Turkish-aware normalization (client_tracking._fold)."""
    __tablename__ = 'tracking_items'
    __table_args__ = (db.Index('ix_tracking_items_order', 'position', 'id'),)

    CATEGORIES = ('hukuki', 'dijital', 'tasarim', 'uretim', 'reklam', 'diger')

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(48), unique=True)         # seed key; NULL when manually added
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(24), nullable=False, default='diger')
    icon = db.Column(db.String(40))                     # lucide icon name (panel resolves it via allowlist)
    position = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, server_default=text('true'), default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))               # SSO sub
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'key': self.key, 'name': self.name,
                'category': self.category, 'icon': self.icon,
                'position': self.position, 'active': self.active}


class ClientTrackingEntry(db.Model):
    """Client × item STATUS CELL (2026-07-25). UNIQUE(client_id, item_id) →
    the cell is upserted, never duplicated.

    WHY THERE'S NO SOFT-DELETE: a unique constraint + `deleted_at` don't work together
    (a deleted row still occupies the slot, re-recording raises IntegrityError; the fix
    would be a partial index = dialect divergence). This row is a STATE, not a
    document; "deleting" = setting status back to 'yok'. History lives in
    `client_activity_notes`."""
    __tablename__ = 'client_tracking_entries'
    __table_args__ = (
        db.UniqueConstraint('client_id', 'item_id', name='uq_client_tracking_entry'),
        db.Index('ix_client_tracking_entries_client', 'client_id'),
    )

    STATUSES = ('var', 'yok', 'surecte', 'ilgilenmiyor')

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('tracking_items.id'), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='yok')
    status_date = db.Column(db.Date)          # day the status applies to (e.g. registration date)
    note = db.Column(db.Text)
    url = db.Column(db.String(1024))          # http(s) required (validated at the endpoint)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'item_id': self.item_id,
                'status': self.status,
                'status_date': self.status_date.isoformat() if self.status_date else None,
                'note': self.note, 'url': self.url,
                'updated_at': iso(self.updated_at), 'updated_by': self.updated_by}


class ClientActivityNote(db.Model):
    """Dated free-text activity log (2026-07-25) — "what's been done lately".

    Append-only stream; deletion is SOFT. The reason it's a SEPARATE table from the
    status cell is that the two kinds of data behave differently: "is there a website"
    is a single/overwritable state, "what was last done" is a cumulative stream.
    `item_id` is optional: a note can be tied to an item ("catalog printed" → catalog
    item) but can also stand alone."""
    __tablename__ = 'client_activity_notes'
    __table_args__ = (db.Index('ix_client_activity_notes_client_date',
                               'client_id', 'happened_on'),)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    happened_on = db.Column(db.Date, nullable=False)   # event day (defaults to today)
    text = db.Column(db.Text, nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('tracking_items.id'))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))              # "author" — SSO sub
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self, author_name=None):
        return {'id': self.id, 'client_id': self.client_id, 'item_id': self.item_id,
                'happened_on': self.happened_on.isoformat() if self.happened_on else None,
                'text': self.text, 'created_by': self.created_by,
                'author_name': author_name, 'created_at': iso(self.created_at)}
