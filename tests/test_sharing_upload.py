"""/api/sharing/upload — dosya yükleme (server→Drive). Drive katmanı mock'lanır."""
import io

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def client_id(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Upload Müşteri"}, headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


@pytest.fixture
def fake_drive(monkeypatch):
    """drive_gateway.upload_file'ı ağa çıkmadan taklit et.

    `grant_anyone_reader` de patch'lenir (2026-07-25: video yüklemesi artık izin
    veriyor) — aksi halde gerçek çağrı env kontrolünde DriveAuthError atıp sessizce
    yutulur; test niyeti belirsiz kalırdı. İzin çağrıları `calls.grants` listesinde."""
    class _Calls(list):
        """Mevcut testler `calls`'ı liste gibi kullanıyor; `grants` ek alanı için
        alt sınıf (düz list'e attribute atanamaz)."""
        grants = ()

    calls = _Calls()
    grants = []

    def fake_upload(folder_id, filename, data, mime):
        # Gerçek uç artık akış (dosya-nesnesi) geçirir; bytes'ı da kabul et.
        payload = data.read() if hasattr(data, "read") else data
        calls.append({"folder_id": folder_id, "filename": filename, "size": len(payload), "mime": mime})
        return {"id": "yeni_file_id", "name": filename, "mimeType": mime, "size": str(len(payload))}

    import drive_gateway
    monkeypatch.setattr(drive_gateway, "upload_file", fake_upload)
    monkeypatch.setattr(drive_gateway, "grant_anyone_reader", lambda fid: grants.append(fid))
    calls.grants = grants          # liste nesnesine bağla (mevcut testler calls'ı liste sanıyor)
    return calls


def _week_folder(client_id, week_number=21, folder_id="FOLDER123"):
    from extensions import db
    from models import ClientWeekFolder
    db.session.add(ClientWeekFolder(client_id=client_id, week_number=week_number,
                                    folder_id=folder_id, name=str(week_number)))
    db.session.commit()


def _post_file(client, client_id, week_iso="2026-W21", category="post", name="a.jpg"):
    return client.post(
        "/api/sharing/upload",
        data={"client_id": str(client_id), "week_iso": week_iso, "category": category,
              "file": (io.BytesIO(b"jpegdata"), name)},
        content_type="multipart/form-data",
        headers=csrf_headers(client))


def test_upload_designer_atanmamis_da_201(client, client_id, fake_drive):
    # Yeni davranış: designer atanmamış müşteriye de yükleyebilir (tam aksiyon).
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, DESIGNER)
    r = _post_file(client, client_id)
    assert r.status_code == 201, r.get_json()


def test_upload_designer_video_201(client, client_id, fake_drive):
    """2026-08-05: designer video da yükleyebilir ve dosya VİDEOGRAF YOLUNDAN geçer.

    Rol kapısının açılması tek başına yetmez — asıl şart video muamelesi: Drive'da
    "bağlantıya sahip herkes" izni ve tarayıcı uyumlu türev (`web_variant`) işi.
    Bu ikisi kategoriye bağlı, role değil; test bunu kilitliyor."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, DESIGNER)
    jobs = []
    import jobqueue
    from unittest.mock import patch
    with patch.object(jobqueue, "enqueue", lambda kind, payload, **kw: jobs.append((kind, payload))):
        r = _post_file(client, client_id, category="video", name="v.mp4")
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["upload"]["category"] == "video"
    assert fake_drive.grants == ["yeni_file_id"]          # anyone-reader izni verildi
    assert [k for k, _ in jobs] == ["web_variant"]        # transkod kuyruğa girdi


def test_upload_boyut_asimi_413(client, client_id, fake_drive, monkeypatch):
    # Ürün limiti (MAX_UPLOAD_BYTES) küçültülür → 8 baytlık dosya bile aşar → anlamlı 413.
    import sharing
    monkeypatch.setattr(sharing, "MAX_UPLOAD_BYTES", 3)
    _week_folder(client_id, 21, "F")
    r = _post_file(client, client_id)  # 'jpegdata' = 8 bayt > 3
    assert r.status_code == 413
    assert "500 MB" in r.get_json()["error"]
    assert fake_drive == []  # sınır aşımında Drive'a hiç gidilmez


def test_upload_dosyasiz_400(client, client_id, fake_drive):
    r = client.post("/api/sharing/upload",
                    data={"client_id": str(client_id), "week_iso": "2026-W21"},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 400


def test_upload_klasor_yok_400(client, client_id, fake_drive):
    # hafta klasörü yok + drive_meta yok → oluşturulamaz
    r = _post_file(client, client_id)
    assert r.status_code == 400
    assert fake_drive == []  # Drive'a hiç gidilmedi


def test_upload_kayit_olusturur(client, client_id, fake_drive):
    _week_folder(client_id, 21, "FOLDER123")
    r = _post_file(client, client_id, name="görsel.jpg")
    assert r.status_code == 201, r.get_json()
    up = r.get_json()["upload"]
    assert up["file_id"] == "yeni_file_id"
    assert up["file_name"] == "görsel.jpg"
    assert up["category"] == "post"
    # doğru klasöre gitti
    assert fake_drive[0]["folder_id"] == "FOLDER123"
    # uploads listesinde görünür
    ups = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso=2026-W21").get_json()["uploads"]
    assert any(u["file_id"] == "yeni_file_id" for u in ups)


def test_upload_hafta_klasoru_yoksa_drive_meta_kokunden_olusturur(client, client_id, fake_drive, monkeypatch):
    from extensions import db
    from models import Client, ClientWeekFolder
    import drive_gateway
    # müşteriye kök klasör linki ver
    c = db.session.get(Client, client_id)
    c.drive_meta = {"client_folder_link": "https://drive.google.com/drive/folders/ROOT999"}
    db.session.commit()
    created = {}

    def fake_ensure(parent_id, name):
        created["parent"] = parent_id
        created["name"] = name
        return "YENI_HAFTA_FOLDER"
    monkeypatch.setattr(drive_gateway, "ensure_subfolder", fake_ensure)

    r = _post_file(client, client_id, week_iso="2026-W30")
    assert r.status_code == 201
    assert created == {"parent": "ROOT999", "name": "30"}
    assert fake_drive[0]["folder_id"] == "YENI_HAFTA_FOLDER"
    # ClientWeekFolder kaydı da oluşmuş olmalı
    wf = ClientWeekFolder.query.filter_by(client_id=client_id, week_number=30).first()
    assert wf and wf.folder_id == "YENI_HAFTA_FOLDER"


VIDEOGRAPHER = {"sub": "7", "email": "video@test.com", "name": "Videocu", "role": "videographer"}


def test_upload_videographer_atanmamis_video_201(client, client_id, fake_drive):
    """2026-07-21 designer paritesi: videografçı ATANMAMIŞ müşteriye de video yükleyebilir."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="video", name="v.mp4")
    assert r.status_code == 201, r.get_json()


def test_upload_videographer_video_disi_403(client, client_id, fake_drive):
    """Videografçı yalnız video kategorisi yükleyebilir; post yasak."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="post")
    assert r.status_code == 403


def test_videographer_cards_rol_ve_assigned(client, client_id):
    """/videographer/cards: videographer erişir, atanmışlık bayrağı doğru; designer 403."""
    from extensions import db
    from models import ClientTeamAssignment
    login_as(client, VIDEOGRAPHER)
    r = client.get("/api/sharing/videographer/cards?week_iso=2026-W21")
    assert r.status_code == 200
    rows = r.get_json()["rows"]
    assert all(row["assigned"] is False for row in rows)   # henüz atama yok
    db.session.add(ClientTeamAssignment(client_id=client_id, role_slot="videographer_shoot",
                                        user_id=VIDEOGRAPHER["sub"]))
    db.session.commit()
    rows = client.get("/api/sharing/videographer/cards?week_iso=2026-W21").get_json()["rows"]
    assert any(row["assigned"] and row["client"]["id"] == client_id for row in rows)
    login_as(client, DESIGNER)
    assert client.get("/api/sharing/videographer/cards?week_iso=2026-W21").status_code == 403


def test_board_video_uploads_karti(client, client_id, fake_drive):
    """Yönetim board satırı bu haftanın video yüklemelerini `video_uploads` ile döner."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="video", name="tanitim.mp4")
    assert r.status_code == 201
    login_as(client, MANAGER)
    rows = client.get("/api/sharing/cards?week_iso=2026-W21").get_json()["rows"]
    row = next(x for x in rows if x["client"]["id"] == client_id)
    assert len(row["video_uploads"]) == 1
    assert row["video_uploads"][0]["file_name"] == "tanitim.mp4"
    assert row["video_uploads"][0]["file_id"]


def test_upload_lokal_kopya_yazar(client, client_id, fake_drive):
    # Drive yüklemesi başarılıysa aynı istekte sunucuda 21 günlük kopya oluşur.
    import media_store
    _week_folder(client_id, 21, "FOLDER123")
    r = _post_file(client, client_id, name="lokal.jpg")
    assert r.status_code == 201
    path = media_store.find_original("yeni_file_id")  # fake_drive'ın döndürdüğü id
    assert path is not None
    with open(path, "rb") as f:
        assert f.read() == b"jpegdata"


# --- Drive izni (2026-07-25): kopyalanan link ajans dışında da açılsın ---

def test_video_yuklemesi_izin_alir(client, client_id, fake_drive):
    from conftest import VIDEOGRAPHER
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    r = _post_file(client, client_id, category="video", name="a.mp4")
    assert r.status_code == 201, r.get_json()
    assert list(fake_drive.grants) == ["yeni_file_id"]


def test_gorsel_yuklemesi_izin_almaz(client, client_id, fake_drive):
    """Görseller proxy'den (/api/sharing/media/<id>) gidiyor — public yapmaya gerek yok."""
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, DESIGNER)
    assert _post_file(client, client_id, category="post").status_code == 201
    assert list(fake_drive.grants) == []


def test_izin_hatasi_yuklemeyi_bozmaz(client, client_id, fake_drive, monkeypatch):
    """İzin en-iyi-çaba: Drive izin çağrısı patlasa da 201 dönmeli."""
    import drive_gateway
    from conftest import VIDEOGRAPHER

    def boom(fid):
        raise drive_gateway.DriveError("izin yok")

    monkeypatch.setattr(drive_gateway, "grant_anyone_reader", boom)
    _week_folder(client_id, 21, "FOLDER123")
    login_as(client, VIDEOGRAPHER)
    assert _post_file(client, client_id, category="video", name="a.mp4").status_code == 201


# --- `used` bayrağı: kartı olan dosya ShareModal seçicisinde gizlenir ------
# ShareModal'ın dosya havuzu bu bayrağa göre süzer; aynı dosyaya ikinci kart
# açılıp çift içerik üretilmesin diye (proje sahibi 2026-07-29, TASLAK kart da sayılır).
# Yüklemeler doğrudan kurulur: `fake_drive` her çağrıda aynı `file_id`'yi
# döndürdüğü için gerçek upload ucuyla farklı dosyalar ayırt edilemezdi.

def _card_upload(client_id, file_id, week="2026-W31", name=None, category="post"):
    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    cu = CardUpload(client_id=client_id, week_iso=week, category=category,
                    file_id=file_id, file_name=name or f"{file_id}.jpg",
                    mime_type="image/jpeg", file_size=10, uploaded_at=utcnow())
    db.session.add(cu)
    db.session.commit()
    return cu


def _mk_share(client, client_id, file_id, week="2026-W31"):
    r = client.post("/api/sharing/shares", headers=csrf_headers(client), json={
        "client_id": client_id, "week_iso": week, "kind": "post", "file_id": file_id})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["share"]


def _uploads(client, client_id, week="2026-W31"):
    r = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso={week}")
    assert r.status_code == 200
    return {u["file_id"]: u for u in r.get_json()["uploads"]}


def test_used_kartsiz_dosyada_false(client, client_id):
    login_as(client, MANAGER)
    _card_upload(client_id, "serbest")
    assert _uploads(client, client_id)["serbest"]["used"] is False


def test_used_taslak_kart_da_isaretler(client, client_id):
    """Taslak kart da 'kullanılmış' sayılır — yayına çıkmamış olması dosyayı
    yeniden seçilebilir yapmaz, aksi halde iki kart aynı dosyayı gösterirdi."""
    login_as(client, MANAGER)
    _card_upload(client_id, "taslakli")
    s = _mk_share(client, client_id, "taslakli")
    assert s["status"] == "draft"
    assert _uploads(client, client_id)["taslakli"]["used"] is True


def test_used_kart_silinince_geri_doner(client, client_id):
    """Kart silinince dosya yeniden seçilebilir olmalı — `used` soft-delete'i sayar."""
    login_as(client, MANAGER)
    _card_upload(client_id, "geridonen")
    sid = _mk_share(client, client_id, "geridonen")["id"]
    assert _uploads(client, client_id)["geridonen"]["used"] is True
    assert client.delete(f"/api/sharing/shares/{sid}",
                         headers=csrf_headers(client)).status_code == 200
    assert _uploads(client, client_id)["geridonen"]["used"] is False


def test_used_baska_haftadaki_kart_da_sayilir(client, client_id):
    """Dosya `move_files` ile başka haftaya taşınmış olabilir; kartı orada durur
    ama yine kullanılmıştır → sorgu HAFTA BAĞIMSIZ."""
    login_as(client, MANAGER)
    _card_upload(client_id, "tasinan", week="2026-W31")
    _mk_share(client, client_id, "tasinan", week="2026-W32")
    assert _uploads(client, client_id, week="2026-W31")["tasinan"]["used"] is True


def test_used_diger_dosyalari_etkilemez(client, client_id):
    login_as(client, MANAGER)
    _card_upload(client_id, "kullanilan")
    _card_upload(client_id, "bos")
    _mk_share(client, client_id, "kullanilan")
    ups = _uploads(client, client_id)
    assert ups["kullanilan"]["used"] is True
    assert ups["bos"]["used"] is False


def test_uploads_sorgu_sayisi_dosya_sayisindan_bagimsiz(client, client_id):
    """`used` yükleme başına `Share.query` ile çözülseydi N+1 olurdu — tek toplu
    sorgu şart. 3 dosya ile 12 dosyanın sorgu sayısı AYNI olmalı."""
    from sqlalchemy import event

    from extensions import db
    login_as(client, MANAGER)

    def say(n, week):
        for i in range(n):
            _card_upload(client_id, f"{week}-d{i}", week=week)
        sayac = []
        eng = db.engine

        def before(conn, cur, stmt, params, ctx, many):  # noqa: ANN001
            sayac.append(stmt)

        event.listen(eng, "before_cursor_execute", before)
        try:
            client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso={week}")
        finally:
            event.remove(eng, "before_cursor_execute", before)
        return len(sayac)

    assert say(3, "2026-W40") == say(12, "2026-W41")


def test_uploads_bos_listede_share_sorgusu_atilmaz(client, client_id):
    """Hiç dosya yoksa `IN ()` üretmemek için Share sorgusu atlanır."""
    login_as(client, MANAGER)
    r = client.get(f"/api/sharing/uploads?client_id={client_id}&week_iso=2026-W52")
    assert r.get_json()["uploads"] == []
