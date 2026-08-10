"""/api/mail/* — oturum, CSRF, yetki, secret sızmaması, 503 fail-closed."""
import mail_gateway as gw
import mail_service as svc
from conftest import MANAGER, login_as
from extensions import db
from models_mail import MailAccount
from test_session_csrf import csrf_headers

OTHER = {'sub': '99', 'email': 'talu@example.com', 'name': 'Talu', 'role': 'management'}


def _mk(owner_sub='1', email='yonetici@example.com', is_shared=False):
    a = MailAccount(owner_sub=owner_sub, email=email, imap_host='h', imap_port=993,
                    imap_ssl=True, smtp_host='h', smtp_port=465, smtp_security='ssl',
                    secret_enc=svc.mail_crypto.encrypt('pw'), is_shared=is_shared, active=True)
    db.session.add(a)
    db.session.commit()
    return a


def test_accounts_requires_login(client):
    assert client.get('/api/mail/accounts').status_code == 401


def test_accounts_hides_secret(client):
    login_as(client, MANAGER)
    _mk(owner_sub=MANAGER['sub'])
    r = client.get('/api/mail/accounts')
    assert r.status_code == 200
    data = r.get_json()['accounts']
    assert len(data) == 1
    assert 'secret_enc' not in data[0] and 'password' not in data[0]
    assert data[0]['has_secret'] is True


def test_create_rejects_foreign_domain(client):
    login_as(client, MANAGER)
    r = client.post('/api/mail/accounts', json={'email': 'x@gmail.com', 'password': 'p'},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_test_endpoint_blocks_foreign_account(client):
    login_as(client, MANAGER)
    a = _mk(owner_sub='someone-else')      # MANAGER'ın olmayan özel hesap
    r = client.post(f'/api/mail/accounts/{a.id}/test', headers=csrf_headers(client))
    assert r.status_code == 403


def test_fail_closed_when_no_key(client, monkeypatch):
    login_as(client, MANAGER)
    monkeypatch.setattr(gw, 'available', lambda: False)
    r = client.get('/api/mail/accounts')
    assert r.status_code == 503


def test_send_requires_recipient(client, monkeypatch):
    login_as(client, MANAGER)
    a = _mk(owner_sub=MANAGER['sub'])
    r = client.post(f'/api/mail/{a.id}/send', json={'to': [], 'subject': 's', 'body_text': 'b'},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_create_account_full_flow(client, monkeypatch):
    login_as(client, MANAGER)
    monkeypatch.setattr(gw, 'test_imap', lambda conn: None)
    monkeypatch.setattr(gw, 'test_smtp', lambda conn: None)
    r = client.post('/api/mail/accounts',
                    json={'email': 'yonetici@example.com', 'password': 'gizli'},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    assert r.get_json()['account']['email'] == 'yonetici@example.com'
    assert MailAccount.query.count() == 1
