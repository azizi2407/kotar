"""Müşteri onay linki (elle seçim) — /api/sharing/approval-* + public /onay/<token>.

Mevcut `/review/<token>` akışından AYRI bir yol (2026-08-06). Bu dosya iki şeyi
kilitler: (1) modalın aday listesi doğru süzülüyor mu (hafta penceresi, kategori,
"yayınlanmış olan çıkar"), (2) public sayfa yalnız kendi linkinin dosyalarına
izin veriyor mu.
"""
import pytest
from conftest import DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def client_id(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Onay Müşteri"}, headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


def _upload(client_id, week_iso="2026-W21", category="post", name="a.jpg", file_id=None):
    from extensions import db
    from models_sharing import CardUpload
    up = CardUpload(client_id=client_id, week_iso=week_iso, category=category,
                    file_name=name, file_id=file_id or f"fid-{name}-{week_iso}",
                    uploaded_by="2")
    db.session.add(up)
    db.session.commit()
    return up


def _yonetici_ekle():
    """Bildirim alıcısı — `_recipients` management rolündeki UserRef'lerden okur."""
    from extensions import db
    from models import UserRef
    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                           name=MANAGER["name"], role="management"))
    db.session.commit()


def _share(client_id, file_id, week_iso="2026-W21", status="published"):
    from extensions import db
    from models_sharing import Share
    s = Share(client_id=client_id, week_iso=week_iso, kind="post", status=status,
              file_id=file_id)
    db.session.add(s)
    db.session.commit()
    return s


def _link(client, client_id, upload_ids):
    r = client.post("/api/sharing/approval-link",
                    json={"client_id": client_id, "upload_ids": upload_ids},
                    headers=csrf_headers(client))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


# --- aday listesi ---

def test_adaylar_uc_haftayi_kapsar(client, client_id):
    """Pencere: bulunulan hafta ± 1. W19 ve W23 dışarıda kalır."""
    login_as(client, DESIGNER)
    for wk in ("2026-W19", "2026-W20", "2026-W21", "2026-W22", "2026-W23"):
        _upload(client_id, week_iso=wk)
    r = client.get(f"/api/sharing/approval-candidates?client_id={client_id}&week_iso=2026-W21")
    assert r.status_code == 200
    d = r.get_json()
    assert [w["week_iso"] for w in d["weeks"]] == ["2026-W20", "2026-W21", "2026-W22"]
    assert all(len(w["uploads"]) == 1 for w in d["weeks"])


def test_adaylar_yayinlanmisi_eler(client, client_id):
    """Ölçüt YAYIN (proje sahibi): taslak kartı olan dosya listede KALIR, yayınlanan düşer."""
    login_as(client, DESIGNER)
    yayinda = _upload(client_id, name="yayin.jpg")
    taslak = _upload(client_id, name="taslak.jpg")
    kartsiz = _upload(client_id, name="kartsiz.jpg")
    _share(client_id, yayinda.file_id, status="published")
    _share(client_id, taslak.file_id, status="draft")
    r = client.get(f"/api/sharing/approval-candidates?client_id={client_id}&week_iso=2026-W21")
    adlar = {u["file_name"] for w in r.get_json()["weeks"] for u in w["uploads"]}
    assert adlar == {"taslak.jpg", "kartsiz.jpg"}


def test_adaylar_yalniz_post_ve_video(client, client_id):
    login_as(client, DESIGNER)
    for kat in ("post", "video", "story", "linkedin"):
        _upload(client_id, category=kat, name=f"{kat}.jpg")
    r = client.get(f"/api/sharing/approval-candidates?client_id={client_id}&week_iso=2026-W21")
    kinds = {u["category"] for w in r.get_json()["weeks"] for u in w["uploads"]}
    assert kinds == {"post", "video"}


def test_adaylar_silinmisi_gizler(client, client_id):
    from extensions import db
    from models import utcnow
    login_as(client, DESIGNER)
    up = _upload(client_id)
    up.deleted_at = utcnow()
    db.session.commit()
    r = client.get(f"/api/sharing/approval-candidates?client_id={client_id}&week_iso=2026-W21")
    assert all(not w["uploads"] for w in r.get_json()["weeks"])


def test_adaylar_gonderilmisi_isaretler_ama_elemez(client, client_id):
    """`sent_before` yalnız rozet: revize sonrası aynı dosya tekrar gönderilebilir."""
    login_as(client, DESIGNER)
    up = _upload(client_id)
    _link(client, client_id, [up.id])
    r = client.get(f"/api/sharing/approval-candidates?client_id={client_id}&week_iso=2026-W21")
    kayit = r.get_json()["weeks"][1]["uploads"][0]
    assert kayit["sent_before"] is True


def test_adaylar_videografa_kapali(client, client_id):
    login_as(client, VIDEOGRAPHER)
    r = client.get(f"/api/sharing/approval-candidates?client_id={client_id}&week_iso=2026-W21")
    assert r.status_code == 403


# --- link üretimi ---

def test_link_secimi_dondurur(client, client_id):
    """Link üretildikten sonra yüklenen dosya linke SIZMAZ."""
    login_as(client, DESIGNER)
    a = _upload(client_id, name="a.jpg")
    token = _link(client, client_id, [a.id])
    _upload(client_id, name="sonradan.jpg")
    r = client.get(f"/onay/{token}/items")
    assert [i["file_name"] for i in r.get_json()["items"]] == ["a.jpg"]


def test_link_ayni_secimde_tekrar_kullanilir(client, client_id):
    login_as(client, DESIGNER)
    a, b = _upload(client_id, name="a.jpg"), _upload(client_id, name="b.jpg")
    t1 = _link(client, client_id, [a.id, b.id])
    r = client.post("/api/sharing/approval-link",
                    json={"client_id": client_id, "upload_ids": [a.id, b.id]},
                    headers=csrf_headers(client))
    assert r.get_json()["reused"] is True
    assert r.get_json()["token"] == t1


def test_link_secim_degisince_yeni_uretilir(client, client_id):
    login_as(client, DESIGNER)
    a, b = _upload(client_id, name="a.jpg"), _upload(client_id, name="b.jpg")
    t1 = _link(client, client_id, [a.id])
    t2 = _link(client, client_id, [a.id, b.id])
    assert t1 != t2


def test_link_sira_korunur(client, client_id):
    login_as(client, DESIGNER)
    a, b, c = (_upload(client_id, name=n) for n in ("a.jpg", "b.jpg", "c.jpg"))
    token = _link(client, client_id, [c.id, a.id, b.id])
    r = client.get(f"/onay/{token}/items")
    assert [i["file_name"] for i in r.get_json()["items"]] == ["c.jpg", "a.jpg", "b.jpg"]


def test_link_bos_secim_400(client, client_id):
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/approval-link",
                    json={"client_id": client_id, "upload_ids": []},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_link_baska_musterinin_dosyasi_400(client, client_id):
    """Kapsam kaçağı: başka müşterinin yüklemesi seçime konamaz."""
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Diğer"}, headers=csrf_headers(client))
    other = r.get_json()["client"]["id"]
    yabanci = _upload(other)
    r = client.post("/api/sharing/approval-link",
                    json={"client_id": client_id, "upload_ids": [yabanci.id]},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_link_videografa_kapali(client, client_id):
    login_as(client, VIDEOGRAPHER)
    up = _upload(client_id)
    r = client.post("/api/sharing/approval-link",
                    json={"client_id": client_id, "upload_ids": [up.id]},
                    headers=csrf_headers(client))
    assert r.status_code == 403


# --- public sayfa ---

def test_sayfa_ve_items_auth_istemez(client, client_id):
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    with client.session_transaction() as sess:
        sess.clear()
    assert client.get(f"/onay/{token}").status_code == 200
    r = client.get(f"/onay/{token}/items")
    assert r.status_code == 200
    assert r.get_json()["client_name"] == "Onay Müşteri"


def test_not_alani_yonu_iki_ekran_icin_de_yazili(client, client_id):
    """Açıklamadaki yön ifadesi CSS breakpoint'ine bağlı (900px): geniş ekranda
    "sağdaki", dar ekranda "sayfanın altındaki". Biri silinirse metin yalan söyler."""
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    html = client.get(f"/onay/{token}").get_data(as_text=True)
    assert 'class="yon-genis">sağdaki<' in html
    assert 'class="yon-dar">sayfanın altındaki<' in html
    assert ".yon-dar{display:inline;}" in html.replace("\n", "")


def test_gecersiz_token_404(client):
    assert client.get("/onay/yok").status_code == 404
    assert client.get("/onay/yok/items").status_code == 404


def test_iptal_edilen_link_404(client, client_id):
    login_as(client, MANAGER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    r = client.post("/api/sharing/approval-link/revoke", json={"token": token},
                    headers=csrf_headers(client))
    assert r.status_code == 200
    assert client.get(f"/onay/{token}").status_code == 404


def test_silinen_dosya_sayfadan_dusar_sayfa_olmez(client, client_id):
    """Link üretildikten sonra dosya silinebiliyor (videograf kalıcı silme ucu)."""
    from extensions import db
    from models import utcnow
    login_as(client, DESIGNER)
    a, b = _upload(client_id, name="a.jpg"), _upload(client_id, name="b.jpg")
    token = _link(client, client_id, [a.id, b.id])
    a.deleted_at = utcnow()
    db.session.commit()
    r = client.get(f"/onay/{token}/items")
    assert r.status_code == 200
    assert [i["file_name"] for i in r.get_json()["items"]] == ["b.jpg"]


def test_medya_yalniz_kendi_dosyalarina(client, client_id):
    """Keyfi file_id proxy'lenemez — linkin kapsamı dışındaki dosya 404."""
    login_as(client, DESIGNER)
    a = _upload(client_id, name="a.jpg")
    disarida = _upload(client_id, name="b.jpg")
    token = _link(client, client_id, [a.id])
    assert client.get(f"/onay/{token}/media/{disarida.file_id}").status_code == 404
    assert client.get(f"/onay/{token}/stream/{disarida.file_id}").status_code == 404


# --- kararlar ---

def test_onay_ortak_tabloya_yazilir(client, client_id):
    """Karar `card_upload_reviews`'a gider → board rozetleri ve mevcut bildirimler
    bu akışta da çalışır (proje sahibi kararı: ortak tablo)."""
    from models_sharing import UploadReview
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    with client.session_transaction() as sess:
        sess.clear()
    r = client.post(f"/onay/{token}/decision", json={"upload_id": up.id, "action": "approve"})
    assert r.status_code == 200
    rv = UploadReview.query.filter_by(upload_id=up.id).first()
    assert rv.status == "approved"


def test_revize_not_zorunlu(client, client_id):
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    r = client.post(f"/onay/{token}/decision", json={"upload_id": up.id, "action": "revise"})
    assert r.status_code == 400
    r = client.post(f"/onay/{token}/decision",
                    json={"upload_id": up.id, "action": "revise", "note": "rengi açılsın"})
    assert r.status_code == 200
    assert r.get_json()["review"]["note"] == "rengi açılsın"


def test_karar_degistirilebilir(client, client_id):
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    client.post(f"/onay/{token}/decision", json={"upload_id": up.id, "action": "approve"})
    r = client.post(f"/onay/{token}/decision",
                    json={"upload_id": up.id, "action": "revise", "note": "vazgeçtim"})
    assert r.get_json()["review"]["status"] == "revision_requested"


def test_kapsam_disi_dosyaya_karar_verilemez(client, client_id):
    login_as(client, DESIGNER)
    a, disarida = _upload(client_id, name="a.jpg"), _upload(client_id, name="b.jpg")
    token = _link(client, client_id, [a.id])
    r = client.post(f"/onay/{token}/decision",
                    json={"upload_id": disarida.id, "action": "approve"})
    assert r.status_code == 404


def test_gecersiz_islem_400(client, client_id):
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    r = client.post(f"/onay/{token}/decision", json={"upload_id": up.id, "action": "sil"})
    assert r.status_code == 400


# --- not defteri ---

def test_not_kaydedilir_ve_okunur(client, client_id):
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    with client.session_transaction() as sess:
        sess.clear()
    r = client.post(f"/onay/{token}/note", json={"note": "  Logo biraz büyük olsun  "})
    assert r.status_code == 200
    assert client.get(f"/onay/{token}/items").get_json()["note"] == "Logo biraz büyük olsun"


def test_yazma_oturumu_tek_bildirime_toplanir(client, client_id):
    """Alan her duraklamada POST ediyor → art arda değişiklikler 30 dk'lık
    coalesce penceresinde tek bildirime toplanır."""
    from models import Notification
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    _yonetici_ekle()
    client.post(f"/onay/{token}/note", json={"note": "ilk"})
    client.post(f"/onay/{token}/note", json={"note": "ilk ve devamı"})
    client.post(f"/onay/{token}/note", json={"note": "daha da uzun"})
    assert Notification.query.filter_by(kind="approval_note").count() == 1


def test_sonraki_not_okunduktan_sonra_yeniden_bildirilir(client, client_id):
    """2026-08-07 canlı bulgusu: müşteri saatler sonra YENİ not yazdığında kimse
    haber alamıyordu (bildirim link başına yalnız ilk yazımdaydı). Coalesce
    penceresi okunmuş bildirimi kapatır → sonraki not yeniden duyurulur."""
    from extensions import db
    from models import Notification, utcnow
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    _yonetici_ekle()
    client.post(f"/onay/{token}/note", json={"note": "ilk not"})
    for n in Notification.query.filter_by(kind="approval_note").all():
        n.read_at = utcnow()          # yönetici bildirimi okudu
    db.session.commit()
    client.post(f"/onay/{token}/note", json={"note": "günler sonra yazılan yeni not"})
    assert Notification.query.filter_by(kind="approval_note").count() == 2


def test_ayni_metin_tekrar_gonderilirse_bildirim_yok(client, client_id):
    """Sayfa duraklamada aynı metni tekrar POST edebilir — bu bir DEĞİŞİKLİK değil."""
    from extensions import db
    from models import Notification, utcnow
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    _yonetici_ekle()
    client.post(f"/onay/{token}/note", json={"note": "aynı metin"})
    for n in Notification.query.filter_by(kind="approval_note").all():
        n.read_at = utcnow()
    db.session.commit()
    client.post(f"/onay/{token}/note", json={"note": "aynı metin"})
    assert Notification.query.filter_by(kind="approval_note").count() == 1


def test_bos_not_bildirim_uretmez(client, client_id):
    from models import Notification
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    _yonetici_ekle()
    client.post(f"/onay/{token}/note", json={"note": "   "})
    assert Notification.query.filter_by(kind="approval_note").count() == 0


def test_not_uzunlugu_kirpilir(client, client_id):
    from client_approval import NOTE_MAX
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    r = client.post(f"/onay/{token}/note", json={"note": "x" * (NOTE_MAX + 500)})
    assert r.get_json()["saved"] == NOTE_MAX


def test_not_metin_degilse_400(client, client_id):
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    assert client.post(f"/onay/{token}/note", json={"note": 5}).status_code == 400


# --- panelden okuma ---

def test_paneldeki_link_listesi_notu_gosterir(client, client_id):
    """Müşterinin yazdığı notu panelde okumanın tek yolu bu uç."""
    login_as(client, DESIGNER)
    up = _upload(client_id)
    token = _link(client, client_id, [up.id])
    client.post(f"/onay/{token}/note", json={"note": "acele etmeyin"})
    r = client.get(f"/api/sharing/approval-links?client_id={client_id}")
    assert r.status_code == 200
    kayit = r.get_json()["links"][0]
    assert kayit["note"] == "acele etmeyin"
    assert kayit["count"] == 1
