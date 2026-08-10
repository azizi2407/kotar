"""Panel mail modülü modelleri — self-servis çoklu hesaplı webmail.

Kimlik SSO'da yaşar; `owner_sub` hesabın sahibinin SSO `sub`'ıdır. Parola
`secret_enc`'te Fernet-şifreli (mail_crypto), API'de ASLA dönmez. Gelen postalar
kalıcı: mail_messages (gövde dahil) + mail_attachments (metadata; bayt on-demand
IMAP'ten). Tablolar db.create_all ile açılır (ALTER yok).
"""
from extensions import db
from models import iso, utcnow


class MailAccount(db.Model):
    """Bir IMAP/SMTP posta hesabı (varsayılan mail sunucusu). owner_sub sahibi; is_shared ise
    ortak kutu (info@) — yalnız superadmin kurar. Parola Fernet-şifreli."""
    __tablename__ = 'mail_accounts'
    __table_args__ = (db.UniqueConstraint('owner_sub', 'email', name='uq_mail_owner_email'),
                      db.Index('ix_mail_accounts_owner', 'owner_sub'))
    id = db.Column(db.Integer, primary_key=True)
    owner_sub = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(255))
    imap_host = db.Column(db.String(255), nullable=False)
    imap_port = db.Column(db.Integer, nullable=False, default=993)
    imap_ssl = db.Column(db.Boolean, nullable=False, default=True)
    smtp_host = db.Column(db.String(255), nullable=False)
    smtp_port = db.Column(db.Integer, nullable=False, default=465)
    # 'ssl' (implicit, 465) | 'starttls' (587) | 'none'
    smtp_security = db.Column(db.String(16), nullable=False, default='ssl')
    secret_enc = db.Column(db.Text, nullable=False)  # Fernet-şifreli parola — API'de dönmez
    is_shared = db.Column(db.Boolean, nullable=False, default=False)
    poll_enabled = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_ok_at = db.Column(db.DateTime(timezone=True))
    last_error = db.Column(db.Text)

    def to_dict(self):
        """Güvenli projeksiyon — secret_enc/parola ASLA dönmez."""
        return {
            'id': self.id, 'owner_sub': self.owner_sub, 'email': self.email,
            'display_name': self.display_name,
            'imap_host': self.imap_host, 'imap_port': self.imap_port, 'imap_ssl': self.imap_ssl,
            'smtp_host': self.smtp_host, 'smtp_port': self.smtp_port,
            'smtp_security': self.smtp_security,
            'is_shared': self.is_shared, 'poll_enabled': self.poll_enabled, 'active': self.active,
            'has_secret': bool(self.secret_enc),
            'created_at': iso(self.created_at), 'updated_at': iso(self.updated_at),
            'last_ok_at': iso(self.last_ok_at), 'last_error': self.last_error,
        }


class MailFolder(db.Model):
    """Hesabın IMAP klasörü (LIST'ten). special_use: inbox|sent|drafts|trash|junk|archive|None."""
    __tablename__ = 'mail_folders'
    __table_args__ = (db.UniqueConstraint('account_id', 'path', name='uq_mail_folder_path'),)
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('mail_accounts.id'), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(512), nullable=False)
    flags = db.Column(db.Text)
    special_use = db.Column(db.String(16))
    uidvalidity = db.Column(db.BigInteger)
    last_sync_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'path': self.path,
                'special_use': self.special_use, 'last_sync_at': iso(self.last_sync_at)}


class MailMessage(db.Model):
    """Senkronlanan mesaj (kalıcı; gövde dahil). Ekler metadata; bayt on-demand."""
    __tablename__ = 'mail_messages'
    __table_args__ = (
        db.UniqueConstraint('account_id', 'uidvalidity', 'folder_id', 'uid',
                            name='uq_mail_msg_uid'),
        db.Index('ix_mail_msg_folder_date', 'folder_id', 'date'),
        db.Index('ix_mail_msg_thread', 'account_id', 'thread_key'),
    )
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('mail_accounts.id'), nullable=False, index=True)
    folder_id = db.Column(db.Integer, db.ForeignKey('mail_folders.id'), nullable=False, index=True)
    uid = db.Column(db.BigInteger, nullable=False)
    uidvalidity = db.Column(db.BigInteger, nullable=False)
    message_id = db.Column(db.String(512))
    in_reply_to = db.Column(db.String(512))
    references = db.Column(db.Text)
    thread_key = db.Column(db.String(512))
    from_addr = db.Column(db.String(255))
    from_name = db.Column(db.String(255))
    to_addrs = db.Column(db.Text)
    cc_addrs = db.Column(db.Text)
    subject = db.Column(db.String(998))
    date = db.Column(db.DateTime(timezone=True))
    snippet = db.Column(db.String(512))
    body_text = db.Column(db.Text)
    body_html = db.Column(db.Text)
    size = db.Column(db.Integer)
    seen = db.Column(db.Boolean, default=False)
    flagged = db.Column(db.Boolean, default=False)
    answered = db.Column(db.Boolean, default=False)
    draft = db.Column(db.Boolean, default=False)
    has_attachments = db.Column(db.Boolean, default=False)
    synced_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self, full=False):
        d = {
            'id': self.id, 'uid': self.uid, 'folder_id': self.folder_id,
            'message_id': self.message_id, 'thread_key': self.thread_key,
            'from_addr': self.from_addr, 'from_name': self.from_name,
            'to_addrs': self.to_addrs, 'cc_addrs': self.cc_addrs,
            'subject': self.subject, 'date': iso(self.date), 'snippet': self.snippet,
            'seen': self.seen, 'flagged': self.flagged, 'answered': self.answered,
            'has_attachments': self.has_attachments,
        }
        if full:
            d.update({'body_text': self.body_text, 'body_html': self.body_html,
                      'in_reply_to': self.in_reply_to, 'references': self.references,
                      'attachments': [a.to_dict() for a in self.attachments]})
        return d


class MailAttachment(db.Model):
    """Ek metadata — bayt on-demand IMAP'ten (part_id ile FETCH)."""
    __tablename__ = 'mail_attachments'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('mail_messages.id'), nullable=False, index=True)
    filename = db.Column(db.String(512))
    content_type = db.Column(db.String(128))
    size = db.Column(db.Integer)
    content_id = db.Column(db.String(255))
    part_id = db.Column(db.String(64))
    message = db.relationship('MailMessage', backref=db.backref('attachments', lazy='selectin',
                                                                cascade='all, delete-orphan'))

    def to_dict(self):
        return {'id': self.id, 'filename': self.filename, 'content_type': self.content_type,
                'size': self.size, 'part_id': self.part_id}


class MailDraft(db.Model):
    """Panelde yazılan taslak (gönderilmeden). İstenirse IMAP Drafts'a APPEND edilir."""
    __tablename__ = 'mail_drafts'
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('mail_accounts.id'), nullable=False, index=True)
    to_addrs = db.Column(db.Text)
    cc_addrs = db.Column(db.Text)
    bcc_addrs = db.Column(db.Text)
    subject = db.Column(db.String(998))
    body_text = db.Column(db.Text)
    body_html = db.Column(db.Text)
    in_reply_to_uid = db.Column(db.BigInteger)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {'id': self.id, 'account_id': self.account_id, 'to_addrs': self.to_addrs,
                'cc_addrs': self.cc_addrs, 'bcc_addrs': self.bcc_addrs, 'subject': self.subject,
                'body_text': self.body_text, 'body_html': self.body_html,
                'in_reply_to_uid': self.in_reply_to_uid, 'updated_at': iso(self.updated_at)}
