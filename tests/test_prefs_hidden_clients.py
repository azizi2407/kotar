"""/api/prefs/hidden-clients — kişisel müşteri gizleme tercihi."""
from conftest import DESIGNER, MANAGER, PENDING, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers

URL = '/api/prefs/hidden-clients'
SCOPE = 'videographer_upload'
VG2 = {"sub": "12", "email": "video2@test.com", "name": "Videograf 2", "role": "videographer"}


def _client_id(client, name='Gizlenecek'):
    login_as(client, MANAGER)
    return client.post('/api/clients', json={'name': name},
                       headers=csrf_headers(client)).get_json()['client']['id']


def _set(client, client_id, hidden, scope=SCOPE):
    return client.put(URL, json={'scope': scope, 'client_id': client_id, 'hidden': hidden},
                      headers=csrf_headers(client))


def _get(client, scope=SCOPE):
    return client.get(f'{URL}?scope={scope}').get_json()


# --- yetki / CSRF --------------------------------------------------------

def test_anonim_401(client):
    assert client.get(URL).status_code == 401


def test_pending_403(client):
    login_as(client, PENDING)
    assert client.get(URL).status_code == 403


def test_csrf_yoksa_403(client):
    cid = _client_id(client)
    r = client.put(URL, json={'scope': SCOPE, 'client_id': cid, 'hidden': True})
    assert r.status_code == 403


# --- temel akış ----------------------------------------------------------

def test_gizle_ve_oku(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    r = _set(client, cid, True)
    assert r.status_code == 200
    assert r.get_json()['client_ids'] == [cid]      # yanıt TAM küme
    assert _get(client)['client_ids'] == [cid]


def test_ayni_put_iki_kez_tek_kayit(client):
    from models import UserHiddenClient
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    _set(client, cid, True)
    assert _set(client, cid, True).status_code == 200      # idempotent, IntegrityError yok
    assert UserHiddenClient.query.count() == 1


def test_geri_ac_kumeyi_bosaltir(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    _set(client, cid, True)
    assert _set(client, cid, False).get_json()['client_ids'] == []


def test_gizlenmemisi_geri_acmak_hatasiz(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    assert _set(client, cid, False).status_code == 200


def test_izolasyon_baska_kullaniciyi_etkilemez(client):
    """Tercih KİŞİSEL — vg A gizlerse vg B ve yönetici etkilenmez."""
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    _set(client, cid, True)
    login_as(client, VG2)
    assert _get(client)['client_ids'] == []
    login_as(client, MANAGER)
    assert _get(client)['client_ids'] == []


def test_designer_kendi_tercihini_yazabilir(client):
    """Uç panel rollerinin hepsine açık; kapsam kontrolü ayrı iş."""
    cid = _client_id(client)
    login_as(client, DESIGNER)
    assert _set(client, cid, True).get_json()['client_ids'] == [cid]


# --- doğrulama -----------------------------------------------------------

def test_bilinmeyen_scope_400(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    assert _set(client, cid, True, scope='uydurma').status_code == 400
    assert client.get(f'{URL}?scope=uydurma').status_code == 400


def test_bilinmeyen_client_404(client):
    login_as(client, VIDEOGRAPHER)
    assert _set(client, 99999, True).status_code == 404


def test_client_id_sayi_degilse_404(client):
    login_as(client, VIDEOGRAPHER)
    assert _set(client, 'abc', True).status_code == 404


def test_impersonation_etkin_kimlige_yazar(client):
    """Yönetici tasarımcı gözünden bakarken tercih HEDEF kullanıcıya yazılır."""
    cid = _client_id(client)
    with client.session_transaction() as sess:
        sess['user'] = dict(DESIGNER)
        sess['impersonator'] = dict(MANAGER)
    _set(client, cid, True)
    login_as(client, DESIGNER)
    assert _get(client)['client_ids'] == [cid]
    login_as(client, MANAGER)
    assert _get(client)['client_ids'] == []
