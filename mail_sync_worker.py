"""Mail poller — poll_enabled hesapların INBOX'ını periyodik IMAP→DB senkronlar.

systemd --user oneshot + timer (agency-mail-sync.timer, ~5 dk). Infisical
enjeksiyonlu (scripts/mail_sync_start.sh → MAIL_ENC_KEY). Hata izole: bir hesap
patlarsa diğerleri sürer, last_error yazılır. Yeni mailde notifications.push
(mail_service.sync_account içinde)."""
import logging

import mail_service
from extensions import db
from models_mail import MailAccount

log = logging.getLogger('agency.mail_sync')


def run_once():
    """poll_enabled + active hesapları senkronla. Döner: {account_email: yeni_sayısı}."""
    result = {}
    accounts = MailAccount.query.filter_by(poll_enabled=True, active=True).all()
    for acc in accounts:
        try:
            n = mail_service.sync_account(acc)
            result[acc.email] = n
            if n:
                log.info('mail_sync %s: %d yeni', acc.email, n)
        except Exception as e:  # noqa: BLE001 — bir hesap diğerlerini engellemesin
            db.session.rollback()
            acc.last_error = str(e)
            db.session.commit()
            result[acc.email] = f'HATA: {e}'
            log.warning('mail_sync %s hata: %s', acc.email, e)
    return result


def main():
    from app import app
    with app.app_context():
        res = run_once()
        print(f'[mail_sync] {res}', flush=True)


if __name__ == '__main__':
    main()
