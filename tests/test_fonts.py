"""Font pool (2026-08-05) — /api/fonts.

Focus: **signature validation** (these endpoints serve the file inline to the browser),
the permission matrix, N:N assignment, and content-hash dedup.
"""
import io

import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers

# Valid signatures — a real font body isn't needed, the endpoint only checks the first 4 bytes.
TTF = b"\x00\x01\x00\x00" + b"govde" * 20
OTF = b"OTTO" + b"govde" * 20
WOFF = b"wOFF" + b"govde" * 20
WOFF2 = b"wOF2" + b"govde" * 20


@pytest.fixture(autouse=True)
def font_store(tmp_path, monkeypatch):
    """Files should be written to a test-specific temp directory, not into the repo."""
    import fonts
    monkeypatch.setattr(fonts, "STORE_DIR", str(tmp_path / "fonts"))
    return tmp_path / "fonts"


def _yukle(client, data=TTF, name="Montserrat-Bold.ttf", **form):
    payload = {"file": (io.BytesIO(data), name)}
    payload.update({k: str(v) for k, v in form.items()})
    return client.post("/api/fonts", data=payload,
                       content_type="multipart/form-data",
                       headers=csrf_headers(client))


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    return client.post("/api/clients", json={"name": "Font Müşterisi"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


# --- format validation -------------------------------------------------------

@pytest.mark.parametrize("data,fmt", [(TTF, "ttf"), (OTF, "otf"),
                                      (WOFF, "woff"), (WOFF2, "woff2")])
def test_gecerli_formatlar(client, data, fmt):
    login_as(client, DESIGNER)
    r = _yukle(client, data, name=f"Aile-Regular.{fmt}")
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["font"]["format"] == fmt


def test_font_olmayan_dosya_reddedilir(client):
    """400 even when the extension is .ttf, if the signature doesn't match — this file
    is served inline to the browser, so the extension can't be trusted."""
    login_as(client, DESIGNER)
    r = _yukle(client, b"<html>merhaba</html>", name="sahte.ttf")
    assert r.status_code == 400
    assert "font" in r.get_json()["error"].lower()


def test_bos_dosya_400(client):
    login_as(client, DESIGNER)
    assert _yukle(client, b"", name="bos.ttf").status_code == 400


def test_boyut_siniri_413(client, monkeypatch):
    import fonts
    monkeypatch.setattr(fonts, "MAX_FONT_BYTES", 10)
    login_as(client, DESIGNER)
    assert _yukle(client).status_code == 413


# --- name inference -----------------------------------------------------------

@pytest.mark.parametrize("dosya,aile,stil", [
    ("Montserrat-Bold.ttf", "Montserrat", "Bold"),
    ("Roboto-BoldItalic.otf", "Roboto", "Bold Italic"),
    ("Inter_Regular.woff2", "Inter", "Regular"),
    ("PlayfairDisplay.ttf", "PlayfairDisplay", "Regular"),
    ("MontserratBold.ttf", "Montserrat", "Bold"),          # no separator
    ("Lato-ExtraBold.ttf", "Lato", "ExtraBold"),           # must not swallow "Bold"
    # Google Fonts variable files: the bracketed part is an AXIS list, not the family name
    ("Montserrat[wght].ttf", "Montserrat", "Variable"),
    ("Inter[opsz,wght].ttf", "Inter", "Variable"),
    ("Montserrat-Italic[wght].ttf", "Montserrat", "Variable Italic"),
])
def test_ad_tahmini(dosya, aile, stil):
    from fonts import _tahmin
    assert _tahmin(dosya) == (aile, stil)


def test_formdaki_ad_tahmini_ezer(client):
    login_as(client, DESIGNER)
    r = _yukle(client, family="Özel Aile", style="Kalın")
    f = r.get_json()["font"]
    assert (f["family"], f["style"]) == ("Özel Aile", "Kalın")


def test_ad_duzeltme(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    r = client.put(f"/api/fonts/{fid}", json={"family": "Montserrat", "style": "Bold"},
                   headers=csrf_headers(client))
    assert r.get_json()["font"]["family"] == "Montserrat"


def test_bos_aile_adi_400(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    assert client.put(f"/api/fonts/{fid}", json={"family": "  "},
                      headers=csrf_headers(client)).status_code == 400


# --- dedup ---------------------------------------------------------------------

def test_ayni_dosya_ikinci_kez_409(client):
    login_as(client, DESIGNER)
    _yukle(client)
    r = _yukle(client, name="baska-ad.ttf")
    assert r.status_code == 409
    assert r.get_json()["font"]["id"]          # returns the existing record


def test_silinen_font_ayni_dosyayla_canlanir(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    client.delete(f"/api/fonts/{fid}", headers=csrf_headers(client))
    r = _yukle(client)
    assert r.status_code == 201
    assert r.get_json()["font"]["id"] == fid   # revived, not a new row


def test_disk_kopyasi_tek(client, font_store):
    login_as(client, DESIGNER)
    _yukle(client)
    fid = _yukle(client, name="x.ttf").get_json()["font"]["id"]
    assert fid
    assert len(list(font_store.iterdir())) == 1


# --- assignment (N:N) -----------------------------------------------------------

def test_ata_ve_kaldir(client, cid):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    r = client.post(f"/api/fonts/{fid}/clients", json={"client_id": cid, "assigned": True},
                    headers=csrf_headers(client))
    assert [c["id"] for c in r.get_json()["font"]["clients"]] == [cid]

    r = client.post(f"/api/fonts/{fid}/clients", json={"client_id": cid, "assigned": False},
                    headers=csrf_headers(client))
    assert r.get_json()["font"]["clients"] == []


def test_tekrar_atama_cakismaz(client, cid):
    """UNIQUE(font_id, client_id) — must be idempotent, must not 500."""
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    for _ in range(3):
        r = client.post(f"/api/fonts/{fid}/clients", json={"client_id": cid},
                        headers=csrf_headers(client))
        assert r.status_code == 200
    assert len(r.get_json()["font"]["clients"]) == 1


def test_bir_font_bircok_musteriye(client, cid):
    login_as(client, MANAGER)
    cid2 = client.post("/api/clients", json={"name": "İkinci"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]
    fid = _yukle(client).get_json()["font"]["id"]
    for c in (cid, cid2):
        client.post(f"/api/fonts/{fid}/clients", json={"client_id": c},
                    headers=csrf_headers(client))
    r = client.get(f"/api/clients/{cid}/fonts")
    assert len(r.get_json()["fonts"]) == 1
    assert len(client.get(f"/api/clients/{cid2}/fonts").get_json()["fonts"]) == 1


def test_yuklerken_musteriye_atanabilir(client, cid):
    """The client_id form field exists so users aren't forced into a two-step 'upload → then assign'."""
    login_as(client, DESIGNER)
    r = _yukle(client, client_id=cid)
    assert [c["id"] for c in r.get_json()["font"]["clients"]] == [cid]


def test_olmayan_musteri_404(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    assert client.post(f"/api/fonts/{fid}/clients", json={"client_id": 999999},
                       headers=csrf_headers(client)).status_code == 404


def test_silinen_font_listelerden_duser(client, cid):
    login_as(client, DESIGNER)
    fid = _yukle(client, client_id=cid).get_json()["font"]["id"]
    client.delete(f"/api/fonts/{fid}", headers=csrf_headers(client))
    assert client.get("/api/fonts").get_json()["fonts"] == []
    assert client.get(f"/api/clients/{cid}/fonts").get_json()["fonts"] == []


# --- serving endpoints --------------------------------------------------------

def test_inline_servis_dogru_mime(client):
    login_as(client, DESIGNER)
    fid = _yukle(client, WOFF2, name="Inter-Regular.woff2").get_json()["font"]["id"]
    with client.get(f"/api/fonts/{fid}/file") as r:
        assert r.status_code == 200
        assert r.mimetype == "font/woff2"
        # @font-face source: must NOT be an attachment
        assert "attachment" not in (r.headers.get("Content-Disposition") or "")
        assert r.data == WOFF2


def test_indirme_attachment(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    with client.get(f"/api/fonts/{fid}/download") as r:
        assert "attachment" in r.headers["Content-Disposition"]


def test_diskte_olmayan_dosya_404(client, font_store):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    for p in font_store.iterdir():
        p.unlink()
    assert client.get(f"/api/fonts/{fid}/file").status_code == 404


# --- permissions ---------------------------------------------------------------

@pytest.mark.parametrize("kullanici", [MANAGER, DESIGNER, CONTENT_CREATOR, VIDEOGRAPHER])
def test_dort_uretim_rolu_okur(client, kullanici):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    login_as(client, kullanici)
    assert client.get("/api/fonts").status_code == 200
    with client.get(f"/api/fonts/{fid}/file") as r:
        assert r.status_code == 200
    with client.get(f"/api/fonts/{fid}/download") as r:
        assert r.status_code == 200


@pytest.mark.parametrize("kullanici", [CONTENT_CREATOR, VIDEOGRAPHER])
def test_yalniz_yonetim_ve_tasarimci_yukler(client, kullanici):
    login_as(client, kullanici)
    assert _yukle(client).status_code == 403


def test_icerikci_silemez_ve_atayamaz(client, cid):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    login_as(client, CONTENT_CREATOR)
    assert client.delete(f"/api/fonts/{fid}", headers=csrf_headers(client)).status_code == 403
    assert client.post(f"/api/fonts/{fid}/clients", json={"client_id": cid},
                       headers=csrf_headers(client)).status_code == 403


def test_oturumsuz_401(client):
    client.get("/auth/logout")
    assert client.get("/api/fonts").status_code == 401


def test_csrf_zorunlu(client):
    login_as(client, DESIGNER)
    r = client.post("/api/fonts", data={"file": (io.BytesIO(TTF), "a.ttf")},
                    content_type="multipart/form-data")
    assert r.status_code == 403


# --- zip upload (2026-08-06) ------------------------------------------------
# Font sites don't give you the font alone — they bundle it in a zip with a license
# PDF + preview image + read-me. `tests/fixtures/bigbelow.zip` is a REAL example
# (Bigbelow.otf + Bigbelow.ttf + Bigbelow.jpg + More Info.txt + Read Me.pdf).

import zipfile

# STRUCTURE of a real archive (identical to a bigbelow.zip downloaded from a font
# site): two font formats + a preview image + a read-me + a license PDF. The actual
# licensed font file was NOT put in the repo; since the rule checks the file
# signature, the bytes don't need to be a real font. The real archive was also
# verified in production (2026-08-06).
def _font_zipi():
    return _zip_yap([
        ("Bigbelow.jpg", b"\xff\xd8\xff\xe0" + b"jpeg" * 10),
        ("Bigbelow.otf", OTF),
        ("Bigbelow.ttf", TTF),
        ("More Info.txt", b"lisans notu"),
        ("Read Me.pdf", b"%PDF-1.4 sahte"),
    ])


def _zip_yap(kayitlar):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ad, veri in kayitlar:
            z.writestr(ad, veri)
    return buf.getvalue()


def test_font_sitesi_zipi(client):
    """Typical archive: 2 fonts are taken, 3 files (jpg/txt/pdf) are skipped and REPORTED."""
    login_as(client, DESIGNER)
    r = _yukle(client, _font_zipi(), name="bigbelow.zip")
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert {(f["family"], f["format"]) for f in d["fonts"]} == {("Bigbelow", "otf"),
                                                                ("Bigbelow", "ttf")}
    assert len(d["skipped"]) == 3           # jpg + txt + pdf
    assert any("jpg" in a.lower() for a in d["skipped"])


def test_zip_havuza_ve_musteriye_islenir(client, cid):
    login_as(client, DESIGNER)
    _yukle(client, _font_zipi(), name="bigbelow.zip", client_id=cid)
    assert len(client.get("/api/fonts").get_json()["fonts"]) == 2
    assert len(client.get(f"/api/clients/{cid}/fonts").get_json()["fonts"]) == 2


def test_zipte_font_yoksa_400(client):
    login_as(client, DESIGNER)
    r = _yukle(client, _zip_yap([("okuma.txt", b"merhaba"), ("kapak.jpg", b"\xff\xd8\xff")]),
               name="fontsuz.zip")
    assert r.status_code == 400
    assert "no font file found" in r.get_json()["error"]
    assert len(r.get_json()["skipped"]) == 2


def test_macos_artiklari_rapora_girmez(client):
    """The user didn't put `__MACOSX/` and `._` entries there; they shouldn't
    clutter the 'skipped' list."""
    login_as(client, DESIGNER)
    r = _yukle(client, _zip_yap([("Aile-Bold.ttf", TTF),
                                 ("__MACOSX/._Aile-Bold.ttf", b"artik"),
                                 ("._gizli", b"artik")]), name="mac.zip")
    assert r.status_code == 201
    assert r.get_json()["skipped"] == []
    assert len(r.get_json()["fonts"]) == 1


def test_zipte_mevcut_font_atlanir_kalani_yuklenir(client):
    """It's normal for a zip to contain both new and already-uploaded fonts — the 409
    from the single-file path must not reject the whole archive here."""
    login_as(client, DESIGNER)
    _yukle(client, TTF, name="Eski-Regular.ttf")
    r = _yukle(client, _zip_yap([("Eski-Regular.ttf", TTF), ("Yeni-Bold.otf", OTF)]),
               name="karisik.zip")
    assert r.status_code == 201
    assert [f["family"] for f in r.get_json()["fonts"]] == ["Yeni"]
    assert any("already in the pool" in a for a in r.get_json()["skipped"])


def test_zip_boyut_siniri_413(client, monkeypatch):
    import fonts
    monkeypatch.setattr(fonts, "ZIP_MAX_BYTES", 10)
    login_as(client, DESIGNER)
    assert _yukle(client, _zip_yap([("a.ttf", TTF)]), name="buyuk.zip").status_code == 413


def test_zip_bomb_acilmis_boyut_siniri(client, monkeypatch):
    """Even if the compressed size is small, it stops if the UNPACKED total exceeds the limit."""
    import fonts
    monkeypatch.setattr(fonts, "ZIP_MAX_TOTAL_BYTES", 100)
    login_as(client, DESIGNER)
    r = _yukle(client, _zip_yap([("a.ttf", TTF), ("b.ttf", b"\x00\x01\x00\x00" + b"x" * 500),
                                 ("c.ttf", OTF)]), name="bomba.zip")
    d = r.get_json()
    assert any("exceeded the extracted size limit" in a for a in d.get("skipped", []))


def test_bozuk_zip_400(client):
    login_as(client, DESIGNER)
    r = _yukle(client, b"PK\x03\x04bozuk-icerik", name="bozuk.zip")
    assert r.status_code == 400
    assert "could not read the archive" in r.get_json()["error"]


def test_zip_icindeki_yol_yok_sayilir(client, font_store):
    """Zip-slip: the entry's path info is NOT used, the file is written under its own hash name."""
    login_as(client, DESIGNER)
    r = _yukle(client, _zip_yap([("../../../etc/kotu.ttf", TTF)]), name="slip.zip")
    assert r.status_code == 201
    yazilanlar = [p.name for p in font_store.iterdir()]
    assert len(yazilanlar) == 1 and yazilanlar[0].endswith(".ttf")
    assert "kotu" not in yazilanlar[0]


def test_zip_icerikci_yukleyemez(client):
    login_as(client, CONTENT_CREATOR)
    assert _yukle(client, _font_zipi(), name="bigbelow.zip").status_code == 403


# --- delete permission (2026-08-06) -------------------------------------------
# Previously the `WRITE_ROLES` gate was enough → ANY designer could delete ANY font
# (including the shared 230-font pool). Now: management can delete anything, a
# designer only what they uploaded. The rule lives in one place, `fonts._silebilir`;
# the list endpoint's `can_delete` is fed from the same source.

BASKA_TASARIMCI = {"sub": "77", "email": "diger@test.com", "name": "Diğer Tasarımcı",
                   "role": "designer"}


def test_tasarimci_kendi_fontunu_silebilir(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    r = client.delete(f"/api/fonts/{fid}", headers=csrf_headers(client))
    assert r.status_code == 200
    assert client.get("/api/fonts").get_json()["fonts"] == []


def test_tasarimci_baskasinin_fontunu_silemez(client):
    """Protection of the shared pool (the Google Fonts import) depends on this rule."""
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    login_as(client, BASKA_TASARIMCI)
    r = client.delete(f"/api/fonts/{fid}", headers=csrf_headers(client))
    assert r.status_code == 403
    assert len(client.get("/api/fonts").get_json()["fonts"]) == 1


def test_yonetim_baskasinin_fontunu_silebilir(client):
    login_as(client, DESIGNER)
    fid = _yukle(client).get_json()["font"]["id"]
    login_as(client, MANAGER)
    assert client.delete(f"/api/fonts/{fid}",
                         headers=csrf_headers(client)).status_code == 200


def test_import_scriptinin_fontunu_tasarimci_silemez(client):
    """The `uploaded_by` of the 200+ fonts that came in via `font_import` is not an SSO sub —
    it matches no designer, so none of them can delete it."""
    from extensions import db
    from models_fonts import Font
    db.session.add(Font(family="Montserrat", style="Regular",
                        file_name="Montserrat[wght].ttf", sha256="abc123",
                        format="ttf", file_size=100, uploaded_by="font_import"))
    db.session.commit()
    fid = Font.query.filter_by(sha256="abc123").first().id
    login_as(client, DESIGNER)
    assert client.delete(f"/api/fonts/{fid}",
                         headers=csrf_headers(client)).status_code == 403


def test_can_delete_bayragi_ucla_ayni_kurali_soyler(client):
    """A mismatch between the flag and the endpoint = the button lying. Both use `_silebilir`."""
    login_as(client, DESIGNER)
    benim = _yukle(client, name="Benim-Regular.ttf").get_json()["font"]["id"]
    login_as(client, BASKA_TASARIMCI)
    onun = _yukle(client, OTF, name="Onun-Regular.otf").get_json()["font"]["id"]

    # From the other designer's point of view: the flag is only on for what they uploaded
    bayraklar = {f["id"]: f["can_delete"] for f in client.get("/api/fonts").get_json()["fonts"]}
    assert bayraklar == {benim: False, onun: True}

    # From management's point of view: all on
    login_as(client, MANAGER)
    bayraklar = {f["id"]: f["can_delete"] for f in client.get("/api/fonts").get_json()["fonts"]}
    assert bayraklar == {benim: True, onun: True}


def test_okuma_rolunde_can_delete_kapali(client):
    """A content creator sees the list but no font has the delete flag on."""
    login_as(client, DESIGNER)
    _yukle(client)
    login_as(client, CONTENT_CREATOR)
    fonts_ = client.get("/api/fonts").get_json()["fonts"]
    assert fonts_ and all(f["can_delete"] is False for f in fonts_)
