"""Monthly report — /api/reports + public /rapor/<token>.

The calculation layer (`aylik_rapor.py`) was MOVED from the project owner's desktop
tool; this file locks in not its internal heuristic rules, but the **web side's
contract**: upload→client parsing, batch generation, overwriting, permissions, share
token. The PDF endpoint requiring Chromium is marked separately (a single test,
skipped if chromium isn't present in the environment).
"""
import io
import os
import shutil

import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

# Meta's "single-field title + Tarih,Primary" format — the shape of real exports.
def _metrik_csv(baslik, *degerler):
    satir = "".join(f'"2026-07-0{i+1}","{v}"\n' for i, v in enumerate(degerler))
    return (f'{baslik}\n"Tarih","Primary"\n' + satir).encode("utf-8")


GONDERI = ('ID,Başlık,Yayınlanma tarihi,Erişim\n'
           '1,İlk gönderi,2026-07-01,900\n2,İkinci,2026-07-05,1500\n').encode("utf-8")
HIKAYE = ('ID,Başlık,Yayınlanma tarihi,Görüntülemeler\n'
          '1,Story A,2026-07-02,300\n').encode("utf-8")
REKLAM = ('Reklamlar,Gün,Yaş,Cinsiyet,Harcanan Tutar (TRY),Erişim,Gösterim,'
          'Tıklamalar (Tümü),Instagram takipleri\n'
          'Kampanya A,2026-07-01,25-34,female,"150,50",5000,7000,120,8\n').encode("utf-8")


def _dosyalar(musteri=None, reklam=False, eksik=False):
    """List of (field_name, (bytes, file_name)) + paths[] values.

    If `musteri` is given, the path becomes "<musteri>/<dosya>" → batch flow; if not,
    a plain file name → single-client flow (the name comes from the client_name
    field)."""
    kayitlar = [
        ("goruntulemeler.csv", _metrik_csv("Görüntülemeler", "1234", "2345")),
        ("erisim.csv", _metrik_csv("Erişim", "800", "1100")),
        ("etkilesim.csv", _metrik_csv("Etkileşim", "50", "75")),
        ("takipler.csv", _metrik_csv("Takipler", "12", "3")),
    ]
    if not eksik:
        kayitlar += [("gonderiler.csv", GONDERI), ("hikayeler.csv", HIKAYE)]
    if reklam:
        kayitlar.append(("meta-reklam-raporu.csv", REKLAM))
    files, paths = [], []
    for ad, veri in kayitlar:
        files.append((io.BytesIO(veri), ad))
        paths.append(f"{musteri}/{ad}" if musteri else ad)
    return files, paths


def _uret(client, musteriler=None, period="2026-07", client_name="Test Müşteri",
          reklam=False, eksik=False):
    data = {"period": period, "client_name": client_name}
    files, paths = [], []
    for m in (musteriler or [None]):
        f, p = _dosyalar(m, reklam=reklam, eksik=eksik)
        files += f
        paths += p
    data["files"] = files
    data["paths"] = paths
    return client.post("/api/reports/generate", data=data,
                       content_type="multipart/form-data",
                       headers=csrf_headers(client))


# --- generation -----------------------------------------------------------------

def test_tek_musteri_rapor_uretir(client):
    login_as(client, MANAGER)
    r = _uret(client, client_name="Şençiçek Gıda")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert len(d["reports"]) == 1
    rapor = d["reports"][0]
    assert rapor["client_name"] == "Şençiçek Gıda"
    assert rapor["period"] == "2026-07"
    assert rapor["ozet"]["Toplam Görüntüleme"] == 3579
    assert rapor["ozet"]["Toplam Erişim"] == 1900


def test_toplu_uretim_klasor_basina_rapor(client):
    """`paths[]` = the browser's webkitRelativePath — each folder is a client."""
    login_as(client, MANAGER)
    r = _uret(client, musteriler=["Budak Kağıt", "KORKUTELİ OSB", "Casaba Mahir"])
    assert r.status_code == 200
    adlar = {x["client_name"] for x in r.get_json()["reports"]}
    assert adlar == {"Budak Kağıt", "KORKUTELİ OSB", "Casaba Mahir"}


def test_eksik_csv_atlanir_ve_sebebi_bildirilir(client):
    """A silent skip would lead to thinking 'I got the report' without noticing the
    missing CSV."""
    login_as(client, MANAGER)
    r = _uret(client, musteriler=["Tam Müşteri"])
    assert len(r.get_json()["reports"]) == 1
    r = _uret(client, musteriler=["Eksik Müşteri"], eksik=True)
    d = r.get_json()
    assert d["reports"] == []
    assert len(d["skipped"]) == 1
    assert d["skipped"][0]["client_name"] == "Eksik Müşteri"
    assert "Gönderiler CSV" in d["skipped"][0]["reason"]


def test_reklam_csv_opsiyonel(client):
    login_as(client, MANAGER)
    assert _uret(client).get_json()["reports"][0]["reklam_var"] is False
    r = _uret(client, reklam=True, period="2026-06")
    assert r.get_json()["reports"][0]["reklam_var"] is True


def test_ayni_donem_uzerine_yazar_token_korunur(client):
    """When the missing CSV is completed and re-uploaded, there shouldn't be two
    conflicting reports."""
    login_as(client, MANAGER)
    rid = _uret(client, client_name="Tekrar").get_json()["reports"][0]["id"]
    token = client.post(f"/api/reports/{rid}/share", json={"shared": True},
                        headers=csrf_headers(client)).get_json()["report"]["token"]
    ikinci = _uret(client, client_name="Tekrar").get_json()["reports"][0]
    assert ikinci["id"] == rid                    # no new row was created
    assert ikinci["token"] == token               # the distributed link wasn't broken
    assert len(client.get("/api/reports").get_json()["reports"]) == 1


def test_farkli_donem_ayri_rapor(client):
    login_as(client, MANAGER)
    _uret(client, client_name="Aynı Müşteri", period="2026-06")
    _uret(client, client_name="Aynı Müşteri", period="2026-07")
    assert len(client.get("/api/reports").get_json()["reports"]) == 2


def test_gecersiz_donem_400(client):
    login_as(client, MANAGER)
    for kotu in ("2026-13", "temmuz", "2026", ""):
        assert _uret(client, period=kotu).status_code == 400


def test_dosyasiz_istek_400(client):
    login_as(client, MANAGER)
    r = client.post("/api/reports/generate", data={"period": "2026-07"},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 400


def test_csv_disi_dosya_atlanir(client):
    login_as(client, MANAGER)
    files, paths = _dosyalar("Müşteri")
    files.append((io.BytesIO(b"okuma notu"), "README.txt"))
    paths.append("Müşteri/README.txt")
    r = client.post("/api/reports/generate",
                    data={"period": "2026-07", "files": files, "paths": paths},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 200
    assert len(r.get_json()["reports"]) == 1


def test_yol_kacisi_gecici_dizinde_kalir(client, tmp_path):
    """`paths[]` comes from the client: a '../../etc' attempt shouldn't write outside
    the directory."""
    login_as(client, MANAGER)
    files, paths = _dosyalar()
    paths = [f"../../../../tmp/kacis/{os.path.basename(p)}" for p in paths]
    r = client.post("/api/reports/generate",
                    data={"period": "2026-07", "files": files, "paths": paths,
                          "client_name": "Kaçış"},
                    content_type="multipart/form-data", headers=csrf_headers(client))
    assert r.status_code == 200
    assert not os.path.exists("/tmp/kacis")
    # The client name is derived from the path segment but is always a single segment
    assert "/" not in r.get_json()["reports"][0]["client_name"]


def test_panel_musterisiyle_eslesir(client):
    """If the name matches, client_id gets linked; if not, the report is still
    generated."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Budak Kağıt"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    r = _uret(client, musteriler=["Budak Kağıt", "Panelde Olmayan"])
    esleme = {x["client_name"]: x["client_id"] for x in r.get_json()["reports"]}
    assert esleme["Budak Kağıt"] == cid
    assert esleme["Panelde Olmayan"] is None


# --- permissions ------------------------------------------------------------------

def test_tasarimciya_kapali(client):
    login_as(client, DESIGNER)
    assert _uret(client).status_code == 403
    assert client.get("/api/reports").status_code == 403


def test_oturumsuz_401(client):
    assert client.get("/api/reports").status_code == 401


# --- viewing ------------------------------------------------------------

def test_html_ciktisi_veriyi_gomer(client):
    login_as(client, MANAGER)
    rid = _uret(client, client_name="Şirket Ğ").get_json()["reports"][0]["id"]
    r = client.get(f"/api/reports/{rid}/html")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "const reportData" in html
    assert "Şirket Ğ - Monthly Report" in html
    # Logo embedded as a data URI → single file (also visible in the public link and PDF)
    assert 'src="data:image/png;base64,' in html
    assert 'src="logo.png"' not in html
    assert r.headers.get("X-Robots-Tag") == "noindex, nofollow"


def test_rapor_detayi_veri_dondurur(client):
    login_as(client, MANAGER)
    rid = _uret(client, reklam=True).get_json()["reports"][0]["id"]
    d = client.get(f"/api/reports/{rid}").get_json()["report"]
    assert d["data"]["totals"]["Toplam Görüntüleme"] == 3579
    assert d["data"]["ads_data"]["metrics"]["Toplam Harcama (TRY)"] == 150.5


def test_olmayan_rapor_404(client):
    login_as(client, MANAGER)
    assert client.get("/api/reports/999999").status_code == 404
    assert client.get("/api/reports/999999/html").status_code == 404


def test_donem_listesi(client):
    login_as(client, MANAGER)
    _uret(client, client_name="A", period="2026-06")
    _uret(client, client_name="B", period="2026-07")
    _uret(client, client_name="C", period="2026-07")
    p = client.get("/api/reports/periods").get_json()["periods"]
    assert p == [{"period": "2026-07", "count": 2}, {"period": "2026-06", "count": 1}]


# --- sharing ---------------------------------------------------------------

def test_paylasim_linki_public_erisir(client):
    login_as(client, MANAGER)
    rid = _uret(client, client_name="Public Test").get_json()["reports"][0]["id"]
    # No public access without a generated token
    assert client.get("/rapor/olmayan-token").status_code == 404
    token = client.post(f"/api/reports/{rid}/share", json={"shared": True},
                        headers=csrf_headers(client)).get_json()["report"]["token"]
    with client.session_transaction() as sess:
        sess.clear()
    r = client.get(f"/rapor/{token}")
    assert r.status_code == 200
    assert "Public Test - Monthly Report" in r.get_data(as_text=True)


def test_paylasim_iptali_404_verir_ve_geri_alinabilir(client):
    login_as(client, MANAGER)
    rid = _uret(client).get_json()["reports"][0]["id"]
    token = client.post(f"/api/reports/{rid}/share", json={"shared": True},
                        headers=csrf_headers(client)).get_json()["report"]["token"]
    client.post(f"/api/reports/{rid}/share", json={"shared": False},
                headers=csrf_headers(client))
    assert client.get(f"/rapor/{token}").status_code == 404
    # Reopening it revives the SAME address — the link sent to the client shouldn't change
    yeni = client.post(f"/api/reports/{rid}/share", json={"shared": True},
                       headers=csrf_headers(client)).get_json()["report"]["token"]
    assert yeni == token
    assert client.get(f"/rapor/{token}").status_code == 200


def test_iptal_edilen_rapor_listede_tokensiz(client):
    login_as(client, MANAGER)
    rid = _uret(client).get_json()["reports"][0]["id"]
    client.post(f"/api/reports/{rid}/share", json={"shared": True},
                headers=csrf_headers(client))
    client.post(f"/api/reports/{rid}/share", json={"shared": False},
                headers=csrf_headers(client))
    kayit = client.get("/api/reports").get_json()["reports"][0]
    assert kayit["token"] is None and kayit["revoked"] is True


def test_silme(client):
    login_as(client, MANAGER)
    rid = _uret(client).get_json()["reports"][0]["id"]
    assert client.delete(f"/api/reports/{rid}",
                         headers=csrf_headers(client)).status_code == 200
    assert client.get("/api/reports").get_json()["reports"] == []


# --- PDF (requires real Chromium; a real subprocess render, slow/flaky on some
# CI runners' snap-packaged chromium — same "not run by default" treatment as
# the other `integration`-marked tests that hit a real external dependency) ---

@pytest.mark.integration
@pytest.mark.skipif(not shutil.which(os.environ.get("CHROMIUM_BIN") or "chromium"),
                    reason="chromium is not installed")
def test_pdf_uretilir(client):
    login_as(client, MANAGER)
    rid = _uret(client, client_name="PDF Testi").get_json()["reports"][0]["id"]
    r = client.get(f"/api/reports/{rid}/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:5] == b"%PDF-"
    assert "attachment" in r.headers.get("Content-Disposition", "")


# --- shape of the page sent to the client (2026-08-07) ----------------------------
# The template came from the desktop tool and had a "Rapor Düzenleyici" (Report
# Editor) form in it (text/number fields + "Raporu Güncelle"). The page is now sent
# to the client; these tests catch the form leaking back in and the palette
# diverging from the special-day page.

def _public_html(client):
    login_as(client, MANAGER)
    rid = _uret(client, client_name="Biçim Testi").get_json()["reports"][0]["id"]
    token = client.post(f"/api/reports/{rid}/share", json={"shared": True},
                        headers=csrf_headers(client)).get_json()["report"]["token"]
    with client.session_transaction() as sess:
        sess.clear()
    return client.get(f"/rapor/{token}").get_data(as_text=True)


def test_musteri_sayfasinda_duzenleme_alani_yok(client):
    html = _public_html(client)
    assert "settings-panel" not in html
    # UI elements: there should be no form field, update button, or click handler.
    # (The JS COMMENT explaining the removal rationale may remain in the template — harmless.)
    assert "<input" not in html
    assert "Raporu Güncelle" not in html
    assert "onclick=" not in html


def test_musteri_sayfasi_ozel_gun_paletini_kullanir(client):
    """Same cream/gold language as the special-day selection page (special_days.py)."""
    html = _public_html(client)
    assert "#FAFAF8" in html          # cream background
    assert "#C9A96E" in html          # gold accent
    assert "Cormorant+Garamond" in html and "DM+Sans" in html
    # The old dark palette is gone now
    assert "#090c13" not in html and "#14181f" not in html


def test_gren_dokusu_yazdirmada_kapali(client):
    """The texture gives a paper feel on screen but when rasterized to PDF it bumped
    the file from ~360 KB to ~3.6 MB (a bitmap covering the whole page)."""
    html = _public_html(client)
    assert "body::before" in html                      # present on screen
    assert "body::before { display: none !important; }" in html   # absent when printing
