"""/api/client-tracking — Müşteri Takip: yetki, katalog, durum hücreleri, aktivite
günlüğü, türetilmiş sinyaller ve liste ucunun N+1 muhafızı."""
import datetime as dt

from sqlalchemy import event

from conftest import DESIGNER, MANAGER, login_as
from extensions import db
from test_session_csrf import csrf_headers

BASE = '/api/client-tracking'


# --- yardımcılar ---------------------------------------------------------

def _client_id(client, name='Takip Müşteri'):
    login_as(client, MANAGER)
    return client.post('/api/clients', json={'name': name},
                       headers=csrf_headers(client)).get_json()['client']['id']


def _items(client):
    return client.get(f'{BASE}/items').get_json()['items']


def _item_id(client, key='web_sitesi'):
    return next(i['id'] for i in _items(client) if i['key'] == key)


def _put_entry(client, cid, item_id, **kw):
    body = {'status': 'var'}
    body.update(kw)
    return client.put(f'{BASE}/clients/{cid}/entries/{item_id}', json=body,
                      headers=csrf_headers(client))


def _add_note(client, cid, **kw):
    body = {'text': 'Katalog baskıya gitti'}
    body.update(kw)
    return client.post(f'{BASE}/clients/{cid}/notes', json=body,
                       headers=csrf_headers(client))


def _row(client, cid):
    """Liste yanıtından bir müşterinin satırını çek."""
    rows = client.get(BASE).get_json()['clients']
    return next(r for r in rows if r['client_id'] == cid)


def _mk_campaign(cid, start, end=None, status='active', deleted=False):
    from models import AdCampaign, utcnow
    camp = AdCampaign(client_id=cid, start_date=dt.date.fromisoformat(start),
                      end_date=dt.date.fromisoformat(end) if end else None,
                      status=status, amount_spent=0)
    if deleted:
        camp.deleted_at = utcnow()
    db.session.add(camp)
    db.session.commit()
    return camp


def _mk_shoot(cid, day, status='pending'):
    from models_sharing import ShootTask
    task = ShootTask(client_id=cid, scheduled_date=dt.date.fromisoformat(day),
                     status=status, title='Çekim')
    db.session.add(task)
    db.session.commit()
    return task


# --- yetki / CSRF --------------------------------------------------------

def test_no_session_401(client):
    assert client.get(BASE).status_code == 401


def test_designer_cannot_list(client):
    _client_id(client)
    login_as(client, DESIGNER)
    assert client.get(BASE).status_code == 403


def test_designer_cannot_write_entry(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    login_as(client, DESIGNER)
    assert _put_entry(client, cid, item_id).status_code == 403


def test_designer_cannot_manage_items(client):
    _client_id(client)
    login_as(client, DESIGNER)
    r = client.post(f'{BASE}/items', json={'name': 'Sızıntı'}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_entry_put_without_csrf_403(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    r = client.put(f'{BASE}/clients/{cid}/entries/{item_id}', json={'status': 'var'})
    assert r.status_code == 403


def test_note_delete_without_csrf_403(client):
    cid = _client_id(client)
    note_id = _add_note(client, cid).get_json()['note']['id']
    assert client.delete(f'{BASE}/notes/{note_id}').status_code == 403


# --- tohumlama -----------------------------------------------------------

def test_seed_creates_twelve_items(client):
    login_as(client, MANAGER)
    items = _items(client)
    assert len(items) == 12
    assert [i['position'] for i in items] == list(range(12))
    assert len({i['key'] for i in items}) == 12
    assert items[0]['name'] == 'Marka Tescili'


def test_seed_idempotent(client):
    login_as(client, MANAGER)
    _items(client)
    client.get(BASE)          # liste ucu da tohumlar — çift kayıt olmamalı
    assert len(_items(client)) == 12


def test_seed_does_not_resurrect_deleted_item(client):
    login_as(client, MANAGER)
    item_id = _item_id(client)
    client.delete(f'{BASE}/items/{item_id}', headers=csrf_headers(client))
    assert len(_items(client)) == 11
    assert len(client.get(BASE).get_json()['items']) == 11


# --- katalog CRUD --------------------------------------------------------

def test_create_item_appends_position(client):
    login_as(client, MANAGER)
    _items(client)
    r = client.post(f'{BASE}/items', json={'name': 'Drone Çekimi', 'category': 'uretim'},
                    headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    item = r.get_json()['item']
    assert item['position'] == 12 and item['key'] is None and item['active'] is True


def test_duplicate_item_name_rejected(client):
    login_as(client, MANAGER)
    _items(client)
    r = client.post(f'{BASE}/items', json={'name': 'web SİTESİ'},
                    headers=csrf_headers(client))
    assert r.status_code == 400
    assert 'zaten var' in r.get_json()['error']


def test_bad_category_rejected(client):
    login_as(client, MANAGER)
    _items(client)
    r = client.post(f'{BASE}/items', json={'name': 'Yeni', 'category': 'uydurma'},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_inactive_item_flagged_but_entry_kept(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    _put_entry(client, cid, item_id)
    client.patch(f'{BASE}/items/{item_id}', json={'active': False},
                 headers=csrf_headers(client))
    payload = client.get(BASE).get_json()
    assert next(i for i in payload['items'] if i['id'] == item_id)['active'] is False
    # kayıt duruyor ama fırsat sayımına girmiyor (aktif değil)
    row = next(r for r in payload['clients'] if r['client_id'] == cid)
    assert row['entries'][str(item_id)]['status'] == 'var'
    assert row['summary']['opportunity'] == 11


def test_delete_item_soft(client):
    from models import TrackingItem
    login_as(client, MANAGER)
    item_id = _item_id(client)
    assert client.delete(f'{BASE}/items/{item_id}',
                         headers=csrf_headers(client)).status_code == 200
    assert db.session.get(TrackingItem, item_id).deleted_at is not None


def test_reorder_items(client):
    login_as(client, MANAGER)
    ids = [i['id'] for i in _items(client)]
    r = client.post(f'{BASE}/items/reorder', json={'order': list(reversed(ids))},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    assert [i['id'] for i in r.get_json()['items']] == list(reversed(ids))
    bad = client.post(f'{BASE}/items/reorder', json={'order': [99999]},
                      headers=csrf_headers(client))
    assert bad.status_code == 400


# --- durum hücreleri -----------------------------------------------------

def test_put_entry_upserts_single_row(client):
    from models import ClientTrackingEntry
    cid = _client_id(client)
    item_id = _item_id(client)
    _put_entry(client, cid, item_id, status='surecte', note='ilk')
    r = _put_entry(client, cid, item_id, status='var', note='ikinci')
    assert r.status_code == 200
    assert ClientTrackingEntry.query.filter_by(client_id=cid, item_id=item_id).count() == 1
    assert r.get_json()['entry']['status'] == 'var'
    assert r.get_json()['entry']['note'] == 'ikinci'


def test_bad_status_rejected(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    r = _put_entry(client, cid, item_id, status='belki')
    assert r.status_code == 400 and 'geçersiz durum' in r.get_json()['error']


def test_bad_url_rejected(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    r = _put_entry(client, cid, item_id, url='ftp://dosya')
    assert r.status_code == 400 and 'http' in r.get_json()['error']


def test_bad_status_date_rejected(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    assert _put_entry(client, cid, item_id, status_date='abc').status_code == 400


def test_entry_unknown_item_rejected(client):
    cid = _client_id(client)
    assert _put_entry(client, cid, 99999).status_code == 400


def test_entry_deleted_client_rejected(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    client.delete(f'/api/clients/{cid}', json={}, headers=csrf_headers(client))
    assert _put_entry(client, cid, item_id).status_code == 400


def test_entry_appears_in_list_keyed_by_item_id(client):
    cid = _client_id(client)
    item_id = _item_id(client)
    _put_entry(client, cid, item_id, status_date='2026-03-04', url='https://ornek.com')
    entry = _row(client, cid)['entries'][str(item_id)]
    assert entry['status'] == 'var'
    assert entry['status_date'] == '2026-03-04'
    assert entry['url'] == 'https://ornek.com'


def test_summary_opportunity_count(client):
    cid = _client_id(client)
    ids = [i['id'] for i in _items(client)]
    _put_entry(client, cid, ids[0], status='var')
    _put_entry(client, cid, ids[1], status='var')
    _put_entry(client, cid, ids[2], status='surecte')
    summary = _row(client, cid)['summary']
    assert summary['var'] == 2 and summary['surecte'] == 1
    assert summary['yok'] == 9
    assert summary['opportunity'] == 9      # 12 aktif kalem - 3 kapatılan


# --- aktivite günlüğü ----------------------------------------------------

def test_add_note_and_last_note_is_newest(client):
    cid = _client_id(client)
    _add_note(client, cid, text='eski', happened_on='2026-01-01')
    _add_note(client, cid, text='yeni', happened_on='2026-06-01')
    assert _row(client, cid)['last_note']['text'] == 'yeni'


def test_note_requires_text(client):
    cid = _client_id(client)
    r = _add_note(client, cid, text='   ')
    assert r.status_code == 400 and 'zorunlu' in r.get_json()['error']


def test_note_soft_delete_falls_back(client):
    from models import ClientActivityNote
    cid = _client_id(client)
    _add_note(client, cid, text='eski', happened_on='2026-01-01')
    newest = _add_note(client, cid, text='yeni', happened_on='2026-06-01')
    note_id = newest.get_json()['note']['id']
    assert client.delete(f'{BASE}/notes/{note_id}',
                         headers=csrf_headers(client)).status_code == 200
    assert _row(client, cid)['last_note']['text'] == 'eski'
    assert db.session.get(ClientActivityNote, note_id).deleted_at is not None


def test_note_author_name_resolved(client):
    from models import UserRef
    cid = _client_id(client)
    db.session.add(UserRef(sub=MANAGER['sub'], email=MANAGER['email'],
                           name=MANAGER['name'], role='management'))
    db.session.commit()
    _add_note(client, cid)
    assert _row(client, cid)['last_note']['author_name'] == 'Yönetici'


def test_note_item_link_optional_and_validated(client):
    cid = _client_id(client)
    item_id = _item_id(client, 'katalog')
    assert _add_note(client, cid, item_id=item_id).status_code == 201
    assert _add_note(client, cid, item_id=None).status_code == 201
    assert _add_note(client, cid, item_id=99999).status_code == 400


def test_note_patch_updates_text(client):
    cid = _client_id(client)
    note_id = _add_note(client, cid).get_json()['note']['id']
    r = client.patch(f'{BASE}/notes/{note_id}', json={'text': 'düzeltildi'},
                     headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()['note']['text'] == 'düzeltildi'


# --- türetilmiş sinyaller ------------------------------------------------

def test_last_ad_date_derived(client):
    cid = _client_id(client)
    _mk_campaign(cid, '2026-01-01', '2026-02-01', status='finished')
    _mk_campaign(cid, '2026-03-01', '2026-03-20', status='finished')
    _mk_campaign(cid, '2026-05-01', '2026-12-31', deleted=True)   # sayılmamalı
    signals = _row(client, cid)['signals']
    assert signals['last_ad_date'] == '2026-03-20'
    assert signals['ad_count'] == 2


def test_ad_active_flag(client):
    """Devam eden kampanyada 'son reklam' BUGÜN'dür — hâlâ yayında."""
    cid = _client_id(client)
    _mk_campaign(cid, '2026-01-01', None, status='active')
    signals = _row(client, cid)['signals']
    assert signals['ad_active'] is True
    assert signals['last_ad_date'] == dt.date.today().isoformat()


def test_future_end_date_clamped_to_today(client):
    """İleride bitecek kampanya 'son reklam'ı İLERİ TARİHE taşımamalı (canlı veride
    görülen kusur: bugün 25 Tem iken 'son reklam 27 Tem' yazıyordu)."""
    cid = _client_id(client)
    today = dt.date.today()
    _mk_campaign(cid, (today - dt.timedelta(days=10)).isoformat(),
                 (today + dt.timedelta(days=2)).isoformat(), status='active')
    assert _row(client, cid)['signals']['last_ad_date'] == today.isoformat()


def test_not_yet_started_campaign_is_not_last_ad(client):
    """Planlanmış ama başlamamış kampanya 'reklam çıkıldı' saymaz."""
    cid = _client_id(client)
    future = (dt.date.today() + dt.timedelta(days=15)).isoformat()
    _mk_campaign(cid, future, None, status='planned')
    signals = _row(client, cid)['signals']
    assert signals['last_ad_date'] is None
    assert signals['ad_count'] == 1          # kayıt var, ama henüz yayına girmedi


def test_last_shoot_prefers_past_plan(client):
    """Sahada `completed` işaretlenmiyor (73 pending / 3 completed) — bu yüzden
    'son çekim' tarihi geçmiş en yeni PLAN, status'e bakılmaz."""
    cid = _client_id(client)
    today = dt.date.today()
    _mk_shoot(cid, (today - dt.timedelta(days=30)).isoformat())
    _mk_shoot(cid, (today - dt.timedelta(days=3)).isoformat())    # pending ama en yeni geçmiş
    _mk_shoot(cid, (today + dt.timedelta(days=10)).isoformat())
    signals = _row(client, cid)['signals']
    assert signals['last_shoot_date'] == (today - dt.timedelta(days=3)).isoformat()
    assert signals['next_shoot_date'] == (today + dt.timedelta(days=10)).isoformat()


def test_team_assignments_in_signals(client):
    from models import UserRef
    cid = _client_id(client)
    db.session.add(UserRef(sub='7', email='ayse@test.com', name='Ayşe', role='designer'))
    db.session.commit()
    client.patch(f'/api/clients/{cid}', json={'team_assignments': {'designer': '7'}},
                 headers=csrf_headers(client))
    team = _row(client, cid)['signals']['team']
    assert team == [{'role_slot': 'designer', 'user_id': '7', 'name': 'Ayşe'}]


def test_signals_null_for_empty_client(client):
    cid = _client_id(client)
    row = _row(client, cid)
    assert row['signals']['last_ad_date'] is None
    assert row['signals']['last_shoot_date'] is None
    assert row['signals']['next_shoot_date'] is None
    assert row['signals']['ad_count'] == 0 and row['signals']['ad_active'] is False
    assert row['last_note'] is None


# --- liste ucu genel -----------------------------------------------------

def test_list_search_filters_by_name(client):
    _client_id(client, 'Alfa Firma')
    _client_id(client, 'Beta Firma')
    rows = client.get(f'{BASE}?q=alfa').get_json()['clients']
    assert [r['client_name'] for r in rows] == ['Alfa Firma']


def test_deleted_client_not_listed(client):
    cid = _client_id(client, 'Gidecek')
    client.delete(f'/api/clients/{cid}', json={}, headers=csrf_headers(client))
    assert all(r['client_id'] != cid for r in client.get(BASE).get_json()['clients'])


def test_list_query_count_constant_with_client_count(client):
    """N+1 MUHAFIZI — liste ucunun sorgu sayısı müşteri sayısından BAĞIMSIZ olmalı.
    Serializer'a lazy erişim (client.ad_campaigns, entry.item…) eklenirse bu test kırılır."""
    def count_queries(n_clients):
        db.drop_all()
        db.create_all()
        login_as(client, MANAGER)
        for i in range(n_clients):
            cid = _client_id(client, f'Firma {i}')
            _put_entry(client, cid, _item_id(client))
            _add_note(client, cid)
            _mk_campaign(cid, '2026-02-01', '2026-02-10')
            _mk_shoot(cid, '2026-02-05')
        seen = []
        engine = db.session.get_bind()

        def _on_exec(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, 'before_cursor_execute', _on_exec)
        try:
            assert client.get(BASE).status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', _on_exec)
        return len(seen)

    assert count_queries(2) == count_queries(6)


# --- detay ucu -----------------------------------------------------------

def test_detail_returns_notes_ads_shoots(client):
    cid = _client_id(client)
    _add_note(client, cid, text='ilk temas')
    _mk_campaign(cid, '2026-02-01', '2026-02-10')
    _mk_shoot(cid, '2026-02-05')
    body = client.get(f'{BASE}/clients/{cid}').get_json()
    assert body['client']['name'] == 'Takip Müşteri'
    assert [n['text'] for n in body['notes']] == ['ilk temas']
    assert len(body['ads']) == 1 and len(body['shoots']) == 1
    assert 'amount_spent' not in body['ads'][0]   # mali veri bilerek dışarıda


def test_detail_unknown_client_404(client):
    login_as(client, MANAGER)
    assert client.get(f'{BASE}/clients/99999').status_code == 404
