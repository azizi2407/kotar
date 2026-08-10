"""/api/sharing/uploads/<id> DELETE — yalnız superadmin soft-delete edebilir."""
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers

SUPERADMIN = {'sub': '9', 'email': 'superadmin@example.com', 'name': 'Superadmin', 'role': 'management'}


def _mk_upload(client):
    """Bir müşteri + CardUpload satırı oluştur, upload id döner."""
    login_as(client, MANAGER)
    cid = client.post('/api/clients', json={'name': 'Silme Müşteri'},
                      headers=csrf_headers(client)).get_json()['client']['id']
    from extensions import db
    from models_sharing import CardUpload
    up = CardUpload(client_id=cid, week_iso='2026-W30', category='post',
                    file_id='FID1', file_name='yanlis.png')
    db.session.add(up)
    db.session.commit()
    return up.id


def test_superadmin_can_soft_delete(client):
    up_id = _mk_upload(client)
    login_as(client, SUPERADMIN)
    r = client.delete(f'/api/sharing/uploads/{up_id}', headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()['ok'] is True
    from extensions import db
    from models_sharing import CardUpload
    assert db.session.get(CardUpload, up_id).deleted_at is not None


def test_non_superadmin_forbidden(client):
    up_id = _mk_upload(client)
    login_as(client, MANAGER)  # management ama superadmin değil
    r = client.delete(f'/api/sharing/uploads/{up_id}', headers=csrf_headers(client))
    assert r.status_code == 403
    from extensions import db
    from models_sharing import CardUpload
    assert db.session.get(CardUpload, up_id).deleted_at is None  # silinmedi


def test_missing_upload_404(client):
    _mk_upload(client)
    login_as(client, SUPERADMIN)
    r = client.delete('/api/sharing/uploads/999999', headers=csrf_headers(client))
    assert r.status_code == 404


def test_no_session_401(client):
    up_id = _mk_upload(client)
    with client.session_transaction() as sess:
        sess.clear()
        sess['csrf'] = 'tok'  # csrf geçsin ama kullanıcı yok → handler 401
    r = client.delete(f'/api/sharing/uploads/{up_id}', headers={'X-CSRFToken': 'tok'})
    assert r.status_code == 401
