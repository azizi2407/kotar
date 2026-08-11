"""Mail Gateway — the single pure adapter for IMAP/SMTP (the drive_gateway counterpart).

Does NOT touch the DB; takes a MailConn (host/port/credentials), does the
network work, returns domain-typed errors (MailError / MailAuthError). IMAP:
imap-tools. SMTP: stdlib smtplib + email. MailBox/SMTP are monkeypatched in
tests → no network access.

example: IMAP mail.example.com:993 SSL, SMTP :465 implicit SSL. imap_tools
returns uids as strings; converted to int here.
"""
import datetime as dt
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from imap_tools import AND, MailBox, MailBoxTls, MailBoxUnencrypted, UidRange
from imap_tools import errors as imap_errors

import mail_crypto

# Folder special-use mapping (IMAP \Special-Use flag, or a name-based fallback)
_SPECIAL_FLAGS = {
    '\\inbox': 'inbox', '\\sent': 'sent', '\\drafts': 'drafts',
    '\\trash': 'trash', '\\junk': 'junk', '\\archive': 'archive',
}
_SPECIAL_NAMES = {
    'inbox': 'inbox', 'sent': 'sent', 'sent items': 'sent', 'gönderilmiş': 'sent',
    'drafts': 'drafts', 'taslaklar': 'drafts', 'trash': 'trash', 'çöp': 'trash',
    'deleted': 'trash', 'junk': 'junk', 'spam': 'junk', 'archive': 'archive',
}


class MailError(Exception):
    """IMAP/SMTP unreachable, or a protocol error."""


class MailAuthError(MailError):
    """Credentials invalid (wrong username/password)."""


@dataclass
class MailConn:
    email: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    smtp_host: str
    smtp_port: int
    smtp_security: str          # 'ssl' | 'starttls' | 'none'
    username: str
    password: str


@dataclass
class MailMsg:
    uid: int
    message_id: str = ''
    in_reply_to: str = ''
    references: str = ''
    subject: str = ''
    from_addr: str = ''
    from_name: str = ''
    to_addrs: str = ''
    cc_addrs: str = ''
    date: object = None
    snippet: str = ''
    body_text: str = ''
    body_html: str = ''
    flags: set = field(default_factory=set)
    size: int = 0
    attachments: list = field(default_factory=list)  # dict: part_id/filename/content_type/size/content_id


def available():
    """Can the mail module run (is there an encryption key)?"""
    return mail_crypto.available()


# --- IMAP -------------------------------------------------------------------
def _mailbox_cls(conn):
    if conn.imap_ssl:
        return MailBox
    return MailBoxUnencrypted


def _open(conn):
    """Returns a logged-in MailBox (context manager). Converts errors to domain types."""
    cls = _mailbox_cls(conn)
    try:
        mb = cls(conn.imap_host, conn.imap_port)
        mb.login(conn.username, conn.password, initial_folder=None)
        return mb
    except imap_errors.MailboxLoginError as e:
        raise MailAuthError(f'IMAP login failed: {e}')
    except (imap_errors.ImapToolsError, OSError, ssl.SSLError) as e:
        raise MailError(f'IMAP connection error: {e}')


def _special_use(name, flags):
    low_flags = {str(f).lower() for f in (flags or ())}
    for f, s in _SPECIAL_FLAGS.items():
        if f in low_flags:
            return s
    return _SPECIAL_NAMES.get((name or '').lower())


def list_folders(conn):
    """The account's folder list: [{name, path, flags, special_use, uidvalidity}]."""
    out = []
    with _open(conn) as mb:
        for fi in mb.folder.list():
            special = _special_use(fi.name, fi.flags)
            uidval = None
            try:
                st = mb.folder.status(fi.name, ['UIDVALIDITY'])
                uidval = st.get('UIDVALIDITY')
            except imap_errors.ImapToolsError:
                pass
            out.append({'name': fi.name, 'path': fi.name,
                        'flags': ','.join(fi.flags or ()), 'special_use': special,
                        'uidvalidity': uidval})
    return out


def _to_msg(m):
    fv = m.from_values
    atts = []
    for i, a in enumerate(m.attachments):
        atts.append({'part_id': str(i), 'filename': a.filename or f'attachment-{i}',
                     'content_type': a.content_type, 'size': a.size,
                     'content_id': a.content_id})
    text = m.text or ''
    hdr = m.headers or {}
    return MailMsg(
        uid=int(m.uid) if m.uid else 0,
        message_id=(hdr.get('message-id') or ('',))[0].strip(),
        in_reply_to=(hdr.get('in-reply-to') or ('',))[0].strip(),
        references=' '.join(hdr.get('references') or ()).strip(),
        subject=m.subject or '',
        from_addr=(fv.email if fv else m.from_) or '',
        from_name=(fv.name if fv else '') or '',
        to_addrs=', '.join(m.to or ()),
        cc_addrs=', '.join(m.cc or ()),
        date=m.date,
        snippet=(text.strip().replace('\n', ' ')[:500]) if text else '',
        body_text=text,
        body_html=m.html or '',
        flags=set(m.flags or ()),
        size=m.size or 0,
        attachments=atts,
    )


def fetch_headers(conn, folder, since_uid=None, limit=50):
    """Envelope + snippet + flags from the folder (NO body/attachments). If
    since_uid is given, only UIDs greater than it. Newest first (reverse)."""
    if since_uid:
        criteria = AND(uid=UidRange(str(int(since_uid) + 1), '*'))
    else:
        criteria = 'ALL'
    out = []
    with _open(conn) as mb:
        mb.folder.set(folder, readonly=True)
        for m in mb.fetch(criteria, mark_seen=False, headers_only=True,
                          reverse=True, limit=limit, bulk=True):
            out.append(_to_msg(m))
    return out


def fetch_message(conn, folder, uid):
    """A single message's full form (body + attachment metadata). MailError if not found."""
    with _open(conn) as mb:
        mb.folder.set(folder, readonly=True)
        for m in mb.fetch(AND(uid=str(int(uid))), mark_seen=False, bulk=False):
            return _to_msg(m)
    raise MailError(f'message not found: uid={uid}')


def fetch_attachment(conn, folder, uid, part_id):
    """The attachment's raw bytes (bytes, filename, content_type). part_id = attachment index."""
    idx = int(part_id)
    with _open(conn) as mb:
        mb.folder.set(folder, readonly=True)
        for m in mb.fetch(AND(uid=str(int(uid))), mark_seen=False, bulk=False):
            atts = list(m.attachments)
            if idx < 0 or idx >= len(atts):
                raise MailError(f'attachment not found: part={part_id}')
            a = atts[idx]
            return a.payload, (a.filename or f'attachment-{idx}'), a.content_type
    raise MailError(f'message not found: uid={uid}')


def store_flags(conn, folder, uid, add=(), remove=()):
    """IMAP STORE — add/remove flags. add/remove: ('\\Seen', '\\Flagged', ...)."""
    with _open(conn) as mb:
        mb.folder.set(folder)
        u = str(int(uid))
        if add:
            mb.flag(u, list(add), True)
        if remove:
            mb.flag(u, list(remove), False)


# --- SMTP -------------------------------------------------------------------
def _build_mime(conn, *, to, cc, subject, body_text, body_html, in_reply_to, references,
                attachments):
    msg = EmailMessage()
    msg['From'] = conn.email
    msg['To'] = ', '.join(to)
    if cc:
        msg['Cc'] = ', '.join(cc)
    msg['Subject'] = subject or ''
    msg['Date'] = formatdate(localtime=True)
    mid = make_msgid(domain=(parseaddr(conn.email)[1].split('@')[-1] or 'localhost'))
    msg['Message-ID'] = mid
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = (references + ' ' + in_reply_to).strip() if references else in_reply_to
    msg.set_content(body_text or '')
    if body_html:
        msg.add_alternative(body_html, subtype='html')
    for att in attachments or ():
        # att: {filename, content_type, data(bytes)}
        maintype, _, subtype = (att.get('content_type') or 'application/octet-stream').partition('/')
        msg.add_attachment(att['data'], maintype=maintype, subtype=subtype or 'octet-stream',
                           filename=att.get('filename') or 'attachment')
    return msg, mid


def send(conn, *, to, cc=(), subject='', body_text='', body_html=None,
         in_reply_to=None, references=None, attachments=()):
    """Send via SMTP. Returns (message_id, raw_bytes) — raw_bytes can be APPENDed to Sent."""
    msg, mid = _build_mime(conn, to=to, cc=cc, subject=subject, body_text=body_text,
                           body_html=body_html, in_reply_to=in_reply_to,
                           references=references, attachments=attachments)
    raw = msg.as_bytes()
    try:
        if conn.smtp_security == 'ssl':
            server = smtplib.SMTP_SSL(conn.smtp_host, conn.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(conn.smtp_host, conn.smtp_port, timeout=30)
        with server:
            if conn.smtp_security == 'starttls':
                server.starttls(context=ssl.create_default_context())
            server.login(conn.username, conn.password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailAuthError(f'SMTP login failed: {e}')
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise MailError(f'SMTP send error: {e}')
    return mid, raw


def append_sent(conn, raw_bytes, folder='Sent'):
    """APPENDs the sent message's raw form to the Sent folder (best-effort)."""
    try:
        with _open(conn) as mb:
            mb.append(raw_bytes, folder, dt.datetime.now(dt.timezone.utc), ('\\Seen',))
    except (MailError, imap_errors.ImapToolsError):
        pass  # if Sent doesn't exist/fails, sending is still counted as successful


def test_imap(conn):
    """Verifies IMAP credentials only (required for reading). MailAuthError/MailError on failure."""
    with _open(conn) as mb:
        mb.folder.list()


def test_smtp(conn):
    """Verifies SMTP credentials/reachability only. On some servers the outgoing
    SMTP port (465/587) may be network-blocked → MailError (timeout). Sending is
    then unavailable, but reading is unaffected."""
    try:
        if conn.smtp_security == 'ssl':
            server = smtplib.SMTP_SSL(conn.smtp_host, conn.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(conn.smtp_host, conn.smtp_port, timeout=30)
        with server:
            if conn.smtp_security == 'starttls':
                server.starttls(context=ssl.create_default_context())
            server.login(conn.username, conn.password)
    except smtplib.SMTPAuthenticationError as e:
        raise MailAuthError(f'SMTP login failed: {e}')
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise MailError(f'SMTP connection error: {e}')


def test_connection(conn):
    """Verifies both IMAP + SMTP credentials (both required). Raises on failure."""
    test_imap(conn)
    test_smtp(conn)
