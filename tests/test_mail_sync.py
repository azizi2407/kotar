"""mail_sync_worker — poll_enabled accounts are processed, errors are isolated."""
import mail_service
import mail_sync_worker as worker
from extensions import db
from models_mail import MailAccount


def _mk(email, poll_enabled=True, active=True):
    a = MailAccount(owner_sub='s', email=email, imap_host='h', imap_port=993, imap_ssl=True,
                    smtp_host='h', smtp_port=465, smtp_security='ssl',
                    secret_enc=mail_service.mail_crypto.encrypt('pw'),
                    poll_enabled=poll_enabled, active=active)
    db.session.add(a)
    db.session.commit()
    return a


def test_run_once_only_poll_enabled(monkeypatch):
    _mk('a@example.com', poll_enabled=True)
    _mk('b@example.com', poll_enabled=False)          # should be skipped
    calls = []
    monkeypatch.setattr(mail_service, 'sync_account', lambda acc: calls.append(acc.email) or 3)
    res = worker.run_once()
    assert calls == ['a@example.com']
    assert res == {'a@example.com': 3}


def test_run_once_isolates_errors(monkeypatch):
    _mk('ok@example.com')
    _mk('bad@example.com')

    def fake_sync(acc):
        if acc.email == 'bad@example.com':
            raise RuntimeError('IMAP patladı')
        return 1

    monkeypatch.setattr(mail_service, 'sync_account', fake_sync)
    res = worker.run_once()
    assert res['ok@example.com'] == 1
    assert 'HATA' in res['bad@example.com']
    bad = MailAccount.query.filter_by(email='bad@example.com').one()
    assert 'IMAP patladı' in bad.last_error
