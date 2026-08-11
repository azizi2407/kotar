"""Voice note (2026-08-09) — audio → transcript → structured note.

Fethi (management) isn't good at keeping notes; let him talk and leave it, have the
system transcribe it, and let the resulting tasks be pushed to the planning board.

**Why a separate table (not `shares`/`card_uploads`):** this isn't a DELIVERABLE,
it's a personal thinking space. It's not tied to a client/week, isn't visible to
anyone but its owner, and its lifecycle (queue status → agent output → push to
board) is entirely its own.
"""
import re
from datetime import date

from extensions import db
from models import JSON_, iso, utcnow

STATUSES = ('queued', 'running', 'done', 'failed')

# Date in the agent's output: ISO ONLY (YYYY-MM-DD). The agent is responsible for
# resolving relative expressions ("next Tuesday") against today's date;
# if it can't resolve one, it must leave it null. Free text leaking through here
# would break the date field in the panel.
#
# Validation MUST be TWO-STAGE:
#   1) format: `YYYY-MM-DD` only (regex) — `date.fromisoformat` alone isn't
#      enough, because on Python 3.11+ it also accepts unseparated `20260220`
#      and ISO week format `2026-W07-3`, forms the board doesn't expect.
#   2) calendar: even with the right format, it could be a date that doesn't
#      exist, like `2026-02-30` or `2026-13-01` — the regex can't catch that,
#      so `date.fromisoformat` confirms it's actually resolvable.
# This field ultimately gets written to the planning board's `PlanningItem.due_date`
# (Postgres `Date`) column; if a calendrically invalid value slips past the regex
# and reaches there, it blows up with `DataError` and the user sees a 500 on
# "Add to board". Since `normalize_structured` is the ONLY line of defense against
# agent output, validation has to be complete right here.
_ISO_TARIH = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _gecerli_iso_tarih(v):
    """Is it in `YYYY-MM-DD` format AND a calendrically real date?"""
    if not _ISO_TARIH.match(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True

TITLE_MAX = 200
OZET_MAX = 2000
MADDE_MAX = 500
# MUST stay ALIGNED with the board's `planning.TITLE_MAX` (300): task text gets
# written to the board as `PlanningItem.title` (`NoteDetail.tsx` panoyaEkle). It
# used to be 500 — when a task over 300 chars got PATCHed to the board,
# `planning._clean_text` raised ValueError and the WHOLE batch came back with a 400
# (including the OTHER tasks in the batch — none of them got added). If it exceeds
# 300 it's truncated right here, so text going to the board already stays within the limit.
GOREV_MAX = 300
MADDE_ADET_MAX = 30
GOREV_ADET_MAX = 30


def _metin(v, sinir):
    return ' '.join(str(v).split())[:sinir] if isinstance(v, str) else ''


def _gorev(ham):
    """Filter a single task suggestion. If `metin` is missing the task is discarded (returns None)."""
    if not isinstance(ham, dict):
        return None
    metin = _metin(ham.get('metin'), GOREV_MAX)
    if not metin:
        return None
    cid = ham.get('client_id')
    try:
        cid = int(cid) if cid is not None and str(cid).strip() != '' else None
    except (TypeError, ValueError):
        cid = None
    sub = ham.get('assignee_sub')
    sub = str(sub).strip()[:64] if sub is not None and str(sub).strip() != '' else None
    tarih = ham.get('due_date')
    tarih = tarih if isinstance(tarih, str) and _gecerli_iso_tarih(tarih) else None
    return {'metin': metin, 'client_id': cid, 'assignee_sub': sub, 'due_date': tarih}


def normalize_structured(raw):
    """Treats the agent's output as UNTRUSTED and reduces it to the schema.

    Always returns four keys. Unknown keys are dropped, types are coerced,
    limits are enforced. Even when the agent produces JSON, it can make up
    field names or write the client's NAME instead of `client_id` — since the
    panel renders this dict directly, the cleanup happens RIGHT HERE."""
    raw = raw if isinstance(raw, dict) else {}
    maddeler = raw.get('maddeler')
    maddeler = maddeler if isinstance(maddeler, list) else []
    gorevler = raw.get('gorevler')
    gorevler = gorevler if isinstance(gorevler, list) else []
    return {
        'baslik': _metin(raw.get('baslik'), TITLE_MAX),
        'ozet': _metin(raw.get('ozet'), OZET_MAX),
        'maddeler': [m for m in (_metin(x, MADDE_MAX) for x in maddeler)
                     if m][:MADDE_ADET_MAX],
        'gorevler': [g for g in (_gorev(x) for x in gorevler)
                     if g][:GOREV_ADET_MAX],
    }


class VoiceNote(db.Model):
    """A single voice note: audio file + transcript + agent output + queue status."""
    __tablename__ = 'voice_notes'
    __table_args__ = (
        db.Index('ix_voice_notes_owner', 'owner_sub', 'deleted_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_sub = db.Column(db.String(64), nullable=False)   # SSO sub (NOT an FK — identity lives in SSO)

    audio_sha256 = db.Column(db.String(64), nullable=False)   # both the disk name AND the content hash
    audio_ext = db.Column(db.String(8), nullable=False)
    mime_type = db.Column(db.String(120))
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    duration_sec = db.Column(db.Integer)                   # ffprobe; NULL if unreadable

    status = db.Column(db.String(16), nullable=False, default='queued')
    transcript = db.Column(db.Text)
    structured = db.Column(JSON_)
    error = db.Column(db.String(500))
    # item_keys of tasks pushed to the board — so the same task isn't added twice.
    pushed_item_keys = db.Column(JSON_)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self, full=False):
        """`full=False` is the list view — does NOT carry `transcript` (can be
        kilobytes, and the list refreshes on every poll)."""
        s = self.structured if isinstance(self.structured, dict) else {}
        d = {'id': self.id, 'status': self.status,
             'baslik': s.get('baslik') or '',
             'duration_sec': self.duration_sec, 'file_size': self.file_size,
             'error': self.error, 'created_at': iso(self.created_at)}
        if full:
            d['transcript'] = self.transcript or ''
            d['structured'] = normalize_structured(s)
            d['pushed_item_keys'] = list(self.pushed_item_keys or [])
        return d
