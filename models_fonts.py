"""Font pool (2026-08-05) — central font store + client assignment.

**Why `kind='font'` wasn't added to `client_assets`:** that table has
`client_id` NOT NULL and is tied to a single client; here the model is N:N
(Montserrat is used by five clients, the file sits once) and a font with no
client is also valid (a trial font sitting in the pool). Also, brand images
live on Drive, but fonts live **on the server** (`data/fonts/`): the preview
downloads every font to the browser, and a Drive proxy would slow the page down.
"""
from extensions import db
from models import iso, utcnow

# Formats the browser can play via @font-face. Determined by the file
# signature, NOT the extension (see fonts.py `_format_of`) — these files are
# served inline to the browser, so "anything with a .ttf extension" can't be accepted.
FONT_FORMATS = ('ttf', 'otf', 'woff', 'woff2')
FONT_MIMES = {'ttf': 'font/ttf', 'otf': 'font/otf',
              'woff': 'font/woff', 'woff2': 'font/woff2'}


class Font(db.Model):
    """A single font FILE in the pool (not a family): 'Montserrat Bold' is one
    row, 'Montserrat Regular' is a separate row. The panel groups by family.

    `sha256` is the content hash and file name: `data/fonts/<sha256>.<ext>`. If
    the same file is uploaded a second time, no second copy is written to disk."""
    __tablename__ = 'fonts'
    __table_args__ = (db.Index('ix_fonts_sha', 'sha256'),
                      db.Index('ix_fonts_family', 'family'))

    id = db.Column(db.Integer, primary_key=True)
    family = db.Column(db.String(160), nullable=False)
    style = db.Column(db.String(64), nullable=False, default='Regular')
    file_name = db.Column(db.String(255), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    format = db.Column(db.String(8), nullable=False)      # ttf | otf | woff | woff2
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    uploaded_by = db.Column(db.String(64))                # SSO sub
    uploaded_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    deleted_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self, clients=None, uploader_name=None, can_delete=None):
        # `can_delete` is computed in the BACKEND (2026-08-06) — the panel
        # doesn't rebuild the rule. If the rule changes, the UI would silently
        # drift and the button would lie; the same pattern is used for video
        # uploads (`_build_rows`).
        return {'id': self.id, 'family': self.family, 'style': self.style,
                'file_name': self.file_name, 'format': self.format,
                'file_size': self.file_size, 'sha256': self.sha256,
                'uploaded_by': self.uploaded_by, 'uploader_name': uploader_name,
                'uploaded_at': iso(self.uploaded_at),
                'can_delete': bool(can_delete),
                'clients': clients if clients is not None else []}


class FontClient(db.Model):
    """Font ↔ client assignment. The row's EXISTENCE = assigned; removal
    deletes the row (no soft-delete — it would occupy the UNIQUE slot, same
    pattern as `user_hidden_clients`)."""
    __tablename__ = 'font_clients'
    __table_args__ = (db.UniqueConstraint('font_id', 'client_id', name='uq_font_client'),
                      db.Index('ix_font_clients_client', 'client_id'))

    id = db.Column(db.Integer, primary_key=True)
    font_id = db.Column(db.Integer, db.ForeignKey('fonts.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    assigned_by = db.Column(db.String(64))
    assigned_at = db.Column(db.DateTime(timezone=True), default=utcnow)
