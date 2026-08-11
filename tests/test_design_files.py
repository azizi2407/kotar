"""Design work files (2026-08-07) — /api/design-files.

Focus: versioning (v1→v2), per-client quota, authorization matrix, server-canonical
storage (Drive best-effort), and X-Accel download.
"""
import io
import os
import stat

import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers


def test_modeller_kayitli():
    """Does create_all see the tables (it won't if app.py isn't imported)."""
    from extensions import db
    from models_design_files import DesignFile, DesignFileVersion
    assert DesignFile.__tablename__ == 'design_files'
    assert DesignFileVersion.__tablename__ == 'design_file_versions'
    assert 'design_files' in db.metadata.tables
    assert 'design_file_versions' in db.metadata.tables


def test_max_file_bytes_uygulama_tavanini_asmiyor(app):
    """If `design_files.MAX_FILE_BYTES` EXCEEDS the app's `MAX_CONTENT_LENGTH`, the body
    never reaches the view and gets caught by Flask's global 413 first — the 1 GB check
    in `_dosya_kontrol` silently becomes dead code (a real bug: while the cap was 512 MB,
    this module's 1 GB check never ran at all). The two must not drift apart from each other."""
    import design_files
    assert design_files.MAX_FILE_BYTES <= app.config['MAX_CONTENT_LENGTH']


def test_dosya_to_dict_bos_surum():
    """A file with no version must still serialize (in case the upload was left incomplete)."""
    from models_design_files import DesignFile
    f = DesignFile(client_id=1, title='Ana Şablon', tags=['şablon'])
    d = f.to_dict()
    assert d['title'] == 'Ana Şablon'
    assert d['tags'] == ['şablon']
    assert d['current'] is None
    assert d['version_count'] == 0
    assert d['can_delete'] is False


def test_surum_to_dict():
    from models_design_files import DesignFileVersion
    v = DesignFileVersion(file_id=1, version_no=2, sha256='a' * 64,
                          file_name='Ana Şablon.psd', mime_type='image/vnd.adobe.photoshop',
                          file_size=1234, note='logo güncellendi')
    d = v.to_dict(uploader_name='Deniz Yıldız')
    assert d['version_no'] == 2
    assert d['file_name'] == 'Ana Şablon.psd'
    assert d['file_size'] == 1234
    assert d['note'] == 'logo güncellendi'
    assert d['uploader_name'] == 'Deniz Yıldız'
    assert d['drive_ok'] is False        # drive_file_id is None


# --- shared helpers ------------------------------------------------------

@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Files should be written to a test-specific temp dir, not into the repo (test_fonts pattern)."""
    import design_files
    monkeypatch.setattr(design_files, 'STORE_DIR', str(tmp_path / 'design-files'))
    return tmp_path / 'design-files'


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    r = client.post('/api/clients', json={'name': 'Tasarım Müşterisi'},
                    headers=csrf_headers(client))
    return r.get_json()['client']['id']


# --- authorization matrix ----------------------------------------------------------

def test_liste_oturumsuz_401(client, cid):
    client.get('/auth/logout')
    r = client.get(f'/api/design-files/client/{cid}')
    assert r.status_code == 401


@pytest.mark.parametrize('who', [CONTENT_CREATOR, VIDEOGRAPHER])
def test_liste_yetkisiz_403(client, cid, who):
    login_as(client, who)
    r = client.get(f'/api/design-files/client/{cid}')
    assert r.status_code == 403


@pytest.mark.parametrize('who', [MANAGER, DESIGNER])
def test_liste_yetkili_bos(client, cid, who):
    login_as(client, who)
    r = client.get(f'/api/design-files/client/{cid}')
    assert r.status_code == 200
    d = r.get_json()
    assert d['files'] == []
    # Quota comes in the SAME response — so the panel doesn't need a second round trip (depot pattern).
    assert d['quota']['limit'] == 2 * 1024 * 1024 * 1024
    assert d['quota']['used'] == 0
    assert d['quota']['remaining'] == d['quota']['limit']


def test_liste_bilinmeyen_musteri_404(client):
    login_as(client, DESIGNER)
    r = client.get('/api/design-files/client/999999')
    assert r.status_code == 404


# --- upload ------------------------------------------------------------

PSD = b'8BPS' + b'\x00' * 60          # a real PSD isn't needed; the endpoint doesn't check the signature


def _yukle(client, cid, data=PSD, name='Ana Şablon.psd', **form):
    payload = {'file': (io.BytesIO(data), name)}
    payload.update({k: str(v) for k, v in form.items()})
    return client.post(f'/api/design-files/client/{cid}', data=payload,
                       content_type='multipart/form-data',
                       headers=csrf_headers(client))


@pytest.fixture(autouse=True)
def drive_stub(monkeypatch):
    """No network access to Drive. Default: successful copy."""
    import design_files
    calls = []

    def sahte(client, version, path):
        calls.append((version.file_name, path))
        version.drive_file_id = 'drv-' + version.sha256[:8]
        return True

    monkeypatch.setattr(design_files, '_drive_kopyala', sahte)
    return calls


def test_yukleme_v1_olusturur(client, cid, store):
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Ana Şablon', tags='["şablon","kurumsal"]',
               note='ilk sürüm')
    assert r.status_code == 201, r.get_json()
    f = r.get_json()['file']
    assert f['title'] == 'Ana Şablon'
    assert f['tags'] == ['şablon', 'kurumsal']
    assert f['version_count'] == 1
    assert f['current']['version_no'] == 1
    assert f['current']['file_name'] == 'Ana Şablon.psd'
    assert f['current']['note'] == 'ilk sürüm'
    assert f['current']['file_size'] == len(PSD)
    assert f['current']['drive_ok'] is True


def test_yukleme_diske_yazar(client, cid, store):
    """The file is CANONICAL on the server: named by sha256, in a two-letter prefix directory."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Ana Şablon')
    sha = r.get_json()['file']['current']['sha256']
    assert sha == hashlib_sha(PSD)
    yol = store / sha[:2] / f'{sha}.psd'
    assert yol.exists()
    assert yol.read_bytes() == PSD


def test_yukleme_dosya_modu_0644(client, cid, store):
    """mkstemp+os.replace leaves the file at 0600; nginx (www-data) reads it from disk via
    X-Accel-Redirect, so _diske_yaz must chmod the final path to 0644."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Ana Şablon')
    sha = r.get_json()['file']['current']['sha256']
    yol = store / sha[:2] / f'{sha}.psd'
    mod = stat.S_IMODE(os.stat(yol).st_mode)
    assert mod == 0o644


def test_yukleme_mime_uzun_kirpilir(client, cid, store):
    """The `mime_type` column is String(120); if the client's Content-Type exceeds it,
    the Postgres commit blows up with a DataError and the file already written to disk
    is orphaned — that's why `_dosya_kontrol` truncates it to [:120]."""
    login_as(client, DESIGNER)
    uzun_mime = 'application/x-' + 'a' * 200
    payload = {'file': (io.BytesIO(PSD), 'Ana Şablon.psd', uzun_mime), 'title': 'Uzun Mime'}
    r = client.post(f'/api/design-files/client/{cid}', data=payload,
                    content_type='multipart/form-data', headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    assert len(r.get_json()['file']['current']['mime_type']) == 120


def test_dedup_dosya_modu_onarilir(client, cid, store):
    """If the target already exists (dedup) but has dropped to 0600 by hand/from a backup,
    re-uploading the same content must repair it to 0644 — otherwise nginx (www-data)
    can never read that file, and a new upload would never notice."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Ana Şablon').get_json()['file']
    sha = f['current']['sha256']
    yol = store / sha[:2] / f'{sha}.psd'
    os.chmod(yol, 0o600)
    assert stat.S_IMODE(os.stat(yol).st_mode) == 0o600
    r = _yukle(client, cid, title='Aynı İçerik Kopyası')     # falls into the dedup branch
    assert r.status_code == 201, r.get_json()
    assert stat.S_IMODE(os.stat(yol).st_mode) == 0o644


def hashlib_sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_yukleme_baslik_zorunlu(client, cid):
    login_as(client, DESIGNER)
    r = _yukle(client, cid)          # title yok
    assert r.status_code == 400
    assert 'title' in r.get_json()['error'].lower()


def test_yukleme_dosya_zorunlu(client, cid):
    login_as(client, DESIGNER)
    r = client.post(f'/api/design-files/client/{cid}',
                    data={'title': 'Boş'}, content_type='multipart/form-data',
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_yukleme_yasak_uzanti_400(client, cid):
    """BLOCKED_EXT is SHARED with the depot — the lists must not drift apart (drift guard)."""
    import depot
    import design_files
    assert design_files.BLOCKED_EXT is depot.BLOCKED_EXT
    login_as(client, DESIGNER)
    r = _yukle(client, cid, name='virus.exe', title='Kötü')
    assert r.status_code == 400


def test_yukleme_boyut_asimi_413(client, cid, monkeypatch):
    import design_files
    monkeypatch.setattr(design_files, 'MAX_FILE_BYTES', 10)
    login_as(client, DESIGNER)
    r = _yukle(client, cid, data=b'x' * 50, title='Büyük')
    assert r.status_code == 413


def test_yukleme_kota_asimi_409(client, cid, monkeypatch):
    """A quota overrun is 409 — NOT 413: 413 means 'this request's body is too big', quota
    is a STATE conflict, and the panel should be able to distinguish the two cases."""
    import design_files
    monkeypatch.setattr(design_files, 'QUOTA_BYTES', 100)
    login_as(client, DESIGNER)
    assert _yukle(client, cid, data=b'x' * 80, title='İlk').status_code == 201
    r = _yukle(client, cid, data=b'y' * 80, name='iki.psd', title='İkinci')
    assert r.status_code == 409
    assert 'quota' in r.get_json()


@pytest.mark.parametrize('who', [CONTENT_CREATOR, VIDEOGRAPHER])
def test_yukleme_yetkisiz_403(client, cid, who):
    login_as(client, who)
    r = _yukle(client, cid, title='Olmaz')
    assert r.status_code == 403


def test_yukleme_drive_hatasi_yine_201(client, cid, monkeypatch, store):
    """Drive is BEST-EFFORT: even if it blows up, the record is created, the file is on disk, drive_ok is False."""
    import design_files

    def patla(client_, version, path):
        return False

    monkeypatch.setattr(design_files, '_drive_kopyala', patla)
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Drive Yok')
    assert r.status_code == 201
    f = r.get_json()['file']
    assert f['current']['drive_ok'] is False
    sha = f['current']['sha256']
    assert (store / sha[:2] / f'{sha}.psd').exists()


def test_etiketler_temizlenir(client, cid):
    """Empty, duplicate, and overly long tags are filtered out; the user's original casing is preserved."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Etiketli',
               tags='["  Şablon  ", "şablon", "", "Kampanya"]')
    assert r.status_code == 201
    assert r.get_json()['file']['tags'] == ['Şablon', 'Kampanya']


def test_etiketler_turkce_i_tekillesir(client, cid):
    """'İstanbul' and 'istanbul' must count as the same tag — plain `casefold()` gets this
    pair wrong (`'İ'.casefold()` produces the combining-dot 'i̇'), so the repo uses a
    TR-aware `_fold()` instead (drift guard)."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='TR Etiket',
               tags='["İstanbul", "istanbul"]')
    assert r.status_code == 201
    assert r.get_json()['file']['tags'] == ['İstanbul']


# --- versioning --------------------------------------------------------------

def _surum_yukle(client, file_id, data=b'8BPS' + b'\x01' * 60,
                 name='Ana Şablon.psd', note=None):
    payload = {'file': (io.BytesIO(data), name)}
    if note:
        payload['note'] = note
    return client.post(f'/api/design-files/{file_id}/versions', data=payload,
                       content_type='multipart/form-data',
                       headers=csrf_headers(client))


@pytest.fixture
def fid(client, cid):
    login_as(client, DESIGNER)
    return _yukle(client, cid, title='Ana Şablon').get_json()['file']['id']


def test_yeni_surum_v2_olur(client, fid):
    login_as(client, DESIGNER)
    r = _surum_yukle(client, fid, note='logo güncellendi')
    assert r.status_code == 201, r.get_json()
    f = r.get_json()['file']
    assert f['current']['version_no'] == 2
    assert f['current']['note'] == 'logo güncellendi'
    assert f['version_count'] == 2


def test_surum_gecmisi_yeniden_eskiye(client, fid):
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    r = client.get(f'/api/design-files/{fid}/versions')
    assert r.status_code == 200
    v = r.get_json()['versions']
    assert [x['version_no'] for x in v] == [2, 1]


def test_baska_tasarimci_surum_yukleyebilir(client, cid, fid):
    """Requirement: ALL designers must be able to share — no assignment is required."""
    login_as(client, {'sub': '9', 'email': 'diger@test.com',
                      'name': 'Diğer Tasarımcı', 'role': 'designer'})
    assert _surum_yukle(client, fid).status_code == 201


def test_surum_yarisi_409(client, fid):
    """If two people upload a version at the same time, the second one gets 409 — silently
    producing v3 and making one person's work invisible would be WRONG.

    Note (deviation from the plan): the method in the brief suggested monkeypatching
    `db.session.commit` to raise IntegrityError. `db.session` is a `scoped_session`
    proxy — in practice it CAN be patched with `setattr` (it writes to the instance
    `__dict__`, `__getattr__` doesn't kick in), so it technically works. But this
    approach does NOT test a real UNIQUE violation, it only verifies the "if commit
    blows up, return 409" behavior — the test's actual claim ("a second insert of the
    same version_no is a conflict") can be verified more strongly by inserting the row
    BY HAND ahead of time and triggering a real `IntegrityError`. So here we write v2 of
    `DesignFileVersion` directly to the DB and try to upload from the endpoint with the
    same number: the endpoint sees this row while computing `MAX+1` and — NOT while
    trying to produce v3 — to behave as if someone else added v2 at that exact moment,
    we insert the row and commit it ourselves during `_diske_yaz` (while the endpoint is
    inside, right before its own commit), and then the endpoint hits a real UNIQUE
    violation on its own commit."""
    import design_files
    from extensions import db
    from models_design_files import DesignFileVersion

    orijinal = design_files._diske_yaz
    kancalandi = {'n': 0}

    def kancali_diske_yaz(stream, file_name):
        # AFTER the endpoint computes its own MAX+1 (2), BEFORE it commits its own
        # INSERT — to imitate the exact moment of the race — we insert v2 as if
        # someone else did, and commit it. When the endpoint then tries to insert
        # its own v2 row, UNIQUE(file_id, version_no) really does blow up.
        if kancalandi['n'] == 0:
            kancalandi['n'] += 1
            rakip = DesignFileVersion(
                file_id=fid, version_no=2, sha256='b' * 64, file_name='rakip.psd',
                mime_type='application/octet-stream', file_size=1,
                uploaded_by='99')
            db.session.add(rakip)
            db.session.commit()
        return orijinal(stream, file_name)

    design_files._diske_yaz = kancali_diske_yaz
    try:
        login_as(client, DESIGNER)
        r = _surum_yukle(client, fid)
    finally:
        design_files._diske_yaz = orijinal
    assert r.status_code == 409
    assert 'refresh' in r.get_json()['error'].lower()


def test_surum_bilinmeyen_dosya_404(client):
    login_as(client, DESIGNER)
    assert _surum_yukle(client, 999999).status_code == 404


def test_surum_gecmisi_musteri_silinmisse_404(client, cid, fid):
    """When a client is archived (`DELETE /api/clients/<id>`), the file row remains but
    its history must no longer be visible — same rule as `client_files`/`file_version_create`
    (a consistency gap flagged in audit, closed here)."""
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    assert client.get(f'/api/design-files/{fid}/versions').status_code == 404


def test_surum_kota_musteriye_ait(client, cid, fid, monkeypatch):
    """Quota is PER CLIENT: a second file for the same client eats into the same pool."""
    import design_files
    monkeypatch.setattr(design_files, 'QUOTA_BYTES', 200)
    login_as(client, DESIGNER)
    r = _surum_yukle(client, fid, data=b'z' * 300)
    assert r.status_code == 409


# --- download ----------------------------------------------------------------

def test_indirme_fallback_govde_dondurur(client, cid, store):
    """Without nginx (test/local), the file is served directly.

    `with` is required: `send_file` leaves the file open, and the test client won't
    close it without `with` — the SAME pattern as fonts.py's download tests
    (ResourceWarning, `error` in `pytest.ini`)."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Ana Şablon').get_json()['file']
    vid = f['current']['id']
    with client.get(f'/api/design-files/versions/{vid}/download') as r:
        assert r.status_code == 200
        assert r.data == PSD


def test_indirme_xaccel_basligi(client, cid, monkeypatch):
    """In nginx mode there's NO body, there's an X-Accel-Redirect — so a 1 GB download
    doesn't tie up a gunicorn thread for minutes."""
    import design_files
    monkeypatch.setattr(design_files, 'XACCEL', True)
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Ana Şablon').get_json()['file']
    sha = f['current']['sha256']
    r = client.get(f"/api/design-files/versions/{f['current']['id']}/download")
    assert r.status_code == 200
    assert r.headers['X-Accel-Redirect'] == f'/_dsg/{sha[:2]}/{sha}.psd'
    assert r.data == b''


def test_indirme_turkce_ad_utf8(client, cid):
    """The name on disk is a hash; the user must download the ORIGINAL name (with Turkish characters)."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, name='Şablon Çalışma.psd', title='Şablon').get_json()['file']
    with client.get(f"/api/design-files/versions/{f['current']['id']}/download") as r:
        cd = r.headers['Content-Disposition']
        assert "UTF-8''" in cd
        assert '%C5%9Eablon' in cd      # 'Ş' percent-encoded


@pytest.mark.parametrize('who', [CONTENT_CREATOR, VIDEOGRAPHER])
def test_indirme_yetkisiz_403(client, cid, who):
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Gizli').get_json()['file']
    vid = f['current']['id']
    login_as(client, who)
    r = client.get(f'/api/design-files/versions/{vid}/download')
    assert r.status_code == 403


def test_indirme_silinmis_surum_404(client, cid, store):
    """A deleted version cannot be downloaded even if the file is still on disk."""
    from extensions import db as _db
    from models import utcnow as _now
    from models_design_files import DesignFileVersion
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Silinen').get_json()['file']
    vid = f['current']['id']
    v = _db.session.get(DesignFileVersion, vid)
    v.deleted_at = _now()
    _db.session.commit()
    assert client.get(f'/api/design-files/versions/{vid}/download').status_code == 404


def test_indirme_disk_dosyasi_yoksa_410(client, cid, store):
    """DB row exists, disk file doesn't (deleted by hand) — a clear 410, not 500."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Kayıp').get_json()['file']
    sha = f['current']['sha256']
    os.unlink(store / sha[:2] / f'{sha}.psd')
    r = client.get(f"/api/design-files/versions/{f['current']['id']}/download")
    assert r.status_code == 410


def test_indirme_musteri_silinmisse_404(client, cid, store):
    """When a client is archived (`DELETE /api/clients/<id>`), the version must not be
    downloadable even though it's still on disk — same rule as `file_versions`/`file_version_create`
    (a consistency gap flagged in audit, closed here)."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Arşivlenecek').get_json()['file']
    vid = f['current']['id']
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    assert client.get(f'/api/design-files/versions/{vid}/download').status_code == 404


# --- editing ----------------------------------------------------------

def test_patch_baslik_ve_etiket(client, fid):
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}',
                     json={'title': 'Yeni Ad', 'tags': ['kampanya']},
                     headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['file']['title'] == 'Yeni Ad'
    assert r.get_json()['file']['tags'] == ['kampanya']


def test_patch_bos_baslik_400(client, fid):
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'title': '   '},
                     headers=csrf_headers(client))
    assert r.status_code == 400


def test_patch_baslik_sayi_400(client, fid):
    """`{"title": 5}` — a non-string title used to hit AttributeError in `.strip()`
    (hence a 500); it must return a clear 400."""
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'title': 5},
                     headers=csrf_headers(client))
    assert r.status_code == 400


def test_patch_etiketler_dizi_degil_400(client, fid):
    """`{"tags": "şablon"}` — a non-array `tags` used to silently turn into [] in
    `_etiketler` and wipe out ALL of the file's tags (200); it must return a clear 400."""
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'tags': 'şablon'},
                     headers=csrf_headers(client))
    assert r.status_code == 400


def test_patch_etiket_gonderilmezse_korunur(client, fid):
    """If the `tags` key is ABSENT from the body, existing tags stay untouched —
    the behavior was already correct, this locks it in."""
    login_as(client, DESIGNER)
    r1 = client.patch(f'/api/design-files/{fid}', json={'tags': ['kampanya']},
                      headers=csrf_headers(client))
    assert r1.status_code == 200
    r2 = client.patch(f'/api/design-files/{fid}', json={'title': 'Yeni Ad'},
                      headers=csrf_headers(client))
    assert r2.status_code == 200
    assert r2.get_json()['file']['tags'] == ['kampanya']


def test_patch_musteri_silinmisse_404(client, cid, fid):
    """Consistency: the version/download endpoints return 404 for an archived client;
    editing must apply the same rule — an archived client's file must not be editable
    (a pattern flagged in audit, applied to PATCH here too)."""
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'title': 'Olmaz'},
                     headers=csrf_headers(client))
    assert r.status_code == 404


# --- delete ----------------------------------------------------------------

def test_silme_yukleyen_yapabilir(client, cid, fid):
    login_as(client, DESIGNER)
    r = client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert r.status_code == 200
    # The list empties and the quota is freed immediately.
    d = client.get(f'/api/design-files/client/{cid}').get_json()
    assert d['files'] == []
    assert d['quota']['used'] == 0


def test_silme_baska_tasarimci_403(client, fid):
    login_as(client, {'sub': '9', 'email': 'diger@test.com',
                      'name': 'Diğer', 'role': 'designer'})
    r = client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert r.status_code == 403


def test_silme_yonetim_ayrimsiz(client, fid):
    login_as(client, MANAGER)
    assert client.delete(f'/api/design-files/{fid}',
                         headers=csrf_headers(client)).status_code == 200


def test_can_delete_bayragi_kuralla_ayni(client, cid, fid):
    """The panel's button must match the backend rule EXACTLY — if they drift apart, it lies."""
    login_as(client, {'sub': '9', 'email': 'diger@test.com',
                      'name': 'Diğer', 'role': 'designer'})
    d = client.get(f'/api/design-files/client/{cid}').get_json()
    assert d['files'][0]['can_delete'] is False
    login_as(client, MANAGER)
    d = client.get(f'/api/design-files/client/{cid}').get_json()
    assert d['files'][0]['can_delete'] is True


def test_dosya_silme_musteri_silinmisse_404(client, cid, fid):
    """The same consistency rule applies to `DELETE /<file_id>` too — an archived
    client's file must not be deletable (a pattern flagged in audit)."""
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    assert client.delete(f'/api/design-files/{fid}',
                         headers=csrf_headers(client)).status_code == 404


def test_surum_silme_son_surum_engellenir(client, fid):
    """When only one version remains, it can't be deleted — if you want to delete it, delete the file."""
    login_as(client, DESIGNER)
    v = client.get(f'/api/design-files/{fid}/versions').get_json()['versions'][0]
    r = client.delete(f"/api/design-files/versions/{v['id']}",
                      headers=csrf_headers(client))
    assert r.status_code == 400
    assert 'last version' in r.get_json()['error'].lower()


def test_surum_silme_eskiyi_kaldirir(client, fid):
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    r = client.delete(f"/api/design-files/versions/{v1['id']}",
                      headers=csrf_headers(client))
    assert r.status_code == 200
    kalan = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    assert [x['version_no'] for x in kalan] == [2]


def test_silme_drive_hatasi_yine_200(client, fid, monkeypatch):
    """Drive is BEST-EFFORT: even if trashing it blows up, the DB delete isn't blocked,
    only drive_ok comes back False (same pattern as `test_yukleme_drive_hatasi_yine_201`
    on the upload side, applied here for delete)."""
    import design_files

    def patla(version):
        return False

    monkeypatch.setattr(design_files, '_drive_cope', patla)
    login_as(client, DESIGNER)
    r = client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['drive_ok'] is False


def test_surum_silme_drive_hatasi_yine_200(client, fid, monkeypatch):
    """Same pattern for deleting a single version: even if Drive trashing blows up,
    the DB delete isn't blocked."""
    import design_files

    def patla(version):
        return False

    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    monkeypatch.setattr(design_files, '_drive_cope', patla)
    r = client.delete(f"/api/design-files/versions/{v1['id']}",
                      headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['drive_ok'] is False


def test_surum_silme_musteri_silinmisse_404(client, cid, fid):
    """The same consistency rule applies to `DELETE /versions/<id>` too."""
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    r = client.delete(f"/api/design-files/versions/{v1['id']}",
                      headers=csrf_headers(client))
    assert r.status_code == 404


def test_silinen_dosya_listede_yok(client, cid, fid):
    """Soft-delete filter: a deleted row shows up in no list."""
    login_as(client, MANAGER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert client.get(f'/api/design-files/client/{cid}').get_json()['files'] == []
    assert client.get(f'/api/design-files/{fid}/versions').status_code == 404


def test_silmede_deleted_by_yazilir(client, fid):
    """`file_delete` now records who deleted it (previously there wasn't even a column)."""
    from extensions import db as _db
    from models_design_files import DesignFile
    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    f = _db.session.get(DesignFile, fid)
    assert f.deleted_by == DESIGNER['sub']


def test_surum_silmede_deleted_by_yazilir(client, fid):
    from extensions import db as _db
    from models_design_files import DesignFileVersion
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))
    v = _db.session.get(DesignFileVersion, v1['id'])
    assert v.deleted_by == DESIGNER['sub']


# --- trash: listing ----------------------------------------------

def test_trash_silinmis_dosya_gorunur_canli_gorunmez(client, cid, fid):
    from extensions import db as _db
    from models import UserRef
    _db.session.add(UserRef(sub=MANAGER['sub'], email=MANAGER['email'],
                            name=MANAGER['name'], role=MANAGER['role']))
    _db.session.commit()
    login_as(client, MANAGER)
    r2 = _yukle(client, cid, title='Canlı Kalan')
    assert r2.status_code == 201
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert [f['id'] for f in d['files']] == [fid]
    assert d['files'][0]['deleted_by'] == MANAGER['sub']
    assert d['files'][0]['deleter_name'] == MANAGER['name']
    assert d['files'][0]['can_restore'] is True
    assert d['files'][0]['can_purge'] is True
    # A live file does not show up in the trash.
    d2 = client.get(f'/api/design-files/client/{cid}').get_json()
    assert len(d2['files']) == 1
    assert d2['files'][0]['title'] == 'Canlı Kalan'


@pytest.mark.parametrize('who', [CONTENT_CREATOR, VIDEOGRAPHER])
def test_trash_yetkisiz_403(client, cid, who):
    login_as(client, who)
    r = client.get(f'/api/design-files/client/{cid}/trash')
    assert r.status_code == 403


def test_trash_designer_can_purge_false(client, cid, fid):
    """The designer can see the trash, but the permanent-delete button must not lie to them."""
    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['files'][0]['can_purge'] is False
    assert d['files'][0]['can_restore'] is True


def test_trash_tekil_silinen_surum_versions_dizisinde(client, fid):
    """The file stays live, but if a single version is deleted, that version shows up
    in the `versions` array — NOT in the `files` array (the file itself wasn't deleted)."""
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    versions = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    v1 = [x for x in versions if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))
    f = _db_get_file(fid)
    d = client.get(f'/api/design-files/client/{f.client_id}/trash').get_json()
    assert d['files'] == []
    assert [v['id'] for v in d['versions']] == [v1['id']]
    assert d['versions'][0]['deleted_by'] == DESIGNER['sub']


def _db_get_file(file_id):
    from extensions import db as _db
    from models_design_files import DesignFile
    return _db.session.get(DesignFile, file_id)


# --- trash: restore -----------------------------------------------

def test_restore_dosya_geri_gelir_kota_geri_yuklenir(client, cid, fid):
    login_as(client, DESIGNER)
    before = client.get(f'/api/design-files/client/{cid}').get_json()['quota']['used']
    assert before > 0
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert client.get(f'/api/design-files/client/{cid}').get_json()['quota']['used'] == 0
    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    d = client.get(f'/api/design-files/client/{cid}').get_json()
    assert [f['id'] for f in d['files']] == [fid]
    assert d['quota']['used'] == before
    # The trash is now empty.
    assert client.get(f'/api/design-files/client/{cid}/trash').get_json()['files'] == []


def test_restore_kapsam_tek_tek_silinen_surum_geri_gelmez(client, cid, fid):
    """Critical rule: delete v2 individually, then delete the file, then restore the
    file — v2 MUST STAY DELETED, v1 must come back. If the `deleted_at` timestamp doesn't
    match (v2's timestamp was stamped BEFORE the file's), it's out of scope for the restore."""
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    versions = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    v2 = [x for x in versions if x['version_no'] == 2][0]
    r = client.delete(f"/api/design-files/versions/{v2['id']}", headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()

    r = client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()

    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()

    kalan = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    assert [x['version_no'] for x in kalan] == [1]        # v2 is still in the trash
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['files'] == []
    assert [x['version_no'] for x in d['versions']] == [2]


def test_restore_silinmemis_satira_400(client, fid):
    login_as(client, MANAGER)
    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 400


def test_restore_bilinmeyen_dosya_404(client):
    login_as(client, MANAGER)
    assert client.post('/api/design-files/999999/restore',
                       headers=csrf_headers(client)).status_code == 404


def test_restore_baska_tasarimci_403(client, fid):
    login_as(client, MANAGER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    login_as(client, {'sub': '9', 'email': 'diger@test.com',
                      'name': 'Diğer', 'role': 'designer'})
    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 403


def test_surum_restore_dosya_coptekiyse_400(client, fid):
    """Restoring a single version while the file itself is in the trash is rejected —
    the file must be restored first, otherwise it would create an inconsistent
    intermediate state: a 'live' version attached to a file that doesn't show up in
    the live list."""
    login_as(client, DESIGNER)
    v1 = client.get(f'/api/design-files/{fid}/versions').get_json()['versions'][0]
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.post(f"/api/design-files/versions/{v1['id']}/restore",
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_surum_restore_calisir(client, fid):
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    versions = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    v1 = [x for x in versions if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))
    r = client.post(f"/api/design-files/versions/{v1['id']}/restore",
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    kalan = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    assert sorted(x['version_no'] for x in kalan) == [1, 2]


# --- trash: purge request -------------------------------------

def test_purge_request_isaretler_hicbir_sey_silmez(client, cid, fid):
    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.post(f'/api/design-files/{fid}/purge-request',
                    json={'requested': True}, headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['requested'] is True
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['files'][0]['purge_requested_by'] == DESIGNER['sub']
    assert d['files'][0]['purge_requested_at'] is not None
    # Still sitting in the DB — nothing was deleted.
    assert _db_get_file(fid) is not None

    r = client.post(f'/api/design-files/{fid}/purge-request',
                    json={'requested': False}, headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['requested'] is False
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['files'][0]['purge_requested_by'] is None
    assert d['files'][0]['purge_requested_at'] is None


def test_purge_request_canli_dosyada_400(client, fid):
    login_as(client, DESIGNER)
    r = client.post(f'/api/design-files/{fid}/purge-request',
                    json={'requested': True}, headers=csrf_headers(client))
    assert r.status_code == 400


# --- trash: permanent delete ---------------------------------------------

def test_purge_designer_403_management_200(client, fid):
    login_as(client, MANAGER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))

    login_as(client, DESIGNER)
    r = client.delete(f'/api/design-files/{fid}/purge', json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 403

    login_as(client, MANAGER)
    r = client.delete(f'/api/design-files/{fid}/purge', json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()


def test_purge_db_satirlari_gercekten_gider(client, fid):
    from extensions import db as _db
    from models_design_files import DesignFile, DesignFileVersion
    login_as(client, MANAGER)
    v1 = client.get(f'/api/design-files/{fid}/versions').get_json()['versions'][0]
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.delete(f'/api/design-files/{fid}/purge', json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert _db.session.get(DesignFile, fid) is None
    assert _db.session.get(DesignFileVersion, v1['id']) is None


def test_purge_confirm_olmadan_400(client, fid):
    login_as(client, MANAGER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.delete(f'/api/design-files/{fid}/purge', headers=csrf_headers(client))
    assert r.status_code == 400
    r2 = client.delete(f'/api/design-files/{fid}/purge', json={'confirm': False},
                       headers=csrf_headers(client))
    assert r2.status_code == 400


def test_purge_canli_dosyada_400(client, fid):
    login_as(client, MANAGER)
    r = client.delete(f'/api/design-files/{fid}/purge', json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 400


def test_purge_disk_dedup_korumasi(client, cid, store):
    """If the same content (same sha256) was uploaded to two different files, purging
    one must NOT delete the disk file — the other still references it. Once both are
    purged, the disk file must go."""
    login_as(client, DESIGNER)
    fa = _yukle(client, cid, title='Dosya A').get_json()['file']
    fb = _yukle(client, cid, title='Dosya B').get_json()['file']    # same PSD → dedup
    sha = fa['current']['sha256']
    assert sha == fb['current']['sha256']
    yol = store / sha[:2] / f'{sha}.psd'
    assert yol.exists()

    login_as(client, MANAGER)
    client.delete(f"/api/design-files/{fa['id']}", headers=csrf_headers(client))
    r = client.delete(f"/api/design-files/{fa['id']}/purge", json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['disk_ok'] is True
    assert yol.exists()          # File B still references it

    client.delete(f"/api/design-files/{fb['id']}", headers=csrf_headers(client))
    r = client.delete(f"/api/design-files/{fb['id']}/purge", json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['disk_ok'] is True
    assert not yol.exists()      # nobody references it anymore


def test_surum_purge_designer_403_management_200(client, fid):
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))

    r = client.delete(f"/api/design-files/versions/{v1['id']}/purge",
                      json={'confirm': True}, headers=csrf_headers(client))
    assert r.status_code == 403

    login_as(client, MANAGER)
    r = client.delete(f"/api/design-files/versions/{v1['id']}/purge",
                      json={'confirm': True}, headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    from extensions import db as _db
    from models_design_files import DesignFileVersion
    assert _db.session.get(DesignFileVersion, v1['id']) is None


def test_surum_purge_dosya_coptekiyse_400(client, fid):
    """Audit finding: unlike `version_restore`, `version_purge` didn't check whether the
    file ITSELF (`f.deleted_at`) was in the trash — when management purged the ONLY
    version of a file that was in the trash, the `design_files` row could end up with
    ZERO versions (a shell file; if later restored, `current: null` shows up in the live
    list). An intermediate state that contradicts `version_delete`'s "the last version
    can't be deleted" rule — must be blocked with 400 here, the version must survive
    intact in the DB (not deleted, just rejected)."""
    login_as(client, MANAGER)
    v1 = client.get(f'/api/design-files/{fid}/versions').get_json()['versions'][0]
    # Trash the ENTIRE file (all its versions get deleted with the same timestamp too).
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.delete(f"/api/design-files/versions/{v1['id']}/purge",
                      json={'confirm': True}, headers=csrf_headers(client))
    assert r.status_code == 400
    assert 'entire file' in r.get_json()['error']
    from extensions import db as _db
    from models_design_files import DesignFileVersion
    assert _db.session.get(DesignFileVersion, v1['id']) is not None   # rejected, not deleted


def test_purge_farkli_uzantili_ayni_icerik_hicbiri_kalmaz(client, cid, store):
    """Audit finding: if the same bytes are uploaded as BOTH `logo.psd` AND `logo.ai`,
    `_yol` includes the extension, so TWO separate physical files are created on disk
    (`<sha>.psd`, `<sha>.ai`) — since the dedup check in `_diske_yaz` targets on a
    (sha+extension) basis, both get written. The old `_purge_disk_dedup` only deleted the
    single path belonging to the LAST processed version's `file_name`, leaving the other
    permanently orphaned. The new version cleans up ALL paths (`glob`) for that sha —
    once both are purged, nothing should remain on disk."""
    login_as(client, DESIGNER)
    fa = _yukle(client, cid, name='logo.psd', title='Logo PSD').get_json()['file']
    fb = _yukle(client, cid, name='logo.ai', title='Logo AI').get_json()['file']
    sha = fa['current']['sha256']
    assert sha == fb['current']['sha256']          # same content → same sha
    yol_psd = store / sha[:2] / f'{sha}.psd'
    yol_ai = store / sha[:2] / f'{sha}.ai'
    assert yol_psd.exists() and yol_ai.exists()    # precondition for the leak scenario

    login_as(client, MANAGER)
    son_yanit = None
    for fid_ in (fa['id'], fb['id']):
        client.delete(f'/api/design-files/{fid_}', headers=csrf_headers(client))
        son_yanit = client.delete(f'/api/design-files/{fid_}/purge', json={'confirm': True},
                                  headers=csrf_headers(client))
        assert son_yanit.status_code == 200, son_yanit.get_json()

    assert son_yanit.get_json()['disk_ok'] is True
    assert not yol_psd.exists()
    assert not yol_ai.exists()


def test_sha_kilidi_yalniz_postgreste_cagrilir(monkeypatch):
    """Verifies the dialect branching of the `_sha_kilidi` TOCTOU lock.

    PROVES: (1) with the test dialect sqlite, `db.session.execute` is NEVER called
    (the lock is silently skipped); (2) when the dialect name is faked as 'postgresql',
    EXACTLY ONE `execute` call is made carrying the text `pg_advisory_xact_lock(hashtext(:k))`
    and the key `design-files:sha:<sha>`.

    DOES NOT PROVE: that `pg_advisory_xact_lock` REALLY serializes two concurrent
    connections on a real Postgres — the repo's tests run on sqlite (`tests/conftest.py`),
    sqlite has no advisory locks, and a real race (two concurrent transactions) CANNOT
    be set up here. That the TOCTOU race between `_diske_yaz` and `_purge_disk_dedup` is
    actually closed on production Postgres can only be verified in an environment close
    to production, with real concurrent connections; this test only proves "is the right
    SQL called under the right condition"."""
    import design_files
    from extensions import db

    calls = []
    monkeypatch.setattr(db.session, 'execute', lambda *a, **kw: calls.append(a))

    # sqlite (the real test dialect): the lock must be skipped, execute must not be called.
    design_files._sha_kilidi('a' * 64)
    assert calls == []

    # fake the 'postgresql' dialect: execute must be called.
    class _SahteDialect:
        name = 'postgresql'

    class _SahteBind:
        dialect = _SahteDialect()

    monkeypatch.setattr(db.session, 'get_bind', lambda: _SahteBind())
    design_files._sha_kilidi('a' * 64)
    assert len(calls) == 1
    assert 'pg_advisory_xact_lock' in str(calls[0][0])
    assert calls[0][1] == {'k': 'design-files:sha:' + 'a' * 64}


def test_diske_yaz_kilidi_sha_store_yaza_gercekten_baglar(monkeypatch):
    """DRIFT GUARD: verifies that `design_files._diske_yaz` REALLY calls `sha_store.yaz`
    with `on_hashed=design_files._sha_kilidi`.

    NO OTHER test proves this connection. If the `on_hashed=_sha_kilidi` line inside
    `_diske_yaz` were removed (e.g. the kwarg forgotten in a refactor), the TOCTOU lock
    would silently become inactive — the race between upload and `_purge_disk_dedup`
    (see the block comment above) would reopen, and the ENTIRE test suite, including
    `test_sha_kilidi_yalniz_postgreste_cagrilir`, would stay GREEN, because that test
    calls `_sha_kilidi` DIRECTLY, not whether `_diske_yaz` actually uses it. This test
    closes that gap by monkeypatching `sha_store.yaz` and comparing the identity (`is`)
    of the `on_hashed` kwarg passed to it against `design_files._sha_kilidi`."""
    import design_files
    import sha_store

    yakalanan = {}

    def sahte_yaz(stream, store_dir, file_name, on_hashed=None):
        yakalanan['on_hashed'] = on_hashed
        return 'b' * 64, 3

    monkeypatch.setattr(sha_store, 'yaz', sahte_yaz)
    sha, boyut = design_files._diske_yaz(io.BytesIO(b'abc'), 'dosya.png')
    assert (sha, boyut) == ('b' * 64, 3)
    assert yakalanan['on_hashed'] is design_files._sha_kilidi


# --- trash: restore should also pull the Drive copy out of the trash ---------------
#
# Soft-delete was throwing the Drive copy into the trash via `_drive_cope`, but the
# restore endpoint wasn't pulling it back OUT — a version reporting `drive_ok:true`
# could actually stay in the Drive trash and get permanently deleted after 30 days.
# This block verifies that `_drive_geri_al` gets called on both restore endpoints.

def test_restore_dosya_drive_geri_al_cagrilir(client, cid, fid, monkeypatch):
    """When a file is restored, Drive trash-removal must be called for EVERY version
    that comes back with the file."""
    import design_files
    calls = []

    def sahte(version):
        calls.append(version.id)
        return True

    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))

    monkeypatch.setattr(design_files, '_drive_geri_al', sahte)
    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['drive_ok'] is True

    versions = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    assert sorted(calls) == sorted(v['id'] for v in versions)


def test_restore_dosya_drive_hatasi_yine_200(client, fid, monkeypatch):
    """Even if Drive restoration blows up, the endpoint returns 200 and the DB row comes
    back — only `drive_ok` becomes False (BEST-EFFORT, same pattern as `_drive_cope`)."""
    import design_files

    def patla(version):
        return False

    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))

    monkeypatch.setattr(design_files, '_drive_geri_al', patla)
    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['drive_ok'] is False

    from extensions import db as _db
    from models_design_files import DesignFile
    f = _db.session.get(DesignFile, fid)
    assert f.deleted_at is None


def test_surum_restore_drive_geri_al_cagrilir(client, fid, monkeypatch):
    """Restoring a single version must also trigger the Drive trash-removal."""
    import design_files
    calls = []

    def sahte(version):
        calls.append(version.id)
        return True

    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))

    monkeypatch.setattr(design_files, '_drive_geri_al', sahte)
    r = client.post(f"/api/design-files/versions/{v1['id']}/restore",
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['drive_ok'] is True
    assert calls == [v1['id']]


def test_surum_restore_drive_hatasi_yine_200(client, fid, monkeypatch):
    import design_files

    def patla(version):
        return False

    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))

    monkeypatch.setattr(design_files, '_drive_geri_al', patla)
    r = client.post(f"/api/design-files/versions/{v1['id']}/restore",
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['drive_ok'] is False

    kalan = client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
    assert sorted(x['version_no'] for x in kalan) == [1, 2]


def test_restore_drive_file_id_null_surumde_cagri_yapilmaz(client, cid, fid, monkeypatch):
    """For a version with `drive_file_id` NULL (never copied to Drive at all), no
    pointless Drive call should be made during restore — `_drive_geri_al` checks this
    itself internally, verified here via the real `dg.untrash_file`."""
    import design_files
    import drive_gateway as dg
    calls = []
    monkeypatch.setattr(dg, 'untrash_file', lambda file_id: calls.append(file_id))

    from extensions import db as _db
    from models_design_files import DesignFileVersion
    v = DesignFileVersion.query.filter_by(file_id=fid).first()
    v.drive_file_id = None
    _db.session.commit()

    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.post(f'/api/design-files/{fid}/restore', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['drive_ok'] is True
    assert calls == []


# --- trash: row summary and total size --------------------------------
#
# `client_trash` used to call `_guncel_surumler` for deleted FILES without
# `silinmisler_dahil` — since that filter looks for `deleted_at IS NULL` (and a
# deleted file's versions are ALL deleted too), it always returned empty, and the
# panel saw `current: null, version_count: 0` on every row.

def test_trash_current_dolu_ve_version_count_1(client, cid, fid):
    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    f = d['files'][0]
    assert f['current'] is not None
    assert f['current']['file_name'] == 'Ana Şablon.psd'
    assert f['current']['file_size'] == len(PSD)
    assert f['current']['version_no'] == 1
    assert f['version_count'] == 1


def test_trash_iki_surumlu_dosya_version_count_2(client, fid):
    """When a two-version file is deleted, the trash shows `version_count == 2` and
    `current.version_no == 2` — the highest version is found INCLUDING deleted ones."""
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    f = _db_get_file(fid)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    d = client.get(f'/api/design-files/client/{f.client_id}/trash').get_json()
    row = d['files'][0]
    assert row['version_count'] == 2
    assert row['current']['version_no'] == 2


def test_trash_bytes_dogru_canli_dosya_girmiyor(client, cid):
    """Delete files of known sizes, verify the `trash_bytes` total.
    The size of a live (non-deleted) file must NOT be included in that total."""
    login_as(client, DESIGNER)
    fid1 = _yukle(client, cid, data=b'a' * 1000, title='Silinecek Küçük').get_json()['file']['id']
    fid2 = _yukle(client, cid, data=b'b' * 2000, title='Silinecek Büyük').get_json()['file']['id']
    _yukle(client, cid, data=b'c' * 3000, title='Canlı Kalan')

    client.delete(f'/api/design-files/{fid1}', headers=csrf_headers(client))
    client.delete(f'/api/design-files/{fid2}', headers=csrf_headers(client))

    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['trash_bytes'] == 1000 + 2000


def test_trash_bytes_tekil_silinen_surum_dahil(client, fid):
    """Even if the file itself isn't in the trash, an individually deleted version
    counts toward `trash_bytes` (the same rows as the `versions` array)."""
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, data=b'x' * 500, note='ikinci')
    v1 = [x for x in client.get(f'/api/design-files/{fid}/versions').get_json()['versions']
          if x['version_no'] == 1][0]
    client.delete(f"/api/design-files/versions/{v1['id']}", headers=csrf_headers(client))
    f = _db_get_file(fid)
    d = client.get(f'/api/design-files/client/{f.client_id}/trash').get_json()
    assert d['trash_bytes'] == len(PSD)


def test_trash_bytes_bos_cop_sifir(client, cid):
    login_as(client, DESIGNER)
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['trash_bytes'] == 0
