"""/api/mail/* — panel mail module endpoints. Session comes from SSO (session);
mutations are CSRF-protected (api.csrf_protect is shared, same pattern as
sharing.py). Access is restricted by owner_sub (IDOR protection lives in
mail_service). 503 if there's no key (fail-closed)."""
from flask import Blueprint, Response, jsonify, request

import mail_gateway as gw
import mail_service as svc
from api import csrf_protect
from sso_client import current_user

bp = Blueprint('mail', __name__)
bp.before_request(csrf_protect)  # same CSRF as api


@bp.before_request
def _require_login_and_config():
    if not current_user():
        return jsonify(error='not authenticated'), 401
    if not gw.available():
        return jsonify(error='mail module not configured (MAIL_ENC_KEY missing)'), 503


@bp.errorhandler(svc.MailAccessError)
def _access(e):
    return jsonify(error=str(e)), 403


@bp.errorhandler(svc.MailConfigError)
def _config(e):
    return jsonify(error=str(e)), 400


@bp.errorhandler(gw.MailAuthError)
def _auth(e):
    return jsonify(error=str(e)), 502


@bp.errorhandler(gw.MailError)
def _mailerr(e):
    return jsonify(error=str(e)), 502


def _u():
    return current_user()


# --- accounts ---------------------------------------------------------------
@bp.get('/accounts')
def accounts():
    return jsonify(accounts=[a.to_dict() for a in svc.accessible_accounts(_u())])


@bp.post('/accounts')
def create_account():
    acc = svc.create_account(_u(), request.get_json(force=True) or {})
    return jsonify(account=acc.to_dict()), 201


@bp.patch('/accounts/<int:acc_id>')
def update_account(acc_id):
    acc = svc.update_account(_u(), acc_id, request.get_json(force=True) or {})
    return jsonify(account=acc.to_dict())


@bp.delete('/accounts/<int:acc_id>')
def delete_account(acc_id):
    svc.delete_account(_u(), acc_id)
    return jsonify(ok=True)


@bp.post('/accounts/<int:acc_id>/test')
def test_account(acc_id):
    return jsonify(svc.test_account(_u(), acc_id))


# --- folders & messages ---------------------------------------------------
@bp.get('/<int:acc_id>/folders')
def folders(acc_id):
    acc = svc.require_account(_u(), acc_id)
    return jsonify(folders=[f.to_dict() for f in svc.refresh_folders(acc)])


@bp.get('/<int:acc_id>/folders/<int:folder_id>/messages')
def messages(acc_id, folder_id):
    acc = svc.require_account(_u(), acc_id)
    page = max(1, int(request.args.get('page', 1)))
    q = request.args.get('q')
    items, total = svc.list_messages(acc, folder_id, page=page, q=q)
    return jsonify(messages=[m.to_dict() for m in items], total=total, page=page)


@bp.post('/<int:acc_id>/folders/<int:folder_id>/sync')
def sync(acc_id, folder_id):
    acc = svc.require_account(_u(), acc_id)
    from models_mail import MailFolder
    from extensions import db
    folder = db.session.get(MailFolder, folder_id)
    if folder is None or folder.account_id != acc.id:
        raise svc.MailAccessError('folder not found')
    new = svc.sync_folder(acc, folder.path)
    return jsonify(new=new)


@bp.get('/<int:acc_id>/messages/<int:msg_id>')
def message(acc_id, msg_id):
    acc = svc.require_account(_u(), acc_id)
    m = svc.get_message(acc, msg_id)
    return jsonify(message=m.to_dict(full=True))


@bp.get('/<int:acc_id>/messages/<int:msg_id>/attachments/<part_id>')
def attachment(acc_id, msg_id, part_id):
    acc = svc.require_account(_u(), acc_id)
    data, filename, ct = svc.get_attachment(acc, msg_id, part_id)
    return Response(data, mimetype=ct or 'application/octet-stream',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@bp.post('/<int:acc_id>/messages/<int:msg_id>/flags')
def flags(acc_id, msg_id):
    acc = svc.require_account(_u(), acc_id)
    body = request.get_json(force=True) or {}
    m = svc.set_message_flags(acc, msg_id, seen=body.get('seen'), flagged=body.get('flagged'))
    return jsonify(message=m.to_dict())


# --- sending & drafts ------------------------------------------------------
@bp.post('/<int:acc_id>/send')
def send(acc_id):
    out = svc.send_message(_u(), acc_id, request.get_json(force=True) or {})
    return jsonify(sent=out), 201


@bp.get('/<int:acc_id>/drafts')
def drafts(acc_id):
    acc = svc.require_account(_u(), acc_id)
    return jsonify(drafts=[d.to_dict() for d in svc.list_drafts(acc)])


@bp.post('/<int:acc_id>/drafts')
def create_draft(acc_id):
    acc = svc.require_account(_u(), acc_id)
    d = svc.save_draft(acc, request.get_json(force=True) or {})
    return jsonify(draft=d.to_dict()), 201


@bp.patch('/<int:acc_id>/drafts/<int:draft_id>')
def update_draft(acc_id, draft_id):
    acc = svc.require_account(_u(), acc_id)
    d = svc.save_draft(acc, request.get_json(force=True) or {}, draft_id=draft_id)
    return jsonify(draft=d.to_dict())


@bp.delete('/<int:acc_id>/drafts/<int:draft_id>')
def remove_draft(acc_id, draft_id):
    acc = svc.require_account(_u(), acc_id)
    svc.delete_draft(acc, draft_id)
    return jsonify(ok=True)
