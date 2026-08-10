"""Kalıcı public medya bağlantısı — GET /m/<file_id> (Drive'a ağ çıkışı YOK).

Bu ucun tamamı OTURUMSUZ çalışır, o yüzden testlerin ağırlığı "neyi servis ETMEZ"
tarafında: uygunluk DB'den sorulur, dosya sisteminden değil.

2026-07-31: uç üç modlu — `/m/<id>` mini izleme/indirme SAYFASI, `?raw=1` ham dosya,
`?dl=1` attachment. Ham davranışın testleri `?raw=1`'e taşındı.
"""
import pytest
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers

DRIVE_PREFIX = "https://drive.google.com/file/d/"
DRIVE_DL = "https://drive.google.com/uc?export=download&id="


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    r = client.post("/api/clients", json={"name": "Public Medya Müşteri"},
                    headers=csrf_headers(client))
    cid = r.get_json()["client"]["id"]
    # Oturumu bırak: aşağıdaki testler OTURUMSUZ olmalı.
    client.get("/auth/logout")
    return cid


def _store(file_id, data=b"dosya-icerigi", mime="video/mp4", name="v.mp4"):
    import media_store
    assert media_store.save_original(file_id, data, mime, name)


def _video(cid, file_id, name="klip.mp4", deleted=False):
    from extensions import db
    from models import utcnow
    from models_sharing import CardUpload
    u = CardUpload(client_id=cid, week_iso="2026-W31", category="video",
                   file_id=file_id, file_name=name,
                   deleted_at=(utcnow() if deleted else None))
    db.session.add(u)
    db.session.commit()
    return u


def _photo(cid, file_id, name="foto.jpg", deleted=False):
    from extensions import db
    from models import utcnow
    from models_sharing import VideographerPhoto
    p = VideographerPhoto(client_id=cid, file_id=file_id, file_name=name,
                          deleted_at=(utcnow() if deleted else None))
    db.session.add(p)
    db.session.commit()
    return p


# --- pozitif: lokal kopya varken dosyanın KENDİSİ servis edilir (?raw=1) ---

def test_video_lokal_kopyadan_OTURUMSUZ_servis_edilir(client, cid):
    _video(cid, "VIDPUB0001")
    _store("VIDPUB0001")
    r = client.get("/m/VIDPUB0001?raw=1")
    try:
        assert r.status_code == 200
        assert r.data == b"dosya-icerigi"
    finally:
        r.close()          # send_file dosya tanıtıcısı — elle kapatılır


def test_video_range_destekli(client, cid):
    """Video seek'i Range ister; conditional=True bunu vermeli."""
    _video(cid, "VIDPUB0002")
    _store("VIDPUB0002")
    r = client.get("/m/VIDPUB0002?raw=1", headers={"Range": "bytes=0-4"})
    try:
        assert r.status_code == 206
        assert r.data == b"dosya"
        assert r.headers.get("Content-Range", "").startswith("bytes 0-4/")
    finally:
        r.close()


def test_foto_lokal_kopyadan_servis_edilir(client, cid):
    _photo(cid, "FOTOPUB001")
    _store("FOTOPUB001", data=b"jpeg-baytlari", mime="image/jpeg", name="f.jpg")
    r = client.get("/m/FOTOPUB001?raw=1")
    try:
        assert r.status_code == 200
        assert r.data == b"jpeg-baytlari"
    finally:
        r.close()


# --- SAYFA modu (varsayılan): oynatıcı + "İndir" düğmesi ---

def test_varsayilan_mod_SAYFA_dondurur_ham_dosya_DEGIL(client, cid):
    """İstenen davranışın kalbi (2026-07-31): kopyalanan linki açan kişi indirme
    düğmesi görmeli — ham video/mp4 yanıtında o düğme yoktu."""
    _video(cid, "VIDSAYFA001", name="reels pazartesi.mp4")
    _store("VIDSAYFA001")
    r = client.get("/m/VIDSAYFA001")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html")
    html = r.get_data(as_text=True)
    assert "<video" in html and 'src="/m/VIDSAYFA001?raw=1"' in html
    assert "/m/VIDSAYFA001?dl=1" in html and "İndir" in html
    assert "reels pazartesi.mp4" in html
    assert r.data != b"dosya-icerigi"


def test_foto_sayfasi_img_basar(client, cid):
    _photo(cid, "FOTOSAYFA01")
    _store("FOTOSAYFA01", data=b"jpeg", mime="image/jpeg", name="f.jpg")
    html = client.get("/m/FOTOSAYFA01").get_data(as_text=True)
    assert "<img" in html and "<video" not in html


def test_sayfa_arama_motorlarina_kapali(client, cid):
    _video(cid, "VIDSAYFA002")
    _store("VIDSAYFA002")
    r = client.get("/m/VIDSAYFA002")
    assert "noindex" in r.headers.get("X-Robots-Tag", "")


def test_dosya_adi_HTML_KACISLI_basilir(client, cid):
    """Dosya adını videograf koyuyor → sayfaya ham gömülürse depolanmış XSS olurdu."""
    _video(cid, "VIDXSS00001", name='<script>alert(1)</script>.mp4')
    _store("VIDXSS00001")
    html = client.get("/m/VIDXSS00001").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_sayfa_lokal_yoksa_DRIVE_gomulu_oynaticiya_duser(client, cid):
    """Sayfa 21 gün sonra da ölmemeli: Drive'ın preview'ına + indirme adresine düşer."""
    _video(cid, "VIDSAYFA003")          # media_store'a HİÇ yazılmadı
    r = client.get("/m/VIDSAYFA003")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f"{DRIVE_PREFIX}VIDSAYFA003/preview" in html
    assert f"{DRIVE_DL}VIDSAYFA003".replace("&", "&amp;") in html


# --- indirme modu (?dl=1) ---

def test_dl_attachment_olarak_iner(client, cid):
    _video(cid, "VIDDL000001", name="haftalık klip.mp4")
    _store("VIDDL000001")
    r = client.get("/m/VIDDL000001?dl=1")
    try:
        assert r.status_code == 200
        cd = r.headers["Content-Disposition"]
        assert cd.startswith("attachment")
        # Türkçe ad: werkzeug RFC5987 ile filename* olarak kodlar.
        assert "klip.mp4" in cd
        assert r.data == b"dosya-icerigi"
    finally:
        r.close()


def test_dl_lokal_yoksa_DRIVE_INDIRME_adresine_yonlenir(client, cid):
    """Niyet indirmeydi → görüntüleyici sayfasına değil, indirme adresine düşülmeli."""
    _video(cid, "VIDDL000002")
    r = client.get("/m/VIDDL000002?dl=1")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{DRIVE_DL}VIDDL000002"


# --- kalıcılık: lokal kopya yoksa Drive'a düşer (link ölmez) ---

def test_lokal_yoksa_DRIVE_LINKINE_yonlendirir(client, cid):
    """İstenen davranışın kalbi: 21 günlük kopya silinse de aynı link çalışmalı."""
    _video(cid, "VIDPUB0003")          # media_store'a HİÇ yazılmadı
    r = client.get("/m/VIDPUB0003?raw=1")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{DRIVE_PREFIX}VIDPUB0003/view"


def test_foto_lokal_yoksa_drive_yonlendirir(client, cid):
    _photo(cid, "FOTOPUB002")
    r = client.get("/m/FOTOPUB002?raw=1")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{DRIVE_PREFIX}FOTOPUB002/view"


def test_yonlendirme_302_kalici_301_DEGIL(client, cid):
    """301 tarayıcıda süresiz cache'lenir → lokal kopya politikası değişirse geri
    dönemezdik. Bilinçli olarak 302."""
    _photo(cid, "FOTOPUB003")
    r = client.get("/m/FOTOPUB003?raw=1")
    assert r.status_code == 302 and r.status_code != 301


# --- NEGATİF: uç, media_store için okuma oracle'ı OLMAMALI ---

@pytest.mark.parametrize("mod", ["", "?raw=1", "?dl=1"])
def test_uygun_olmayan_dosya_lokalde_VARSA_BILE_404(client, cid, mod):
    """GÜVENLİĞİN KALBİ: media_store'da müşteri tasarım yüklemeleri de duruyor ve
    onların Drive'da public izni YOK. Uç yalnız videograf videosu + çekim fotoğrafı
    servis etmeli; başka bir kayıt tipi lokalde dursa bile 404 — HER modda."""
    from extensions import db
    from models_sharing import CardUpload
    if not CardUpload.query.filter_by(file_id="TASARIM0001").first():
        db.session.add(CardUpload(client_id=cid, week_iso="2026-W31",
                                  category="post",      # video DEĞİL → uygun değil
                                  file_id="TASARIM0001", file_name="tasarim.png"))
        db.session.commit()
        _store("TASARIM0001", data=b"gizli-tasarim", mime="image/png", name="t.png")

    r = client.get(f"/m/TASARIM0001{mod}")
    assert r.status_code == 404
    assert b"gizli-tasarim" not in r.data


def test_hicbir_kayda_bagli_olmayan_id_lokalde_varsa_bile_404(client, cid):
    """DB'de karşılığı olmayan ama diskte duran bir file_id de sızmamalı."""
    _store("YETIM000001", data=b"yetim-dosya")
    r = client.get("/m/YETIM000001")
    assert r.status_code == 404
    assert b"yetim-dosya" not in r.data


@pytest.mark.parametrize("mod", ["", "?raw=1", "?dl=1"])
def test_silinen_video_linki_OLUR(client, cid, mod):
    """Panelden silinen (soft-delete) dosyanın kopyalanmış linki artık çalışmamalı —
    lokal kopya hâlâ diskte olsa bile. Sayfa/ham/indirme modlarının hepsinde."""
    if not _has_video("VIDSILINDI1"):
        _video(cid, "VIDSILINDI1", deleted=True)
        _store("VIDSILINDI1")
    r = client.get(f"/m/VIDSILINDI1{mod}")
    assert r.status_code == 404
    assert b"dosya-icerigi" not in r.data


def _has_video(file_id):
    from models_sharing import CardUpload
    return CardUpload.query.filter_by(file_id=file_id).first() is not None


def test_silinen_foto_linki_olur(client, cid):
    _photo(cid, "FOTOSILINDI", deleted=True)
    _store("FOTOSILINDI", data=b"silinmis-foto")
    r = client.get("/m/FOTOSILINDI")
    assert r.status_code == 404


def test_bilinmeyen_id_404_drive_yonlendirmesi_YOK(client, cid):
    """Bilinmeyen id Drive'a yönlendirilmemeli — aksi halde uç, herhangi bir Drive
    kimliği için açık yönlendirici (open redirect) olurdu."""
    r = client.get("/m/BILINMEYEN01")
    assert r.status_code == 404
    assert "Location" not in r.headers


@pytest.mark.parametrize("bad", ["kisa", "bosluk%20var", "nokta.nokta", "egik/cizgi"])
def test_gecersiz_id_bicimi_404(client, bad):
    r = client.get(f"/m/{bad}")
    assert r.status_code == 404


def test_uc_oturum_ISTEMEZ(client, cid):
    """Karşıt kontrol: aynı dosya oturum isteyen /api/sharing/media ucunda 401 verirken
    public uçta 200 dönüyor — yani public'lik gerçekten bu uca özgü, test ortamının
    yan etkisi değil."""
    _video(cid, "VIDPUB0004")
    _store("VIDPUB0004")
    assert client.get("/api/sharing/media/VIDPUB0004").status_code == 401
    r = client.get("/m/VIDPUB0004?raw=1")
    try:
        assert r.status_code == 200
    finally:
        r.close()
