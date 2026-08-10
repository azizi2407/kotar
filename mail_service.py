"""Mail servis katmanı — yetki + Fernet + IMAP→DB senkron + gönderim orkestrasyonu.

HTTP uçları (mail_api) ve poller (mail_sync_worker) buradan geçer; ikisi de aynı
mail_gateway'i kullanır. Parola Fernet'le saklanır/çözülür (mail_crypto). Yetki:
sahip yalnız kendi + is_shared görünür hesap; is_shared/başkası adına/poll_enabled
yalnız superadmin. Domain allowlist config'ten (MAIL_ALLOWED_DOMAINS)."""
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
    """Kullanıcının bu hesaba/işleme erişim yetkisi yok (→ 403/404)."""


class MailConfigError(Exception):
    """Geçersiz yapılandırma (domain dışı, eksik alan → 400)."""


# --- yetki & erişim ---------------------------------------------------------
def _allowed_domains():
    return current_app.config.get('MAIL_ALLOWED_DOMAINS') or set()


def _can_see(user, acc):
    return acc.owner_sub == user.get('sub') or acc.is_shared


def accessible_accounts(user):
    """Kullanıcının görebildiği hesaplar: kendi + ortak (is_shared)."""
    sub = user.get('sub')
    return (MailAccount.query
            .filter(db.or_(MailAccount.owner_sub == sub, MailAccount.is_shared.is_(True)))
            .filter_by(active=True)
            .order_by(MailAccount.is_shared, MailAccount.email)
            .all())


def require_account(user, account_id, *, write=False):
    """Hesabı getir + erişim doğrula. write=True ortak hesapta superadmin ister."""
    acc = db.session.get(MailAccount, account_id)
    if acc is None or not acc.active:
        raise MailAccessError('hesap bulunamadı')
    if not _can_see(user, acc):
        raise MailAccessError('bu hesaba erişiminiz yok')
    if write and acc.is_shared and not (acc.owner_sub == user.get('sub') or is_superadmin(user)):
        raise MailAccessError('ortak hesabı değiştirmek için yetki gerekli')
    return acc


def conn_for(acc):
    """MailAccount → gw.MailConn (secret_enc çözülür)."""
    return gw.MailConn(
        email=acc.email, imap_host=acc.imap_host, imap_port=acc.imap_port,
        imap_ssl=acc.imap_ssl, smtp_host=acc.smtp_host, smtp_port=acc.smtp_port,
        smtp_security=acc.smtp_security, username=acc.email,
        password=mail_crypto.decrypt(acc.secret_enc))


# --- hesap CRUD -------------------------------------------------------------
def create_account(user, data):
    """Self-servis hesap bağla. Domain allowlist + gw.test_connection; başarılıysa kaydet.
    is_shared/başkası adına yalnız superadmin. data: email, password, display_name?,
    imap_host?, imap_port?, smtp_host?, smtp_port?, smtp_security?, is_shared?, poll_enabled?"""
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        raise MailConfigError('e-posta ve parola zorunlu')
    domain = email.rsplit('@', 1)[-1]
    if domain not in _allowed_domains():
        raise MailConfigError(f'yalnız şu alan adları: {", ".join(sorted(_allowed_domains()))}')

    is_shared = bool(data.get('is_shared'))
    poll_enabled = bool(data.get('poll_enabled'))
    owner_sub = data.get('owner_sub') or user.get('sub')
    superadmin = is_superadmin(user)
    if (is_shared or poll_enabled or owner_sub != user.get('sub')) and not superadmin:
        raise MailAccessError('ortak hesap / poller / başkası adına yalnız superadmin')

    # Aynı (owner_sub, email) zaten varsa `uq_mail_owner_email` ihlali → ham
    # IntegrityError 500. Panel bunu "Bağlanamadı (e-posta/şifre?)" diye
    # gösteriyordu, yani parola DOĞRUYKEN bile tam ters yönü işaret ediyordu
    # (2026-07-31 vakası: parola değişince "Şifreyi güncelle" bu yola giriyordu).
    # Aktif satır → anlaşılır hata; PASİF satır (soft-delete) → canlandır, çünkü
    # `delete_account` satırı silmiyor ve "kaldır, yeniden bağla" yolu aksi halde
    # kalıcı olarak kısıta çarpardı.
    mevcut = MailAccount.query.filter_by(owner_sub=owner_sub, email=email).first()
    if mevcut is not None and mevcut.active:
        raise MailConfigError(
            f'{email} zaten bağlı — parolası değiştiyse hesap kartındaki '
            '"Şifreyi güncelle" ile yenileyin.')

    cfg = current_app.config
    acc = mevcut or MailAccount(owner_sub=owner_sub, email=email)
    _apply_conn_fields(acc, data, cfg)
    acc.display_name = data.get('display_name')
    acc.secret_enc = mail_crypto.encrypt(password)
    acc.is_shared, acc.poll_enabled, acc.active = is_shared, poll_enabled, True
    acc.last_error = None

    # IMAP zorunlu (okuma) — yanlış parola/erişim erken yakalanır.
    conn = conn_for(acc)
    gw.test_imap(conn)
    acc.last_ok_at = utcnow()
    # SMTP yumuşak: erişilemese bile hesap kurulur (okuma çalışır), uyarı yazılır.
    # AMA kimlik hatası ile erişim hatası AYRI raporlanır: `MailAuthError`
    # `MailError`'ın alt sınıfı olduğu için tek `except` ikisini de yakalıyordu ve
    # yanlış SMTP parolası "gönderim şimdilik kapalı" (ağ engeli) gibi görünüyordu.
    # Ağ engeli kalktıktan sonra bu metin aktif olarak yanlış yönlendirir:
    # düzeltilebilir bir parola sorununu "sunucu engeli" sanıp kimse dokunmaz.
    try:
        gw.test_smtp(conn)
    except gw.MailAuthError as e:
        acc.last_error = f'IMAP OK · SMTP kimliği reddedildi (parolayı kontrol edin): {e}'
    except gw.MailError as e:
        acc.last_error = f'IMAP OK · SMTP sunucusuna erişilemedi (gönderim kapalı): {e}'
    db.session.add(acc)
    db.session.commit()
    return acc


def _apply_conn_fields(acc, data, cfg):
    """Bağlantı alanlarını data + varsayılanlardan doldurur (yeni ve canlandırılan
    hesap aynı yolu kullansın diye ayrıldı)."""
    acc.imap_host = data.get('imap_host') or cfg['MAIL_DEFAULT_IMAP_HOST']
    acc.imap_port = int(data.get('imap_port') or cfg['MAIL_DEFAULT_IMAP_PORT'])
    acc.imap_ssl = bool(data.get('imap_ssl', True))
    acc.smtp_host = data.get('smtp_host') or cfg['MAIL_DEFAULT_SMTP_HOST']
    acc.smtp_port = int(data.get('smtp_port') or cfg['MAIL_DEFAULT_SMTP_PORT'])
    acc.smtp_security = data.get('smtp_security') or 'ssl'


def update_account(user, account_id, data):
    """Hesabı güncelle. `password` verilirse **IMAP ile doğrulanır** ve ancak
    geçerse kaydedilir — yanlış parolayı sessizce yazmak kullanıcıyı tam olarak
    düzeltmeye çalıştığı bozuk durumda bırakırdı (2026-07-31)."""
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
            acc.secret_enc = eski          # doğrulanmayan parola kaydedilmez
            db.session.rollback()
            raise
        # Parola düzeldi → eski sağlık notu artık yalan; şerit `last_error`'a
        # bakıyor, temizlenmezse hesap düzeldikten sonra da "bozuk" görünürdü.
        acc.last_ok_at = utcnow()
        acc.last_error = None
    if 'is_shared' in data or 'poll_enabled' in data:
        if not superadmin:
            raise MailAccessError('is_shared/poll_enabled yalnız superadmin')
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
    """Bağlantıyı test et; IMAP ve SMTP kanallarını ayrı raporla. IMAP zorunlu; SMTP
    engelliyse (giden port bloğu) imap_ok=True, smtp_ok=False döner. Döner: dict."""
    acc = require_account(user, account_id)
    conn = conn_for(acc)
    result = {'imap_ok': False, 'smtp_ok': False, 'imap_error': None, 'smtp_error': None,
              # Hatanın SINIFI: 'auth' (parola) / 'connect' (erişim). Panelde
              # "parolayı düzelt" ile "sunucuya ulaşılamıyor" farklı işler.
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


# --- senkron ----------------------------------------------------------------
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
    """Bir klasörü IMAP→DB senkronla. UIDVALIDITY değişimi → tam yeniden. Idempotent
    (unique kısıt). Döner: yeni eklenen mesaj sayısı."""
    conn = conn_for(acc)
    folders = _upsert_folders(acc, conn)
    folder = folders.get(folder_path)
    if folder is None:
        raise MailConfigError(f'klasör yok: {folder_path}')

    remote_uidval = folder.uidvalidity
    existing = (MailMessage.query
                .filter_by(account_id=acc.id, folder_id=folder.id)
                .all())
    # UIDVALIDITY değiştiyse bu klasörün yerel mesajlarını sil, sıfırdan çek
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
    """Klasör listesini IMAP'ten tazele (upsert) ve MailFolder satırlarını döner.

    IMAP hatası **sağlık kaydına da yazılır** (2026-07-31): parola sunucuda
    değişince ilk belirti bu ucun 502 vermesi, ama `last_error` yazılmadığı için
    hesap şeridi "Bağlantı sorunu bildirilmedi" (yeşil) demeye devam ediyordu →
    "Şifreyi güncelle" düğmesi hiç görünmüyor, kullanıcı elle test etmek zorunda
    kalıyordu."""
    self_conn = conn_for(acc)
    try:
        _upsert_folders(acc, self_conn)
    except gw.MailError as e:
        db.session.rollback()
        # Sınıfı metne gömüyoruz: panel şeridi "parola sorunu" ile "sunucuya
        # ulaşılamıyor"u ayırt etmek için `last_error` metnine bakıyor ve
        # gateway'in ham "IMAP giriş başarısız" metni o ayrımı vermiyordu
        # (kolon eklemek elle ALTER gerektirir; metin sözleşmesi `create_account`
        # ile aynı kelimeleri kullanıyor).
        acc.last_error = (f'IMAP kimliği reddedildi (parolayı kontrol edin): {e}'
                          if isinstance(e, gw.MailAuthError)
                          else f'IMAP sunucusuna erişilemedi: {e}')
        db.session.commit()
        raise
    acc.last_ok_at = utcnow()
    db.session.commit()
    return (MailFolder.query.filter_by(account_id=acc.id)
            .order_by(MailFolder.special_use.isnot(None).desc(), MailFolder.name)
            .all())


def list_messages(acc, folder_id, page=1, per_page=50, q=None):
    """Klasördeki mesajlar (DB — snippet listesi). q verilirse konu/gönderen/snippet arar.
    Döner: (items, total)."""
    folder = db.session.get(MailFolder, folder_id)
    if folder is None or folder.account_id != acc.id:
        raise MailAccessError('klasör bulunamadı')
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
    """Tam mesaj (lazy gövde) + okundu işaretle. Döner: MailMessage."""
    m = db.session.get(MailMessage, message_id)
    if m is None or m.account_id != acc.id:
        raise MailAccessError('mesaj bulunamadı')
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
        raise MailAccessError('mesaj bulunamadı')
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
    """Ek baytları (data, filename, content_type) — on-demand IMAP."""
    m = db.session.get(MailMessage, message_id)
    if m is None or m.account_id != acc.id:
        raise MailAccessError('mesaj bulunamadı')
    folder = db.session.get(MailFolder, m.folder_id)
    return gw.fetch_attachment(conn_for(acc), folder.path, m.uid, part_id)


# --- taslak -----------------------------------------------------------------
def save_draft(acc, data, draft_id=None):
    if draft_id:
        d = db.session.get(MailDraft, draft_id)
        if d is None or d.account_id != acc.id:
            raise MailAccessError('taslak bulunamadı')
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
        raise MailAccessError('taslak bulunamadı')
    db.session.delete(d)
    db.session.commit()


def load_message_body(acc, message):
    """Mesajın gövdesi/ekleri DB'de yoksa IMAP'ten çekip kalıcı yaz (lazy). Döner: message."""
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
    """Poller: INBOX + izlenmeye değer klasörler. Yeni mailde bildirim. Döner: yeni sayısı."""
    conn = conn_for(acc)
    folders = _upsert_folders(acc, conn)
    total = 0
    # INBOX (special_use=inbox) öncelik; yoksa 'INBOX' path
    inbox_paths = [p for p, f in folders.items() if f.special_use == 'inbox'] or ['INBOX']
    for path in inbox_paths:
        if path not in folders:
            continue
        n = sync_folder(acc, path)
        total += n
        if n:
            notifications.push(acc.owner_sub, 'mail', f'{n} yeni e-posta',
                               f'{acc.email} · {path}', link='/posta')
    if total:
        db.session.commit()
    acc.last_ok_at = utcnow()
    acc.last_error = None
    db.session.commit()
    return total


# --- gönderim ---------------------------------------------------------------
def send_message(user, account_id, payload):
    """SMTP gönder + Sent APPEND + DB kayıt. payload: to[], cc[], subject, body_text,
    body_html?, in_reply_to?, reply_to_message_id? (yerel MailMessage.id → \\Answered)."""
    acc = require_account(user, account_id)
    to = [a.strip() for a in (payload.get('to') or []) if a.strip()]
    if not to:
        raise MailConfigError('en az bir alıcı (to) gerekli')
    cc = [a.strip() for a in (payload.get('cc') or []) if a.strip()]
    conn = conn_for(acc)

    reply_src = None
    in_reply_to = payload.get('in_reply_to')
    if payload.get('reply_to_message_id'):
        reply_src = db.session.get(MailMessage, payload['reply_to_message_id'])
        if reply_src and reply_src.account_id == acc.id:
            in_reply_to = reply_src.message_id
    references = reply_src.references if reply_src else None

    # Gönderim sonucu hesabın SAĞLIK DURUMUNU günceller. Eskiden bunu yalnız
    # `test_account` yapıyordu ve o uç panelden hiç çağrılmıyor → SMTP ağ engeli
    # döneminde yazılan "gönderim kapalı" notu, gönderim çalışmaya başladıktan
    # sonra da kayıtta kalıyordu (kendiliğinden temizlenen bir yol yoktu).
    # Artık ilk başarılı gönderim notu siler, başarısız gönderim gerçek sebebi yazar.
    try:
        mid, raw = gw.send(conn, to=to, cc=cc, subject=payload.get('subject', ''),
                           body_text=payload.get('body_text', ''),
                           body_html=payload.get('body_html'),
                           in_reply_to=in_reply_to, references=references,
                           attachments=payload.get('attachments') or ())
    except gw.MailError as e:
        acc.last_error = (
            f'Gönderim başarısız — SMTP kimliği reddedildi (parolayı kontrol edin): {e}'
            if isinstance(e, gw.MailAuthError)
            else f'Gönderim başarısız — SMTP sunucusuna erişilemedi: {e}')
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
