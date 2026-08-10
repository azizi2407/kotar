"""Public onay sayfası (/review/<token>) — auth'suz, token korumalı.

SÖZLEŞME (proje sahibi 2026-07-24): sayfa sharing board paylaşımlarını DEĞİL, tasarımcının
o hafta yüklediği **post + video** dosyalarını (`card_uploads`) gösterir; caption/
hashtag gönderilmez; personel (yönetim/tasarımcı) içerikleri sayfadan kaldırabilir.
"""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"


def _mk_upload(cid, file_id, category="post", name=None, week=WK):
    from extensions import db
    from models_sharing import CardUpload
    up = CardUpload(client_id=cid, week_iso=week, category=category,
                    file_id=file_id, file_name=name or f"{file_id}.jpg")
    db.session.add(up)
    db.session.commit()
    return up.id


@pytest.fixture
def setup(client):
    """management ile müşteri + 1 post + 1 video yüklemesi + review-link kur.
    Döner: (token, client_id, post_upload_id, video_upload_id)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Review Müşteri"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    post_id = _mk_upload(cid, "f1", "post")
    video_id = _mk_upload(cid, "vid9", "video")
    token = client.post("/api/sharing/review-link",
                        json={"client_id": cid, "week_iso": WK},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()  # public uçlar oturumsuz test edilir
    return token, cid, post_id, video_id


# --- token güvenliği ---------------------------------------------------------
def test_gecersiz_token_404(client):
    assert client.get("/review/yok/shares").status_code == 404


def test_iptal_edilmis_token_404(client, setup):
    token, _, _, _ = setup
    login_as(client, MANAGER)
    client.post("/api/sharing/review-link/revoke", json={"token": token},
                headers=csrf_headers(client))
    with client.session_transaction() as s:
        s.clear()
    assert client.get(f"/review/{token}/shares").status_code == 404


def test_review_sayfasi_html(client, setup):
    r = client.get(f"/review/{setup[0]}")
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert "noindex" in r.headers.get("X-Robots-Tag", "")


# --- içerik kaynağı: YÜKLEMELER ---------------------------------------------
def test_yuklemeler_gosterilir(client, setup):
    """Sayfa tasarımcının o hafta yüklediklerini gösterir."""
    token, _, post_id, video_id = setup
    d = client.get(f"/review/{token}/shares").get_json()
    ids = [s["id"] for s in d["shares"]]
    assert post_id in ids and video_id in ids and len(ids) == 2
    assert d["client_name"] == "Review Müşteri" and d["week_iso"] == WK


def test_paylasim_degil_yukleme_gosterilir(client, setup):
    """KRİTİK: sharing board'da 'paylaşıldı' işaretlenen Share'ler sayfayı ETKİLEMEZ —
    kaynak yalnız card_uploads (proje sahibi 2026-07-24)."""
    token, cid, post_id, video_id = setup
    login_as(client, MANAGER)
    sh = client.post("/api/sharing/shares",
                     json={"client_id": cid, "week_iso": WK, "kind": "post", "file_id": "SHARE1"},
                     headers=csrf_headers(client)).get_json()["share"]
    client.post(f"/api/sharing/shares/{sh['id']}/publish", headers=csrf_headers(client))
    with client.session_transaction() as s:
        s.clear()
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert sorted(ids) == sorted([post_id, video_id])  # share eklenmedi/çıkarmadı


def test_story_ve_linkedin_gizli(client, setup):
    """Yalnız post + video görünür."""
    token, cid, post_id, video_id = setup
    _mk_upload(cid, "st1", "story")
    _mk_upload(cid, "li1", "linkedin")
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert sorted(ids) == sorted([post_id, video_id])


def test_silinen_yukleme_gorunmez(client, setup):
    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    token, _, post_id, video_id = setup
    db.session.get(CardUpload, post_id).deleted_at = utcnow()
    db.session.commit()
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert ids == [video_id]


def test_baska_hafta_gorunmez(client, setup):
    token, cid, post_id, video_id = setup
    _mk_upload(cid, "other", "post", week="2026-W22")
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert sorted(ids) == sorted([post_id, video_id])


def test_caption_hashtag_musteriye_gonderilmez(client, setup):
    """Sayfa yalnız görsel/video gösterir — caption/hashtag ne JSON'da ne şablonda."""
    token, _, _, _ = setup
    r = client.get(f"/review/{token}/shares")
    assert all("caption_text" not in s and "hashtag_text" not in s
               for s in r.get_json()["shares"])
    page = client.get(f"/review/{token}").get_data(as_text=True)
    assert "caption_text" not in page and "hashtag_text" not in page


def test_medya_tam_boyut_servis_edilir(client, setup):
    page = client.get(f"/review/{setup[0]}").get_data(as_text=True)
    assert "?size=full" in page and "srcset" not in page


def test_video_drive_linki_verilir_gorsele_verilmez(client, setup):
    token, _, post_id, video_id = setup
    items = {s["id"]: s for s in client.get(f"/review/{token}/shares").get_json()["shares"]}
    assert items[video_id]["drive_url"] == "https://drive.google.com/file/d/vid9/view"
    assert items[post_id]["drive_url"] is None


# --- personel: sayfadan kaldırma --------------------------------------------
def test_musteri_kaldirma_bilgisi_gormez(client, setup):
    """Oturumsuz (müşteri) görünümde can_manage=false ve excluded alanı yok."""
    d = client.get(f"/review/{setup[0]}/shares").get_json()
    assert d["can_manage"] is False
    assert all("excluded" not in s for s in d["shares"])


def test_personel_can_manage_gorur(client, setup):
    token, _, _, _ = setup
    login_as(client, MANAGER)
    d = client.get(f"/review/{token}/shares").get_json()
    assert d["can_manage"] is True
    assert all("excluded" in s for s in d["shares"])


@pytest.mark.parametrize("role", [MANAGER, DESIGNER])
def test_yonetim_ve_tasarimci_kaldirabilir(client, setup, role):
    token, _, post_id, video_id = setup
    login_as(client, role)
    r = client.post(f"/review/{token}/exclude",
                    json={"upload_id": post_id, "excluded": True},
                    headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["excluded"] is True
    with client.session_transaction() as s:
        s.clear()
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert ids == [video_id]  # müşteri artık görmüyor


def test_kaldirma_geri_alinabilir(client, setup):
    token, _, post_id, video_id = setup
    login_as(client, MANAGER)
    client.post(f"/review/{token}/exclude", json={"upload_id": post_id, "excluded": True},
                headers=csrf_headers(client))
    # personel görünümünde kaldırılmış olarak durur
    items = {s["id"]: s for s in client.get(f"/review/{token}/shares").get_json()["shares"]}
    assert items[post_id]["excluded"] is True
    client.post(f"/review/{token}/exclude", json={"upload_id": post_id, "excluded": False},
                headers=csrf_headers(client))
    with client.session_transaction() as s:
        s.clear()
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert sorted(ids) == sorted([post_id, video_id])


def test_kaldirma_yuklemeyi_silmez(client, setup):
    """Yıkıcı değil: card_uploads satırı durur (board'da görünmeye devam eder)."""
    from extensions import db
    from models_sharing import CardUpload
    token, _, post_id, _ = setup
    login_as(client, MANAGER)
    client.post(f"/review/{token}/exclude", json={"upload_id": post_id, "excluded": True},
                headers=csrf_headers(client))
    up = db.session.get(CardUpload, post_id)
    assert up is not None and up.deleted_at is None


def test_kaldirma_oturumsuz_401(client, setup):
    token, _, post_id, _ = setup
    r = client.post(f"/review/{token}/exclude", json={"upload_id": post_id})
    assert r.status_code == 401


def test_kaldirma_csrfsiz_403(client, setup):
    token, _, post_id, _ = setup
    login_as(client, MANAGER)
    client.get("/api/session")  # token üretilsin ama header'a KOYMA
    r = client.post(f"/review/{token}/exclude", json={"upload_id": post_id})
    assert r.status_code == 403


def test_kaldirilan_icerige_onay_verilemez(client, setup):
    token, _, post_id, _ = setup
    login_as(client, MANAGER)
    client.post(f"/review/{token}/exclude", json={"upload_id": post_id, "excluded": True},
                headers=csrf_headers(client))
    with client.session_transaction() as s:
        s.clear()
    r = client.post(f"/review/{token}/action", json={"upload_id": post_id, "action": "approve"})
    assert r.status_code == 404


# --- onay / revize (yükleme bazında) ----------------------------------------
def test_action_approve(client, setup):
    from extensions import db
    from models_sharing import UploadReview
    token, _, post_id, _ = setup
    r = client.post(f"/review/{token}/action", json={"upload_id": post_id, "action": "approve"})
    assert r.status_code == 200 and r.get_json()["review"]["status"] == "approved"
    rv = UploadReview.query.filter_by(upload_id=post_id).one()
    assert rv.status == "approved"
    db.session.remove()


def test_action_revise_not_zorunlu(client, setup):
    token, _, post_id, _ = setup
    assert client.post(f"/review/{token}/action",
                       json={"upload_id": post_id, "action": "revise"}).status_code == 400
    r = client.post(f"/review/{token}/action",
                    json={"upload_id": post_id, "action": "revise", "note": "rengi değişsin"})
    assert r.status_code == 200
    rv = r.get_json()["review"]
    assert rv["status"] == "revision_requested" and rv["note"] == "rengi değişsin"


def test_action_karar_guncellenir(client, setup):
    """Müşteri fikrini değiştirirse tek satır güncellenir (yükleme başına tek karar)."""
    from models_sharing import UploadReview
    token, _, post_id, _ = setup
    client.post(f"/review/{token}/action", json={"upload_id": post_id, "action": "approve"})
    client.post(f"/review/{token}/action",
                json={"upload_id": post_id, "action": "revise", "note": "olmadı"})
    rows = UploadReview.query.filter_by(upload_id=post_id).all()
    assert len(rows) == 1 and rows[0].status == "revision_requested"


def test_action_sonrasi_sayfada_durum_gorunur(client, setup):
    token, _, post_id, _ = setup
    client.post(f"/review/{token}/action", json={"upload_id": post_id, "action": "approve"})
    items = {s["id"]: s for s in client.get(f"/review/{token}/shares").get_json()["shares"]}
    assert items[post_id]["review"]["status"] == "approved"


def test_action_bildirim_yazar(client, setup):
    """notify_client_review çağrılır (yönetime panel-içi bildirim)."""
    from extensions import db
    from models import Notification, UserRef
    token, _, post_id, _ = setup
    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                           name=MANAGER["name"], role="management"))
    db.session.commit()
    assert Notification.query.filter_by(kind="client_review").count() == 0
    client.post(f"/review/{token}/action", json={"upload_id": post_id, "action": "approve"})
    notifs = Notification.query.filter_by(kind="client_review").all()
    assert len(notifs) == 1 and notifs[0].recipient_sub == MANAGER["sub"]


def test_action_baska_haftadaki_yukleme_reddedilir(client, setup):
    token, cid, _, _ = setup
    other = _mk_upload(cid, "x9", "post", week="2026-W22")
    r = client.post(f"/review/{token}/action", json={"upload_id": other, "action": "approve"})
    assert r.status_code == 404


def test_action_story_yuklemesi_reddedilir(client, setup):
    """Sayfada görünmeyen kategori üzerinden karar verilemez."""
    token, cid, _, _ = setup
    st = _mk_upload(cid, "st9", "story")
    r = client.post(f"/review/{token}/action", json={"upload_id": st, "action": "approve"})
    assert r.status_code == 404


# --- medya proxy (kapsam + cache) -------------------------------------------
def test_media_kapsam_disi_file_id_404(client, setup):
    assert client.get(f"/review/{setup[0]}/media/BASKA").status_code == 404


def test_media_full_s2048_ayri_cachelenir(client, setup, monkeypatch):
    import drive_gateway
    from extensions import db
    from models_sharing import DriveThumbnail
    token, _, _, _ = setup
    calls = []

    def fake_thumb(fid, width):
        calls.append(width)
        return b"IMGDATA", "image/jpeg"
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "thumbnail_bytes", fake_thumb)
    assert client.get(f"/review/{token}/media/f1").status_code == 200
    assert client.get(f"/review/{token}/media/f1?size=full").status_code == 200
    assert calls == [600, 2048]
    assert db.session.get(DriveThumbnail, ("f1", 600)) is not None
    assert db.session.get(DriveThumbnail, ("f1", 2048)) is not None
    client.get(f"/review/{token}/media/f1?size=full")
    assert calls == [600, 2048]  # cache'ten


def test_media_gecersiz_size_thumba_duser(client, setup, monkeypatch):
    """Keyfi ?size= genişlik enjeksiyonuna dönüşmemeli."""
    import drive_gateway
    calls = []
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "thumbnail_bytes",
                        lambda fid, width: (calls.append(width), (b"X", "image/jpeg"))[1])
    client.get(f"/review/{setup[0]}/media/f1?size=99999")
    assert calls == [600]


def test_kaldirilan_icerigin_medyasi_personele_acik_kalir(client, setup, monkeypatch):
    """Personel geri alma önizlemesi görebilsin diye kaldırılanın medyası 404 olmaz."""
    import drive_gateway
    token, _, post_id, _ = setup
    monkeypatch.setattr(drive_gateway, "available", lambda: True)
    monkeypatch.setattr(drive_gateway, "thumbnail_bytes", lambda fid, w: (b"X", "image/jpeg"))
    login_as(client, MANAGER)
    client.post(f"/review/{token}/exclude", json={"upload_id": post_id, "excluded": True},
                headers=csrf_headers(client))
    assert client.get(f"/review/{token}/media/f1").status_code == 200


# --- video akışı (lokal 21 gün penceresi) -----------------------------------
def test_shares_video_url_lokalde_var(client, setup, monkeypatch):
    import media_store
    token, _, _, video_id = setup
    monkeypatch.setattr(media_store, "has_original", lambda fid: fid == "vid9")
    items = {s["id"]: s for s in client.get(f"/review/{token}/shares").get_json()["shares"]}
    assert items[video_id]["video_url"].endswith("/stream/vid9")


def test_shares_video_url_lokal_yoksa_none(client, setup, monkeypatch):
    import media_store
    token, _, _, video_id = setup
    monkeypatch.setattr(media_store, "has_original", lambda fid: False)
    items = {s["id"]: s for s in client.get(f"/review/{token}/shares").get_json()["shares"]}
    assert items[video_id]["video_url"] is None


def test_stream_kapsam_disi_file_id_404(client, setup):
    assert client.get(f"/review/{setup[0]}/stream/BASKA").status_code == 404


def test_stream_lokal_yoksa_404(client, setup, monkeypatch):
    import media_store
    monkeypatch.setattr(media_store, "find_original", lambda fid: None)
    assert client.get(f"/review/{setup[0]}/stream/vid9").status_code == 404


# --- onay linki içerik sayısı (tasarımcı mesajının tekil/çoğul kaynağı) ------
def test_share_count_tekil(client):
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Sayım 1"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    _mk_upload(cid, "a1", "post")
    r = client.post("/api/sharing/review-link", json={"client_id": cid, "week_iso": WK},
                    headers=csrf_headers(client))
    assert r.get_json()["share_count"] == 1


def test_share_count_cogul(client):
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Sayım 2"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    _mk_upload(cid, "a1", "post")
    _mk_upload(cid, "a2", "video")
    _mk_upload(cid, "a3", "story")  # sayılmaz
    r = client.post("/api/sharing/review-link", json={"client_id": cid, "week_iso": WK},
                    headers=csrf_headers(client))
    assert r.get_json()["share_count"] == 2


def test_share_count_kaldirilan_haric(client, setup):
    """Sayfadan kaldırılan içerik tekil/çoğul sayımına girmez."""
    token, cid, post_id, _ = setup
    login_as(client, MANAGER)
    client.post(f"/review/{token}/exclude", json={"upload_id": post_id, "excluded": True},
                headers=csrf_headers(client))
    r = client.post("/api/sharing/review-link", json={"client_id": cid, "week_iso": WK},
                    headers=csrf_headers(client))
    assert r.get_json()["share_count"] == 1


def test_share_count_sayfayla_ayni(client, setup):
    """KRİTİK: mesajdaki sayı = müşterinin sayfada gördüğü içerik sayısı."""
    token, cid, _, _ = setup
    page_count = len(client.get(f"/review/{token}/shares").get_json()["shares"])
    login_as(client, MANAGER)
    link_count = client.post("/api/sharing/review-link",
                             json={"client_id": cid, "week_iso": WK},
                             headers=csrf_headers(client)).get_json()["share_count"]
    assert link_count == page_count


# --- ÖN-ONAY akışı (iç kapı, 2026-07-24) -------------------------------------
@pytest.fixture
def pre_setup(client, setup):
    """setup'a ek olarak ön-onay linki üret. Döner: (pre_token, token, cid, post, video)."""
    token, cid, post_id, video_id = setup
    login_as(client, MANAGER)
    pre = client.post("/api/sharing/pre-approval-link",
                      json={"client_id": cid, "week_iso": WK},
                      headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    return pre, token, cid, post_id, video_id


def test_on_onay_linki_tasarimci_uretebilir(client, setup):
    _, cid, _, _ = setup
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/pre-approval-link", json={"client_id": cid, "week_iso": WK},
                    headers=csrf_headers(client))
    assert r.status_code == 200 and r.get_json()["token"]


def test_on_onay_linki_musteri_linkinden_farkli(client, pre_setup):
    pre, token, _, _, _ = pre_setup
    assert pre != token


def test_on_onay_sayfasi_oturumsuz_403(client, pre_setup):
    """Ön-onay linki iç akış — müşteri/yabancı göremez."""
    r = client.get(f"/review/{pre_setup[0]}/shares")
    assert r.status_code == 403


def test_on_onay_yonetim_karar_verebilir(client, pre_setup):
    pre, _, _, post_id, _ = pre_setup
    login_as(client, MANAGER)
    d = client.get(f"/review/{pre}/shares").get_json()
    assert d["mode"] == "pre" and d["can_decide"] is True
    r = client.post(f"/review/{pre}/action", json={"upload_id": post_id, "action": "approve"})
    assert r.status_code == 200 and r.get_json()["pre"]["status"] == "approved"


def test_on_onay_tasarimci_gorur_karar_veremez(client, pre_setup):
    pre, _, _, post_id, _ = pre_setup
    login_as(client, DESIGNER)
    d = client.get(f"/review/{pre}/shares").get_json()
    assert d["mode"] == "pre" and d["can_decide"] is False
    assert len(d["shares"]) == 2  # durumu görebiliyor
    r = client.post(f"/review/{pre}/action", json={"upload_id": post_id, "action": "approve"})
    assert r.status_code == 403


def test_on_onay_revize_not_zorunlu(client, pre_setup):
    pre, _, _, post_id, _ = pre_setup
    login_as(client, MANAGER)
    assert client.post(f"/review/{pre}/action",
                       json={"upload_id": post_id, "action": "revise"}).status_code == 400
    r = client.post(f"/review/{pre}/action",
                    json={"upload_id": post_id, "action": "revise", "note": "yazı tipi"})
    assert r.status_code == 200 and r.get_json()["pre"]["note"] == "yazı tipi"


def test_on_onay_revize_bildirim_yazar(client, pre_setup):
    """Revizyonda tasarımcı/ekip bilgilendirilir."""
    from extensions import db
    from models import Notification, UserRef
    pre, _, _, post_id, _ = pre_setup
    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                           name=MANAGER["name"], role="management"))
    db.session.commit()
    login_as(client, MANAGER)
    client.post(f"/review/{pre}/action",
                json={"upload_id": post_id, "action": "revise", "note": "olmadı"})
    assert Notification.query.filter_by(kind="pre_approval").count() >= 1


# --- KADEMELİ KAPI ------------------------------------------------------------
def test_kapi_karar_yoksa_hepsi_gorunur(client, pre_setup):
    """Hiç ön-onay kararı yoksa müşteri eskisi gibi hepsini görür (geriye uyum)."""
    _, token, _, post_id, video_id = pre_setup
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert sorted(ids) == sorted([post_id, video_id])


def test_kapi_karar_varsa_yalniz_onayli_gorunur(client, pre_setup):
    """Bir karar verildiği anda kapı devreye girer: müşteri yalnız ön-onaylıyı görür."""
    pre, token, _, post_id, video_id = pre_setup
    login_as(client, MANAGER)
    client.post(f"/review/{pre}/action", json={"upload_id": post_id, "action": "approve"})
    with client.session_transaction() as s:
        s.clear()
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert ids == [post_id]  # video henüz ön-onaylanmadı → müşteriye gitmez


def test_kapi_revize_istenen_musteriye_gitmez(client, pre_setup):
    pre, token, _, post_id, video_id = pre_setup
    login_as(client, MANAGER)
    client.post(f"/review/{pre}/action", json={"upload_id": post_id, "action": "approve"})
    client.post(f"/review/{pre}/action",
                json={"upload_id": video_id, "action": "revise", "note": "kısalt"})
    with client.session_transaction() as s:
        s.clear()
    ids = [s["id"] for s in client.get(f"/review/{token}/shares").get_json()["shares"]]
    assert ids == [post_id]


def test_kapi_share_count_da_uygular(client, pre_setup):
    """Tekil/çoğul sayımı da kapıya uyar (mesaj sayfayla tutarlı kalır)."""
    pre, _, cid, post_id, _ = pre_setup
    login_as(client, MANAGER)
    client.post(f"/review/{pre}/action", json={"upload_id": post_id, "action": "approve"})
    r = client.post("/api/sharing/review-link", json={"client_id": cid, "week_iso": WK},
                    headers=csrf_headers(client))
    assert r.get_json()["share_count"] == 1


def test_on_onay_sayfasi_kapidan_etkilenmez(client, pre_setup):
    """Yönetici ön-onay sayfasında HER ŞEYİ görmeye devam eder (kapı uygulanmaz)."""
    pre, _, _, post_id, _ = pre_setup
    login_as(client, MANAGER)
    client.post(f"/review/{pre}/action",
                json={"upload_id": post_id, "action": "revise", "note": "x"})
    d = client.get(f"/review/{pre}/shares").get_json()
    assert len(d["shares"]) == 2
