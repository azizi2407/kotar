"""Planning Board schema (2026-07-25) — a PERSON-axis freeform board.

The old `weekly_canvas` (models_sharing) was week-axis: `week_iso` UNIQUE → one
board per week, shared across the whole agency, born empty every Monday. This
module replaces it; the axis is now **the board owner**:

  * `management`   — a SINGLE board shared by all managers (employees can't access it)
  * `user:<sub>`   — a SHARED workspace per person (the manager + that employee write to it)

`weekly_canvas` has been **archived** — these tables took over its data via a
migration script (`scripts/migrate_planning_boards.py`); the source table remains
as read-only (a rollback path).

WHY ROW-PER-ITEM INSTEAD OF A JSONB ARRAY: the old model kept all cards in a
single JSONB column; dragging one card rewrote the entire 588-card column
(~300 KB WAL), and when two people wrote at the same time one would silently
disappear. The row model is the natural fit for delta PATCH; it also makes
status/due-date/assignee queryable as structured data.
"""
# `text` is used as a column name in the class body (PlanningItem.text) → alias
# sqlalchemy.text, otherwise it's shadowed in the class scope and can't be called.
from sqlalchemy import text as sa_text

from extensions import db
from models import iso, utcnow
from models_sharing import JSONB_

# Board kinds and item vocabularies (endpoint-side validation looks at these).
BOARD_KINDS = ('management', 'user')
# `image` (2026-07-28): an image pasted/dragged onto the board. The file lives on
# the server (`planning_images`, one directory per board), the item points to it
# via `extra.image = {name,w,h}` — not the `link` column, because that has
# http(s) validation and this is a file name. The `type` column is varchar(16),
# NOT a Postgres enum → no ALTER was needed for the new type.
ITEM_TYPES = ('card', 'note', 'region', 'edge', 'image')
ITEM_STATUSES = ('open', 'done')
ITEM_SOURCES = ('panel', 'legacy')


class PlanningBoard(db.Model):
    """A planning board. Its identity is `board_key` alone.

    WHY NOT `UNIQUE(kind, owner_sub)`: in Postgres, NULLs aren't considered equal
    to each other → MULTIPLE rows could silently be created for the management
    board where `owner_sub IS NULL`, and which one gets read would depend on
    `first()`'s ordering. The single source of truth is `board_key` ('management'
    | 'user:<sub>'); `kind`/`owner_sub` are derived columns kept only for
    read/query convenience, they carry no constraint.

    `version` increments by +1 on every write. This is NOT a gate, it's a "someone
    wrote" signal: the panel polls the cheap `/version` endpoint and refreshes the
    board if needed.

    The board title isn't kept in the DB — it's derived at the endpoint
    ('Yönetim Panosu' / UserRef.name), so the board name updates automatically
    when the user's name changes."""
    __tablename__ = 'planning_boards'

    id = db.Column(db.Integer, primary_key=True)
    board_key = db.Column(db.String(64), nullable=False, unique=True)
    kind = db.Column(db.String(16), nullable=False, default='user')
    owner_sub = db.Column(db.String(64))          # SSO sub if kind='user'; NULL for management
    version = db.Column(db.Integer, nullable=False, server_default=sa_text('1'), default=1)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_modified_by = db.Column(db.String(64))

    items = db.relationship('PlanningItem', backref='board', lazy='select',
                            cascade='all, delete-orphan')

    def to_dict(self, title=None, item_count=None, last_modified_name=None):
        return {'key': self.board_key, 'kind': self.kind, 'owner_sub': self.owner_sub,
                'title': title, 'version': self.version,
                'updated_at': iso(self.updated_at),
                'last_modified_by': self.last_modified_by,
                'last_modified_name': last_modified_name,
                'item_count': item_count}


class PlanningItem(db.Model):
    """A single item on the board: card / note / region / connector line.

    `item_key` is generated client-side (the counterpart of the old `task_id`) and
    is unique within a board — it's the addressing unit for delta PATCH.

    SOFT-DELETE IS DELIBERATELY ABSENT: `UNIQUE(board_id, item_key)` doesn't work
    together with `deleted_at` — a deleted row would occupy the key, and undo
    (Ctrl+Z) writing the same item_key again would raise IntegrityError. The fix
    would be a partial index = a Postgres/sqlite divergence. The same rationale
    applies to `client_tracking_entries` (see the 03-veri.md invariants). Delete is
    HARD; undo re-INSERTs the row.

    `x`/`y` are NOT NULL: some old JSONB cards had no `position` field at all
    (2025-W44 and 2026-W13, 78 cards) and piled up at 0,0 with `left: undefined`
    in the panel. The schema shuts this off at the root.

    `rev` is for item-level conflict detection: the client sends the rev it knows,
    and if the server's is higher the item goes into `conflicts[]` (last write
    wins, the panel warns).

    `legacy_ref` is the migration idempotency key ('weekly_canvas:<week_iso>:<task_id>');
    it's filled only for migrated rows, stays NULL for ones added from the panel."""
    __tablename__ = 'planning_items'
    __table_args__ = (
        db.UniqueConstraint('board_id', 'item_key', name='uq_planning_item_key'),
        db.Index('ix_planning_items_board', 'board_id'),
        # The assignee index is for the cross-board `GET /assigned` query: that
        # endpoint looks at assignee_sub WITHOUT a board_id filter — without an
        # index it would be a seq scan.
        db.Index('ix_planning_items_assignee', 'assignee_sub'),
        db.Index('ix_planning_items_shoot', 'shoot_task_id'),
        db.Index('ix_planning_items_campaign', 'ad_campaign_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('planning_boards.id'), nullable=False)
    item_key = db.Column(db.String(64), nullable=False)

    type = db.Column(db.String(16), nullable=False, default='card')
    title = db.Column(db.String(300))
    text = db.Column(db.Text)                      # note body / card description
    color = db.Column(db.String(16))               # #rrggbb
    label = db.Column(db.String(80))
    link = db.Column(db.String(1024))              # http(s) only — validated at the endpoint

    x = db.Column(db.Integer, nullable=False, server_default=sa_text('0'), default=0)
    y = db.Column(db.Integer, nullable=False, server_default=sa_text('0'), default=0)
    width = db.Column(db.Integer)                  # NULL = type default (the panel decides)
    height = db.Column(db.Integer)
    z = db.Column(db.Integer, nullable=False, server_default=sa_text('0'), default=0)

    from_key = db.Column(db.String(64))            # type='edge' source
    to_key = db.Column(db.String(64))              # type='edge' target

    status = db.Column(db.String(16), nullable=False, default='open')   # open | done
    due_date = db.Column(db.Date)
    assignee_sub = db.Column(db.String(64))        # users_ref.sub (not an FK — identity lives in SSO)

    # Domain links — tie the card to the rest of the panel. `extra` is NOT jsonb,
    # a real FK: validated (no dead reference pointing at a deleted campaign),
    # indexed and queryable. `ondelete='SET NULL'` is deliberate — when a
    # shoot/campaign is deleted the card lives on, only the link breaks (the card
    # is planning data, not that record's own).
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    shoot_task_id = db.Column(db.Integer, db.ForeignKey('shoot_tasks.id', ondelete='SET NULL'))
    ad_campaign_id = db.Column(db.Integer, db.ForeignKey('ad_campaigns.id', ondelete='SET NULL'))
    extra = db.Column(JSONB_)                      # migration leftovers (legacy_status/metadata/week_iso)

    source = db.Column(db.String(16), nullable=False, default='panel')  # panel | legacy
    legacy_ref = db.Column(db.String(96), unique=True)
    rev = db.Column(db.Integer, nullable=False, server_default=sa_text('1'), default=1)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(64))

    def to_dict(self, assignee_name=None, updated_by_name=None,
                client_name=None, shoot_title=None, campaign_title=None):
        """Derived names (…_name/…_title) are passed in FROM OUTSIDE, not read from
        the relationship.

        Rationale: writing `self.client.name` would spawn a lazy query per item and
        break the `test_board_get_sorgu_sayisi_oge_sayisindan_bagimsiz` guard. The
        caller (`planning._items_payload`) resolves the names in a single batched
        query."""
        return {
            'item_key': self.item_key, 'type': self.type,
            'title': self.title, 'text': self.text, 'color': self.color,
            'label': self.label, 'link': self.link,
            'x': self.x, 'y': self.y, 'width': self.width, 'height': self.height,
            'z': self.z, 'from_key': self.from_key, 'to_key': self.to_key,
            'status': self.status,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'assignee_sub': self.assignee_sub, 'assignee_name': assignee_name,
            'client_id': self.client_id, 'client_name': client_name,
            'shoot_task_id': self.shoot_task_id, 'shoot_title': shoot_title,
            'ad_campaign_id': self.ad_campaign_id, 'campaign_title': campaign_title,
            'extra': self.extra or {},
            'rev': self.rev, 'updated_at': iso(self.updated_at),
            'updated_by': self.updated_by, 'updated_by_name': updated_by_name,
        }
