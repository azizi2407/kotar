"""/api/planning — Planning Board: permission matrix, delta PATCH, conflicts, validation."""
import datetime as dt

from sqlalchemy import event

from conftest import (CONTENT_CREATOR, DESIGNER, MANAGER, PENDING, VIDEOGRAPHER, login_as)
from extensions import db
from test_session_csrf import csrf_headers

BASE = '/api/planning'
MGMT = f'{BASE}/boards/management'


# --- helpers ---------------------------------------------------------

def _seed_users():
    """Write panel-role users into users_ref (permission + name resolution relies on this)."""
    from models import UserRef
    for u in (MANAGER, DESIGNER, CONTENT_CREATOR, VIDEOGRAPHER):
        db.session.add(UserRef(sub=u['sub'], email=u['email'], name=u['name'], role=u['role']))
    db.session.commit()


def _patch(client, board_key, upsert=None, delete=None, base_version=None):
    body = {'upsert': upsert or [], 'delete': delete or []}
    if base_version is not None:
        body['base_version'] = base_version
    return client.patch(f'{BASE}/boards/{board_key}/items', json=body,
                        headers=csrf_headers(client))


def _card(key, **kw):
    body = {'item_key': key, 'type': 'card', 'title': f'Kart {key}', 'x': 10, 'y': 20}
    body.update(kw)
    return body


def _items(client, board_key):
    return client.get(f'{BASE}/boards/{board_key}').get_json()['items']


def _by_key(items):
    return {i['item_key']: i for i in items}


# --- permission matrix -------------------------------------------------------

def test_anonim_401(client):
    assert client.get(MGMT).status_code == 401
    assert client.get(f'{BASE}/boards').status_code == 401


def test_management_yonetim_panosuna_erisir(client):
    login_as(client, MANAGER)
    assert client.get(MGMT).status_code == 200
    assert _patch(client, 'management', [_card('a1')]).status_code == 200


def test_management_kendi_panosuna_erisir(client):
    login_as(client, MANAGER)
    assert client.get(f'{BASE}/boards/user:1').status_code == 200


def test_management_baskasinin_panosuna_erisir(client):
    login_as(client, MANAGER)
    _seed_users()
    assert client.get(f'{BASE}/boards/user:2').status_code == 200
    assert _patch(client, 'user:2', [_card('a1')]).status_code == 200


def test_designer_yonetim_panosunu_goremez(client):
    login_as(client, DESIGNER)
    r = client.get(MGMT)
    assert r.status_code == 403
    assert 'management' in r.get_json()['error']


def test_designer_yonetim_panosuna_yazamaz(client):
    login_as(client, DESIGNER)
    assert _patch(client, 'management', [_card('a1')]).status_code == 403


def test_designer_kendi_panosunu_okur_ve_yazar(client):
    login_as(client, DESIGNER)
    assert client.get(f'{BASE}/boards/user:2').status_code == 200
    assert _patch(client, 'user:2', [_card('a1')]).status_code == 200


def test_designer_baskasinin_panosunu_goremez(client):
    login_as(client, DESIGNER)
    _seed_users()
    r = client.get(f'{BASE}/boards/user:1')
    assert r.status_code == 403
    assert 'own' in r.get_json()['error']


def test_designer_baskasinin_panosuna_yazamaz(client):
    login_as(client, DESIGNER)
    _seed_users()
    assert _patch(client, 'user:3', [_card('a1')]).status_code == 403


def test_content_creator_kendi_panosu_evet_yonetim_hayir(client):
    login_as(client, CONTENT_CREATOR)
    assert client.get(f'{BASE}/boards/user:3').status_code == 200
    assert client.get(MGMT).status_code == 403


def test_videographer_kendi_panosu_evet_yonetim_hayir(client):
    login_as(client, VIDEOGRAPHER)
    assert client.get(f'{BASE}/boards/user:4').status_code == 200
    assert client.get(MGMT).status_code == 403


def test_pending_rol_hicbir_panoya_erisemez(client):
    login_as(client, PENDING)
    assert client.get(MGMT).status_code == 403
    assert client.get(f'{BASE}/boards/user:9').status_code == 403
    assert client.get(f'{BASE}/boards').status_code == 403


def test_bozuk_board_key_400(client):
    login_as(client, MANAGER)
    for bad in ('user:', 'yonetim', 'user:a b', 'USER:1'):
        assert client.get(f'{BASE}/boards/{bad}').status_code == 400, bad


def test_bilinmeyen_sub_404_management(client):
    login_as(client, MANAGER)
    _seed_users()
    assert client.get(f'{BASE}/boards/user:9999').status_code == 404


def test_impersonation_yonetim_panosunu_acmaz(client):
    """Authorization goes through current_user() — when the manager looks through the
    designer's eyes, they CANNOT see the management board. (Role elevation is not
    possible via impersonation.)"""
    with client.session_transaction() as sess:
        sess['user'] = dict(DESIGNER)          # effective identity
        sess['impersonator'] = dict(MANAGER)   # real identity
    assert client.get(MGMT).status_code == 403


# --- board list --------------------------------------------------------

def test_boards_calisana_yalniz_kendi_panosunu_dondurur(client):
    """The dropdown being hidden derives from the API — not just a frontend decision."""
    login_as(client, DESIGNER)
    _seed_users()
    boards = client.get(f'{BASE}/boards').get_json()['boards']
    assert [b['key'] for b in boards] == ['user:2']


def test_boards_management_yonetim_plus_tum_panel_rolleri(client):
    login_as(client, MANAGER)
    _seed_users()
    boards = client.get(f'{BASE}/boards').get_json()['boards']
    assert boards[0]['key'] == 'management'
    assert boards[0]['title'] == 'Management Board'
    assert set(b['key'] for b in boards) == {'management', 'user:1', 'user:2', 'user:3', 'user:4'}
    assert next(b for b in boards if b['key'] == 'user:2')['title'] == 'Tasarımcı'


def test_boards_acilmamis_pano_sanal_doner(client):
    login_as(client, MANAGER)
    _seed_users()
    boards = client.get(f'{BASE}/boards').get_json()['boards']
    assert all(b['version'] == 0 and b['item_count'] == 0 for b in boards)


def test_boards_item_count_dogru(client):
    login_as(client, MANAGER)
    _seed_users()
    _patch(client, 'management', [_card('a1'), _card('a2')])
    boards = client.get(f'{BASE}/boards').get_json()['boards']
    assert next(b for b in boards if b['key'] == 'management')['item_count'] == 2


# --- board / version --------------------------------------------------------

def test_pano_lazy_olusur_ve_mukerrerlesmez(client):
    from models_planning import PlanningBoard
    login_as(client, MANAGER)
    client.get(MGMT)
    client.get(MGMT)
    assert PlanningBoard.query.filter_by(board_key='management').count() == 1


def test_version_monoton_artar(client):
    login_as(client, MANAGER)
    assert client.get(f'{MGMT}/version').get_json()['version'] == 0
    _patch(client, 'management', [_card('a1')])
    v1 = client.get(f'{MGMT}/version').get_json()['version']
    _patch(client, 'management', [_card('a2')])
    v2 = client.get(f'{MGMT}/version').get_json()['version']
    assert v2 > v1


def test_version_ucu_ucuz_alanlar(client):
    login_as(client, MANAGER)
    _seed_users()
    _patch(client, 'management', [_card('a1')])
    body = client.get(f'{MGMT}/version').get_json()
    assert set(body) == {'version', 'updated_at', 'last_modified_by',
                         'last_modified_name', 'item_count'}
    assert body['item_count'] == 1 and body['last_modified_name'] == 'Yönetici'


def test_version_ucu_yetki_kapisindan_gecer(client):
    login_as(client, DESIGNER)
    assert client.get(f'{MGMT}/version').status_code == 403


# --- delta PATCH ---------------------------------------------------------

def test_upsert_yaratir_sonra_gunceller_satir_cogaltmaz(client):
    from models_planning import PlanningItem
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1', title='ilk')])
    r = _patch(client, 'management', [{'item_key': 'a1', 'title': 'ikinci'}])
    assert r.status_code == 200
    assert PlanningItem.query.count() == 1
    assert _by_key(_items(client, 'management'))['a1']['title'] == 'ikinci'


def test_delete_yalniz_verilen_keyleri_siler(client):
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1'), _card('a2'), _card('a3')])
    _patch(client, 'management', delete=['a2'])
    assert set(_by_key(_items(client, 'management'))) == {'a1', 'a3'}


def test_iki_farkli_karta_yazma_birbirini_ezmez(client):
    """THE ACTUAL REGRESSION TEST — in the old full-array PATCH, the second writer used
    to delete the first writer's card. In the delta model, if two sessions touch
    different cards, both are preserved."""
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1', title='A'), _card('a2', title='B')])
    # both clients know base_version=1 (stale) but write to different cards
    _patch(client, 'management', [{'item_key': 'a1', 'title': 'A-yeni'}], base_version=1)
    _patch(client, 'management', [{'item_key': 'a2', 'title': 'B-yeni'}], base_version=1)
    items = _by_key(_items(client, 'management'))
    assert items['a1']['title'] == 'A-yeni'
    assert items['a2']['title'] == 'B-yeni'


def test_bayat_base_version_yine_uygulanir_stale_true(client):
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1')])
    r = _patch(client, 'management', [{'item_key': 'a1', 'title': 'yine de yaz'}],
               base_version=1)
    body = r.get_json()
    assert r.status_code == 200 and body['stale'] is True
    assert body['applied'][0]['title'] == 'yine de yaz'
    assert body['items'] is not None and len(body['items']) == 1


def test_guncel_base_version_stale_false_items_none(client):
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1')])
    version = client.get(f'{MGMT}/version').get_json()['version']
    body = _patch(client, 'management', [_card('a2')], base_version=version).get_json()
    assert body['stale'] is False and body['items'] is None


def test_ayni_ogede_bayat_rev_conflicts_dondurur_son_yazan_kazanir(client):
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1', title='ilk')])
    _patch(client, 'management', [{'item_key': 'a1', 'title': 'ikinci'}])   # rev 1 → 2
    body = _patch(client, 'management',
                  [{'item_key': 'a1', 'title': 'bayat yazan', 'rev': 1}]).get_json()
    assert body['conflicts'] == ['a1']
    assert _by_key(_items(client, 'management'))['a1']['title'] == 'bayat yazan'


def test_kismi_upsert_gonderilmeyen_alani_korur(client):
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1', title='başlık', color='#aabbcc', x=100)])
    _patch(client, 'management', [{'item_key': 'a1', 'x': 250}])
    item = _by_key(_items(client, 'management'))['a1']
    assert item['x'] == 250 and item['title'] == 'başlık' and item['color'] == '#aabbcc'


def test_todo_alanlari_yazilir_ve_okunur(client):
    login_as(client, MANAGER)
    _seed_users()
    _patch(client, 'management', [_card('a1', status='done', due_date='2026-08-01',
                                        assignee_sub='2')])
    item = _by_key(_items(client, 'management'))['a1']
    assert item['status'] == 'done' and item['due_date'] == '2026-08-01'
    assert item['assignee_sub'] == '2' and item['assignee_name'] == 'Tasarımcı'


def test_panolar_birbirinden_yalitik(client):
    login_as(client, MANAGER)
    _seed_users()
    _patch(client, 'management', [_card('a1')])
    _patch(client, 'user:2', [_card('a1')])       # aynı item_key, farklı pano
    assert len(_items(client, 'management')) == 1
    assert len(_items(client, 'user:2')) == 1


# --- validation (all 400) ----------------------------------------------

def test_item_key_yoksa_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [{'title': 'anahtarsız'}]).status_code == 400


def test_gecersiz_item_key_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [{'item_key': 'boşluk var'}]).status_code == 400


def test_gecersiz_type_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', type='uydurma')]).status_code == 400


def test_javascript_link_reddedilir(client):
    """The old canvas link field wasn't filtered at all."""
    login_as(client, MANAGER)
    r = _patch(client, 'management', [_card('a1', link='javascript:alert(1)')])
    assert r.status_code == 400 and 'http' in r.get_json()['error']


def test_http_link_kabul_edilir(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management',
                  [_card('a1', link='https://ornek.com')]).status_code == 200


def test_x_sayi_degilse_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', x='abc')]).status_code == 400


def test_koordinat_siniri_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', x=999999)]).status_code == 400


def test_boyut_araligi_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', width=5)]).status_code == 400
    assert _patch(client, 'management', [_card('a1', height=9000)]).status_code == 400


def test_uzun_baslik_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', title='x' * 301)]).status_code == 400


def test_gecersiz_renk_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', color='kirmizi')]).status_code == 400


def test_gecersiz_status_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', status='belki')]).status_code == 400


def test_bozuk_due_date_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', due_date='dun')]).status_code == 400


def test_bilinmeyen_assignee_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management', [_card('a1', assignee_sub='9999')]).status_code == 400


def test_from_key_siz_edge_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management',
                  [{'item_key': 'e1', 'type': 'edge', 'from_key': 'a1'}]).status_code == 400


def test_extra_cok_buyuk_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management',
                  [_card('a1', extra={'k': 'x' * 5000})]).status_code == 400


def test_batch_siniri_400(client):
    login_as(client, MANAGER)
    assert _patch(client, 'management',
                  [_card(f'a{i}') for i in range(201)]).status_code == 400


def test_upsert_liste_degilse_400(client):
    login_as(client, MANAGER)
    r = client.patch(f'{BASE}/boards/management/items', json={'upsert': 'x'},
                     headers=csrf_headers(client))
    assert r.status_code == 400


def test_dogrulama_hatasi_hicbir_seyi_yazmaz(client):
    """If there's an error in the middle of the batch, the whole thing is rolled back."""
    from models_planning import PlanningItem
    login_as(client, MANAGER)
    _patch(client, 'management', [_card('a1'), _card('a2', type='uydurma')])
    assert PlanningItem.query.count() == 0


# --- CSRF / schema ---------------------------------------------------------

def test_patch_csrf_yoksa_403(client):
    login_as(client, MANAGER)
    r = client.patch(f'{BASE}/boards/management/items', json={'upsert': []})
    assert r.status_code == 403


def test_tablolar_create_all_ile_gelir(client):
    """If `import models_planning` is forgotten in app.py, the tables never get created
    in prod → 500. This test locks in that import line."""
    names = set(db.metadata.tables)
    assert {'planning_boards', 'planning_items'} <= names


def test_board_get_sorgu_sayisi_oge_sayisindan_bagimsiz(client):
    """N+1 guard — breaks if assignee_name/updated_by_name get resolved via lazy access."""
    def count_queries(n_items):
        db.drop_all()
        db.create_all()
        login_as(client, MANAGER)
        _seed_users()
        _patch(client, 'management', [_card(f'a{i}', assignee_sub='2') for i in range(n_items)])
        seen = []
        engine = db.session.get_bind()

        def _on_exec(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, 'before_cursor_execute', _on_exec)
        try:
            assert client.get(MGMT).status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', _on_exec)
        return len(seen)

    assert count_queries(3) == count_queries(20)


# =========================================================================
# Domain links + linkables + assigned (2026-07-26 React Flow migration)
# =========================================================================

def _seed_domain():
    """Linkable domain records: client, shoot task, ad campaign."""
    from models import AdCampaign, Client
    from models_sharing import ShootTask
    c = Client(name='ALBA İNŞAAT', status='active')
    db.session.add(c)
    db.session.flush()
    t = ShootTask(client_id=c.id, title='Şantiye çekimi',
                  scheduled_date=dt.date(2026, 7, 30), status='pending')
    a = AdCampaign(client_id=c.id, title='Yaz kampanyası', platform='meta',
                   start_date=dt.date(2026, 7, 1), status='active')
    db.session.add_all([t, a])
    db.session.commit()
    return c.id, t.id, a.id


# --- FK fields -----------------------------------------------------------

def test_domain_baglari_yazilir_ve_adlari_cozulur(client):
    login_as(client, MANAGER); _seed_users()
    cid, tid, aid = _seed_domain()
    r = _patch(client, 'management', [_card('a1', client_id=cid,
                                            shoot_task_id=tid, ad_campaign_id=aid)])
    assert r.status_code == 200, r.get_json()
    it = _by_key(_items(client, 'management'))['a1']
    assert (it['client_id'], it['shoot_task_id'], it['ad_campaign_id']) == (cid, tid, aid)
    # Names are resolved on the SERVER — the panel doesn't make a second request.
    assert it['client_name'] == 'ALBA İNŞAAT'
    assert it['shoot_title'] == 'Şantiye çekimi'
    assert it['campaign_title'] == 'Yaz kampanyası'


def test_bilinmeyen_cekim_gorevi_400(client):
    login_as(client, MANAGER); _seed_users()
    r = _patch(client, 'management', [_card('a1', shoot_task_id=9999)])
    assert r.status_code == 400
    assert 'shoot task' in r.get_json()['error']


def test_bilinmeyen_kampanya_400(client):
    login_as(client, MANAGER); _seed_users()
    r = _patch(client, 'management', [_card('a1', ad_campaign_id=9999)])
    assert r.status_code == 400
    assert 'ad campaign' in r.get_json()['error']


def test_domain_bagi_sayi_olmali_400(client):
    login_as(client, MANAGER); _seed_users()
    r = _patch(client, 'management', [_card('a1', shoot_task_id='abc')])
    assert r.status_code == 400


def test_domain_bagi_bosa_cekilebilir(client):
    login_as(client, MANAGER); _seed_users()
    cid, tid, _ = _seed_domain()
    _patch(client, 'management', [_card('a1', client_id=cid, shoot_task_id=tid)])
    _patch(client, 'management', [{'item_key': 'a1', 'shoot_task_id': None}])
    it = _by_key(_items(client, 'management'))['a1']
    assert it['shoot_task_id'] is None
    assert it['client_id'] == cid          # untouched link is preserved
    assert it['shoot_title'] is None


def test_domain_fk_leri_set_null_ile_tanimli():
    """A link that does NOT destroy the card — locks in the schema intent.

    The behavior itself (the column dropping to NULL on delete) CANNOT be verified in
    sqlite: since `PRAGMA foreign_keys` is off, FK actions never fire, and turning the
    pragma on would change FK behavior for all 864 tests. Verified against live
    Postgres (2026-07-26: `pg_constraint.confdeltype = 'n'`).
    Here it's the definition itself that's locked in — if someone drops `ondelete`,
    this test breaks. The same reasoning applies to `with_for_update` (see
    board_items_patch)."""
    from models_planning import PlanningItem
    fks = {fk.parent.name: fk for fk in PlanningItem.__table__.foreign_keys}
    for col in ('shoot_task_id', 'ad_campaign_id'):
        assert fks[col].ondelete == 'SET NULL', col


def test_cekim_silinince_kart_yasar(client):
    """When the shoot record goes away, the planning card STAYS — the card is planning
    data, not the shoot's record."""
    from models_sharing import ShootTask
    login_as(client, MANAGER); _seed_users()
    _, tid, _ = _seed_domain()
    _patch(client, 'management', [_card('a1', shoot_task_id=tid)])
    db.session.delete(db.session.get(ShootTask, tid))
    db.session.commit()
    assert len(_items(client, 'management')) == 1


def test_bilinmeyen_alan_sessizce_yutulur(client):
    """GUARD — documentation in nature: `_apply_item` ignores unknown keys, doesn't
    give 400. So a typo returns 200 but writes NOTHING. Locked in so this behavior is
    known when adding new fields."""
    login_as(client, MANAGER); _seed_users()
    r = _patch(client, 'management', [_card('a1', shoot_taskid=5, bilinmeyen='x')])
    assert r.status_code == 200
    it = _by_key(_items(client, 'management'))['a1']
    assert it['shoot_task_id'] is None
    assert 'bilinmeyen' not in it


# --- extra shallow-merge ---------------------------------------------------

def test_extra_merge_edilir_ezilmez(client):
    login_as(client, MANAGER); _seed_users()
    _patch(client, 'management', [_card('a1', extra={'a': 1, 'b': 2})])
    _patch(client, 'management', [{'item_key': 'a1', 'extra': {'b': 3, 'c': 4}}])
    it = _by_key(_items(client, 'management'))['a1']
    assert it['extra'] == {'a': 1, 'b': 3, 'c': 4}


def test_extra_anahtari_null_ile_silinir(client):
    login_as(client, MANAGER); _seed_users()
    _patch(client, 'management', [_card('a1', extra={'a': 1, 'b': 2})])
    _patch(client, 'management', [{'item_key': 'a1', 'extra': {'a': None}}])
    assert _by_key(_items(client, 'management'))['a1']['extra'] == {'b': 2}


# --- N+1 guard (extended) ------------------------------------------

def test_domain_adlari_sorgu_sayisini_buyutmez(client):
    """Three derived names (client/shoot/campaign) — one batch query per LINK TYPE.
    If `r.client.name` is written, a per-item lazy query appears and this test
    breaks."""
    def count(n_items):
        db.drop_all(); db.create_all()
        login_as(client, MANAGER); _seed_users()
        cid, tid, aid = _seed_domain()
        _patch(client, 'management',
               [_card(f'a{i}', client_id=cid, shoot_task_id=tid, ad_campaign_id=aid)
                for i in range(n_items)])
        seen = []
        engine = db.session.get_bind()

        def _on_exec(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, 'before_cursor_execute', _on_exec)
        try:
            assert client.get(MGMT).status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', _on_exec)
        return len(seen)

    assert count(3) == count(20)


# --- /linkables permission matrix ------------------------------------------------

LINKABLES = f'{BASE}/linkables'


def test_linkables_anonim_401(client):
    assert client.get(LINKABLES).status_code == 401


def test_linkables_pending_403(client):
    login_as(client, PENDING); _seed_users()
    assert client.get(LINKABLES).status_code == 403


def test_linkables_management_dort_bolum(client):
    login_as(client, MANAGER); _seed_users(); _seed_domain()
    d = client.get(LINKABLES).get_json()
    assert len(d['clients']) == 1
    assert len(d['shoot_tasks']) == 1
    assert len(d['ad_campaigns']) == 1
    assert len(d['users']) == 4


def test_linkables_videographer_kampanyayi_gormez(client):
    """Financial info — like ads.py, open only to management. NOT 403, empty array:
    the panel just doesn't render the section."""
    login_as(client, VIDEOGRAPHER); _seed_users(); _seed_domain()
    d = client.get(LINKABLES).get_json()
    assert d['ad_campaigns'] == []
    assert len(d['shoot_tasks']) == 1     # the shoot is their job, they see it
    assert len(d['clients']) == 1


def test_linkables_designer_cekim_ve_kampanya_gormez(client):
    login_as(client, DESIGNER); _seed_users(); _seed_domain()
    d = client.get(LINKABLES).get_json()
    assert d['shoot_tasks'] == []
    assert d['ad_campaigns'] == []
    assert len(d['clients']) == 1


def test_linkables_users_pending_icermez(client):
    """`/api/users` also returns pending users who have NO board access at all; it's
    filtered here so cards can't be assigned to people without access."""
    from models import UserRef
    login_as(client, MANAGER); _seed_users()
    db.session.add(UserRef(sub=PENDING['sub'], email=PENDING['email'],
                           name=PENDING['name'], role='pending'))
    db.session.commit()
    subs = {u['sub'] for u in client.get(LINKABLES).get_json()['users']}
    assert PENDING['sub'] not in subs


def test_linkables_arama_suzuyor(client):
    login_as(client, MANAGER); _seed_users(); _seed_domain()
    d = client.get(f'{LINKABLES}?q=ALBA').get_json()
    assert len(d['clients']) == 1
    assert client.get(f'{LINKABLES}?q=YOKBOYLE').get_json()['clients'] == []


# --- /assigned ---------------------------------------------------------------

ASSIGNED = f'{BASE}/assigned'


def test_assigned_anonim_401(client):
    assert client.get(ASSIGNED).status_code == 401


def test_assigned_calisan_baskasini_soramaz(client):
    login_as(client, DESIGNER); _seed_users()
    r = client.get(f"{ASSIGNED}?assignee_sub={MANAGER['sub']}")
    assert r.status_code == 403
    assert 'own' in r.get_json()['error']


def test_assigned_management_herkesi_sorar(client):
    login_as(client, MANAGER); _seed_users()
    assert client.get(f"{ASSIGNED}?assignee_sub={DESIGNER['sub']}").status_code == 200


def test_assigned_yonetim_panosu_karti_calisana_DONER(client):
    """INTENTIONAL PERMISSION GAP (2026-07-26, approved by the project owner).

    The designer CANNOT open the management board (403), but a card assigned to them
    is still returned by this endpoint — otherwise a manager's assignment would stay
    invisible to the employee. This test locks in that decision: if it breaks, the
    product decision has changed, so update it deliberately, not by accident."""
    login_as(client, MANAGER); _seed_users()
    _patch(client, 'management', [_card('a1', assignee_sub=DESIGNER['sub'],
                                        due_date='2026-08-01')])
    login_as(client, DESIGNER)
    assert client.get(MGMT).status_code == 403          # board is still closed
    items = client.get(ASSIGNED).get_json()['items']    # but the card is visible
    assert [i['item_key'] for i in items] == ['a1']
    assert items[0]['board_key'] == 'management'
    assert items[0]['board_title'] == 'Management Board'


def test_assigned_sizan_alan_kumesi_dar(client):
    """Body text, color, position, and extra are NOT returned — the leak surface is
    intentionally kept narrow."""
    login_as(client, MANAGER); _seed_users()
    _patch(client, 'management', [_card('a1', assignee_sub=DESIGNER['sub'],
                                        text='gizli notlar', color='#ff0000',
                                        extra={'gizli': 1})])
    login_as(client, DESIGNER)
    it = client.get(ASSIGNED).get_json()['items'][0]
    for leak in ('text', 'color', 'x', 'y', 'extra'):
        assert leak not in it


def test_assigned_edge_dondurmez(client):
    login_as(client, MANAGER); _seed_users()
    _patch(client, 'management', [
        _card('a1', assignee_sub=DESIGNER['sub']),
        _card('a2', assignee_sub=DESIGNER['sub']),
        {'item_key': 'e1', 'type': 'edge', 'from_key': 'a1', 'to_key': 'a2',
         'assignee_sub': DESIGNER['sub'], 'x': 0, 'y': 0},
    ])
    login_as(client, DESIGNER)
    keys = {i['item_key'] for i in client.get(ASSIGNED).get_json()['items']}
    assert keys == {'a1', 'a2'}


def test_assigned_status_suzgeci(client):
    login_as(client, MANAGER); _seed_users()
    _patch(client, 'management', [
        _card('a1', assignee_sub=DESIGNER['sub'], status='open'),
        _card('a2', assignee_sub=DESIGNER['sub'], status='done'),
    ])
    login_as(client, DESIGNER)
    keys = {i['item_key'] for i in client.get(f'{ASSIGNED}?status=open').get_json()['items']}
    assert keys == {'a1'}


def test_assigned_yazma_yolu_yok(client):
    login_as(client, MANAGER); _seed_users()
    for method in ('post', 'patch', 'delete', 'put'):
        r = getattr(client, method)(ASSIGNED, json={}, headers=csrf_headers(client))
        assert r.status_code == 405, method
