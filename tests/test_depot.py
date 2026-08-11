"""/api/depot — Videographer Depot: authorization, quota, type/name restrictions, shared ownership, deletion."""
import io
import os

import pytest

from conftest import (CONTENT_CREATOR, DESIGNER, MANAGER, PENDING, VIDEOGRAPHER, login_as)
from extensions import db
from test_session_csrf import csrf_headers

BASE = '/api/depot'
VG2 = {"sub": "12", "email": "video2@test.com", "name": "Videograf 2", "role": "videographer"}


@pytest.fixture(autouse=True)
def content_root(monkeypatch):
    monkeypatch.setenv('DRIVE_CONTENT_ROOT_ID', 'CONTENT_ROOT')


@pytest.fixture
def fake_drive(monkeypatch):
    """Mock the Drive layer without going over the network; record the calls."""
    calls = {'upload': [], 'folder': [], 'grant': [], 'trash': []}

    def fake_upload(folder_id, filename, data, mime):
        # Is it going as a stream? (if bytes are passed, the contract is broken)
        calls['upload'].append({'folder_id': folder_id, 'filename': filename, 'mime': mime,
                                'streamed': hasattr(data, 'read')})
        payload = data.read() if hasattr(data, 'read') else data
        return {'id': f'fid{len(calls["upload"])}', 'name': filename, 'mimeType': mime,
                'size': str(len(payload))}

    def fake_folder(parent_id, name):
        calls['folder'].append({'parent': parent_id, 'name': name})
        return 'DEPOT_FOLDER'

    import drive_gateway
    monkeypatch.setattr(drive_gateway, 'upload_file', fake_upload)
    monkeypatch.setattr(drive_gateway, 'ensure_subfolder', fake_folder)
    monkeypatch.setattr(drive_gateway, 'grant_anyone_reader',
                        lambda fid: calls['grant'].append(fid))
    monkeypatch.setattr(drive_gateway, 'trash_file',
                        lambda fid: calls['trash'].append(fid))
    return calls


def _upload(client, name='rapor.pdf', body=b'veri', note=None):
    data = {'file': (io.BytesIO(body), name)}
    if note:
        data['note'] = note
    return client.post(f'{BASE}/upload', data=data, content_type='multipart/form-data',
                       headers=csrf_headers(client))


def _files(client):
    return client.get(f'{BASE}/files').get_json()


def _seed(size, name='eski.mp4'):
    """Insert a row directly for quota tests (without going to Drive)."""
    from models_sharing import DepotFile
    row = DepotFile(file_id=f'seed{name}', file_name=name, file_size=size,
                    mime_type='video/mp4', uploaded_by='1')
    db.session.add(row)
    db.session.commit()
    return row


# --- authorization / CSRF ---------------------------------------------------

def test_anonim_401(client):
    assert client.get(f'{BASE}/files').status_code == 401
    assert client.get(f'{BASE}/quota').status_code == 401


def test_designer_403(client, fake_drive):
    login_as(client, DESIGNER)
    assert client.get(f'{BASE}/files').status_code == 403
    assert _upload(client).status_code == 403


def test_content_creator_403(client):
    login_as(client, CONTENT_CREATOR)
    assert client.get(f'{BASE}/files').status_code == 403


def test_pending_403(client):
    login_as(client, PENDING)
    assert client.get(f'{BASE}/files').status_code == 403


def test_videographer_ve_management_erisir(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    assert client.get(f'{BASE}/files').status_code == 200
    login_as(client, MANAGER)
    assert client.get(f'{BASE}/files').status_code == 200


def test_csrf_yoksa_403(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    r = client.post(f'{BASE}/upload', data={'file': (io.BytesIO(b'x'), 'a.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 403
    row = _seed(10)
    assert client.delete(f'{BASE}/files/{row.id}').status_code == 403


# --- upload -------------------------------------------------------------------

def test_dosyasiz_400(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    r = client.post(f'{BASE}/upload', data={}, content_type='multipart/form-data',
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_drive_koku_yoksa_400_ve_driveya_gidilmez(client, fake_drive, monkeypatch):
    monkeypatch.delenv('DRIVE_CONTENT_ROOT_ID', raising=False)
    login_as(client, VIDEOGRAPHER)
    r = _upload(client)
    assert r.status_code == 400 and 'DRIVE_CONTENT_ROOT_ID' in r.get_json()['error']
    assert fake_drive['upload'] == []


def test_yukleme_basarili(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    r = _upload(client, 'brief.pdf', b'0123456789', note='ham çekim notu')
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body['file']['file_name'] == 'brief.pdf'
    assert body['file']['file_size'] == 10
    assert body['file']['note'] == 'ham çekim notu'
    assert body['file']['uploaded_by'] == VIDEOGRAPHER['sub']
    assert body['quota']['used'] == 10
    # folder was opened under the content root with the correct name
    assert fake_drive['folder'] == [{'parent': 'CONTENT_ROOT', 'name': 'Videograf Deposu'}]
    # permission granted so the link opens for everyone
    assert fake_drive['grant'] == ['fid1']


def test_upload_akistan_gider(client, fake_drive):
    """Contract guard: the file must not be loaded into RAM and passed as bytes."""
    login_as(client, VIDEOGRAPHER)
    _upload(client)
    assert fake_drive['upload'][0]['streamed'] is True


def test_media_store_kullanilmaz(client, fake_drive):
    """Depot file is NOT written to the 21-day local copy (Drive is canonical)."""
    login_as(client, VIDEOGRAPHER)
    _upload(client)
    originals = os.path.join(os.environ['MEDIA_STORE_DIR'], 'originals')
    assert not os.path.isdir(originals) or os.listdir(originals) == []


def test_dosya_limiti_asilirsa_413_ve_driveya_gidilmez(client, fake_drive, monkeypatch):
    import depot
    monkeypatch.setattr(depot, 'MAX_FILE_BYTES', 5)
    login_as(client, VIDEOGRAPHER)
    r = _upload(client, 'buyuk.mp4', b'0123456789')
    assert r.status_code == 413 and '500 MB' in r.get_json()['error']
    assert fake_drive['upload'] == []


def test_kota_asilirsa_409_ve_driveya_gidilmez(client, fake_drive, monkeypatch):
    import depot
    monkeypatch.setattr(depot, 'QUOTA_BYTES', 100)
    _seed(95)
    login_as(client, VIDEOGRAPHER)
    r = _upload(client, 'yeni.mp4', b'0123456789')
    assert r.status_code == 409
    body = r.get_json()
    assert 'Storage is full' in body['error'] and 'quota' in body
    assert fake_drive['upload'] == []


def test_drive_hatasi_502_ve_satir_yazilmaz(client, fake_drive, monkeypatch):
    from models_sharing import DepotFile
    import drive_gateway

    def boom(*a, **kw):
        raise drive_gateway.DriveError('kota bitti')

    monkeypatch.setattr(drive_gateway, 'upload_file', boom)
    login_as(client, VIDEOGRAPHER)
    assert _upload(client).status_code == 502
    assert DepotFile.query.count() == 0


def test_dosya_limiti_sharing_ile_ayni(client):
    """Drift guard — the two modules' product limit must not diverge."""
    import depot
    import sharing
    assert depot.MAX_FILE_BYTES == sharing.MAX_UPLOAD_BYTES


def test_depot_files_tablosu_create_all_ile_gelir(client):
    assert 'depot_files' in set(db.metadata.tables)


# --- type / name ---------------------------------------------------------------

def test_yasak_uzanti_400_ve_driveya_gidilmez(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    r = _upload(client, 'virus.exe')
    assert r.status_code == 400 and 'executable' in r.get_json()['error']
    assert fake_drive['upload'] == []


def test_son_uzantiya_bakilir(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    assert _upload(client, 'rapor.pdf.exe').status_code == 400


def test_serbest_turler_kabul(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    for name in ('proje.psd', 'arsiv.zip', 'cekim.mp4', 'sozlesme.pdf', 'liste.xlsx'):
        assert _upload(client, name).status_code == 201, name


def test_yol_ayirici_ve_kontrol_karakteri_temizlenir(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    _upload(client, '../../etc/passwd.pdf')
    name = fake_drive['upload'][0]['filename']
    assert '/' not in name and '\\' not in name


def test_turkce_ad_aynen_korunur(client, fake_drive):
    """This test breaks if secure_filename gets added — the repo preserves Turkish names."""
    login_as(client, VIDEOGRAPHER)
    _upload(client, 'çekim özetİ ğüş.pdf')
    assert fake_drive['upload'][0]['filename'] == 'çekim özetİ ğüş.pdf'


def test_kesme_isaretli_ad_kabul(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    r = _upload(client, "proje sahibi'in dosyası.pdf")
    assert r.status_code == 201
    assert fake_drive['upload'][0]['filename'] == "proje sahibi'in dosyası.pdf"


# --- shared ownership / list ----------------------------------------------------

def test_depo_ortak_baska_videograf_gorur(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    _upload(client, 'benim.pdf')
    login_as(client, VG2)
    assert [f['file_name'] for f in _files(client)['files']] == ['benim.pdf']


def test_herkes_her_dosyayi_silebilir(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client, 'benim.pdf').get_json()['file']['id']
    login_as(client, VG2)                     # someone else's file
    assert client.delete(f'{BASE}/files/{row_id}',
                         headers=csrf_headers(client)).status_code == 200


def test_management_de_silebilir(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client).get_json()['file']['id']
    login_as(client, MANAGER)
    assert client.delete(f'{BASE}/files/{row_id}',
                         headers=csrf_headers(client)).status_code == 200


def test_ad_aramasi(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    _upload(client, 'kamera-ayar.pdf')
    _upload(client, 'ses-notu.txt')
    files = client.get(f'{BASE}/files?q=kamera').get_json()['files']
    assert [f['file_name'] for f in files] == ['kamera-ayar.pdf']


def test_uploader_name_cozulur(client, fake_drive):
    from models import UserRef
    db.session.add(UserRef(sub=VIDEOGRAPHER['sub'], email=VIDEOGRAPHER['email'],
                           name=VIDEOGRAPHER['name'], role='videographer'))
    db.session.commit()
    login_as(client, VIDEOGRAPHER)
    _upload(client)
    assert _files(client)['files'][0]['uploader_name'] == 'Videografçı'


def test_liste_sorgu_sayisi_dosya_sayisindan_bagimsiz(client, fake_drive):
    """N+1 guard — breaks if uploader_name gets resolved via lazy access."""
    from sqlalchemy import event

    def count_queries(n):
        db.drop_all()
        db.create_all()
        login_as(client, VIDEOGRAPHER)
        for i in range(n):
            _upload(client, f'd{i}.pdf')
        seen = []
        engine = db.session.get_bind()

        def _on_exec(conn, cursor, statement, params, context, executemany):
            seen.append(statement)

        event.listen(engine, 'before_cursor_execute', _on_exec)
        try:
            assert client.get(f'{BASE}/files').status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', _on_exec)
        return len(seen)

    assert count_queries(1) == count_queries(5)


# --- quota / deletion ---------------------------------------------------------

def test_silinen_dosya_kotadan_dusulur(client, fake_drive, monkeypatch):
    import depot
    monkeypatch.setattr(depot, 'QUOTA_BYTES', 20)
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client, 'a.pdf', b'0123456789').get_json()['file']['id']
    assert _upload(client, 'b.pdf', b'0123456789').status_code == 201
    assert _upload(client, 'c.pdf', b'0123456789').status_code == 409     # full
    client.delete(f'{BASE}/files/{row_id}', headers=csrf_headers(client))
    assert _upload(client, 'c.pdf', b'0123456789').status_code == 201     # room freed up


def test_kota_yalniz_silinmemisleri_toplar(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client, 'a.pdf', b'0123456789').get_json()['file']['id']
    assert client.get(f'{BASE}/quota').get_json()['quota']['used'] == 10
    client.delete(f'{BASE}/files/{row_id}', headers=csrf_headers(client))
    assert client.get(f'{BASE}/quota').get_json()['quota']['used'] == 0


def test_silme_drive_copune_tasir(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client).get_json()['file']['id']
    r = client.delete(f'{BASE}/files/{row_id}', headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()['drive_ok'] is True
    assert fake_drive['trash'] == ['fid1']


def test_drive_silme_hatasi_yine_soft_delete_eder(client, fake_drive, monkeypatch):
    from models_sharing import DepotFile
    import drive_gateway
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client).get_json()['file']['id']

    def boom(fid):
        raise drive_gateway.DriveError('erişim yok')

    monkeypatch.setattr(drive_gateway, 'trash_file', boom)
    r = client.delete(f'{BASE}/files/{row_id}', headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()['drive_ok'] is False
    assert db.session.get(DepotFile, row_id).deleted_at is not None


def test_silinmisi_tekrar_silme_404(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client).get_json()['file']['id']
    client.delete(f'{BASE}/files/{row_id}', headers=csrf_headers(client))
    assert client.delete(f'{BASE}/files/{row_id}',
                         headers=csrf_headers(client)).status_code == 404


def test_silinen_listede_gorunmez(client, fake_drive):
    login_as(client, VIDEOGRAPHER)
    row_id = _upload(client).get_json()['file']['id']
    client.delete(f'{BASE}/files/{row_id}', headers=csrf_headers(client))
    assert _files(client)['files'] == []
