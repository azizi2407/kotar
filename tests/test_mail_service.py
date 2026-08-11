"""mail_service — authorization, domain allowlist, Fernet round-trip, sync idempotency."""
import pytest

import mail_gateway as gw
import mail_service as svc
from extensions import db
from models_mail import MailAccount, MailMessage

NORMAL = {'sub': 's1', 'email': 'mert@example.com', 'name': 'Mert', 'role': 'management'}
OTHER = {'sub': 's2', 'email': 'talu@example.com', 'name': 'Talu', 'role': 'management'}
SUPER = {'sub': 's0', 'email': 'superadmin@example.com', 'name': 'Superadmin', 'role': 'management'}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(gw, 'test_imap', lambda conn: None)
    monkeypatch.setattr(gw, 'test_smtp', lambda conn: None)
    monkeypatch.setattr(gw, 'test_connection', lambda conn: None)


def _mk(owner_sub='s1', email='mert@example.com', is_shared=False, secret='pw'):
    a = MailAccount(owner_sub=owner_sub, email=email, imap_host='h', imap_port=993,
                    imap_ssl=True, smtp_host='h', smtp_port=465, smtp_security='ssl',
                    secret_enc=svc.mail_crypto.encrypt(secret), is_shared=is_shared, active=True)
    db.session.add(a)
    db.session.commit()
    return a


def test_create_rejects_foreign_domain():
    with pytest.raises(svc.MailConfigError):
        svc.create_account(NORMAL, {'email': 'x@gmail.com', 'password': 'p'})


def test_create_own_account_ok():
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'gizli'})
    assert acc.owner_sub == 's1' and acc.id
    # secret is stored encrypted, and can be decrypted
    assert svc.mail_crypto.decrypt(acc.secret_enc) == 'gizli'


def test_create_succeeds_when_smtp_blocked(monkeypatch):
    # Even if SMTP is unreachable, the account still gets created as long as IMAP is OK, and a warning is written.
    # (2026-07-31: the text changed from "SMTP could not be verified" to "SMTP server unreachable";
    #  deliberately changed so it can be distinguished from an auth error.)
    monkeypatch.setattr(gw, 'test_smtp', lambda conn: (_ for _ in ()).throw(gw.MailError('timed out')))
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})
    assert acc.id
    assert 'unreachable' in (acc.last_error or '')
    assert 'password' not in (acc.last_error or '')     # network issue, NOT an auth issue


def test_create_smtp_KIMLIK_hatasini_agdan_AYIRIR(monkeypatch):
    """`MailAuthError` is a subclass of `MailError`; a single `except gw.MailError`
    caught both, so a wrong SMTP password looked like "sending is currently disabled"
    (network block). After the network block was lifted, this text actively
    misleads: a fixable password problem gets mistaken for a "server block"
    and nobody touches it."""
    monkeypatch.setattr(gw, 'test_smtp',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535 bad password')))
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})
    assert acc.id
    assert 'password' in (acc.last_error or '')
    assert 'unreachable' not in (acc.last_error or '')


def test_test_account_hata_SINIFINI_raporlar(monkeypatch):
    """"fix the password" and "server unreachable" are different actions in the panel →
    the endpoint also returns the error's class."""
    a = _mk()
    monkeypatch.setattr(gw, 'test_smtp',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535')))
    r = svc.test_account(NORMAL, a.id)
    assert r['imap_ok'] is True and r['smtp_ok'] is False
    assert r['smtp_error_kind'] == 'auth'

    monkeypatch.setattr(gw, 'test_smtp',
                        lambda conn: (_ for _ in ()).throw(gw.MailError('timed out')))
    assert svc.test_account(NORMAL, a.id)['smtp_error_kind'] == 'connect'


def test_create_fails_when_imap_bad(monkeypatch):
    monkeypatch.setattr(gw, 'test_imap', lambda conn: (_ for _ in ()).throw(gw.MailAuthError('bad')))
    with pytest.raises(gw.MailAuthError):
        svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})


def test_shared_account_superadmin_only():
    with pytest.raises(svc.MailAccessError):
        svc.create_account(NORMAL, {'email': 'info@example.com', 'password': 'p',
                                    'is_shared': True})
    acc = svc.create_account(SUPER, {'email': 'info@example.com', 'password': 'p',
                                     'is_shared': True})
    assert acc.is_shared is True


def test_require_account_blocks_foreign():
    a = _mk(owner_sub='s1', email='mert@example.com')
    with pytest.raises(svc.MailAccessError):
        svc.require_account(OTHER, a.id)  # s2 is someone else's private account


def test_accessible_accounts_own_plus_shared():
    _mk(owner_sub='s1', email='mert@example.com')
    _mk(owner_sub='s2', email='talu@example.com')            # someone else's private one — not visible
    _mk(owner_sub='s0', email='info@example.com', is_shared=True)  # shared — visible
    emails = {a.email for a in svc.accessible_accounts(NORMAL)}
    assert emails == {'mert@example.com', 'info@example.com'}


# --- password update (2026-07-31) -----------------------------------------
# The user changed their password on the mail server → the panel connection broke →
# "Update password" returned a 500. Root cause: the panel wasn't UPDATING the
# existing account, it was trying to CREATE a new one (violating `uq_mail_owner_email`).
# This block pins down the backend side of that flow.

def test_parola_guncelleme_IMAP_ile_DOGRULANIR(monkeypatch):
    """Silently saving a wrong password leaves the user in a broken state —
    exactly the state they were trying to fix. It must not be written without verification."""
    a = _mk(secret='eski')
    monkeypatch.setattr(gw, 'test_imap',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535 bad')))
    with pytest.raises(gw.MailAuthError):
        svc.update_account(NORMAL, a.id, {'password': 'yanlis'})
    db.session.rollback()
    assert svc.mail_crypto.decrypt(db.session.get(MailAccount, a.id).secret_enc) == 'eski'


def test_parola_guncelleme_basarilida_saglik_kaydini_TEMIZLER(monkeypatch):
    """The banner looks at `last_error`; if it isn't cleared, the account keeps looking
    "broken" even after the password is fixed (same class of bug fixed on the sending path 2026-07-31)."""
    a = _mk(secret='eski')
    a.last_error = 'IMAP credentials rejected'
    db.session.commit()
    out = svc.update_account(NORMAL, a.id, {'password': 'yeni'})
    assert svc.mail_crypto.decrypt(out.secret_enc) == 'yeni'
    assert out.last_error is None and out.last_ok_at is not None


def test_parolasiz_guncelleme_IMAP_TESTI_YAPMAZ(monkeypatch):
    """A request that only changes display_name must not go over the network."""
    a = _mk()
    monkeypatch.setattr(gw, 'test_imap',
                        lambda conn: (_ for _ in ()).throw(AssertionError('ağa çıkıldı')))
    assert svc.update_account(NORMAL, a.id, {'display_name': 'Mert Y'}).display_name == 'Mert Y'


# --- linking the same email a second time -----------------------------------

def test_ayni_hesabi_tekrar_baglamak_ANLASILIR_hata_verir():
    """A raw `IntegrityError` returned a 500 and the panel showed it as "Could not connect
    (email/password?)" — even when the password was CORRECT. The message pointed in exactly the wrong direction."""
    svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})
    with pytest.raises(svc.MailConfigError) as e:
        svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p2'})
    assert 'already connected' in str(e.value)


def test_silinmis_hesap_yeniden_baglanabilir():
    """`delete_account` soft-deletes (`active=False`), but the unique constraint still sees
    the row → the "remove, reconnect" path was hitting the constraint. Reconnecting revives the row."""
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'eski'})
    acc_id = acc.id
    svc.delete_account(NORMAL, acc_id)
    again = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'yeni'})
    assert again.id == acc_id                 # not a NEW row — the same row was revived
    assert again.active is True
    assert svc.mail_crypto.decrypt(again.secret_enc) == 'yeni'


# --- does the read path update the health record? ---------------------------

def test_klasor_tazelemede_kimlik_hatasi_SAGLIGA_YAZILIR(monkeypatch):
    """When the password changes on the server, the first symptom is the folder list returning 502.
    If the health record isn't written, the banner keeps saying "No connection issue reported" (green)
    and the "Update password" button NEVER appears — the user has to test manually.
    That's exactly what happened today."""
    a = _mk()
    monkeypatch.setattr(gw, 'list_folders',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535 auth')))
    with pytest.raises(gw.MailAuthError):
        svc.refresh_folders(a)
    # The text must carry the same words as `create_account`: the banner tells apart
    # "password issue" from "server unreachable" using this text, and the gateway's raw
    # "IMAP login failed" text doesn't give that distinction.
    err = db.session.get(MailAccount, a.id).last_error or ''
    assert '535 auth' in err and 'password' in err and 'credentials' in err


def test_klasor_tazelemede_AG_hatasi_parola_hatasi_gibi_YAZILMAZ(monkeypatch):
    """Counter-check: an access error must not say "check your password", otherwise the banner
    sends the user to fix something they can't fix."""
    a = _mk()
    monkeypatch.setattr(gw, 'list_folders',
                        lambda conn: (_ for _ in ()).throw(gw.MailError('timed out')))
    with pytest.raises(gw.MailError):
        svc.refresh_folders(a)
    err = db.session.get(MailAccount, a.id).last_error or ''
    assert 'unreachable' in err and 'password' not in err


def test_conn_for_decrypts():
    a = _mk(secret='45124512*Talat')
    conn = svc.conn_for(a)
    assert conn.password == '45124512*Talat' and conn.username == 'mert@example.com'


def test_sync_folder_idempotent(monkeypatch):
    a = _mk()
    monkeypatch.setattr(gw, 'list_folders', lambda conn: [
        {'name': 'INBOX', 'path': 'INBOX', 'flags': '\\Inbox', 'special_use': 'inbox',
         'uidvalidity': 10}])
    msgs = [gw.MailMsg(uid=1, subject='bir', message_id='<1@t>', flags=set()),
            gw.MailMsg(uid=2, subject='iki', message_id='<2@t>', flags={'\\Seen'})]

    def fake_fetch(conn, folder, since_uid=None, limit=50):
        return [m for m in msgs if not since_uid or m.uid > since_uid]

    monkeypatch.setattr(gw, 'fetch_headers', fake_fetch)
    assert svc.sync_folder(a, 'INBOX') == 2
    assert svc.sync_folder(a, 'INBOX') == 0          # idempotent
    assert MailMessage.query.filter_by(account_id=a.id).count() == 2


def test_send_sets_reply_and_answered(monkeypatch):
    a = _mk()
    # source message (to be replied to)
    monkeypatch.setattr(gw, 'list_folders', lambda conn: [
        {'name': 'INBOX', 'path': 'INBOX', 'flags': '', 'special_use': 'inbox',
         'uidvalidity': 5}])
    monkeypatch.setattr(gw, 'fetch_headers', lambda conn, folder, since_uid=None, limit=50: [
        gw.MailMsg(uid=7, subject='soru', message_id='<src@t>', flags=set())])
    svc.sync_folder(a, 'INBOX')
    src = MailMessage.query.filter_by(account_id=a.id, uid=7).one()

    captured = {}
    def fake_send(conn, **kw):
        captured.update(kw)
        return '<new@t>', b'RAW'
    monkeypatch.setattr(gw, 'send', fake_send)
    monkeypatch.setattr(gw, 'append_sent', lambda conn, raw, folder='Sent': None)
    monkeypatch.setattr(gw, 'store_flags', lambda *a, **k: None)

    out = svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'subject': 'Ynt',
                                          'body_text': 'cevap', 'reply_to_message_id': src.id})
    assert captured['in_reply_to'] == '<src@t>'
    assert out['message_id'] == '<new@t>'
    db.session.refresh(src)
    assert src.answered is True


def test_send_requires_recipient():
    a = _mk()
    with pytest.raises(svc.MailConfigError):
        svc.send_message(NORMAL, a.id, {'to': [], 'subject': 's', 'body_text': 'b'})


# --- sending updates the account's HEALTH status (2026-07-31) ----------------
# Previously only `test_account` did this, and that endpoint is NEVER called from the panel
# (`testAccount` exists in `lib/mail.ts`, but no component uses it). Result: the "sending
# disabled" note written during the SMTP network block period stayed on the record even
# after the block was lifted and sending started working again — there was no self-clearing path.

def test_basarili_gonderim_BAYAT_hatayi_temizler(monkeypatch):
    a = _mk()
    a.last_error = 'IMAP OK · SMTP server unreachable (sending disabled): timed out'
    a.last_ok_at = None
    db.session.commit()

    monkeypatch.setattr(gw, 'send', lambda conn, **kw: ('<m@t>', b'RAW'))
    monkeypatch.setattr(gw, 'append_sent', lambda conn, raw, folder='Sent': None)
    svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})

    db.session.refresh(a)
    assert a.last_error is None, 'başarılı gönderim bayat "kapalı" notunu silmedi'
    assert a.last_ok_at is not None


def test_basarisiz_gonderim_sebebi_yazar_ve_hatayi_YUKSELTIR(monkeypatch):
    a = _mk()
    monkeypatch.setattr(gw, 'send',
                        lambda conn, **kw: (_ for _ in ()).throw(gw.MailError('timed out')))
    with pytest.raises(gw.MailError):
        svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})
    db.session.refresh(a)
    assert 'unreachable' in (a.last_error or '')
    assert 'password' not in (a.last_error or '')


def test_gonderim_kimlik_hatasi_parolayi_isaret_eder(monkeypatch):
    a = _mk()
    monkeypatch.setattr(gw, 'send',
                        lambda conn, **kw: (_ for _ in ()).throw(gw.MailAuthError('535')))
    with pytest.raises(gw.MailAuthError):
        svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})
    db.session.refresh(a)
    assert 'password' in (a.last_error or '')


def test_basarisiz_gonderim_Sent_APPEND_etmez(monkeypatch):
    """A message that failed to send must not be written to the Sent folder — otherwise
    the user thinks an email went out when it didn't."""
    a = _mk()
    appended = []
    monkeypatch.setattr(gw, 'send',
                        lambda conn, **kw: (_ for _ in ()).throw(gw.MailError('x')))
    monkeypatch.setattr(gw, 'append_sent',
                        lambda conn, raw, folder='Sent': appended.append(raw))
    with pytest.raises(gw.MailError):
        svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})
    assert appended == []
