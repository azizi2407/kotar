"""Cutover C2 — new client Drive folder tree provisioning tests.

NO REAL Drive calls: `client_provision.dg` (drive_gateway) is mocked.
Verified behaviors:
  - client create → the expected folder tree (root + weeks 1..52) calls are made,
  - idempotent: an existing folder is not recreated (a repeat provision adds 0 new rows),
  - a Drive error does NOT BLOCK client create (best-effort; the error is swallowed).
"""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

import client_provision as cp
from extensions import db
from models import Client, ClientWeekFolder, Notification, UserRef

CONTENT_ROOT = 'root_content_id'


class FakeDrive:
    """Imitates drive_gateway.ensure_subfolder — find-or-create (idempotent)."""

    def __init__(self):
        self.tree = {}          # (parent, name) -> id
        self.calls = []         # [(parent, name), ...] in order
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
    """Make Drive identity look 'available', set the root, and mock ensure_subfolder."""
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


# --- Provisioning: folder tree is created ---

def test_create_provizyon_klasor_agaci(client, fake_drive):
    login_as(client, MANAGER)
    data = _create(client)

    # root folder under the content root, named after the client
    assert fake_drive.calls[0] == (CONTENT_ROOT, 'Örnek Kafe')
    client_root = fake_drive.tree[(CONTENT_ROOT, 'Örnek Kafe')]
    # then weeks 1..52 subfolders, under the client root, in order
    assert fake_drive.calls[1:] == [(client_root, str(wn)) for wn in range(1, 53)]

    # drive_meta root link + 52 ClientWeekFolder rows written
    assert data['drive_meta']['client_folder_link'].endswith(client_root)
    rows = ClientWeekFolder.query.filter_by(client_id=data['id']).all()
    assert {r.week_number for r in rows} == set(range(1, 53))


# --- Idempotent: repeat provisioning produces no new rows ---

def test_provizyon_idempotent(client, fake_drive):
    login_as(client, MANAGER)
    data = _create(client)
    c = db.session.get(Client, data['id'])

    before = fake_drive.tree.copy()
    result = cp.provision_client_folders(c)

    assert result == {'client_folder_id': before[(CONTENT_ROOT, 'Örnek Kafe')],
                      'weeks_created': 0}
    # no new folder ids were generated (all resolved via find)
    assert fake_drive.tree == before
    # week row count is still 52 (no duplicates)
    assert ClientWeekFolder.query.filter_by(client_id=c.id).count() == 52


def test_provizyon_mevcut_kok_yeni_yaratmaz(client, fake_drive):
    """If the root is already recorded in drive_meta, no new root is OPENED under the content root."""
    login_as(client, MANAGER)
    c = Client(name='Göçen Müşteri',
               drive_meta={'client_folder_link':
                           'https://drive.google.com/drive/folders/mevcut_kok'})
    db.session.add(c)
    db.session.commit()

    cp.provision_client_folders(c)

    # NO call to create a name-based root under the content root; week folders go under the existing root
    assert (CONTENT_ROOT, 'Göçen Müşteri') not in fake_drive.calls
    assert fake_drive.calls == [('mevcut_kok', str(wn)) for wn in range(1, 53)]


# --- Best-effort: a Drive error doesn't block create ---

def test_drive_hatasi_create_bloklamaz(client, fake_drive, monkeypatch):
    def boom(parent, name):
        raise cp.dg.DriveError('drive erişilemedi')

    monkeypatch.setattr(cp.dg, 'ensure_subfolder', boom)
    login_as(client, MANAGER)

    r = client.post('/api/clients', json={'name': 'Hata Kafe'},
                    headers=csrf_headers(client))
    # client is still created (201), only Drive provisioning is skipped
    assert r.status_code == 201
    data = r.get_json()['client']
    assert data['drive_meta'] is None
    c = db.session.get(Client, data['id'])
    assert c is not None
    assert ClientWeekFolder.query.filter_by(client_id=c.id).count() == 0


def test_drive_hatasi_provision_failed_bildirimi_gonderir(client, fake_drive, monkeypatch):
    """On a Drive error, a 'provision_failed' notification is sent to management (best-effort
    but not silent — Observer finding: this behavior wasn't covered by any test)."""
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
    assert n.title == 'Could not set up Drive folder'
    assert 'Hata Kafe' in n.body
    assert n.link == f"/panel/clients/{data['id']}"


# --- Skip silently if identity/root is missing ---

def test_drive_kimligi_yoksa_atlanir(client, monkeypatch):
    """If Drive identity isn't configured, provisioning is skipped without making any calls."""
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


# --- Manual completion endpoint (existing client) ---

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
    """A role without write permission (designer) cannot call the provisioning endpoint."""
    login_as(client, DESIGNER)
    r = client.post('/api/clients/1/provision-drive', headers=csrf_headers(client))
    assert r.status_code == 403
