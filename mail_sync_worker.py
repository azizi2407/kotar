"""Mail poller — periodically syncs poll_enabled accounts' INBOX from IMAP→DB.

systemd --user oneshot + timer (agency-mail-sync.timer, ~5 min). Infisical-injected
(scripts/mail_sync_start.sh → MAIL_ENC_KEY). Errors are isolated: if one account
blows up, the others keep going, last_error is written. notifications.push on new
mail (inside mail_service.sync_account)."""
import logging

import mail_service
from extensions import db
from models_mail import MailAccount

log = logging.getLogger('agency.mail_sync')


def run_once():
    """Sync poll_enabled + active accounts. Returns: {account_email: new_count}."""
    result = {}
    accounts = MailAccount.query.filter_by(poll_enabled=True, active=True).all()
    for acc in accounts:
        try:
            n = mail_service.sync_account(acc)
            result[acc.email] = n
            if n:
                log.info('mail_sync %s: %d yeni', acc.email, n)
        except Exception as e:  # noqa: BLE001 — one account shouldn't block the others
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
