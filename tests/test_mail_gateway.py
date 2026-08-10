"""mail_gateway — IMAP/SMTP saf adaptör; MailBox ve smtplib mock'lu (ağa çıkmaz)."""
import pytest

import mail_gateway as gw
from imap_tools import errors as imap_errors


def _conn(**kw):
    base = dict(email='info@example.com', imap_host='mail.example.com', imap_port=993,
                imap_ssl=True, smtp_host='mail.example.com', smtp_port=465,
                smtp_security='ssl', username='info@example.com', password='pw')
    base.update(kw)
    return gw.MailConn(**base)


class FakeAtt:
    def __init__(self, filename='rapor.pdf', ct='application/pdf', payload=b'PDF', cid=None):
        self.filename, self.content_type, self.payload, self.size, self.content_id = \
            filename, ct, payload, len(payload), cid


class FakeEmailAddr:
    def __init__(self, name, email):
        self.name, self.email = name, email


class FakeMsg:
    def __init__(self, uid='5', subject='Merhaba', flags=('\\Seen',), atts=None):
        self.uid = uid
        self.subject = subject
        self.from_ = 'gonderen@example.com'
        self.from_values = FakeEmailAddr('Gönderen', 'gonderen@example.com')
        self.to = ('info@example.com',)
        self.cc = ()
        self.date = None
        self.date_str = 'Wed, 23 Jul 2026 10:00:00 +0300'
        self.text = 'gövde metni'
        self.html = '<p>gövde</p>'
        self.flags = flags
        self.size = 42
        self.attachments = atts or []
        self.headers = {'message-id': ('<abc@example.com>',),
                        'in-reply-to': ('',), 'references': ()}


class FakeFolderMgr:
    def __init__(self, msgs):
        self._msgs = msgs

    def list(self):
        from imap_tools import FolderInfo
        return [FolderInfo('INBOX', '/', ('\\Inbox',)),
                FolderInfo('Sent', '/', ('\\Sent',))]

    def status(self, name, opts):
        return {'UIDVALIDITY': 111}

    def set(self, name, readonly=False):
        return ('OK', [b''])


class FakeBox:
    """imap_tools.MailBox yerine geçer. Sınıf değişkeni fetch_result ile beslenir."""
    fetch_result = None
    login_error = None
    appended = []
    flagged = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.folder = FakeFolderMgr(self.fetch_result or [])

    def login(self, u, p, initial_folder='INBOX'):
        if FakeBox.login_error:
            raise FakeBox.login_error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetch(self, criteria='ALL', mark_seen=True, headers_only=False, reverse=False,
              limit=None, bulk=False):
        return list(FakeBox.fetch_result or [])

    def flag(self, uid, flags, value):
        FakeBox.flagged.append((uid, tuple(flags), value))

    def append(self, raw, folder, date, flag_set):
        FakeBox.appended.append((folder, raw))


@pytest.fixture(autouse=True)
def _reset():
    FakeBox.fetch_result = None
    FakeBox.login_error = None
    FakeBox.appended = []
    FakeBox.flagged = []


def test_list_folders(monkeypatch):
    monkeypatch.setattr(gw, 'MailBox', FakeBox)
    folders = gw.list_folders(_conn())
    names = {f['path'] for f in folders}
    assert names == {'INBOX', 'Sent'}
    sent = next(f for f in folders if f['path'] == 'Sent')
    assert sent['special_use'] == 'sent' and sent['uidvalidity'] == 111


def test_fetch_headers(monkeypatch):
    FakeBox.fetch_result = [FakeMsg()]
    monkeypatch.setattr(gw, 'MailBox', FakeBox)
    msgs = gw.fetch_headers(_conn(), 'INBOX', limit=10)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.uid == 5 and m.subject == 'Merhaba'
    assert m.from_addr == 'gonderen@example.com' and m.from_name == 'Gönderen'
    assert m.message_id == '<abc@example.com>'
    assert '\\Seen' in m.flags


def test_fetch_message_with_attachment(monkeypatch):
    FakeBox.fetch_result = [FakeMsg(atts=[FakeAtt()])]
    monkeypatch.setattr(gw, 'MailBox', FakeBox)
    m = gw.fetch_message(_conn(), 'INBOX', 5)
    assert m.body_text == 'gövde metni' and m.body_html == '<p>gövde</p>'
    assert len(m.attachments) == 1 and m.attachments[0]['part_id'] == '0'
    assert m.attachments[0]['filename'] == 'rapor.pdf'


def test_fetch_attachment_bytes(monkeypatch):
    FakeBox.fetch_result = [FakeMsg(atts=[FakeAtt(payload=b'PDFDATA')])]
    monkeypatch.setattr(gw, 'MailBox', FakeBox)
    data, fn, ct = gw.fetch_attachment(_conn(), 'INBOX', 5, '0')
    assert data == b'PDFDATA' and fn == 'rapor.pdf' and ct == 'application/pdf'


def test_login_auth_error(monkeypatch):
    FakeBox.login_error = imap_errors.MailboxLoginError('bad', 'NO')
    monkeypatch.setattr(gw, 'MailBox', FakeBox)
    with pytest.raises(gw.MailAuthError):
        gw.list_folders(_conn())


def test_store_flags(monkeypatch):
    monkeypatch.setattr(gw, 'MailBox', FakeBox)
    gw.store_flags(_conn(), 'INBOX', 5, add=('\\Seen',), remove=('\\Flagged',))
    assert ('5', ('\\Seen',), True) in FakeBox.flagged
    assert ('5', ('\\Flagged',), False) in FakeBox.flagged


def test_send_builds_reply_headers(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            sent['host'] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): sent['login'] = (u, p)
        def send_message(self, msg): sent['msg'] = msg

    monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', FakeSMTP)
    mid, raw = gw.send(_conn(), to=['x@example.com'], subject='Yanıt',
                       body_text='sel', in_reply_to='<abc@example.com>')
    assert sent['login'] == ('info@example.com', 'pw')
    assert sent['msg']['In-Reply-To'] == '<abc@example.com>'
    assert '<abc@example.com>' in sent['msg']['References']
    assert isinstance(raw, bytes) and mid.startswith('<')


def test_send_auth_error(monkeypatch):
    import smtplib

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): raise smtplib.SMTPAuthenticationError(535, b'bad')
        def send_message(self, m): pass

    monkeypatch.setattr(gw.smtplib, 'SMTP_SSL', FakeSMTP)
    with pytest.raises(gw.MailAuthError):
        gw.send(_conn(), to=['x@example.com'], subject='s', body_text='b')
