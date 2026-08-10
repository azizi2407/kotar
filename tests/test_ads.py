"""/api/ads — Reklam Takibi: yetki, CRUD, doğrulama, özet, soft-delete, filtreler."""
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


def _client_id(client, name='Reklam Müşteri'):
    login_as(client, MANAGER)
    return client.post('/api/clients', json={'name': name},
                       headers=csrf_headers(client)).get_json()['client']['id']


def _create(client, cid, **kw):
    body = {'client_id': cid, 'start_date': '2026-07-01', 'end_date': '2026-07-10',
            'amount_spent': '1500.50', 'platform': 'meta', 'status': 'active',
            'title': 'Yaz Kampanyası'}
    body.update(kw)
    return client.post('/api/ads', json=body, headers=csrf_headers(client))


# --- yetki ---------------------------------------------------------------
def test_designer_cannot_see(client):
    _client_id(client)
    login_as(client, DESIGNER)
    assert client.get('/api/ads').status_code == 403


def test_no_session_401(client):
    assert client.get('/api/ads').status_code == 401


def test_designer_cannot_create(client):
    cid = _client_id(client)
    login_as(client, DESIGNER)
    assert _create(client, cid).status_code == 403


# --- CRUD ----------------------------------------------------------------
def test_create_and_list(client):
    cid = _client_id(client)
    r = _create(client, cid)
    assert r.status_code == 201, r.get_json()
    camp = r.get_json()['campaign']
    assert camp['amount_spent'] == 1500.50
    assert camp['client_name'] == 'Reklam Müşteri'
    assert camp['platform'] == 'meta' and camp['status'] == 'active'

    data = client.get('/api/ads').get_json()
    assert len(data['campaigns']) == 1
    assert data['summary']['total_amount'] == 1500.50
    assert data['summary']['count'] == 1


def test_update(client):
    cid = _client_id(client)
    camp_id = _create(client, cid).get_json()['campaign']['id']
    r = client.patch(f'/api/ads/{camp_id}',
                     json={'amount_spent': '2000', 'status': 'finished', 'reach': 12000},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    camp = r.get_json()['campaign']
    assert camp['amount_spent'] == 2000.0 and camp['status'] == 'finished'
    assert camp['reach'] == 12000


def test_soft_delete_hides_but_keeps_row(client):
    cid = _client_id(client)
    camp_id = _create(client, cid).get_json()['campaign']['id']
    assert client.delete(f'/api/ads/{camp_id}',
                         headers=csrf_headers(client)).status_code == 200
    assert client.get('/api/ads').get_json()['campaigns'] == []
    from extensions import db
    from models import AdCampaign
    assert db.session.get(AdCampaign, camp_id).deleted_at is not None  # satır duruyor


def test_delete_missing_404(client):
    _client_id(client)
    assert client.delete('/api/ads/999999',
                         headers=csrf_headers(client)).status_code == 404


# --- doğrulama -----------------------------------------------------------
def test_end_before_start_rejected(client):
    cid = _client_id(client)
    r = _create(client, cid, start_date='2026-07-10', end_date='2026-07-01')
    assert r.status_code == 400 and 'bitiş' in r.get_json()['error']


def test_negative_amount_rejected(client):
    cid = _client_id(client)
    r = _create(client, cid, amount_spent='-5')
    assert r.status_code == 400


def test_bad_platform_and_status_rejected(client):
    cid = _client_id(client)
    assert _create(client, cid, platform='faks').status_code == 400
    assert _create(client, cid, status='belirsiz').status_code == 400


def test_missing_start_date_rejected(client):
    cid = _client_id(client)
    r = _create(client, cid, start_date=None)
    assert r.status_code == 400


def test_unknown_client_rejected(client):
    _client_id(client)
    r = client.post('/api/ads', json={'client_id': 999999, 'start_date': '2026-07-01',
                                      'amount_spent': '10'},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_end_date_optional(client):
    cid = _client_id(client)
    r = _create(client, cid, end_date=None)
    assert r.status_code == 201 and r.get_json()['campaign']['end_date'] is None


# --- özet + filtreler ----------------------------------------------------
def test_summary_by_client(client):
    a = _client_id(client, 'A Müşteri')
    b = _client_id(client, 'B Müşteri')
    _create(client, a, amount_spent='100')
    _create(client, a, amount_spent='250')
    _create(client, b, amount_spent='400')

    s = client.get('/api/ads').get_json()['summary']
    assert s['total_amount'] == 750.0 and s['count'] == 3
    # en çok harcayan başta
    assert [e['client_name'] for e in s['by_client']] == ['B Müşteri', 'A Müşteri']
    a_row = next(e for e in s['by_client'] if e['client_name'] == 'A Müşteri')
    assert a_row['total'] == 350.0 and a_row['count'] == 2


def test_client_filter(client):
    a = _client_id(client, 'A Müşteri')
    b = _client_id(client, 'B Müşteri')
    _create(client, a, amount_spent='100')
    _create(client, b, amount_spent='400')
    data = client.get(f'/api/ads?client_id={b}').get_json()
    assert data['summary']['total_amount'] == 400.0 and len(data['campaigns']) == 1


def test_status_filter(client):
    cid = _client_id(client)
    _create(client, cid, status='active')
    _create(client, cid, status='planned')
    assert len(client.get('/api/ads?status=planned').get_json()['campaigns']) == 1


def test_date_range_overlap_filter(client):
    cid = _client_id(client)
    _create(client, cid, start_date='2026-06-01', end_date='2026-06-10')  # aralık dışı
    _create(client, cid, start_date='2026-07-05', end_date='2026-07-20')  # örtüşür
    _create(client, cid, start_date='2026-07-01', end_date=None)          # devam ediyor → örtüşür
    got = client.get('/api/ads?from=2026-07-10&to=2026-07-31').get_json()['campaigns']
    assert len(got) == 2


def test_bad_date_filter_400(client):
    _client_id(client)
    assert client.get('/api/ads?from=abc').status_code == 400
