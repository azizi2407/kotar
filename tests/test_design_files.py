"""Tasarım çalışma dosyaları (2026-08-07) — /api/design-files.

Vurgu: sürümleme (v1→v2), müşteri başına kota, yetki matrisi, sunucu-kanonik
depolama (Drive en-iyi-çaba) ve X-Accel indirme.
"""
import io
import os
import stat

import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers


def test_modeller_kayitli():
    """create_all tabloları görüyor mu (app.py import edilmemişse görmez)."""
    from extensions import db
    from models_design_files import DesignFile, DesignFileVersion
    assert DesignFile.__tablename__ == 'design_files'
    assert DesignFileVersion.__tablename__ == 'design_file_versions'
    assert 'design_files' in db.metadata.tables
    assert 'design_file_versions' in db.metadata.tables


def test_max_file_bytes_uygulama_tavanini_asmiyor(app):
    """`design_files.MAX_FILE_BYTES` app'in `MAX_CONTENT_LENGTH`'ini AŞARSA gövde
    view'a hiç ulaşmadan Flask'ın global 413'üne takılır — `_dosya_kontrol`'deki
    1 GB kontrolü sessizce ölü kod olur (yaşanmış hata: tavan 512 MB iken bu
    modülün 1 GB kontrolü hiç çalışmıyordu). İkisi burada birbirinden ayrışmasın."""
    import design_files
    assert design_files.MAX_FILE_BYTES <= app.config['MAX_CONTENT_LENGTH']


def test_dosya_to_dict_bos_surum():
    """Sürümü olmayan dosya da serileşebilmeli (yükleme yarıda kaldıysa)."""
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
    assert d['drive_ok'] is False        # drive_file_id None


# --- ortak yardımcılar ------------------------------------------------------

@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Dosyalar repo içine değil teste özel geçici dizine yazılsın (test_fonts deseni)."""
    import design_files
    monkeypatch.setattr(design_files, 'STORE_DIR', str(tmp_path / 'design-files'))
    return tmp_path / 'design-files'


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    r = client.post('/api/clients', json={'name': 'Tasarım Müşterisi'},
                    headers=csrf_headers(client))
    return r.get_json()['client']['id']


# --- yetki matrisi ----------------------------------------------------------

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
    # Kota AYNI yanıtta gelir — panel ikinci tur atmasın (depo deseni).
    assert d['quota']['limit'] == 2 * 1024 * 1024 * 1024
    assert d['quota']['used'] == 0
    assert d['quota']['remaining'] == d['quota']['limit']


def test_liste_bilinmeyen_musteri_404(client):
    login_as(client, DESIGNER)
    r = client.get('/api/design-files/client/999999')
    assert r.status_code == 404


# --- yükleme ------------------------------------------------------------

PSD = b'8BPS' + b'\x00' * 60          # gerçek PSD gerekmiyor; uç imzaya bakmıyor


def _yukle(client, cid, data=PSD, name='Ana Şablon.psd', **form):
    payload = {'file': (io.BytesIO(data), name)}
    payload.update({k: str(v) for k, v in form.items()})
    return client.post(f'/api/design-files/client/{cid}', data=payload,
                       content_type='multipart/form-data',
                       headers=csrf_headers(client))


@pytest.fixture(autouse=True)
def drive_stub(monkeypatch):
    """Drive'a ağ çıkışı yok. Varsayılan: başarılı kopya."""
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
    """Dosya sunucuda KANONİK: sha256 adıyla, iki harfli ön ek dizininde."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Ana Şablon')
    sha = r.get_json()['file']['current']['sha256']
    assert sha == hashlib_sha(PSD)
    yol = store / sha[:2] / f'{sha}.psd'
    assert yol.exists()
    assert yol.read_bytes() == PSD


def test_yukleme_dosya_modu_0644(client, cid, store):
    """mkstemp+os.replace dosyayı 0600 bırakır; nginx (www-data) X-Accel-Redirect
    ile diskten okuyor, o yüzden _diske_yaz kalıcı yolu 0644'e çevirmeli."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Ana Şablon')
    sha = r.get_json()['file']['current']['sha256']
    yol = store / sha[:2] / f'{sha}.psd'
    mod = stat.S_IMODE(os.stat(yol).st_mode)
    assert mod == 0o644


def test_yukleme_mime_uzun_kirpilir(client, cid, store):
    """`mime_type` kolonu String(120); istemciden gelen Content-Type bunu aşarsa
    Postgres commit'i DataError ile patlar ve diske yazılmış dosya öksüz kalır —
    `_dosya_kontrol` bu yüzden [:120] kırpar."""
    login_as(client, DESIGNER)
    uzun_mime = 'application/x-' + 'a' * 200
    payload = {'file': (io.BytesIO(PSD), 'Ana Şablon.psd', uzun_mime), 'title': 'Uzun Mime'}
    r = client.post(f'/api/design-files/client/{cid}', data=payload,
                    content_type='multipart/form-data', headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    assert len(r.get_json()['file']['current']['mime_type']) == 120


def test_dedup_dosya_modu_onarilir(client, cid, store):
    """Hedef zaten var (dedup) ama elle/yedekten 0600'e düşmüşse, aynı içerik
    tekrar yüklenince 0644'e onarılmalı — aksi halde nginx (www-data) o dosyayı
    kalıcı olarak okuyamaz ve yeni yükleme bunu hiç fark etmez."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Ana Şablon').get_json()['file']
    sha = f['current']['sha256']
    yol = store / sha[:2] / f'{sha}.psd'
    os.chmod(yol, 0o600)
    assert stat.S_IMODE(os.stat(yol).st_mode) == 0o600
    r = _yukle(client, cid, title='Aynı İçerik Kopyası')     # dedup dalına düşer
    assert r.status_code == 201, r.get_json()
    assert stat.S_IMODE(os.stat(yol).st_mode) == 0o644


def hashlib_sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_yukleme_baslik_zorunlu(client, cid):
    login_as(client, DESIGNER)
    r = _yukle(client, cid)          # title yok
    assert r.status_code == 400
    assert 'başlık' in r.get_json()['error'].lower()


def test_yukleme_dosya_zorunlu(client, cid):
    login_as(client, DESIGNER)
    r = client.post(f'/api/design-files/client/{cid}',
                    data={'title': 'Boş'}, content_type='multipart/form-data',
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_yukleme_yasak_uzanti_400(client, cid):
    """BLOCKED_EXT depo ile ORTAK — liste ayrışmasın (drift muhafızı)."""
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
    """Kota aşımı 409 — 413 DEĞİL: 413 'bu isteğin gövdesi büyük' demek, kota bir
    DURUM çakışması ve panel iki vakayı ayırt etmeli."""
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
    """Drive EN-İYİ-ÇABA: patlasa da kayıt oluşur, dosya diskte, drive_ok False."""
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
    """Boş, tekrarlı ve fazla uzun etiketler süzülür; kullanıcının yazdığı hâl korunur."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='Etiketli',
               tags='["  Şablon  ", "şablon", "", "Kampanya"]')
    assert r.status_code == 201
    assert r.get_json()['file']['tags'] == ['Şablon', 'Kampanya']


def test_etiketler_turkce_i_tekillesir(client, cid):
    """'İstanbul' ve 'istanbul' aynı etiket sayılmalı — çıplak `casefold()` bu
    çiftte yanılır (`'İ'.casefold()` birleşik noktalı 'i̇' üretir), repo bu yüzden
    TR-duyarlı `_fold()` kullanıyor (drift muhafızı)."""
    login_as(client, DESIGNER)
    r = _yukle(client, cid, title='TR Etiket',
               tags='["İstanbul", "istanbul"]')
    assert r.status_code == 201
    assert r.get_json()['file']['tags'] == ['İstanbul']


# --- sürümleme --------------------------------------------------------------

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
    """Gereksinim: TÜM tasarımcılar paylaşabilmeli — atama aranmaz."""
    login_as(client, {'sub': '9', 'email': 'diger@test.com',
                      'name': 'Diğer Tasarımcı', 'role': 'designer'})
    assert _surum_yukle(client, fid).status_code == 201


def test_surum_yarisi_409(client, fid):
    """İki kişi aynı anda sürüm yüklerse ikincisi 409 alır — sessizce v3 üretip
    birinin işini görünmez kılmak YANLIŞ olurdu.

    Not (plandan sapma): brief'teki yöntem `db.session.commit`'i monkeypatch'leyip
    IntegrityError fırlatmayı öneriyordu. `db.session` bir `scoped_session`
    proxy'si — pratikte `setattr` ile yamanabiliyor (instance `__dict__`'e
    yazıyor, `__getattr__` devreye girmiyor), yani teknik olarak çalışıyor. Ama bu
    yaklaşım gerçek UNIQUE ihlalini test ETMİYOR, sadece "commit patlarsa 409
    dön" davranışını doğruluyor — testin asıl iddiası ("aynı version_no ikinci
    kez eklenirse çakışma") daha güçlü biçimde, satırı ELDEN önceden ekleyip
    gerçek `IntegrityError`'ı tetikleyerek doğrulanabilir. O yüzden burada
    `DesignFileVersion` v2'yi doğrudan DB'ye yazıp uçtan aynı numarayla
    yüklemeyi deniyoruz: uç `MAX+1` hesaplarken bu satırı görüyor ve v3
    üretmeye çalışırken DEĞİL — v2'yi tam o an başka biri eklemiş gibi davranmak
    için `_diske_yaz` sırasında (uç içeride, commit'ten hemen önce) satırı
    ekleyip commit'i biz yapıyoruz, sonra uç kendi commit'inde gerçek UNIQUE
    ihlaliyle karşılaşıyor."""
    import design_files
    from extensions import db
    from models_design_files import DesignFileVersion

    orijinal = design_files._diske_yaz
    kancalandi = {'n': 0}

    def kancali_diske_yaz(stream, file_name):
        # Uç kendi MAX+1'ini (2) hesapladıktan SONRA, kendi INSERT'ini
        # commit'lemeden ÖNCE — tam yarış anını taklit etmek için — v2'yi
        # başka biri gibi ekleyip commit'liyoruz. Uç sonra kendi v2 satırını
        # eklemeye çalışınca UNIQUE(file_id, version_no) gerçekten patlar.
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
    assert 'tazele' in r.get_json()['error'].lower()


def test_surum_bilinmeyen_dosya_404(client):
    login_as(client, DESIGNER)
    assert _surum_yukle(client, 999999).status_code == 404


def test_surum_gecmisi_musteri_silinmisse_404(client, cid, fid):
    """Müşteri arşivlenince (`DELETE /api/clients/<id>`) dosya satırı kalır ama
    geçmişi artık görünmemeli — `client_files`/`file_version_create` ile aynı
    kural (denetimde işaretlenen tutarlılık açığı, burada kapatıldı)."""
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    assert client.get(f'/api/design-files/{fid}/versions').status_code == 404


def test_surum_kota_musteriye_ait(client, cid, fid, monkeypatch):
    """Kota MÜŞTERİ başına: aynı müşterinin ikinci dosyası da aynı havuzu yer."""
    import design_files
    monkeypatch.setattr(design_files, 'QUOTA_BYTES', 200)
    login_as(client, DESIGNER)
    r = _surum_yukle(client, fid, data=b'z' * 300)
    assert r.status_code == 409


# --- indirme ----------------------------------------------------------------

def test_indirme_fallback_govde_dondurur(client, cid, store):
    """nginx yokken (test/yerel) dosya doğrudan servis edilir.

    `with` şart: `send_file` dosyayı açık bırakır, test client `with` olmadan
    kapatmaz — fonts.py'nin indirme testleriyle AYNI desen (ResourceWarning,
    `pytest.ini`'de `error`)."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Ana Şablon').get_json()['file']
    vid = f['current']['id']
    with client.get(f'/api/design-files/versions/{vid}/download') as r:
        assert r.status_code == 200
        assert r.data == PSD


def test_indirme_xaccel_basligi(client, cid, monkeypatch):
    """nginx modunda gövde YOK, X-Accel-Redirect var — 1 GB'lık indirme bir
    gunicorn thread'ini dakikalarca tutmasın."""
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
    """Diskteki ad hash; kullanıcı ÖZGÜN adı indirmeli (Türkçe karakterli)."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, name='Şablon Çalışma.psd', title='Şablon').get_json()['file']
    with client.get(f"/api/design-files/versions/{f['current']['id']}/download") as r:
        cd = r.headers['Content-Disposition']
        assert "UTF-8''" in cd
        assert '%C5%9Eablon' in cd      # 'Ş' yüzde-kodlu


@pytest.mark.parametrize('who', [CONTENT_CREATOR, VIDEOGRAPHER])
def test_indirme_yetkisiz_403(client, cid, who):
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Gizli').get_json()['file']
    vid = f['current']['id']
    login_as(client, who)
    r = client.get(f'/api/design-files/versions/{vid}/download')
    assert r.status_code == 403


def test_indirme_silinmis_surum_404(client, cid, store):
    """Diskte dosya dursa bile silinmiş sürüm indirilemez."""
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
    """DB satırı var, disk dosyası yok (elle silinmiş) — 500 değil, anlaşılır 410."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Kayıp').get_json()['file']
    sha = f['current']['sha256']
    os.unlink(store / sha[:2] / f'{sha}.psd')
    r = client.get(f"/api/design-files/versions/{f['current']['id']}/download")
    assert r.status_code == 410


def test_indirme_musteri_silinmisse_404(client, cid, store):
    """Müşteri arşivlenince (`DELETE /api/clients/<id>`) sürüm satırı diskte
    kalsa da indirilememeli — `file_versions`/`file_version_create` ile aynı
    kural (denetimde işaretlenen tutarlılık açığı, burada kapatıldı)."""
    login_as(client, DESIGNER)
    f = _yukle(client, cid, title='Arşivlenecek').get_json()['file']
    vid = f['current']['id']
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    assert client.get(f'/api/design-files/versions/{vid}/download').status_code == 404


# --- düzenleme ----------------------------------------------------------

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
    """`{"title": 5}` — string olmayan başlık `.strip()`'te AttributeError'a
    (dolayısıyla 500'e) düşerdi; anlaşılır 400 dönmeli."""
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'title': 5},
                     headers=csrf_headers(client))
    assert r.status_code == 400


def test_patch_etiketler_dizi_degil_400(client, fid):
    """`{"tags": "şablon"}` — dizi olmayan `tags` `_etiketler`'de sessizce []
    dönüp dosyanın TÜM etiketlerini silerdi (200); anlaşılır 400 dönmeli."""
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'tags': 'şablon'},
                     headers=csrf_headers(client))
    assert r.status_code == 400


def test_patch_etiket_gonderilmezse_korunur(client, fid):
    """Gövdede `tags` anahtarı YOKSA mevcut etiketler dokunulmadan kalır —
    davranış zaten doğruydu, burada kilitleniyor."""
    login_as(client, DESIGNER)
    r1 = client.patch(f'/api/design-files/{fid}', json={'tags': ['kampanya']},
                      headers=csrf_headers(client))
    assert r1.status_code == 200
    r2 = client.patch(f'/api/design-files/{fid}', json={'title': 'Yeni Ad'},
                      headers=csrf_headers(client))
    assert r2.status_code == 200
    assert r2.get_json()['file']['tags'] == ['kampanya']


def test_patch_musteri_silinmisse_404(client, cid, fid):
    """Tutarlılık: sürüm/indirme uçları arşivlenmiş müşteride 404 dönüyor,
    düzenleme de aynı kuralı uygulamalı — arşivlenmiş müşterinin dosyası
    düzenlenememeli (denetimde işaretlenen desen, burada PATCH'e de uygulandı)."""
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    r = client.patch(f'/api/design-files/{fid}', json={'title': 'Olmaz'},
                     headers=csrf_headers(client))
    assert r.status_code == 404


# --- silme ----------------------------------------------------------------

def test_silme_yukleyen_yapabilir(client, cid, fid):
    login_as(client, DESIGNER)
    r = client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert r.status_code == 200
    # Liste boşalır ve kota anında serbest kalır.
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
    """Panelin düğmesi backend kuralıyla BİREBİR aynı olmalı — ayrışırsa yalan söyler."""
    login_as(client, {'sub': '9', 'email': 'diger@test.com',
                      'name': 'Diğer', 'role': 'designer'})
    d = client.get(f'/api/design-files/client/{cid}').get_json()
    assert d['files'][0]['can_delete'] is False
    login_as(client, MANAGER)
    d = client.get(f'/api/design-files/client/{cid}').get_json()
    assert d['files'][0]['can_delete'] is True


def test_dosya_silme_musteri_silinmisse_404(client, cid, fid):
    """Aynı tutarlılık kuralı `DELETE /<file_id>` için de geçerli — arşivlenmiş
    müşterinin dosyası silinememeli (denetimde işaretlenen desen)."""
    login_as(client, MANAGER)
    r = client.delete(f'/api/clients/{cid}', headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    login_as(client, DESIGNER)
    assert client.delete(f'/api/design-files/{fid}',
                         headers=csrf_headers(client)).status_code == 404


def test_surum_silme_son_surum_engellenir(client, fid):
    """Tek sürüm kaldıysa silinmez — dosyayı silmek isteyen dosyayı silsin."""
    login_as(client, DESIGNER)
    v = client.get(f'/api/design-files/{fid}/versions').get_json()['versions'][0]
    r = client.delete(f"/api/design-files/versions/{v['id']}",
                      headers=csrf_headers(client))
    assert r.status_code == 400
    assert 'son sürüm' in r.get_json()['error'].lower()


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
    """Drive EN-İYİ-ÇABA: çöpe atma patlasa da DB silmesi engellenmez, yalnız
    drive_ok False döner (yükleme tarafındaki `test_yukleme_drive_hatasi_yine_201`
    ile aynı desen, silme tarafı için)."""
    import design_files

    def patla(version):
        return False

    monkeypatch.setattr(design_files, '_drive_cope', patla)
    login_as(client, DESIGNER)
    r = client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()['drive_ok'] is False


def test_surum_silme_drive_hatasi_yine_200(client, fid, monkeypatch):
    """Aynı desen tek sürüm silmede: Drive çöpe atma patlarsa da DB silmesi
    engellenmez."""
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
    """Aynı tutarlılık kuralı `DELETE /versions/<id>` için de geçerli."""
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
    """Soft-delete süzgeci: silinmiş satır hiçbir listede görünmez."""
    login_as(client, MANAGER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    assert client.get(f'/api/design-files/client/{cid}').get_json()['files'] == []
    assert client.get(f'/api/design-files/{fid}/versions').status_code == 404


def test_silmede_deleted_by_yazilir(client, fid):
    """`file_delete` artık kim sildiğini kaydediyor (önceden kolon bile yoktu)."""
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


# --- çöp kutusu: listeleme ----------------------------------------------

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
    # Canlı dosya çöp kutusunda görünmez.
    d2 = client.get(f'/api/design-files/client/{cid}').get_json()
    assert len(d2['files']) == 1
    assert d2['files'][0]['title'] == 'Canlı Kalan'


@pytest.mark.parametrize('who', [CONTENT_CREATOR, VIDEOGRAPHER])
def test_trash_yetkisiz_403(client, cid, who):
    login_as(client, who)
    r = client.get(f'/api/design-files/client/{cid}/trash')
    assert r.status_code == 403


def test_trash_designer_can_purge_false(client, cid, fid):
    """Tasarımcı çöpü görür ama kalıcı silme düğmesi ona yalan söylemesin."""
    login_as(client, DESIGNER)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['files'][0]['can_purge'] is False
    assert d['files'][0]['can_restore'] is True


def test_trash_tekil_silinen_surum_versions_dizisinde(client, fid):
    """Dosya canlı kalır ama tek bir sürümü silinirse, o sürüm `versions`
    dizisinde görünür — `files` dizisinde DEĞİL (dosyanın kendisi silinmedi)."""
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


# --- çöp kutusu: geri alma -----------------------------------------------

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
    # Çöp kutusu artık boş.
    assert client.get(f'/api/design-files/client/{cid}/trash').get_json()['files'] == []


def test_restore_kapsam_tek_tek_silinen_surum_geri_gelmez(client, cid, fid):
    """Kritik kural: v2'yi tek tek sil, sonra dosyayı sil, sonra dosyayı geri
    al — v2 SİLİNMİŞ KALMALI, v1 dönmeli. `deleted_at` damgası eşleşmezse
    (v2'nin damgası dosyanınkinden ÖNCE atıldı) geri alma kapsamına girmez."""
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
    assert [x['version_no'] for x in kalan] == [1]        # v2 hâlâ çöpte
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
    """Dosyanın kendisi çöpteyken tekil sürüm geri alma reddedilir — önce
    dosya geri alınmalı, aksi halde canlı listede görünmeyen bir dosyaya
    bağlı 'canlı' bir sürüm gibi tutarsız bir ara durum doğardı."""
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


# --- çöp kutusu: kalıcı silme talebi -------------------------------------

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
    # Hâlâ DB'de duruyor — hiçbir şey silinmedi.
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


# --- çöp kutusu: kalıcı silme ---------------------------------------------

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
    """Aynı içerik (aynı sha256) iki farklı dosyaya yüklenmişse, birini purge
    etmek disk dosyasını SİLMEMELİ — diğeri hâlâ referans veriyor. İkisi de
    purge edilince disk dosyası gitmeli."""
    login_as(client, DESIGNER)
    fa = _yukle(client, cid, title='Dosya A').get_json()['file']
    fb = _yukle(client, cid, title='Dosya B').get_json()['file']    # aynı PSD → dedup
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
    assert yol.exists()          # Dosya B hâlâ referans veriyor

    client.delete(f"/api/design-files/{fb['id']}", headers=csrf_headers(client))
    r = client.delete(f"/api/design-files/{fb['id']}/purge", json={'confirm': True},
                      headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['disk_ok'] is True
    assert not yol.exists()      # artık kimse referans vermiyor


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
    """Denetim bulgusu: `version_purge` `version_restore`'un aksine dosyanın
    KENDİSİNİN (`f.deleted_at`) çöp kutusunda olup olmadığına bakmıyordu —
    yönetim çöpteki bir dosyanın TEK sürümünü purge edince `design_files`
    satırı SIFIR sürümle kalabiliyordu (kabuk dosya; sonra restore edilirse
    canlı listede `current: null` görünür). `version_delete`'in "son sürüm
    silinemez" kararına aykırı bir ara durum — burada 400 ile engellenmeli,
    sürüm DB'de sağ salim kalmalı (silinemedi, yalnız reddedildi)."""
    login_as(client, MANAGER)
    v1 = client.get(f'/api/design-files/{fid}/versions').get_json()['versions'][0]
    # Dosyanın TAMAMINI çöpe at (tüm sürümleri de aynı damgayla silinir).
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    r = client.delete(f"/api/design-files/versions/{v1['id']}/purge",
                      json={'confirm': True}, headers=csrf_headers(client))
    assert r.status_code == 400
    assert 'dosyanın tamamını' in r.get_json()['error']
    from extensions import db as _db
    from models_design_files import DesignFileVersion
    assert _db.session.get(DesignFileVersion, v1['id']) is not None   # reddedildi, silinmedi


def test_purge_farkli_uzantili_ayni_icerik_hicbiri_kalmaz(client, cid, store):
    """Denetim bulgusu: aynı bayt'lar `logo.psd` VE `logo.ai` olarak yüklenirse
    `_yol` uzantı içerdiği için diskte İKİ ayrı fiziksel dosya oluşur
    (`<sha>.psd`, `<sha>.ai`) — `_diske_yaz`'daki dedup kontrolü hedefi
    (sha+uzantı) bazında yaptığı için ikisi de yazılır. Eski `_purge_disk_dedup`
    yalnız SON işlenen sürümün `file_name`'ine ait tek yolu siliyordu, diğeri
    kalıcı öksüz kalıyordu. Yeni sürüm o sha'ya ait TÜM yolları (`glob`) temizler
    — ikisi de purge edilince diskte hiçbir şey kalmamalı."""
    login_as(client, DESIGNER)
    fa = _yukle(client, cid, name='logo.psd', title='Logo PSD').get_json()['file']
    fb = _yukle(client, cid, name='logo.ai', title='Logo AI').get_json()['file']
    sha = fa['current']['sha256']
    assert sha == fb['current']['sha256']          # aynı içerik → aynı sha
    yol_psd = store / sha[:2] / f'{sha}.psd'
    yol_ai = store / sha[:2] / f'{sha}.ai'
    assert yol_psd.exists() and yol_ai.exists()    # sızıntı senaryosunun ön koşulu

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
    """`_sha_kilidi` TOCTOU kilidinin dialect dallanmasını doğrular.

    KANITLAR: (1) test dialect'i sqlite iken `db.session.execute` HİÇ
    çağrılmıyor (kilit sessizce atlanıyor); (2) dialect adı 'postgresql'
    taklit edildiğinde `pg_advisory_xact_lock(hashtext(:k))` metnini ve
    `design-files:sha:<sha>` anahtarını taşıyan TEK bir `execute` çağrısı
    yapılıyor.

    KANITLAMAZ: `pg_advisory_xact_lock`'ın gerçek bir Postgres'te iki eşzamanlı
    bağlantıyı GERÇEKTEN serileştirdiğini — repo testleri sqlite'ta koşuyor
    (`tests/conftest.py`), sqlite'ta advisory lock yok ve gerçek yarış (iki
    eşzamanlı transaction) burada KURULAMAZ. `_diske_yaz` → `_purge_disk_dedup`
    arasındaki TOCTOU yarışının prod Postgres'inde gerçekten kapandığı ancak
    canlıya yakın bir ortamda, gerçek eşzamanlı bağlantılarla doğrulanabilir;
    bu test yalnız "doğru koşulda doğru SQL çağrılıyor mu" sorusunu kanıtlar."""
    import design_files
    from extensions import db

    calls = []
    monkeypatch.setattr(db.session, 'execute', lambda *a, **kw: calls.append(a))

    # sqlite (gerçek test dialect'i): kilit atlanmalı, execute çağrılmamalı.
    design_files._sha_kilidi('a' * 64)
    assert calls == []

    # 'postgresql' dialect'i taklit et: execute çağrılmalı.
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
    """DRIFT MUHAFIZI: `design_files._diske_yaz`'ın `sha_store.yaz`'ı GERÇEKTEN
    `on_hashed=design_files._sha_kilidi` ile çağırdığını doğrular.

    Bu bağı kanıtlayan başka HİÇBİR test yok. `_diske_yaz` içindeki
    `on_hashed=_sha_kilidi` satırı silinse (ör. bir refactor'da kwarg'ı
    unutulsa) TOCTOU kilidi sessizce devre dışı kalır — yükleme ile
    `_purge_disk_dedup` arasındaki yarış (yukarıdaki blok yorumu) yeniden
    açılır ve `test_sha_kilidi_yalniz_postgreste_cagrilir` dahil TÜM test
    takımı yine YEŞİL kalırdı, çünkü o test `_sha_kilidi`'yi DOĞRUDAN çağırıyor,
    `_diske_yaz`'ın onu gerçekten kullandığını değil. Bu test `sha_store.yaz`'ı
    monkeypatch'leyip kendisine geçilen `on_hashed` kwarg'ının kimliğini
    (`is`) `design_files._sha_kilidi` ile karşılaştırarak o boşluğu kapatır."""
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


# --- çöp kutusu: geri alma Drive kopyasını da çöpten çıkarsın ---------------
#
# Soft-delete Drive kopyasını `_drive_cope` ile çöp kutusuna atıyordu ama geri
# alma ucu bunu geri ÇIKARMIYORDU — `drive_ok:true` diyen bir sürüm aslında
# hâlâ Drive çöpünde kalıp 30 gün sonra kalıcı silinebiliyordu. Bu blok
# `_drive_geri_al`'ın her iki restore ucunda da çağrıldığını doğrular.

def test_restore_dosya_drive_geri_al_cagrilir(client, cid, fid, monkeypatch):
    """Dosya geri alınınca, dosyayla birlikte dönen HER sürüm için Drive
    çöpten çıkarma çağrılmalı."""
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
    """Drive geri alma patlarsa bile uç 200 döner ve DB satırı geri gelir —
    yalnız `drive_ok` False olur (EN-İYİ-ÇABA, `_drive_cope` ile aynı desen)."""
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
    """Tekil sürüm geri alma da Drive çöpten çıkarmayı tetiklemeli."""
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
    """`drive_file_id` NULL olan bir sürüm (Drive'a hiç kopyalanamamış) için
    geri alma sırasında boşuna Drive çağrısı atılmamalı — `_drive_geri_al`
    kendi içinde bunu kontrol eder, burada gerçek `dg.untrash_file` üzerinden
    doğrulanır."""
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


# --- çöp kutusu: satır özeti ve toplam boyut --------------------------------
#
# `client_trash` silinmiş DOSYALAR için `_guncel_surumler`i eskiden
# `silinmisler_dahil` olmadan çağırıyordu — o filtre `deleted_at IS NULL`
# aradığı için (silinmiş dosyanın TÜM sürümleri silinmiş) hep boş dönüyor,
# panel her satırda `current: null, version_count: 0` görüyordu.

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
    """İki sürümlü bir dosya silinince çöpte `version_count == 2` ve
    `current.version_no == 2` — en yüksek sürüm SİLİNMİŞLER DAHİL bulunur."""
    login_as(client, DESIGNER)
    _surum_yukle(client, fid, note='ikinci')
    f = _db_get_file(fid)
    client.delete(f'/api/design-files/{fid}', headers=csrf_headers(client))
    d = client.get(f'/api/design-files/client/{f.client_id}/trash').get_json()
    row = d['files'][0]
    assert row['version_count'] == 2
    assert row['current']['version_no'] == 2


def test_trash_bytes_dogru_canli_dosya_girmiyor(client, cid):
    """Bilinen boyutlarda dosyalar sil, `trash_bytes` toplamını doğrula.
    Canlı (silinmemiş) dosyanın boyutu bu toplama GİRMEMELİ."""
    login_as(client, DESIGNER)
    fid1 = _yukle(client, cid, data=b'a' * 1000, title='Silinecek Küçük').get_json()['file']['id']
    fid2 = _yukle(client, cid, data=b'b' * 2000, title='Silinecek Büyük').get_json()['file']['id']
    _yukle(client, cid, data=b'c' * 3000, title='Canlı Kalan')

    client.delete(f'/api/design-files/{fid1}', headers=csrf_headers(client))
    client.delete(f'/api/design-files/{fid2}', headers=csrf_headers(client))

    d = client.get(f'/api/design-files/client/{cid}/trash').get_json()
    assert d['trash_bytes'] == 1000 + 2000


def test_trash_bytes_tekil_silinen_surum_dahil(client, fid):
    """Dosyanın kendisi çöpte olmasa da tek tek silinmiş bir sürüm
    `trash_bytes`'a girer (`versions` dizisiyle aynı satırlar)."""
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
