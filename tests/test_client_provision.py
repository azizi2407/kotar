"""Cutover C2 — yeni müşteri Drive klasör ağacı provizyonu testleri.

GERÇEK Drive çağrısı YOK: `client_provision.dg` (drive_gateway) mock'lanır.
Doğrulanan davranışlar:
  - müşteri create → beklenen klasör ağacı (kök + 1..52 hafta) çağrıları yapılır,
  - idempotent: mevcut klasör yeniden oluşturulmaz (tekrar provizyon 0 yeni satır),
  - Drive hatası müşteri create'i BLOKLAMAZ (best-effort; hata yutulur).
"""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

import client_provision as cp
from extensions import db
from models import Client, ClientWeekFolder, Notification, UserRef

CONTENT_ROOT = 'root_content_id'


class FakeDrive:
    """drive_gateway.ensure_subfolder taklidi — bul-veya-oluştur (idempotent)."""

    def __init__(self):
        self.tree = {}          # (parent, name) -> id
        self.calls = []         # [(parent, name), ...] sırayla
        self._n = 0

    def ensure_subfolder(self, parent, name):
        self.calls.append((parent, name))
        key = (parent, name)
        if key not in self.tree:
            self._n += 1
            self.tree[key] = f'fid{self._n}'
        return self.tree[key]


@pytest.fixture
def fake_drive(monkeypatch):
    """Drive kimliğini 'var' göster, kökü ayarla ve ensure_subfolder'ı mock'la."""
    fake = FakeDrive()
    monkeypatch.setenv('DRIVE_CONTENT_ROOT_ID', CONTENT_ROOT)
    monkeypatch.setattr(cp.dg, 'available', lambda: True)
    monkeypatch.setattr(cp.dg, 'ensure_subfolder', fake.ensure_subfolder)
    return fake


def _create(client):
    r = client.post('/api/clients', json={'name': 'Örnek Kafe'},
                    headers=csrf_headers(client))
    assert r.status_code == 201, r.get_json()
    return r.get_json()['client']


# --- Provizyon: klasör ağacı oluşur ---

def test_create_provizyon_klasor_agaci(client, fake_drive):
    login_as(client, MANAGER)
    data = _create(client)

    # kök klasör içerik kökü altında, ada göre
    assert fake_drive.calls[0] == (CONTENT_ROOT, 'Örnek Kafe')
    client_root = fake_drive.tree[(CONTENT_ROOT, 'Örnek Kafe')]
    # ardından 1..52 hafta alt klasörü, müşteri kökü altında, sırayla
    assert fake_drive.calls[1:] == [(client_root, str(wn)) for wn in range(1, 53)]

    # drive_meta kök linki + 52 ClientWeekFolder satırı yazıldı
    assert data['drive_meta']['client_folder_link'].endswith(client_root)
    rows = ClientWeekFolder.query.filter_by(client_id=data['id']).all()
    assert {r.week_number for r in rows} == set(range(1, 53))


# --- İdempotent: tekrar provizyon yeni satır üretmez ---

def test_provizyon_idempotent(client, fake_drive):
    login_as(client, MANAGER)
    data = _create(client)
    c = db.session.get(Client, data['id'])

    before = fake_drive.tree.copy()
    result = cp.provision_client_folders(c)

    assert result == {'client_folder_id': before[(CONTENT_ROOT, 'Örnek Kafe')],
                      'weeks_created': 0}
    # yeni klasör id üretilmedi (hepsi bul-ile karşılandı)
    assert fake_drive.tree == before
    # hafta satırı sayısı hâlâ 52 (mükerrer yok)
    assert ClientWeekFolder.query.filter_by(client_id=c.id).count() == 52


def test_provizyon_mevcut_kok_yeni_yaratmaz(client, fake_drive):
    """drive_meta'da kök zaten kayıtlıysa içerik kökü altında yeni kök AÇILMAZ."""
    login_as(client, MANAGER)
    c = Client(name='Göçen Müşteri',
               drive_meta={'client_folder_link':
                           'https://drive.google.com/drive/folders/mevcut_kok'})
    db.session.add(c)
    db.session.commit()

    cp.provision_client_folders(c)

    # içerik kökü altında ad-bazlı kök oluşturma çağrısı YOK; hafta klasörleri mevcut kök altında
    assert (CONTENT_ROOT, 'Göçen Müşteri') not in fake_drive.calls
    assert fake_drive.calls == [('mevcut_kok', str(wn)) for wn in range(1, 53)]


# --- Best-effort: Drive hatası create'i bloklamaz ---

def test_drive_hatasi_create_bloklamaz(client, fake_drive, monkeypatch):
    def boom(parent, name):
        raise cp.dg.DriveError('drive erişilemedi')

    monkeypatch.setattr(cp.dg, 'ensure_subfolder', boom)
    login_as(client, MANAGER)

    r = client.post('/api/clients', json={'name': 'Hata Kafe'},
                    headers=csrf_headers(client))
    # müşteri yine oluşur (201), sadece Drive provizyonu atlanır
    assert r.status_code == 201
    data = r.get_json()['client']
    assert data['drive_meta'] is None
    c = db.session.get(Client, data['id'])
    assert c is not None
    assert ClientWeekFolder.query.filter_by(client_id=c.id).count() == 0


def test_drive_hatasi_provision_failed_bildirimi_gonderir(client, fake_drive, monkeypatch):
    """Drive hatasında yönetime 'provision_failed' bildirimi gönderilir (best-effort
    ama sessiz değil — Observer bulgusu: bu davranış hiçbir testle korunmuyordu)."""
    def boom(parent, name):
        raise cp.dg.DriveError('drive erişilemedi')

    monkeypatch.setattr(cp.dg, 'ensure_subfolder', boom)
    db.session.add(UserRef(sub=MANAGER['sub'], email=MANAGER['email'],
                           name=MANAGER['name'], role='management'))
    db.session.commit()
    login_as(client, MANAGER)

    r = client.post('/api/clients', json={'name': 'Hata Kafe'},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    data = r.get_json()['client']

    notifs = Notification.query.filter_by(kind='provision_failed').all()
    assert len(notifs) == 1
    n = notifs[0]
    assert n.recipient_sub == MANAGER['sub']
    assert n.title == 'Drive klasörü kurulamadı'
    assert 'Hata Kafe' in n.body
    assert n.link == f"/panel/clients/{data['id']}"


# --- Kimlik/kök yoksa sessizce atla ---

def test_drive_kimligi_yoksa_atlanir(client, monkeypatch):
    """Drive kimliği yapılandırılmamışsa provizyon hiç çağrı yapmadan atlar."""
    monkeypatch.setattr(cp.dg, 'available', lambda: False)
    login_as(client, MANAGER)
    data = _create(client)
    assert data['drive_meta'] is None
    assert ClientWeekFolder.query.filter_by(client_id=data['id']).count() == 0


def test_content_root_yoksa_atlanir(client, monkeypatch):
    monkeypatch.setattr(cp.dg, 'available', lambda: True)
    monkeypatch.delenv('DRIVE_CONTENT_ROOT_ID', raising=False)
    called = []
    monkeypatch.setattr(cp.dg, 'ensure_subfolder',
                        lambda p, n: called.append((p, n)))
    login_as(client, MANAGER)
    data = _create(client)
    assert called == []
    assert data['drive_meta'] is None


# --- Elle tamamlama ucu (mevcut müşteri) ---

def test_provision_drive_endpoint(client, fake_drive):
    login_as(client, MANAGER)
    c = Client(name='Elle Kafe')
    db.session.add(c)
    db.session.commit()

    r = client.post(f'/api/clients/{c.id}/provision-drive',
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['provision']['weeks_created'] == 52
    assert body['client']['drive_meta']['client_folder_link']


def test_provision_drive_endpoint_yetki(client, fake_drive):
    """Yazma yetkisi olmayan rol (designer) provizyon ucunu çağıramaz."""
    login_as(client, DESIGNER)
    r = client.post('/api/clients/1/provision-drive', headers=csrf_headers(client))
    assert r.status_code == 403
