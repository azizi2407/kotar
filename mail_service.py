"""Mail service layer — authorization + Fernet + IMAP→DB sync + send orchestration.

HTTP endpoints (mail_api) and the poller (mail_sync_worker) both go through here;
both use the same mail_gateway. Passwords are stored/decrypted with Fernet
(mail_crypto). Authorization: an owner sees only their own + is_shared accounts;
is_shared/on someone else's behalf/poll_enabled is superadmin only. Domain
allowlist comes from config (MAIL_ALLOWED_DOMAINS)."""
import logging

from flask import current_app

import mail_crypto
import mail_gateway as gw
import notifications
from extensions import db
from models import utcnow
from models_mail import MailAccount, MailFolder, MailMessage, MailAttachment, MailDraft
from sso_client import is_superadmin

log = logging.getLogger('agency.mail')


class MailAccessError(Exception):
    """The user isn't authorized for this account/operation (→ 403/404)."""


class MailConfigError(Exception):
    """Invalid configuration (domain not allowed, missing field → 400)."""


# --- authorization & access ---------------------------------------------------------
def _allowed_domains():
    return current_app.config.get('MAIL_ALLOWED_DOMAINS') or set()


def _can_see(user, acc):
    return acc.owner_sub == user.get('sub') or acc.is_shared


def accessible_accounts(user):
    """Accounts the user can see: their own + shared (is_shared)."""
    sub = user.get('sub')
    return (MailAccount.query
            .filter(db.or_(MailAccount.owner_sub == sub, MailAccount.is_shared.is_(True)))
            .filter_by(active=True)
            .order_by(MailAccount.is_shared, MailAccount.email)
            .all())


def require_account(user, account_id, *, write=False):
    """Fetch the account + verify access. write=True requires superadmin on a
    shared account."""
    acc = db.session.get(MailAccount, account_id)
    if acc is None or not acc.active:
        raise MailAccessError('account not found')
    if not _can_see(user, acc):
        raise MailAccessError('you do not have access to this account')
    if write and acc.is_shared and not (acc.owner_sub == user.get('sub') or is_superadmin(user)):
        raise MailAccessError('permission required to modify a shared account')
    return acc


def conn_for(acc):
    """MailAccount → gw.MailConn (secret_enc is decrypted)."""
    return gw.MailConn(
        email=acc.email, imap_host=acc.imap_host, imap_port=acc.imap_port,
        imap_ssl=acc.imap_ssl, smtp_host=acc.smtp_host, smtp_port=acc.smtp_port,
        smtp_security=acc.smtp_security, username=acc.email,
        password=mail_crypto.decrypt(acc.secret_enc))


# --- account CRUD -------------------------------------------------------------
def create_account(user, data):
    """Self-service account linking. Domain allowlist + gw.test_connection; saved on
    success. is_shared/on someone else's behalf is superadmin only. data: email,
    password, display_name?, imap_host?, imap_port?, smtp_host?, smtp_port?,
    smtp_security?, is_shared?, poll_enabled?"""
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        raise MailConfigError('email and password are required')
    domain = email.rsplit('@', 1)[-1]
    if domain not in _allowed_domains():
        raise MailConfigError(f'allowed domains only: {", ".join(sorted(_allowed_domains()))}')

    is_shared = bool(data.get('is_shared'))
    poll_enabled = bool(data.get('poll_enabled'))
    owner_sub = data.get('owner_sub') or user.get('sub')
    superadmin = is_superadmin(user)
    if (is_shared or poll_enabled or owner_sub != user.get('sub')) and not superadmin:
        raise MailAccessError('shared account / poller / on behalf of another user is superadmin-only')

    # If (owner_sub, email) already exists, `uq_mail_owner_email` is violated → a raw
    # IntegrityError 500. The panel used to show this as "Couldn't connect
    # (email/password?)", i.e. it pointed in exactly the wrong direction even when
    # the password was CORRECT (2026-07-31 incident: "Update password" hit this
    # path when the password changed). An active row → clear error; an INACTIVE row
    # (soft-deleted) → revive it, because `delete_account` doesn't delete the row
    # and the "remove, reconnect" path would otherwise permanently hit the
    # constraint.
    mevcut = MailAccount.query.filter_by(owner_sub=owner_sub, email=email).first()
    if mevcut is not None and mevcut.active:
        raise MailConfigError(
            f'{email} is already connected — if the password changed, refresh it with '
            '"Update password" on the account card.')

    cfg = current_app.config
    acc = mevcut or MailAccount(owner_sub=owner_sub, email=email)
    _apply_conn_fields(acc, data, cfg)
    acc.display_name = data.get('display_name')
    acc.secret_enc = mail_crypto.encrypt(password)
    acc.is_shared, acc.poll_enabled, acc.active = is_shared, poll_enabled, True
    acc.last_error = None

    # IMAP is mandatory (reading) — a wrong password/access issue is caught early.
    conn = conn_for(acc)
    gw.test_imap(conn)
    acc.last_ok_at = utcnow()
    # SMTP is soft: the account is set up even if it's unreachable (reading works),
    # a warning is written. BUT an auth error and an access error are reported
    # SEPARATELY: since `MailAuthError` is a subclass of `MailError`, a single
    # `except` was catching both and a wrong SMTP password looked like "sending is
    # currently disabled" (network block). Once the network block is lifted, this
    # text actively misdirects: a fixable password issue gets mistaken for a
    # "server block" and nobody touches it.
    try:
        gw.test_smtp(conn)
    except gw.MailAuthError as e:
        acc.last_error = f'IMAP OK · SMTP credentials rejected (check the password): {e}'
    except gw.MailError as e:
        acc.last_error = f'IMAP OK · SMTP server unreachable (sending disabled): {e}'
    db.session.add(acc)
    db.session.commit()
    return acc


def _apply_conn_fields(acc, data, cfg):
    """Fill connection fields from data + defaults (split out so a new account and a
    revived account use the same path)."""
    acc.imap_host = data.get('imap_host') or cfg['MAIL_DEFAULT_IMAP_HOST']
    acc.imap_port = int(data.get('imap_port') or cfg['MAIL_DEFAULT_IMAP_PORT'])
    acc.imap_ssl = bool(data.get('imap_ssl', True))
    acc.smtp_host = data.get('smtp_host') or cfg['MAIL_DEFAULT_SMTP_HOST']
    acc.smtp_port = int(data.get('smtp_port') or cfg['MAIL_DEFAULT_SMTP_PORT'])
    acc.smtp_security = data.get('smtp_security') or 'ssl'


def update_account(user, account_id, data):
    """Update the account. If `password` is given it's **verified against IMAP**
    and only saved if it's valid — silently writing a wrong password would leave
    the user in exactly the broken state they were trying to fix (2026-07-31)."""
    acc = require_account(user, account_id, write=True)
    superadmin = is_superadmin(user)
    for f in ('display_name', 'imap_host', 'smtp_host', 'smtp_security'):
        if f in data:
            setattr(acc, f, data[f])
    for f in ('imap_port', 'smtp_port'):
        if f in data and data[f]:
            setattr(acc, f, int(data[f]))
    if data.get('password'):
        eski = acc.secret_enc
        acc.secret_enc = mail_crypto.encrypt(data['password'])
        try:
            gw.test_imap(conn_for(acc))
        except gw.MailError:
            acc.secret_enc = eski          # an unverified password isn't saved
            db.session.rollback()
            raise
        # The password is fixed → the old health note is now a lie; the badge
        # looks at `last_error`, if it isn't cleared the account would still look
        # "broken" after it's fixed.
        acc.last_ok_at = utcnow()
        acc.last_error = None
    if 'is_shared' in data or 'poll_enabled' in data:
        if not superadmin:
            raise MailAccessError('is_shared/poll_enabled is superadmin-only')
        if 'is_shared' in data:
            acc.is_shared = bool(data['is_shared'])
        if 'poll_enabled' in data:
            acc.poll_enabled = bool(data['poll_enabled'])
    db.session.commit()
    return acc


def delete_account(user, account_id):
    acc = require_account(user, account_id, write=True)
    acc.active = False
    db.session.commit()


def test_account(user, account_id):
    """Test the connection; report the IMAP and SMTP channels separately. IMAP is
    mandatory; if SMTP is blocked (outbound port block), returns imap_ok=True,
    smtp_ok=False. Returns: dict."""
    acc = require_account(user, account_id)
    conn = conn_for(acc)
    result = {'imap_ok': False, 'smtp_ok': False, 'imap_error': None, 'smtp_error': None,
              # The error's CLASS: 'auth' (password) / 'connect' (access). In the
              # panel, "fix the password" and "can't reach the server" trigger
              # different actions.
              'imap_error_kind': None, 'smtp_error_kind': None}
    try:
        gw.test_imap(conn)
        result['imap_ok'] = True
        acc.last_ok_at = utcnow()
    except gw.MailError as e:
        result['imap_error'] = str(e)
        result['imap_error_kind'] = 'auth' if isinstance(e, gw.MailAuthError) else 'connect'
    try:
        gw.test_smtp(conn)
        result['smtp_ok'] = True
    except gw.MailError as e:
        result['smtp_error'] = str(e)
        result['smtp_error_kind'] = 'auth' if isinstance(e, gw.MailAuthError) else 'connect'
    acc.last_error = None if (result['imap_ok'] and result['smtp_ok']) else \
        (result['imap_error'] or result['smtp_error'])
    db.session.commit()
    return result


# --- sync ----------------------------------------------------------------
def _upsert_folders(acc, conn):
    remote = gw.list_folders(conn)
    by_path = {f.path: f for f in MailFolder.query.filter_by(account_id=acc.id).all()}
    result = {}
    for r in remote:
        f = by_path.get(r['path'])
        if f is None:
            f = MailFolder(account_id=acc.id, path=r['path'])
            db.session.add(f)
        f.name = r['name']
        f.flags = r['flags']
        f.special_use = r['special_use']
        f.uidvalidity = r['uidvalidity']
        result[r['path']] = f
    db.session.flush()
    return result


def sync_folder(acc, folder_path, limit=100):
    """Sync one folder IMAP→DB. A UIDVALIDITY change → full resync. Idempotent
    (unique constraint). Returns: number of newly added messages."""
    conn = conn_for(acc)
    folders = _upsert_folders(acc, conn)
    folder = folders.get(folder_path)
    if folder is None:
        raise MailConfigError(f'folder not found: {folder_path}')

    remote_uidval = folder.uidvalidity
    existing = (MailMessage.query
                .filter_by(account_id=acc.id, folder_id=folder.id)
                .all())
    # If UIDVALIDITY changed, delete this folder's local messages, refetch from scratch
    if existing and remote_uidval is not None and existing[0].uidvalidity != remote_uidval:
        for m in existing:
            db.session.delete(m)
        db.session.flush()
        existing = []

    max_uid = max((m.uid for m in existing), default=0)
    known = {m.uid: m for m in existing}
    new_count = 0
    for msg in gw.fetch_headers(conn, folder_path, since_uid=max_uid or None, limit=limit):
        if msg.uid in known:
            continue
        row = MailMessage(
            account_id=acc.id, folder_id=folder.id, uid=msg.uid,
            uidvalidity=remote_uidval or 0, message_id=msg.message_id or None,
            in_reply_to=msg.in_reply_to or None, references=msg.references or None,
            thread_key=(msg.references.split()[0] if msg.references else msg.message_id) or None,
            from_addr=msg.from_addr, from_name=msg.from_name,
            to_addrs=msg.to_addrs, cc_addrs=msg.cc_addrs, subject=msg.subject[:998],
            date=msg.date, snippet=msg.snippet,
            seen='\\Seen' in msg.flags, flagged='\\Flagged' in msg.flags,
            answered='\\Answered' in msg.flags, draft='\\Draft' in msg.flags,
            has_attachments=bool(msg.attachments), size=msg.size)
        db.session.add(row)
        new_count += 1
    folder.last_sync_at = utcnow()
    db.session.commit()
    return new_count


def refresh_folders(acc):
    """Refresh (upsert) the folder list from IMAP and return the MailFolder rows.

    An IMAP error **is also written to the health record** (2026-07-31): when the
    password changes on the server, the first symptom is this endpoint returning
    502, but since `last_error` wasn't written, the account badge kept saying "No
    connection issue reported" (green) → the "Update password" button never
    showed up, the user had to test manually."""
    self_conn = conn_for(acc)
    try:
        _upsert_folders(acc, self_conn)
    except gw.MailError as e:
        db.session.rollback()
        # We embed the class in the text: the panel badge looks at the
        # `last_error` text to distinguish "password issue" from "can't reach the
        # server", and the gateway's raw "IMAP login failed" text didn't make that
        # distinction (adding a column would require a manual ALTER; the text
        # contract uses the same wording as `create_account`).
        acc.last_error = (f'IMAP credentials rejected (check the password): {e}'
                          if isinstance(e, gw.MailAuthError)
                          else f'IMAP server unreachable: {e}')
        db.session.commit()
        raise
    acc.last_ok_at = utcnow()
    db.session.commit()
    return (MailFolder.query.filter_by(account_id=acc.id)
            .order_by(MailFolder.special_use.isnot(None).desc(), MailFolder.name)
            .all())


def list_messages(acc, folder_id, page=1, per_page=50, q=None):
    """Messages in the folder (DB — snippet list). If q is given, searches
    subject/sender/snippet. Returns: (items, total)."""
    folder = db.session.get(MailFolder, folder_id)
    if folder is None or folder.account_id != acc.id:
        raise MailAccessError('folder not found')
    query = MailMessage.query.filter_by(account_id=acc.id, folder_id=folder_id)
    if q:
        like = f'%{q.strip()}%'
        query = query.filter(db.or_(MailMessage.subject.ilike(like),
                                    MailMessage.from_addr.ilike(like),
                                    MailMessage.snippet.ilike(like)))
    total = query.count()
    items = (query.order_by(MailMessage.date.desc().nullslast(), MailMessage.uid.desc())
             .offset((page - 1) * per_page).limit(per_page).all())
    return items, total


def get_message(acc, message_id, mark_seen=True):
    """Full message (lazy body) + mark as read. Returns: MailMessage."""
    m = db.session.get(MailMessage, message_id)
    if m is None or m.account_id != acc.id:
        raise MailAccessError('message not found')
    load_message_body(acc, m)
    if mark_seen and not m.seen:
        folder = db.session.get(MailFolder, m.folder_id)
        try:
            gw.store_flags(conn_for(acc), folder.path, m.uid, add=('\\Seen',))
        except gw.MailError:
            pass
        m.seen = True
        db.session.commit()
    return m


def set_message_flags(acc, message_id, *, seen=None, flagged=None):
    m = db.session.get(MailMessage, message_id)
    if m is None or m.account_id != acc.id:
        raise MailAccessError('message not found')
    folder = db.session.get(MailFolder, m.folder_id)
    add, remove = [], []
    if seen is not None:
        (add if seen else remove).append('\\Seen')
        m.seen = bool(seen)
    if flagged is not None:
        (add if flagged else remove).append('\\Flagged')
        m.flagged = bool(flagged)
    gw.store_flags(conn_for(acc), folder.path, m.uid, add=add, remove=remove)
    db.session.commit()
    return m


def get_attachment(acc, message_id, part_id):
    """Attachment bytes (data, filename, content_type) — on-demand IMAP."""
    m = db.session.get(MailMessage, message_id)
    if m is None or m.account_id != acc.id:
        raise MailAccessError('message not found')
    folder = db.session.get(MailFolder, m.folder_id)
    return gw.fetch_attachment(conn_for(acc), folder.path, m.uid, part_id)


# --- draft -----------------------------------------------------------------
def save_draft(acc, data, draft_id=None):
    if draft_id:
        d = db.session.get(MailDraft, draft_id)
        if d is None or d.account_id != acc.id:
            raise MailAccessError('draft not found')
    else:
        d = MailDraft(account_id=acc.id)
        db.session.add(d)
    for f in ('to_addrs', 'cc_addrs', 'bcc_addrs', 'subject', 'body_text', 'body_html'):
        if f in data:
            setattr(d, f, data[f])
    if 'in_reply_to_uid' in data:
        d.in_reply_to_uid = data['in_reply_to_uid']
    db.session.commit()
    return d


def list_drafts(acc):
    return (MailDraft.query.filter_by(account_id=acc.id)
            .order_by(MailDraft.updated_at.desc()).all())


def delete_draft(acc, draft_id):
    d = db.session.get(MailDraft, draft_id)
    if d is None or d.account_id != acc.id:
        raise MailAccessError('draft not found')
    db.session.delete(d)
    db.session.commit()


def load_message_body(acc, message):
    """If the message's body/attachments aren't in the DB, fetch them from IMAP and
    persist them (lazy). Returns: message."""
    if message.body_text is not None or message.body_html is not None:
        return message
    folder = db.session.get(MailFolder, message.folder_id)
    full = gw.fetch_message(conn_for(acc), folder.path, message.uid)
    message.body_text = full.body_text
    message.body_html = full.body_html
    message.has_attachments = bool(full.attachments)
    MailAttachment.query.filter_by(message_id=message.id).delete()
    for a in full.attachments:
        db.session.add(MailAttachment(
            message_id=message.id, filename=a['filename'], content_type=a['content_type'],
            size=a['size'], content_id=a['content_id'], part_id=a['part_id']))
    db.session.commit()
    return message


def sync_account(acc):
    """Poller: INBOX + folders worth watching. Notification on new mail. Returns:
    count of new messages."""
    conn = conn_for(acc)
    folders = _upsert_folders(acc, conn)
    total = 0
    # INBOX (special_use=inbox) takes priority; otherwise the 'INBOX' path
    inbox_paths = [p for p, f in folders.items() if f.special_use == 'inbox'] or ['INBOX']
    for path in inbox_paths:
        if path not in folders:
            continue
        n = sync_folder(acc, path)
        total += n
        if n:
            notifications.push(acc.owner_sub, 'mail', f'{n} new emails',
                               f'{acc.email} · {path}', link='/posta')
    if total:
        db.session.commit()
    acc.last_ok_at = utcnow()
    acc.last_error = None
    db.session.commit()
    return total


# --- sending ---------------------------------------------------------------
def send_message(user, account_id, payload):
    """Send via SMTP + Sent APPEND + DB record. payload: to[], cc[], subject,
    body_text, body_html?, in_reply_to?, reply_to_message_id? (local MailMessage.id
    → \\Answered)."""
    acc = require_account(user, account_id)
    to = [a.strip() for a in (payload.get('to') or []) if a.strip()]
    if not to:
        raise MailConfigError('at least one recipient (to) is required')
    cc = [a.strip() for a in (payload.get('cc') or []) if a.strip()]
    conn = conn_for(acc)

    reply_src = None
    in_reply_to = payload.get('in_reply_to')
    if payload.get('reply_to_message_id'):
        reply_src = db.session.get(MailMessage, payload['reply_to_message_id'])
        if reply_src and reply_src.account_id == acc.id:
            in_reply_to = reply_src.message_id
    references = reply_src.references if reply_src else None

    # The send result updates the account's HEALTH STATUS. This used to be done
    # only by `test_account`, and that endpoint is never called from the panel →
    # the "sending disabled" note written during an SMTP network block stayed in
    # the record even after sending started working again (there was no
    # self-clearing path). Now the first successful send clears the note, a failed
    # send writes the real reason.
    try:
        mid, raw = gw.send(conn, to=to, cc=cc, subject=payload.get('subject', ''),
                           body_text=payload.get('body_text', ''),
                           body_html=payload.get('body_html'),
                           in_reply_to=in_reply_to, references=references,
                           attachments=payload.get('attachments') or ())
    except gw.MailError as e:
        acc.last_error = (
            f'Send failed — SMTP credentials rejected (check the password): {e}'
            if isinstance(e, gw.MailAuthError)
            else f'Send failed — SMTP server unreachable: {e}')
        db.session.commit()
        raise
    acc.last_error = None
    acc.last_ok_at = utcnow()
    db.session.commit()
    gw.append_sent(conn, raw)

    if reply_src and reply_src.account_id == acc.id:
        folder = db.session.get(MailFolder, reply_src.folder_id)
        try:
            gw.store_flags(conn, folder.path, reply_src.uid, add=('\\Answered',))
        except gw.MailError:
            pass
        reply_src.answered = True
        db.session.commit()
    return {'message_id': mid, 'to': to, 'subject': payload.get('subject', '')}
