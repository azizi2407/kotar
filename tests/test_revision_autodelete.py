"""Revize yüklenince eski sürümün otomatik silinmesi — entegrasyon (2026-08-01).

Eşleşme KURALININ testleri `test_revision_match.py`'de (saf modül). Burada
davranış test ediliyor: kim silinir, kim korunur, ne bildirilir.

Silme geri dönüşü zor (Drive çöpü 30 gün) → koruma kapıları testle çivileniyor.
"""
import pytest
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Revize Müşteri"},
                    headers=csrf_headers(client))
    return r.get_json()["client"]["id"]


def _video(cid, ad, file_id, dakika=0):
    """Bir video yüklemesi kaydı; `dakika` kadar GEÇMİŞE yazar (sıra önemli)."""
    from datetime import timedelta

    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    u = CardUpload(client_id=cid, week_iso="2026-W31", category="video",
                   file_id=file_id, file_name=ad,
                   uploaded_at=utcnow() - timedelta(minutes=dakika))
    db.session.add(u)
    db.session.commit()
    return u


def _yeni_yukleme(cid, ad, file_id="YENIFILE001"):
    """Yeni yüklemeyi kaydedip revize taramasını çalıştır (upload() ucunun
    Drive'a çıkmayan özü)."""
    import sharing
    cu = _video(cid, ad, file_id, dakika=0)
    sharing._supersede_previous_videos(cu)
    return cu


def _var_mi(upload_id):
    from models_sharing import CardUpload
    return CardUpload.query.filter_by(id=upload_id).first() is not None


# --- silinmesi gerekenler ---

def test_revize_eski_surumu_siler(client, cid, monkeypatch):
    import sharing
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    eski = _video(cid, "camsaş0728.mp4", "ESKIFILE0001", dakika=60)
    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert not _var_mi(eski.id), "eski sürüm silinmeliydi"


def test_silme_sunucu_kopyasini_da_kaldirir(client, cid, monkeypatch, tmp_path):
    """Kalsaydı silinen video /m/<file_id> üzerinden 21 gün daha izlenebilirdi."""
    import media_store
    import sharing
    monkeypatch.setenv("MEDIA_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    media_store.save_original("ESKIFILE0002", b"video", "video/mp4", "v.mp4")
    with open(media_store.web_path("ESKIFILE0002"), "wb") as f:
        f.write(b"turev")
    _video(cid, "rizon0727.mp4", "ESKIFILE0002", dakika=60)

    _yeni_yukleme(cid, "rizonr0727.mp4")
    assert media_store.find_original("ESKIFILE0002") is None
    assert media_store.find_web("ESKIFILE0002") is None, "web türevi de gitmeli"


def test_drive_copune_tasinir(client, cid, monkeypatch):
    import sharing
    tasinan = []
    monkeypatch.setattr(sharing.dg, "available", lambda: True)
    monkeypatch.setattr(sharing.dg, "trash_file", lambda fid: tasinan.append(fid))
    _video(cid, "budak0730.mp4", "ESKIFILE0003", dakika=60)
    _yeni_yukleme(cid, "BUDAK0730R.mp4")
    assert tasinan == ["ESKIFILE0003"]


def test_zincirde_ara_surumler_de_gider(client, cid, monkeypatch):
    import sharing
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    t = "Antalya Tenis İhtisas Kulübü"
    a = _video(cid, f"{t} - 28.mp4", "ZINCIR000001", dakika=90)
    b = _video(cid, f"{t} - 28 - 2.mp4", "ZINCIR000002", dakika=60)
    c = _video(cid, f"{t} - 28 - 3.mp4", "ZINCIR000003", dakika=30)
    _yeni_yukleme(cid, f"{t} - 28 - 4.mp4")
    assert not any(_var_mi(x.id) for x in (a, b, c))


# --- korunması gerekenler ---

def test_paylasilmis_video_korunur(client, cid, monkeypatch):
    """Silinseydi müşteri onay sayfasındaki öğe sessizce kırılırdı."""
    import sharing
    from extensions import db
    from models_sharing import Share
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    eski = _video(cid, "camsaş0728.mp4", "PAYLASIM0001", dakika=60)
    db.session.add(Share(client_id=cid, week_iso="2026-W31", kind="video",
                         status="published", file_id="PAYLASIM0001"))
    db.session.commit()

    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert _var_mi(eski.id), "paylaşımdaki video silinmemeliydi"


def test_musteri_onayina_girmis_video_korunur(client, cid, monkeypatch):
    import sharing
    from extensions import db
    from models_sharing import UploadReview
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    eski = _video(cid, "camsaş0728.mp4", "ONAY00000001", dakika=60)
    db.session.add(UploadReview(upload_id=eski.id, status="approved"))
    db.session.commit()

    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert _var_mi(eski.id)


def test_silinmis_paylasim_korumaz(client, cid, monkeypatch):
    """Soft-delete edilmiş paylaşım artık müşteriye görünmüyor → koruma yok."""
    import sharing
    from extensions import db
    from models import utcnow
    from models_sharing import Share
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    eski = _video(cid, "camsaş0728.mp4", "SILINMIS0001", dakika=60)
    db.session.add(Share(client_id=cid, week_iso="2026-W31", kind="video",
                         status="draft", file_id="SILINMIS0001",
                         deleted_at=utcnow()))
    db.session.commit()

    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert not _var_mi(eski.id)


def test_baska_musterinin_ayni_adli_videosuna_dokunulmaz(client, monkeypatch):
    """Ad kalıbı tutsa bile kapsam AYNI MÜŞTERİ ile sınırlı."""
    import sharing
    from extensions import db
    from models import Client
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    a = Client(name="Müşteri A", status="active")
    b = Client(name="Müşteri B", status="active")
    db.session.add_all([a, b])
    db.session.commit()

    yabanci = _video(b.id, "camsaş0728.mp4", "YABANCI00001", dakika=60)
    _video(a.id, "camsaş0728.mp4", "BENIMKI00001", dakika=60)
    _yeni_yukleme(a.id, "camsaşr0728.mp4")
    assert _var_mi(yabanci.id), "başka müşterinin videosu silinemez"


def test_eslesme_yoksa_hicbir_sey_silinmez(client, cid, monkeypatch):
    import sharing
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    a = _video(cid, "kids0721.mp4", "DOKUNMA00001", dakika=60)
    b = _video(cid, "albains0723.mp4", "DOKUNMA00002", dakika=30)
    _yeni_yukleme(cid, "albarev0731.mp4")   # tabanı 'alba0731' — ikisi de değil
    assert _var_mi(a.id) and _var_mi(b.id)


def test_daha_yeni_video_silinmez(client, cid, monkeypatch):
    """Yalnız DAHA ESKİ kayıtlar süpersede edilebilir."""
    import sharing
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    sonraki = _video(cid, "camsaş0728.mp4", "SONRAKI00001", dakika=-60)  # gelecek
    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert _var_mi(sonraki.id)


# --- bildirim ---

def test_silince_bildirim_dusor(client, cid, monkeypatch):
    import sharing
    cagrilar = []
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    monkeypatch.setattr(sharing.notifications, "notify_old_video_removed",
                        lambda *a: cagrilar.append(a))
    _video(cid, "camsaş0728.mp4", "BILDIRIM0001", dakika=60)
    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert len(cagrilar) == 1
    assert "camsaş0728.mp4" in cagrilar[0][3]


def test_korununca_uyari_bildirimi_dusor(client, cid, monkeypatch):
    """Sessiz kalmak olmaz: revize geldi ama eski duruyor — insan karar vermeli."""
    import sharing
    from extensions import db
    from models_sharing import Share
    cagrilar = []
    monkeypatch.setattr(sharing.dg, "available", lambda: False)
    monkeypatch.setattr(sharing.notifications, "notify_old_video_kept",
                        lambda *a: cagrilar.append(a))
    _video(cid, "camsaş0728.mp4", "UYARI0000001", dakika=60)
    db.session.add(Share(client_id=cid, week_iso="2026-W31", kind="video",
                         status="published", file_id="UYARI0000001"))
    db.session.commit()

    _yeni_yukleme(cid, "camsaşr0728.mp4")
    assert len(cagrilar) == 1
    assert "paylaşımda" in cagrilar[0][4]
