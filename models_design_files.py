"""Design working files (2026-08-07) — a client-scoped, VERSIONED file area.

**Why not `card_uploads`:** that table is a week-scoped DELIVERY channel
(output going to the client). A working file doesn't belong to a week, it
belongs to the brand, and never goes to the client at all.

**Why not `depot_files`:** the depot is NOT client-scoped and belongs to the
videographer team; here the file is a specific client's material.

**Why two tables:** "v3 of the same template" can only be expressed once the
logical file (identity) is separated from the upload (content). A single
table with a self-referencing `parent_id` was also possible, but then "which
row is canonical" would need to be re-resolved on every query.

Files are CANONICAL on the server
(`data/design-files/<sha[:2]>/<sha>.<ext>`), best-effort COPIED to Drive:
download authorization can only be enforced while the file is ours (depot
files sit under 'anyone with the link can download' permission — which is
unacceptable for a client's source file).
"""
from extensions import db
from models import JSON_, iso, utcnow


class DesignFile(db.Model):
    """Logical working file: identity is `title`, content lives in versions.

    A `current_version_id` counter column is DELIBERATELY absent — every
    crash between the Drive upload and the DB commit would leave a permanent
    drift (same lesson as `depot_files`'s quota). The current version is
    found via `MAX(version_no)`; the file count per client is two digits."""
    __tablename__ = 'design_files'
    __table_args__ = (db.Index('ix_design_files_client', 'client_id', 'deleted_at'),)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    tags = db.Column(JSON_)                # free-form tag list (["template", ...])
    created_by = db.Column(db.String(64))  # SSO sub
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))              # SSO sub — trash (2026-08-08)
    purge_requested_at = db.Column(db.DateTime(timezone=True))
    purge_requested_by = db.Column(db.String(64))      # SSO sub

    def to_dict(self, current=None, version_count=0, uploader_name=None,
                can_delete=False, can_restore=False, can_purge=False,
                deleter_name=None, purge_requester_name=None):
        """`current`: the output of the current `DesignFileVersion.to_dict()`, or None.

        `can_delete`/`can_restore`/`can_purge` are computed on the BACKEND
        (same pattern as fonts/`_build_rows`) — the panel doesn't rebuild the
        rule, so it can't diverge and make the button lie. The trash fields
        (`deleted_by`, `purge_*`) already come back NULL/False in the live
        list, they're only meaningful in the trash response."""
        return {'id': self.id, 'client_id': self.client_id, 'title': self.title,
                'tags': list(self.tags or []),
                'created_by': self.created_by, 'created_at': iso(self.created_at),
                'creator_name': uploader_name,
                'current': current, 'version_count': version_count,
                'can_delete': bool(can_delete),
                'deleted_at': iso(self.deleted_at), 'deleted_by': self.deleted_by,
                'deleter_name': deleter_name,
                'can_restore': bool(can_restore), 'can_purge': bool(can_purge),
                'purge_requested_at': iso(self.purge_requested_at),
                'purge_requested_by': self.purge_requested_by,
                'purge_requester_name': purge_requester_name}


class DesignFileVersion(db.Model):
    """A single upload. UNIQUE(file_id, version_no): if two people upload a
    version at the same time, the second gets a 409 — saying "someone else
    uploaded, refresh" is the correct behavior, rather than silently
    producing v4/v5 and making one person's work invisible."""
    __tablename__ = 'design_file_versions'
    __table_args__ = (
        db.UniqueConstraint('file_id', 'version_no', name='uq_design_file_version'),
        db.Index('ix_design_versions_file', 'file_id', 'deleted_at'),
        db.Index('ix_design_versions_sha', 'sha256'),
    )

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('design_files.id'), nullable=False)
    version_no = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)   # disk name AND content hash
    file_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120))
    # BigInteger + NOT NULL: quota arithmetic runs off this column, it can
    # never be NULL (same rationale as `depot_files.file_size`).
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    note = db.Column(db.String(300))
    drive_file_id = db.Column(db.String(80))   # NULL = no Drive copy / it failed
    uploaded_by = db.Column(db.String(64))
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by = db.Column(db.String(64))      # SSO sub — trash (2026-08-08)

    def to_dict(self, uploader_name=None, can_delete=False, can_restore=False,
                can_purge=False, deleter_name=None):
        return {'id': self.id, 'file_id': self.file_id, 'version_no': self.version_no,
                'file_name': self.file_name, 'mime_type': self.mime_type,
                'file_size': self.file_size, 'note': self.note,
                'sha256': self.sha256,
                # Source of the "not copied to Drive" badge in the panel.
                'drive_ok': bool(self.drive_file_id),
                'uploaded_by': self.uploaded_by, 'uploader_name': uploader_name,
                'uploaded_at': iso(self.uploaded_at),
                'can_delete': bool(can_delete),
                'deleted_at': iso(self.deleted_at), 'deleted_by': self.deleted_by,
                'deleter_name': deleter_name,
                'can_restore': bool(can_restore), 'can_purge': bool(can_purge)}
