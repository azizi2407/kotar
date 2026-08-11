"""Monthly report models (2026-08-07) — Meta/Instagram performance reports.

What's stored is **computed data**, NOT the uploaded CSVs: the raw files
carry client data and aren't needed to regenerate the report; keeping them
would be a leak surface with no value. For the same reason HTML isn't stored
either — it's regenerated from `data` on every view, so when the template is
updated, OLD reports also get the new look.
"""
from sqlalchemy.dialects.postgresql import JSONB

from extensions import db
from models import iso, utcnow

JSONB_ = JSONB(none_as_null=True).with_variant(db.JSON(none_as_null=True), 'sqlite')


class MonthlyReport(db.Model):
    """A client's performance report for one month.

    `client_id` CAN BE NULL: reports are generated in bulk from CSV folder
    names coming out of Meta, and those names don't always match panel client
    records (a client not yet onboarded, a different spelling). If a match
    can be made it's linked, if not the report is still generated —
    `client_name` is always populated and is the name shown.
    """
    __tablename__ = 'monthly_reports'
    __table_args__ = (db.Index('ix_reports_client_period', 'client_id', 'period'),)

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'))
    client_name = db.Column(db.String(200), nullable=False)
    period = db.Column(db.String(7), nullable=False)      # "YYYY-MM"
    data = db.Column(JSONB_, nullable=False)              # generate_report_data output
    warnings = db.Column(JSONB_)                          # warnings raised during generation
    # Public sharing: NULL until a token is generated — not every report is
    # opened up for sharing automatically, the user gets a link when they want one.
    token = db.Column(db.String(64), unique=True, index=True)
    revoked = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self, ozet=False):
        d = {'id': self.id, 'client_id': self.client_id, 'client_name': self.client_name,
             'period': self.period, 'token': self.token if not self.revoked else None,
             'revoked': self.revoked, 'created_by': self.created_by,
             'created_at': iso(self.created_at),
             'warnings': list(self.warnings or [])}
        if not ozet:
            d['data'] = self.data
        else:
            # A few headline metrics for the list view — carrying the whole
            # `data` would be unnecessary weight in a list of 30 reports.
            totals = (self.data or {}).get('totals') or {}
            d['ozet'] = {k: totals.get(k) for k in
                         ('Toplam Görüntüleme', 'Toplam Erişim', 'Toplam Etkileşim')}
            d['reklam_var'] = bool((self.data or {}).get('ads_data', {}).get('metrics'))
        return d
