"""Permanent public media link — GET /m/<file_id> (NO network access to Drive).

This endpoint runs entirely WITHOUT a session, so the tests weigh heavily on the
"what it does NOT serve" side: eligibility is queried from the DB, not the filesystem.

2026-07-31: the endpoint has three modes — `/m/<id>` a mini viewing/download PAGE,
`?raw=1` the raw file, `?dl=1` attachment. Tests for the raw behavior moved to `?raw=1`.
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
    # Drop the session: the tests below must run WITHOUT a session.
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


# --- positive: when a local copy exists, the file ITSELF is served (?raw=1) ---

def test_video_lokal_kopyadan_OTURUMSUZ_servis_edilir(client, cid):
    _video(cid, "VIDPUB0001")
    _store("VIDPUB0001")
    r = client.get("/m/VIDPUB0001?raw=1")
    try:
        assert r.status_code == 200
        assert r.data == b"dosya-icerigi"
    finally:
        r.close()          # send_file file handle — closed manually


def test_video_range_destekli(client, cid):
    """Video seeking requires Range; conditional=True should provide it."""
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


# --- PAGE mode (default): player + "Download" button ---

def test_varsayilan_mod_SAYFA_dondurur_ham_dosya_DEGIL(client, cid):
    """The heart of the intended behavior (2026-07-31): whoever opens the copied link
    should see a download button — the raw video/mp4 response didn't have that button."""
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
    """The videographer sets the file name → embedding it raw in the page would be stored XSS."""
    _video(cid, "VIDXSS00001", name='<script>alert(1)</script>.mp4')
    _store("VIDXSS00001")
    html = client.get("/m/VIDXSS00001").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_sayfa_lokal_yoksa_DRIVE_gomulu_oynaticiya_duser(client, cid):
    """The page must not die after 21 days: it falls back to Drive's preview + download address."""
    _video(cid, "VIDSAYFA003")          # NEVER written to media_store
    r = client.get("/m/VIDSAYFA003")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f"{DRIVE_PREFIX}VIDSAYFA003/preview" in html
    assert f"{DRIVE_DL}VIDSAYFA003".replace("&", "&amp;") in html


# --- download mode (?dl=1) ---

def test_dl_attachment_olarak_iner(client, cid):
    _video(cid, "VIDDL000001", name="haftalık klip.mp4")
    _store("VIDDL000001")
    r = client.get("/m/VIDDL000001?dl=1")
    try:
        assert r.status_code == 200
        cd = r.headers["Content-Disposition"]
        assert cd.startswith("attachment")
        # Turkish name: werkzeug encodes it as filename* per RFC5987.
        assert "klip.mp4" in cd
        assert r.data == b"dosya-icerigi"
    finally:
        r.close()


def test_dl_lokal_yoksa_DRIVE_INDIRME_adresine_yonlenir(client, cid):
    """The intent was to download → should fall through to the download address, not the viewer page."""
    _video(cid, "VIDDL000002")
    r = client.get("/m/VIDDL000002?dl=1")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{DRIVE_DL}VIDDL000002"


# --- persistence: falls back to Drive if there's no local copy (link doesn't die) ---

def test_lokal_yoksa_DRIVE_LINKINE_yonlendirir(client, cid):
    """The heart of the intended behavior: even if the 21-day copy is deleted, the same link must still work."""
    _video(cid, "VIDPUB0003")          # NEVER written to media_store
    r = client.get("/m/VIDPUB0003?raw=1")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{DRIVE_PREFIX}VIDPUB0003/view"


def test_foto_lokal_yoksa_drive_yonlendirir(client, cid):
    _photo(cid, "FOTOPUB002")
    r = client.get("/m/FOTOPUB002?raw=1")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{DRIVE_PREFIX}FOTOPUB002/view"


def test_yonlendirme_302_kalici_301_DEGIL(client, cid):
    """301 gets cached indefinitely in the browser → if the local-copy policy changes, we
    couldn't go back. Deliberately using 302."""
    _photo(cid, "FOTOPUB003")
    r = client.get("/m/FOTOPUB003?raw=1")
    assert r.status_code == 302 and r.status_code != 301


# --- NEGATIVE: the endpoint must NOT be a read oracle for media_store ---

@pytest.mark.parametrize("mod", ["", "?raw=1", "?dl=1"])
def test_uygun_olmayan_dosya_lokalde_VARSA_BILE_404(client, cid, mod):
    """THE HEART OF SECURITY: client design uploads also sit in media_store, and
    they do NOT have public permission on Drive. The endpoint must serve only
    videographer video + shoot photos; any other record type gets 404 even if it
    sits locally — in EVERY mode."""
    from extensions import db
    from models_sharing import CardUpload
    if not CardUpload.query.filter_by(file_id="TASARIM0001").first():
        db.session.add(CardUpload(client_id=cid, week_iso="2026-W31",
                                  category="post",      # NOT video → not eligible
                                  file_id="TASARIM0001", file_name="tasarim.png"))
        db.session.commit()
        _store("TASARIM0001", data=b"gizli-tasarim", mime="image/png", name="t.png")

    r = client.get(f"/m/TASARIM0001{mod}")
    assert r.status_code == 404
    assert b"gizli-tasarim" not in r.data


def test_hicbir_kayda_bagli_olmayan_id_lokalde_varsa_bile_404(client, cid):
    """A file_id with no DB record but sitting on disk must not leak either."""
    _store("YETIM000001", data=b"yetim-dosya")
    r = client.get("/m/YETIM000001")
    assert r.status_code == 404
    assert b"yetim-dosya" not in r.data


@pytest.mark.parametrize("mod", ["", "?raw=1", "?dl=1"])
def test_silinen_video_linki_OLUR(client, cid, mod):
    """A copied link to a file deleted from the panel (soft-delete) must no longer work —
    even if the local copy is still on disk. In all of page/raw/download modes."""
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
    """An unknown id must not redirect to Drive — otherwise the endpoint would be an
    open redirect for any Drive id."""
    r = client.get("/m/BILINMEYEN01")
    assert r.status_code == 404
    assert "Location" not in r.headers


@pytest.mark.parametrize("bad", ["kisa", "bosluk%20var", "nokta.nokta", "egik/cizgi"])
def test_gecersiz_id_bicimi_404(client, bad):
    r = client.get(f"/m/{bad}")
    assert r.status_code == 404


def test_uc_oturum_ISTEMEZ(client, cid):
    """Control check: the same file returns 401 on the session-requiring /api/sharing/media
    endpoint but 200 on the public endpoint — so the public-ness is really specific to
    this endpoint, not a side effect of the test environment."""
    _video(cid, "VIDPUB0004")
    _store("VIDPUB0004")
    assert client.get("/api/sharing/media/VIDPUB0004").status_code == 401
    r = client.get("/m/VIDPUB0004?raw=1")
    try:
        assert r.status_code == 200
    finally:
        r.close()
