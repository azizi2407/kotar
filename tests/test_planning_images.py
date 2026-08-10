"""Planlama Panosu görselleri — depolama katmanı + iki uç + yetki.

Dosya SUNUCUDA saklanır (`planning_images`, pano başına dizin) ve öğe ona
`type='image'` + `extra.image={name,w,h}` ile işaret eder. Yetkilendirme dizin
yerleşiminden gelir: her iki uç da `_board_access`'ten geçtiği ve yol pano
dizininden kurulduğu için başka panonun görseli adı bilinse bile okunamaz.
"""
import io
import os

import pytest
from PIL import Image

import planning_images
from conftest import DESIGNER, MANAGER, PENDING, login_as
from extensions import db
from test_session_csrf import csrf_headers

BASE = '/api/planning'


def _png(w=40, h=30, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), color).save(buf, format='PNG')
    return buf.getvalue()


def _gif(w=10, h=10):
    buf = io.BytesIO()
    Image.new('P', (w, h)).save(buf, format='GIF')
    return buf.getvalue()


@pytest.fixture
def img_dir(tmp_path, monkeypatch):
    """Görsel deposunu teste izole et — repo `data/`'sına dokunulmaz."""
    monkeypatch.setenv('PLANNING_IMAGE_DIR', str(tmp_path))
    return tmp_path


def _seed_users():
    from models import UserRef
    for u in (MANAGER, DESIGNER):
        db.session.add(UserRef(sub=u['sub'], email=u['email'], name=u['name'], role=u['role']))
    db.session.commit()


def _upload(client, board_key, data=None, filename='ss.png', content_type='image/png'):
    return client.post(f'{BASE}/boards/{board_key}/images',
                       data={'file': (io.BytesIO(data if data is not None else _png()), filename)},
                       content_type='multipart/form-data', headers=csrf_headers(client))


# --- depolama katmanı ----------------------------------------------------

def test_store_png_yazar_ve_olculeri_doner(img_dir):
    meta = planning_images.store(7, _png(40, 30))
    assert meta['width'] == 40 and meta['height'] == 30
    assert meta['name'].endswith('.png') and len(meta['name']) == 36
    assert os.path.isfile(os.path.join(img_dir, '7', meta['name']))


def test_store_buyuk_gorseli_kucultur(img_dir):
    """2000 px üstü kenar küçültülür — panoda tam çözünürlük tutmanın karşılığı yok."""
    meta = planning_images.store(1, _png(3000, 1500))
    assert meta['width'] == planning_images.MAX_EDGE
    assert meta['height'] == planning_images.MAX_EDGE // 2


def test_store_gif_kucultulmez(img_dir):
    """Küçültme animasyonlu GIF'i ilk kareye indirirdi → GIF olduğu gibi saklanır."""
    meta = planning_images.store(1, _gif(10, 10))
    assert meta['name'].endswith('.gif')


def test_store_gorsel_olmayani_reddeder(img_dir):
    with pytest.raises(planning_images.ImageError, match='görsel değil'):
        planning_images.store(1, b'bu bir metin dosyasi')


def test_store_svg_reddeder(img_dir):
    """SVG script taşıyabilir ve panoda inline render edilecek — bilerek dışarıda."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(planning_images.ImageError):
        planning_images.store(1, svg)


def test_store_buyuk_dosyayi_reddeder(img_dir, monkeypatch):
    monkeypatch.setattr(planning_images, 'MAX_BYTES', 100)
    with pytest.raises(planning_images.ImageError, match='sınırını aşıyor'):
        planning_images.store(1, _png(200, 200))


def test_store_pano_kotasini_asamaz(img_dir, monkeypatch):
    monkeypatch.setattr(planning_images, 'MAX_BOARD_BYTES', 200)
    planning_images.store(1, _png(10, 10))
    with pytest.raises(planning_images.ImageError, match='dolu'):
        for _ in range(20):
            planning_images.store(1, _png(60, 60))


def test_store_grup_yazilabilir_birakir(img_dir):
    """Dosyayı Flask (svc-agency) yazar, temizlik timer'ı `proje sahibi` olarak siler —
    ikisi de `appdev` grubunda; grup yazma izni olmazsa janitor silemez."""
    meta = planning_images.store(3, _png())
    mode = os.stat(os.path.join(img_dir, '3', meta['name'])).st_mode
    assert mode & 0o020, 'grup yazma izni yok'


@pytest.mark.parametrize('kotu', [
    '../../../etc/passwd', 'abc.png', 'a' * 32 + '.exe', '', None, 42,
    '/etc/passwd', '../' + 'a' * 32 + '.png', 'a' * 31 + 'z.png',
])
def test_safe_name_kotu_adlari_reddeder(kotu):
    assert planning_images.safe_name(kotu) is None


def test_safe_name_gecerli_adi_kabul_eder():
    assert planning_images.safe_name('a' * 32 + '.PNG') == 'a' * 32 + '.png'


# --- uçlar: yükleme ------------------------------------------------------

def test_upload_management_kendi_panosuna(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    r = _upload(client, 'management')
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['image']['width'] == 40


def test_upload_designer_kendi_panosuna(client, img_dir):
    _seed_users()
    login_as(client, DESIGNER)
    r = _upload(client, f'user:{DESIGNER["sub"]}')
    assert r.status_code == 201


def test_upload_designer_yonetim_panosuna_403(client, img_dir):
    _seed_users()
    login_as(client, DESIGNER)
    assert _upload(client, 'management').status_code == 403


def test_upload_designer_baskasinin_panosuna_403(client, img_dir):
    _seed_users()
    login_as(client, DESIGNER)
    assert _upload(client, f'user:{MANAGER["sub"]}').status_code == 403


def test_upload_pending_403(client, img_dir):
    _seed_users()
    login_as(client, PENDING)
    assert _upload(client, 'management').status_code == 403


def test_upload_oturumsuz_403_csrf(client, img_dir):
    """CSRF kapısı (`before_request`) oturum kontrolünden ÖNCE çalışır → oturumsuz
    istek 401'e hiç ulaşmaz, 403 CSRF ile döner. Token session'dan üretildiği için
    oturumsuz geçerli token da mümkün değil; bu uçta 401 erişilemez bir daldır."""
    r = client.post(f'{BASE}/boards/management/images', data={},
                    content_type='multipart/form-data')
    assert r.status_code == 403
    assert 'CSRF' in r.get_json()['error']


def test_upload_dosyasiz_400(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    r = client.post(f'{BASE}/boards/management/images', data={},
                    content_type='multipart/form-data', headers=csrf_headers(client))
    assert r.status_code == 400


def test_upload_gorsel_olmayan_400(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    r = _upload(client, 'management', data=b'metin', filename='not.txt')
    assert r.status_code == 400
    assert 'görsel değil' in r.get_json()['error']


# --- uçlar: servis -------------------------------------------------------

def test_serve_yukleyen_okuyabilir(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    name = _upload(client, 'management').get_json()['image']['name']
    r = client.get(f'{BASE}/boards/management/images/{name}')
    try:
        assert r.status_code == 200
        assert r.data[:8] == b'\x89PNG\r\n\x1a\n'
    finally:
        r.close()   # send_file dosyayı açık bırakır; ResourceWarning testi kırar


def test_serve_yetkisiz_rol_403(client, img_dir):
    """Yönetim panosunun görseli tasarımcıya KAPALI — pano kuralının aynısı."""
    _seed_users()
    login_as(client, MANAGER)
    name = _upload(client, 'management').get_json()['image']['name']
    login_as(client, DESIGNER)
    assert client.get(f'{BASE}/boards/management/images/{name}').status_code == 403


def test_serve_capraz_pano_404(client, img_dir):
    """Ad bilinse bile BAŞKA panonun dizininde aranır → bulunamaz.
    Yol istekten değil pano kaydından kurulduğu için sızıntı imkânsız."""
    _seed_users()
    login_as(client, MANAGER)
    name = _upload(client, 'management').get_json()['image']['name']
    r = client.get(f'{BASE}/boards/user:{MANAGER["sub"]}/images/{name}')
    assert r.status_code == 404


def test_serve_path_traversal_404(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    _upload(client, 'management')
    r = client.get(f'{BASE}/boards/management/images/..%2F..%2Fapp.py')
    assert r.status_code in (404, 400)


def test_serve_olmayan_ad_404(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    _upload(client, 'management')
    assert client.get(f'{BASE}/boards/management/images/{"b" * 32}.png').status_code == 404


# --- öğe tipi ------------------------------------------------------------

def test_image_ogesi_yazilabilir(client, img_dir):
    """`type='image'` + `extra.image` — `link` kolonu DEĞİL (orada http(s) şartı var)."""
    _seed_users()
    login_as(client, MANAGER)
    meta = _upload(client, 'management').get_json()['image']
    r = client.patch(f'{BASE}/boards/management/items', headers=csrf_headers(client),
                     json={'upsert': [{'item_key': 'img1', 'type': 'image', 'x': 10, 'y': 20,
                                       'extra': {'image': meta}}]})
    assert r.status_code == 200, r.get_json()
    item = r.get_json()['applied'][0]
    assert item['type'] == 'image'
    assert item['extra']['image']['name'] == meta['name']


# --- temizlik script'i ---------------------------------------------------

def test_cleanup_yetim_siler_kullanilani_korur(client, img_dir):
    import time
    from scripts.cleanup_planning_images import run
    _seed_users()
    login_as(client, MANAGER)
    kullanilan = _upload(client, 'management').get_json()['image']
    yetim = _upload(client, 'management').get_json()['image']
    client.patch(f'{BASE}/boards/management/items', headers=csrf_headers(client),
                 json={'upsert': [{'item_key': 'img1', 'type': 'image',
                                   'extra': {'image': kullanilan}}]})
    from models_planning import PlanningBoard
    bid = PlanningBoard.query.filter_by(board_key='management').first().id
    # İkisini de eskit: yaş eşiği yükleme yarışını kapatır, testte onu aşmalıyız.
    eski = time.time() - 40 * 86400
    for n in (kullanilan['name'], yetim['name']):
        os.utime(os.path.join(img_dir, str(bid), n), (eski, eski))

    silinen, korunan, _ = run(days=30, apply=True)
    assert silinen == 1 and korunan == 1
    assert os.path.isfile(os.path.join(img_dir, str(bid), kullanilan['name']))
    assert not os.path.isfile(os.path.join(img_dir, str(bid), yetim['name']))


def test_cleanup_genc_yetimi_korur(client, img_dir):
    """Yükleme ile öğe PATCH'i arasındaki pencerede dosya yetim görünür — yaş
    eşiği olmasaydı janitor onu daha öğe yazılmadan silerdi."""
    from scripts.cleanup_planning_images import run
    _seed_users()
    login_as(client, MANAGER)
    _upload(client, 'management')
    silinen, korunan, _ = run(days=30, apply=True)
    assert silinen == 0 and korunan == 1


def test_cleanup_kuru_kosu_dosyaya_dokunmaz(client, img_dir):
    import time
    from scripts.cleanup_planning_images import run
    from models_planning import PlanningBoard
    _seed_users()
    login_as(client, MANAGER)
    meta = _upload(client, 'management').get_json()['image']
    bid = PlanningBoard.query.filter_by(board_key='management').first().id
    p = os.path.join(img_dir, str(bid), meta['name'])
    eski = time.time() - 40 * 86400
    os.utime(p, (eski, eski))
    silinen, _, _ = run(days=30, apply=False)
    assert silinen == 1
    assert os.path.isfile(p), 'kuru koşu dosyayı sildi'
