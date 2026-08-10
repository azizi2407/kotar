"""/api/sharing/videographer/cards — bekleyen video kuyruğu + kişisel gizleme."""
import datetime as dt

from conftest import MANAGER, VIDEOGRAPHER, login_as
from extensions import db
from test_session_csrf import csrf_headers

WEEK = '2026-W30'
CARDS = f'/api/sharing/videographer/cards?week_iso={WEEK}'
VG2 = {"sub": "12", "email": "video2@test.com", "name": "Videograf 2", "role": "videographer"}


def _client_id(client, name='Video Müşteri'):
    login_as(client, MANAGER)
    return client.post('/api/clients', json={'name': name},
                       headers=csrf_headers(client)).get_json()['client']['id']


def _video(client_id, file_id, minutes_ago=0):
    """Yüklenmiş video kaydı (Drive'a gitmeden)."""
    from models_sharing import CardUpload
    row = CardUpload(client_id=client_id, week_iso=WEEK, category='video',
                     file_id=file_id, file_name=f'{file_id}.mp4',
                     uploaded_at=dt.datetime.now(dt.timezone.utc)
                     - dt.timedelta(minutes=minutes_ago))
    db.session.add(row)
    db.session.commit()
    return row


def _share(client_id, file_id, deleted=False):
    """Videoyu bir paylaşıma bağla → artık 'bekleyen' değil."""
    from models import utcnow
    from models_sharing import Share
    row = Share(client_id=client_id, week_iso=WEEK, kind='video', status='draft',
                file_id=file_id, file_name=f'{file_id}.mp4')
    if deleted:
        row.deleted_at = utcnow()
    db.session.add(row)
    db.session.commit()
    return row


def _row(client, client_id):
    body = client.get(CARDS).get_json()
    return next(r for r in body['rows'] if r['client']['id'] == client_id), body


def _hide(client, client_id, hidden=True):
    return client.put('/api/prefs/hidden-clients',
                      json={'scope': 'videographer_upload', 'client_id': client_id,
                            'hidden': hidden},
                      headers=csrf_headers(client))


# --- bekleyen kuyruk -----------------------------------------------------

def test_paylasilmis_video_bekleyenden_dusulur(client):
    cid = _client_id(client)
    _video(cid, 'f_bekleyen')
    _video(cid, 'f_paylasilan')
    _share(cid, 'f_paylasilan')
    login_as(client, VIDEOGRAPHER)
    row, _ = _row(client, cid)
    assert row['video_total_count'] == 2
    assert row['video_pending_count'] == 1
    assert [v['file_id'] for v in row['video_pending']] == ['f_bekleyen']


def test_soft_delete_edilmis_share_videoyu_yeniden_bekliyor_yapar(client):
    cid = _client_id(client)
    _video(cid, 'f1')
    _share(cid, 'f1', deleted=True)
    login_as(client, VIDEOGRAPHER)
    row, _ = _row(client, cid)
    assert row['video_pending_count'] == 1


def test_liste_en_fazla_bes_en_yeni_once(client):
    cid = _client_id(client)
    for i in range(7):
        _video(cid, f'f{i}', minutes_ago=i)      # f0 en yeni
    login_as(client, VIDEOGRAPHER)
    row, _ = _row(client, cid)
    assert row['video_pending_count'] == 7        # GERÇEK sayı, kırpılmaz
    assert len(row['video_pending']) == 5
    assert [v['file_id'] for v in row['video_pending']] == ['f0', 'f1', 'f2', 'f3', 'f4']


def test_video_uploads_semantigi_degismedi(client):
    """REGRESYON MUHAFIZI — aynı _build_rows yönetim ve designer board'unu da besliyor;
    `video_uploads` haftanın TÜM videolarını döndürmeye devam etmeli."""
    cid = _client_id(client)
    _video(cid, 'f_bekleyen')
    _video(cid, 'f_paylasilan')
    _share(cid, 'f_paylasilan')
    login_as(client, MANAGER)
    rows = client.get(f'/api/sharing/cards?week_iso={WEEK}').get_json()['rows']
    row = next(r for r in rows if r['client']['id'] == cid)
    assert len(row['video_uploads']) == 2         # ikisi de duruyor


def test_videosuz_musteride_sifirlar(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    row, _ = _row(client, cid)
    assert row['video_total_count'] == 0 and row['video_pending_count'] == 0
    assert row['video_pending'] == []


# --- kişisel gizleme -----------------------------------------------------

def test_gizli_musteri_satirda_yok(client):
    cid = _client_id(client, 'Gizlenen')
    other = _client_id(client, 'Kalan')
    login_as(client, VIDEOGRAPHER)
    _hide(client, cid)
    body = client.get(CARDS).get_json()
    ids = [r['client']['id'] for r in body['rows']]
    assert cid not in ids and other in ids
    assert body['hidden_client_ids'] == [cid] and body['hidden_count'] == 1


def test_gizleme_baska_kullaniciyi_etkilemez(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    _hide(client, cid)
    login_as(client, VG2)
    body = client.get(CARDS).get_json()
    assert cid in [r['client']['id'] for r in body['rows']]
    assert body['hidden_count'] == 0


def test_gizleme_yonetim_ve_designer_boardini_etkilemez(client):
    cid = _client_id(client)
    login_as(client, MANAGER)
    _hide(client, cid)                       # yönetici kendi vg sayfasında gizledi
    rows = client.get(f'/api/sharing/cards?week_iso={WEEK}').get_json()['rows']
    assert cid in [r['client']['id'] for r in rows]
    drows = client.get(f'/api/sharing/designer/cards?week_iso={WEEK}').get_json()['rows']
    assert cid in [r['client']['id'] for r in drows]


def test_geri_acilan_musteri_tekrar_gorunur(client):
    cid = _client_id(client)
    login_as(client, VIDEOGRAPHER)
    _hide(client, cid)
    _hide(client, cid, hidden=False)
    body = client.get(CARDS).get_json()
    assert cid in [r['client']['id'] for r in body['rows']]
    assert body['hidden_count'] == 0


# --- N+1 muhafızı --------------------------------------------------------

def test_sorgu_sayisi_musteri_sayisindan_bagimsiz(client):
    """Paylaşılmış tespiti tek toplu sorgu olmalı; döngüye sorgu girerse kırılır."""
    from sqlalchemy import event

    def count_queries(n):
        db.drop_all()
        db.create_all()
        for i in range(n):
            cid = _client_id(client, f'Firma {i}')
            _video(cid, f'c{i}_a')
            _video(cid, f'c{i}_b')
            _share(cid, f'c{i}_b')
        login_as(client, VIDEOGRAPHER)
        seen = []
        engine = db.session.get_bind()

        def _on_exec(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, 'before_cursor_execute', _on_exec)
        try:
            assert client.get(CARDS).status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', _on_exec)
        return len(seen)

    assert count_queries(2) == count_queries(6)


# --- `shared` bayrağı: videograf paylaşılmış videoyu da izleyebilsin -------
# 2026-07-30: sayfa artık haftanın TÜM videolarını listeliyor (paylaşılmışlar
# rozetli) ve panelde oynatıyor. Eskiden paylaşılan video listeden düşüyordu →
# videograf kendi yüklediği videoyu izleyemiyordu.

def test_shared_bayragi_paylasilmis_videoyu_isaretler(client):
    cid = _client_id(client)
    _video(cid, 'f_paylasilan')
    _video(cid, 'f_bekleyen')
    _share(cid, 'f_paylasilan')
    row, _ = _row(client, cid)
    by = {v['file_id']: v for v in row['video_uploads']}
    assert by['f_paylasilan']['shared'] is True
    assert by['f_bekleyen']['shared'] is False


def test_shared_soft_delete_edilmis_share_i_saymaz(client):
    """Kart silinince video yeniden 'bekliyor' olmalı — rozet de kalkar."""
    cid = _client_id(client)
    _video(cid, 'f_geri')
    _share(cid, 'f_geri', deleted=True)
    row, _ = _row(client, cid)
    assert row['video_uploads'][0]['shared'] is False


def test_shared_video_pending_ile_tutarli(client):
    """`video_pending` ve `shared` aynı kaynaktan türer — çelişmemeleri gerekir."""
    cid = _client_id(client)
    for i in range(3):
        _video(cid, f'v{i}', minutes_ago=i)
    _share(cid, 'v1')
    row, _ = _row(client, cid)
    paylasilmis = {v['file_id'] for v in row['video_uploads'] if v['shared']}
    bekleyen = {v['file_id'] for v in row['video_pending']}
    assert paylasilmis == {'v1'}
    assert paylasilmis.isdisjoint(bekleyen)
    assert len(row['video_uploads']) == 3           # hepsi listede


def test_shared_videografa_da_doner(client):
    """Bayrak videografın kendi görünümünde de olmalı (sayfanın asıl tüketicisi)."""
    cid = _client_id(client)
    _video(cid, 'f_vg')
    _share(cid, 'f_vg')
    login_as(client, VIDEOGRAPHER)
    row, _ = _row(client, cid)
    assert row['video_uploads'][0]['shared'] is True


def test_shared_dosyasiz_yuklemede_false(client):
    """file_id yoksa paylaşılmış olamaz — `in` kontrolü None'da patlamamalı."""
    cid = _client_id(client)
    _video(cid, None)
    row, _ = _row(client, cid)
    assert row['video_uploads'][0]['shared'] is False
