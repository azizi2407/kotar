"""Mail Gateway — IMAP/SMTP için tek saf adaptör (drive_gateway muadili).

DB'ye DOKUNMAZ; bir MailConn (host/port/kimlik) alır, ağ işini yapar, domain tipli
hata döndürür (MailError / MailAuthError). IMAP: imap-tools. SMTP: stdlib smtplib +
email. Testlerde MailBox/SMTP monkeypatch'lenir → ağa çıkılmaz.

örnek: IMAP mail.example.com:993 SSL, SMTP :465 implicit SSL. imap_tools uid'leri
string döndürür; burada int'e çevrilir.
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

# Klasör özel-kullanım eşlemesi (IMAP \Special-Use flag'i veya ad tabanlı fallback)
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
    """IMAP/SMTP erişilemedi veya protokol hatası."""


class MailAuthError(MailError):
    """Kimlik geçersiz (kullanıcı/parola yanlış)."""


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
    """Mail modülü çalışabilir mi (şifreleme anahtarı var mı)?"""
    return mail_crypto.available()


# --- IMAP -------------------------------------------------------------------
def _mailbox_cls(conn):
    if conn.imap_ssl:
        return MailBox
    return MailBoxUnencrypted


def _open(conn):
    """Giriş yapılmış MailBox döner (context manager). Hataları domain tipine çevirir."""
    cls = _mailbox_cls(conn)
    try:
        mb = cls(conn.imap_host, conn.imap_port)
        mb.login(conn.username, conn.password, initial_folder=None)
        return mb
    except imap_errors.MailboxLoginError as e:
        raise MailAuthError(f'IMAP giriş başarısız: {e}')
    except (imap_errors.ImapToolsError, OSError, ssl.SSLError) as e:
        raise MailError(f'IMAP bağlantı hatası: {e}')


def _special_use(name, flags):
    low_flags = {str(f).lower() for f in (flags or ())}
    for f, s in _SPECIAL_FLAGS.items():
        if f in low_flags:
            return s
    return _SPECIAL_NAMES.get((name or '').lower())


def list_folders(conn):
    """Hesabın klasör listesi: [{name, path, flags, special_use, uidvalidity}]."""
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
        atts.append({'part_id': str(i), 'filename': a.filename or f'ek-{i}',
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
    """Klasörden envelope + snippet + flag (gövde/ek YOK). since_uid verilirse yalnız
    ondan büyük UID'ler. En yeni önce (reverse)."""
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
    """Tek mesajın tam hali (gövde + ek metadata). Bulunamazsa MailError."""
    with _open(conn) as mb:
        mb.folder.set(folder, readonly=True)
        for m in mb.fetch(AND(uid=str(int(uid))), mark_seen=False, bulk=False):
            return _to_msg(m)
    raise MailError(f'mesaj bulunamadı: uid={uid}')


def fetch_attachment(conn, folder, uid, part_id):
    """Ekin ham baytları (bytes, filename, content_type). part_id = ek sırası."""
    idx = int(part_id)
    with _open(conn) as mb:
        mb.folder.set(folder, readonly=True)
        for m in mb.fetch(AND(uid=str(int(uid))), mark_seen=False, bulk=False):
            atts = list(m.attachments)
            if idx < 0 or idx >= len(atts):
                raise MailError(f'ek bulunamadı: part={part_id}')
            a = atts[idx]
            return a.payload, (a.filename or f'ek-{idx}'), a.content_type
    raise MailError(f'mesaj bulunamadı: uid={uid}')


def store_flags(conn, folder, uid, add=(), remove=()):
    """IMAP STORE — flag ekle/kaldır. add/remove: ('\\Seen', '\\Flagged', ...)."""
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
                           filename=att.get('filename') or 'ek')
    return msg, mid


def send(conn, *, to, cc=(), subject='', body_text='', body_html=None,
         in_reply_to=None, references=None, attachments=()):
    """SMTP ile gönder. (message_id, raw_bytes) döner — raw_bytes Sent'e APPEND edilebilir."""
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
        raise MailAuthError(f'SMTP giriş başarısız: {e}')
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise MailError(f'SMTP gönderim hatası: {e}')
    return mid, raw


def append_sent(conn, raw_bytes, folder='Sent'):
    """Gönderilen mesajın ham halini Sent klasörüne APPEND eder (best-effort)."""
    try:
        with _open(conn) as mb:
            mb.append(raw_bytes, folder, dt.datetime.now(dt.timezone.utc), ('\\Seen',))
    except (MailError, imap_errors.ImapToolsError):
        pass  # Sent yoksa/başarısızsa gönderim yine başarılı sayılır


def test_imap(conn):
    """Yalnız IMAP kimliğini doğrular (okuma için zorunlu). Başarısızsa MailAuthError/MailError."""
    with _open(conn) as mb:
        mb.folder.list()


def test_smtp(conn):
    """Yalnız SMTP kimliğini/erişimini doğrular. Bazı sunucularda giden SMTP portu (465/587)
    ağ düzeyinde engelli olabilir → MailError (timeout). Gönderim o zaman kullanılamaz ama
    okuma etkilenmez."""
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
        raise MailAuthError(f'SMTP giriş başarısız: {e}')
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise MailError(f'SMTP bağlantı hatası: {e}')


def test_connection(conn):
    """IMAP + SMTP kimliğini doğrular (ikisi de zorunlu). Başarısızsa hata atar."""
    test_imap(conn)
    test_smtp(conn)
