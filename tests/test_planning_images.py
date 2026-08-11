"""Planning Board images — storage layer + two endpoints + permissions.

The file is stored ON THE SERVER (`planning_images`, one directory per board) and
the item points to it via `type='image'` + `extra.image={name,w,h}`. Authorization
comes from the directory layout: since both endpoints go through `_board_access`
and the path is built from the board record, another board's image can't be read
even if its name is known.
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
    """Isolate the image store for the test — the repo's `data/` is not touched."""
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


# --- storage layer --------------------------------------------------------

def test_store_png_yazar_ve_olculeri_doner(img_dir):
    meta = planning_images.store(7, _png(40, 30))
    assert meta['width'] == 40 and meta['height'] == 30
    assert meta['name'].endswith('.png') and len(meta['name']) == 36
    assert os.path.isfile(os.path.join(img_dir, '7', meta['name']))


def test_store_buyuk_gorseli_kucultur(img_dir):
    """Edges over 2000 px are shrunk — there's no point keeping full resolution on the board."""
    meta = planning_images.store(1, _png(3000, 1500))
    assert meta['width'] == planning_images.MAX_EDGE
    assert meta['height'] == planning_images.MAX_EDGE // 2


def test_store_gif_kucultulmez(img_dir):
    """Shrinking would reduce an animated GIF to its first frame → GIFs are stored as-is."""
    meta = planning_images.store(1, _gif(10, 10))
    assert meta['name'].endswith('.gif')


def test_store_gorsel_olmayani_reddeder(img_dir):
    with pytest.raises(planning_images.ImageError, match='görsel değil'):
        planning_images.store(1, b'bu bir metin dosyasi')


def test_store_svg_reddeder(img_dir):
    """SVG can carry a script and would be rendered inline on the board — deliberately excluded."""
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
    """Flask (svc-agency) writes the file, the cleanup timer deletes it as `project owner` —
    both are in the `appdev` group; without group write permission, the janitor can't delete it."""
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


# --- endpoints: upload -----------------------------------------------------

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
    """The CSRF gate (`before_request`) runs BEFORE the session check → a session-less
    request never reaches 401, it returns 403 CSRF instead. Since the token is derived
    from the session, a valid token without a session is also impossible; on this
    endpoint 401 is an unreachable branch."""
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


# --- endpoints: serving -----------------------------------------------------

def test_serve_yukleyen_okuyabilir(client, img_dir):
    _seed_users()
    login_as(client, MANAGER)
    name = _upload(client, 'management').get_json()['image']['name']
    r = client.get(f'{BASE}/boards/management/images/{name}')
    try:
        assert r.status_code == 200
        assert r.data[:8] == b'\x89PNG\r\n\x1a\n'
    finally:
        r.close()   # send_file leaves the file open; a ResourceWarning would break the test


def test_serve_yetkisiz_rol_403(client, img_dir):
    """The management board's image is CLOSED to a designer — same rule as the board itself."""
    _seed_users()
    login_as(client, MANAGER)
    name = _upload(client, 'management').get_json()['image']['name']
    login_as(client, DESIGNER)
    assert client.get(f'{BASE}/boards/management/images/{name}').status_code == 403


def test_serve_capraz_pano_404(client, img_dir):
    """Even if the name is known, it's looked up in ANOTHER board's directory → not found.
    Since the path is built from the board record, not from the request, leakage is impossible."""
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


# --- item type --------------------------------------------------------------

def test_image_ogesi_yazilabilir(client, img_dir):
    """`type='image'` + `extra.image` — NOT the `link` column (that one requires http(s))."""
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


# --- cleanup script ----------------------------------------------------------

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
    # Age both: the age threshold closes off the upload race, and the test needs to exceed it.
    eski = time.time() - 40 * 86400
    for n in (kullanilan['name'], yetim['name']):
        os.utime(os.path.join(img_dir, str(bid), n), (eski, eski))

    silinen, korunan, _ = run(days=30, apply=True)
    assert silinen == 1 and korunan == 1
    assert os.path.isfile(os.path.join(img_dir, str(bid), kullanilan['name']))
    assert not os.path.isfile(os.path.join(img_dir, str(bid), yetim['name']))


def test_cleanup_genc_yetimi_korur(client, img_dir):
    """In the window between upload and the item PATCH, the file looks orphaned — without
    the age threshold, the janitor would delete it before the item is even written."""
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
