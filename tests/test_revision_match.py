"""Revize eşleşme kuralı — saf modül testleri (DB'ye/Drive'a dokunmaz).

Örnekler gerçekçi video dosya adlandırma kalıplarını (tarih eki, 'r'/'rev'
revize eki, sürüm numarası) temsil eder. Aşağıdaki negatifler, "insan
gözüyle belki revize ama adı kanıtlamıyor" sınıfını koruyor — kural
onlara dokunmamalı.
"""
import pytest

import revision_match as rm


# --- normalize: Türkçe katlama ---

@pytest.mark.parametrize("a,b", [
    ("İNCİ0725R.mp4", "inci0725r"),      # İ → i (düz casefold 'i̇' üretirdi)
    ("KOZA0730R.mp4", "koza0730r"),
    ("Gök-Taş - 25.mp4", "gök-taş - 25"),
    ("GÜZEL  ÇOCUKLAR  - 23.mp4", "güzel çocuklar - 23"),   # boşluk teklenir
    ("  papatya (1).mp4  ", "papatya (1)"),
])
def test_normalize(a, b):
    assert rm.normalize(a) == b


def test_nfd_ve_nfc_ayni_sayilir():
    """REGRESYON: üretimdeki adlar NFD geliyor (`ş` = s + birleşen çengel).
    macOS/iPhone NFD, Windows/Android NFC üretir — videograf cihaz
    değiştirdiğinde iki sürüm farklı formda kaydolur ve eşleşme sessizce
    kaçardı (2026-08-01, canlı veride yakalandı)."""
    import unicodedata as ud
    nfd = ud.normalize("NFD", "göktaşinşaat0801.mp4")
    nfc = ud.normalize("NFC", "göktaşinşaat0801.mp4")
    assert nfd != nfc, "test kurulumu: iki form gerçekten farklı olmalı"
    assert rm.normalize(nfd) == rm.normalize(nfc)


def test_nfd_yeni_nfc_eski_eslesir():
    """Yeni dosya NFD, eski NFC (ya da tersi) → yine de eşleşmeli."""
    import unicodedata as ud
    eski = _K(ud.normalize("NFC", "göktaşinşaat0801.mp4"))
    yeni = ud.normalize("NFD", "göktaşinşaat0801r.mp4")
    assert rm.find_superseded(yeni, [eski]) == [eski]


def test_normalize_turkce_I_ciftini_esitler():
    """`İNCİ0725R` ile `inci0725r` aynı kökten sayılmalı — yoksa İNCİ
    müşterisinin revizesi hiç eşleşmezdi (üretimde görülen bir örüntü)."""
    assert rm.normalize("İNCİ0725R.mp4") == rm.normalize("inci0725r.MP4")


# --- base_candidates: ek çıkarma ---

@pytest.mark.parametrize("yeni,beklenen", [
    # 'r' eki — tarihten ÖNCE
    ("göktaşr0728.mp4", "göktaş0728"),
    ("orkider0727.mp4", "orkide0727"),
    ("papatyar0725.mp4", "papatya0725"),
    # 'r' eki — tarihten SONRA
    ("göktaşinşaat0801r.mp4", "göktaşinşaat0801"),
    ("İNCİ0725R.mp4", "inci0725"),
    ("KOZA0730R.mp4", "koza0730"),
    # 'rev' eki
    ("sardunyarev0731.mp4", "sardunya0731"),
])
def test_harf_eki_aday_taban_uretir(yeni, beklenen):
    assert beklenen in rm.base_candidates(yeni)


# --- version_key: sayılı sürüm ekleri ---

@pytest.mark.parametrize("ad,taban,no", [
    # İKİ seviyeli → sürüm eki
    ("GÜZEL ÇOCUKLAR - 23 - 2.mp4", "güzel çocuklar - 23", 2),
    ("Örnek Spor Kulübü - 28 - 4.mp4", "örnek spor kulübü - 28", 4),
    ("göktaş0728.2.mp4", "göktaş0728", 2),
    # TEK seviyeli → HAFTA videosu, sürüm değil (no=0)
    ("GÜZEL ÇOCUKLAR - 23.mp4", "güzel çocuklar - 23", 0),
    ("Deniz İnşaat - 28.mp4", "deniz inşaat - 28", 0),
    ("göktaş0728.mp4", "göktaş0728", 0),
])
def test_version_key(ad, taban, no):
    assert rm.version_key(ad) == (taban, no)


def test_kendisi_aday_degildir():
    """Dosya kendi kendisinin eski sürümü olamaz."""
    assert rm.normalize("göktaşr0728.mp4") not in rm.base_candidates("göktaşr0728.mp4")


def test_eksiz_ad_bos_aday_uretir():
    """İçinde r/rev/sayı eki olmayan ad hiçbir şeyi süpersede etmemeli."""
    assert rm.base_candidates("oyun0731.mp4") == set()


# --- find_superseded: hangi kayıtlar eski sürüm ---

class _K:
    """Test için minimal kayıt (gerçekte CardUpload)."""
    def __init__(self, ad, sira=0):
        self.file_name = ad
        self.sira = sira


def test_gercek_revize_ciftleri_eslesir():
    eski = _K("göktaş0728.mp4")
    assert rm.find_superseded("göktaşr0728.mp4", [eski]) == [eski]


def test_zincir_ara_surumleri_de_yakalar():
    """`- 28 - 4` gelince `- 28`, `- 28 - 2`, `- 28 - 3` birlikte gitmeli.
    Yalnız tam ad karşılaştırılsaydı ara sürümler birikirdi (üretimde görülen bir örüntü:
    Örnek Spor Kulübü'nde dört sürüm yan yana duruyor)."""
    t = "Örnek Spor Kulübü"
    eskiler = [_K(f"{t} - 28.mp4"), _K(f"{t} - 28 - 2.mp4"), _K(f"{t} - 28 - 3.mp4")]
    bulunan = rm.find_superseded(f"{t} - 28 - 4.mp4", eskiler)
    assert set(bulunan) == set(eskiler)


@pytest.mark.parametrize("yeni,eski", [
    ("Deniz İnşaat - 28.mp4", "Deniz İnşaat - 27.mp4"),
    ("Yıldız Mimarlık - 24.mp4", "Yıldız Mimarlık - 23.mp4"),
    ("Deniz İnşaat - 25.mp4", "Deniz İnşaat - 23.mp4"),
    ("OYUN EVİ - 26.mp4", "OYUN EVİ - 24.mp4"),
])
def test_ardisik_hafta_videolari_surum_degildir(yeni, eski):
    """REGRESYON (2026-08-01 kuru koşusu): `MÜŞTERİ - 27` ve `MÜŞTERİ - 28`
    FARKLI HAFTALARIN videoları, sürüm çifti değil. İlk kural ikisini de
    'MÜŞTERİ' tabanına indirip kardeş sanıyordu → sağlam videolar silinecekti
    (yalnız paylaşımda oldukları için kurtuldular)."""
    assert rm.find_superseded(yeni, [_K(eski)]) == []


def test_farkli_taban_eslesmez():
    """`sardunyains0723` ile `sardunyarev0731`: ikisi de Sardunya'nın ama farklı çekim.
    'rev' içeriyor diye eşleşirse sağlam video silinirdi."""
    assert rm.find_superseded("sardunyarev0731.mp4", [_K("sardunyains0723.mp4")]) == []


@pytest.mark.parametrize("yeni,eski", [
    ("kzrev.mp4", "KOZA0725.mp4"),          # taban 'kz' ≠ 'koza0725'
    ("GUNESRENKREV.mp4", "gunes0725.mp4"),    # taban 'gunesrenk' ≠ 'gunes0725'
    ("oyun0731.mp4", "oyun0721.mp4"),         # farklı tarih, ek yok
    ("md0727.mp4", "md0724.mp4"),
])
def test_ad_kanitlamayan_dosyalara_dokunulmaz(yeni, eski):
    """İnsan gözüyle revize olabilir ama ad bunu kanıtlamıyor → dokunma.
    Kaçırmak, sağlam videoyu silmekten iyidir."""
    assert rm.find_superseded(yeni, [_K(eski)]) == []


def test_bos_liste_bos_sonuc():
    assert rm.find_superseded("göktaşr0728.mp4", []) == []


def test_adsiz_kayit_patlatmaz():
    assert rm.find_superseded("göktaşr0728.mp4", [_K(None), _K("")]) == []
    assert rm.base_candidates(None) == set()
