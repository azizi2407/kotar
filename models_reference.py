"""Client example (reference) accounts — 2026-08-07.

Examples the designer/videographer look at for "how is content produced for
this client": Instagram accounts in the same sector doing the same kind of work.

**There's an approval gate (project owner's decision):** accounts are born as
`candidate`, management reviews them one by one and marks them
`approved`/`rejected`. Only `approved` ones show up to production roles in the
brand guide. This is the same pattern as the project's AI content gate: an
automatically compiled list isn't shown to the team without human approval —
otherwise a wrong-sector or dead account would look like "the agency's suggestion"."""
from extensions import db
from models import iso, utcnow

REFERENCE_STATUSES = ('candidate', 'approved', 'rejected')


class ClientReferenceAccount(db.Model):
    """An Instagram account that can be used as an example for a client.

    `handle` is UNIQUE within a client: nominating the same account twice
    pollutes the list. The same handle is free across different clients — one
    account can be an example for multiple clients (e.g. two construction firms).
    """
    __tablename__ = 'client_reference_accounts'
    __table_args__ = (
        db.UniqueConstraint('client_id', 'handle', name='uq_client_reference_handle'),
        db.Index('ix_reference_client_status', 'client_id', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    handle = db.Column(db.String(64), nullable=False)      # NO '@', plain username
    title = db.Column(db.String(200))                      # display name / business name
    note = db.Column(db.Text)                              # why it's an example: what it does well
    followers = db.Column(db.Integer)                      # rough count at time of adding
    # Source trail: added manually or compiled via research. The answer to
    # "did I add this?" when management is reviewing.
    source = db.Column(db.String(16), nullable=False, default='manual')   # manual | research
    status = db.Column(db.String(16), nullable=False, default='candidate')
    added_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    decided_by = db.Column(db.String(64))
    decided_at = db.Column(db.DateTime(timezone=True))

    @property
    def url(self):
        return f'https://www.instagram.com/{self.handle}/'

    def to_dict(self):
        return {'id': self.id, 'client_id': self.client_id, 'handle': self.handle,
                'title': self.title, 'note': self.note, 'followers': self.followers,
                'url': self.url, 'source': self.source, 'status': self.status,
                'added_by': self.added_by, 'created_at': iso(self.created_at),
                'decided_by': self.decided_by, 'decided_at': iso(self.decided_at)}
