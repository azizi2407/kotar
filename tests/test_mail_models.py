"""models_mail — to_dict güvenli projeksiyonu (secret sızmaz) + create_all."""
from models_mail import MailAccount, MailMessage


def test_account_to_dict_hides_secret():
    a = MailAccount(owner_sub='sub1', email='proje sahibi@example.com', imap_host='h',
                    imap_port=993, imap_ssl=True, smtp_host='h', smtp_port=465,
                    smtp_security='ssl', secret_enc='ENCRYPTED', is_shared=False)
    d = a.to_dict()
    assert d['email'] == 'proje sahibi@example.com'
    assert 'secret_enc' not in d and 'password' not in d
    assert d['imap_port'] == 993 and d['smtp_security'] == 'ssl'
    assert d['has_secret'] is True


def test_message_to_dict_full_vs_list():
    m = MailMessage(account_id=1, folder_id=1, uid=5, uidvalidity=1, subject='Merhaba',
                    snippet='gövde', body_text='tam gövde', from_addr='x@example.com')
    assert 'body_text' not in m.to_dict()
    assert m.to_dict(full=True)['body_text'] == 'tam gövde'


def test_tables_created(client):
    # autouse fixture create_all çalıştırır; tabloların insert edilebildiğini doğrula
    from extensions import db
    a = MailAccount(owner_sub='s', email='info@example.com', imap_host='h', imap_port=993,
                    imap_ssl=True, smtp_host='h', smtp_port=465, smtp_security='ssl',
                    secret_enc='e', is_shared=True)
    db.session.add(a)
    db.session.commit()
    assert MailAccount.query.count() == 1
