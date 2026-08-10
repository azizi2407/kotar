"""Müşteri medya sayfası uçları — GET /clients/<id>/media + POST /uploads/move-week.

Drive katmanı mock'lanır (ağa çıkış yok). Taşımanın Drive tarafı `move_file`
çağrılarıyla, panel tarafı `week_iso`/`moved_from_week_iso` alanlarıyla doğrulanır.
"""
import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def client_id(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Medya Müşteri"},
                    headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


@pytest.fixture
def fake_drive(monkeypatch):
    """`dg.available` + `dg.move_file` taklidi. Çağrılar listede birikir."""
    calls = []

    def fake_move(file_id, new_parent, old_parent=None):
        calls.append({"file_id": file_id, "to": new_parent, "from": old_parent})
        return {"id": file_id, "name": "x.jpg", "mimeType": "image/jpeg", "size": "10"}

    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "move_file", fake_move)
    return calls


def _week_folder(client_id, week_number, folder_id):
    from extensions import db
    from models import ClientWeekFolder
    db.session.add(ClientWeekFolder(client_id=client_id, week_number=week_number,
                                    folder_id=folder_id, name=str(week_number)))
    db.session.commit()


def _upload(client_id, week_iso, file_id="F1", category="post", name="a.jpg",
            deleted=False):
    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    up = CardUpload(client_id=client_id, week_iso=week_iso, file_id=file_id,
                    file_name=name, category=category, mime_type="image/jpeg",
                    uploaded_at=utcnow(), deleted_at=utcnow() if deleted else None)
    db.session.add(up)
    db.session.commit()
    return up.id


def _share(client_id, week_iso, file_id, deleted=False):
    from extensions import db
    from models import utcnow
    from models_sharing import Share
    s = Share(client_id=client_id, week_iso=week_iso, file_id=file_id, kind="post",
              status="draft", deleted_at=utcnow() if deleted else None)
    db.session.add(s)
    db.session.commit()
    return s.id


def _weeks(payload):
    return {w["week_iso"]: w["uploads"] for w in payload["weeks"]}


# --- GET /clients/<id>/media ---

def test_medya_designer_erisebilir(client, client_id):
    _upload(client_id, "2026-W20")
    login_as(client, DESIGNER)
    r = client.get(f"/api/sharing/clients/{client_id}/media")
    assert r.status_code == 200, r.get_json()
    assert _weeks(r.get_json())["2026-W20"]


def test_medya_content_creator_403(client, client_id):
    login_as(client, CONTENT_CREATOR)
    r = client.get(f"/api/sharing/clients/{client_id}/media")
    assert r.status_code == 403


def test_medya_oturumsuz_401(client, client_id):
    with client.session_transaction() as sess:
        sess.clear()
    r = client.get(f"/api/sharing/clients/{client_id}/media")
    assert r.status_code == 401


def test_medya_bilinmeyen_musteri_404(client, client_id):
    login_as(client, MANAGER)
    r = client.get("/api/sharing/clients/999999/media")
    assert r.status_code == 404


def test_medya_haftalar_azalan_sirali(client, client_id):
    _upload(client_id, "2026-W20", file_id="A")
    _upload(client_id, "2026-W22", file_id="B")
    login_as(client, MANAGER)
    order = [w["week_iso"] for w in client.get(
        f"/api/sharing/clients/{client_id}/media").get_json()["weeks"]]
    assert order == sorted(order, reverse=True)


def test_medya_bos_komsu_haftalar_da_doner(client, client_id):
    """Dosyası olmayan komşu haftalar sürükleme HEDEFİ olarak render edilmeli."""
    import sharing
    login_as(client, MANAGER)
    weeks = _weeks(client.get(f"/api/sharing/clients/{client_id}/media").get_json())
    cur = sharing._current_week_iso()
    assert weeks[cur] == []
    assert sharing._shift_week(cur, sharing.MEDIA_WEEK_WINDOW) in weeks
    assert sharing._shift_week(cur, -sharing.MEDIA_WEEK_WINDOW) in weeks


def test_medya_silinmis_yukleme_gorunmez(client, client_id):
    _upload(client_id, "2026-W20", file_id="A")
    _upload(client_id, "2026-W20", file_id="B", deleted=True)
    login_as(client, MANAGER)
    ups = _weeks(client.get(f"/api/sharing/clients/{client_id}/media").get_json())["2026-W20"]
    assert [u["file_id"] for u in ups] == ["A"]


def test_medya_used_bayragi(client, client_id):
    _upload(client_id, "2026-W20", file_id="A")
    _upload(client_id, "2026-W20", file_id="B")
    _upload(client_id, "2026-W20", file_id="C")
    _share(client_id, "2026-W20", "A")
    _share(client_id, "2026-W20", "C", deleted=True)   # silinmiş kart sayılmaz
    login_as(client, MANAGER)
    ups = _weeks(client.get(f"/api/sharing/clients/{client_id}/media").get_json())["2026-W20"]
    used = {u["file_id"]: u["used"] for u in ups}
    assert used == {"A": True, "B": False, "C": False}


def test_medya_baska_musterinin_yuklemesi_sizmaz(client, client_id):
    login_as(client, MANAGER)
    other = client.post("/api/clients", json={"name": "Öteki"},
                        headers=csrf_headers(client)).get_json()["client"]["id"]
    _upload(client_id, "2026-W20", file_id="BENIM")
    _upload(other, "2026-W20", file_id="ONUN")
    ups = _weeks(client.get(f"/api/sharing/clients/{client_id}/media").get_json())["2026-W20"]
    assert [u["file_id"] for u in ups] == ["BENIM"]


# --- POST /uploads/move-week ---

def test_tasima_hafta_alanlarini_gunceller(client, client_id, fake_drive):
    _week_folder(client_id, 20, "KLASOR20")
    _week_folder(client_id, 22, "KLASOR22")
    uid = _upload(client_id, "2026-W20", file_id="A")
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"moved": 1, "errors": []}

    from models_sharing import CardUpload
    from extensions import db
    up = db.session.get(CardUpload, uid)
    assert up.week_iso == "2026-W22"
    assert up.moved_from_week_iso == "2026-W20"
    assert up.moved_at is not None
    assert fake_drive == [{"file_id": "A", "to": "KLASOR22", "from": "KLASOR20"}]


def test_tasima_designer_yetkili(client, client_id, fake_drive):
    _week_folder(client_id, 22, "KLASOR22")
    uid = _upload(client_id, "2026-W20", file_id="A")
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()


def test_tasima_content_creator_403(client, client_id, fake_drive):
    uid = _upload(client_id, "2026-W20", file_id="A")
    login_as(client, CONTENT_CREATOR)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 403


def test_tasima_karisik_kaynak_haftalar_tek_istekte(client, client_id, fake_drive):
    """Bu ucun `move-files`'tan farkı: kaynak hafta HER KAYITTAN okunur."""
    _week_folder(client_id, 20, "KLASOR20")
    _week_folder(client_id, 21, "KLASOR21")
    _week_folder(client_id, 22, "KLASOR22")
    a = _upload(client_id, "2026-W20", file_id="A")
    b = _upload(client_id, "2026-W21", file_id="B")
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [a, b], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.get_json()["moved"] == 2
    assert {c["from"] for c in fake_drive} == {"KLASOR20", "KLASOR21"}


def test_tasima_kaynak_klasor_kaydi_yoksa_none_gecer(client, client_id, fake_drive):
    """Klasör kaydı yoksa `move_file` dosyanın mevcut parent'ını kendisi çözer."""
    _week_folder(client_id, 22, "KLASOR22")
    uid = _upload(client_id, "2026-W20", file_id="A")
    login_as(client, MANAGER)
    client.post("/api/sharing/uploads/move-week",
                json={"upload_ids": [uid], "to_week_iso": "2026-W22"},
                headers=csrf_headers(client))
    assert fake_drive[0]["from"] is None


def test_tasima_drive_hatasi_kismi_basari(client, client_id, fake_drive, monkeypatch):
    """Tek dosya patlayınca toplu taşıma çökmez: kalanlar taşınır, hata döner."""
    _week_folder(client_id, 22, "KLASOR22")
    a = _upload(client_id, "2026-W20", file_id="PATLA", name="kotu.jpg")
    b = _upload(client_id, "2026-W20", file_id="IYI", name="iyi.jpg")

    import drive_gateway

    def boom(file_id, new_parent, old_parent=None):
        if file_id == "PATLA":
            raise drive_gateway.DriveError("taşınamadı")
        return {"id": file_id}

    monkeypatch.setattr(drive_gateway, "move_file", boom)
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [a, b], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    body = r.get_json()
    assert body["moved"] == 1
    assert len(body["errors"]) == 1 and "kotu.jpg" in body["errors"][0]

    from extensions import db
    from models_sharing import CardUpload
    assert db.session.get(CardUpload, a).week_iso == "2026-W20"   # taşınmadı
    assert db.session.get(CardUpload, b).week_iso == "2026-W22"   # taşındı


def test_tasima_capraz_musteri_400(client, client_id, fake_drive):
    login_as(client, MANAGER)
    other = client.post("/api/clients", json={"name": "Öteki"},
                        headers=csrf_headers(client)).get_json()["client"]["id"]
    a = _upload(client_id, "2026-W20", file_id="A")
    b = _upload(other, "2026-W20", file_id="B")
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [a, b], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_tasima_gecersiz_hafta_400(client, client_id, fake_drive):
    uid = _upload(client_id, "2026-W20", file_id="A")
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "abc"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_tasima_bos_liste_400(client, client_id, fake_drive):
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_tasima_bilinmeyen_yukleme_404(client, client_id, fake_drive):
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [999999], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 404


def test_tasima_silinmis_yukleme_tasinmaz(client, client_id, fake_drive):
    uid = _upload(client_id, "2026-W20", file_id="A", deleted=True)
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 404


def test_tasima_ayni_haftaya_drive_cagrisi_yapmaz(client, client_id, fake_drive):
    uid = _upload(client_id, "2026-W20", file_id="A")
    _week_folder(client_id, 20, "KLASOR20")
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "2026-W20"},
                    headers=csrf_headers(client))
    assert r.get_json() == {"moved": 0, "errors": []}
    assert fake_drive == []


def test_tasima_hedef_klasor_yoksa_400(client, client_id, monkeypatch):
    """Hedef hafta klasörü yok ve Drive'da oluşturulamıyorsa taşıma reddedilir."""
    import drive_gateway
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "ensure_subfolder",
                        lambda *a, **k: (_ for _ in ()).throw(drive_gateway.DriveError("yok")))
    uid = _upload(client_id, "2026-W20", file_id="A")
    login_as(client, MANAGER)
    r = client.post("/api/sharing/uploads/move-week",
                    json={"upload_ids": [uid], "to_week_iso": "2026-W22"},
                    headers=csrf_headers(client))
    assert r.status_code == 400
