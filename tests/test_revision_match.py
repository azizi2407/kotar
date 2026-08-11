"""Revision matching rule — pure module tests (does not touch DB/Drive).

The examples represent realistic video file naming patterns (date suffix,
'r'/'rev' revision suffix, version number). The negative cases below guard
the "might look like a revision to a human but the name doesn't prove it"
class — the rule must not touch those.
"""
import pytest

import revision_match as rm


# --- normalize: Turkish casefolding ---

@pytest.mark.parametrize("a,b", [
    ("İNCİ0725R.mp4", "inci0725r"),      # İ → i (plain casefold would produce 'i̇')
    ("KOZA0730R.mp4", "koza0730r"),
    ("Gök-Taş - 25.mp4", "gök-taş - 25"),
    ("GÜZEL  ÇOCUKLAR  - 23.mp4", "güzel çocuklar - 23"),   # whitespace collapsed
    ("  papatya (1).mp4  ", "papatya (1)"),
])
def test_normalize(a, b):
    assert rm.normalize(a) == b


def test_nfd_ve_nfc_ayni_sayilir():
    """REGRESSION: names in production arrive as NFD (`ş` = s + combining cedilla).
    macOS/iPhone produce NFD, Windows/Android produce NFC — when a videographer
    switches devices, the two versions get saved in different forms and matching
    would silently miss them (caught in production data on 2026-08-01)."""
    import unicodedata as ud
    nfd = ud.normalize("NFD", "göktaşinşaat0801.mp4")
    nfc = ud.normalize("NFC", "göktaşinşaat0801.mp4")
    assert nfd != nfc, "test kurulumu: iki form gerçekten farklı olmalı"
    assert rm.normalize(nfd) == rm.normalize(nfc)


def test_nfd_yeni_nfc_eski_eslesir():
    """New file NFD, old file NFC (or vice versa) → should still match."""
    import unicodedata as ud
    eski = _K(ud.normalize("NFC", "göktaşinşaat0801.mp4"))
    yeni = ud.normalize("NFD", "göktaşinşaat0801r.mp4")
    assert rm.find_superseded(yeni, [eski]) == [eski]


def test_normalize_turkce_I_ciftini_esitler():
    """`İNCİ0725R` and `inci0725r` must be considered the same root — otherwise
    the İNCİ client's revision would never match (a pattern seen in production)."""
    assert rm.normalize("İNCİ0725R.mp4") == rm.normalize("inci0725r.MP4")


# --- base_candidates: suffix extraction ---

@pytest.mark.parametrize("yeni,beklenen", [
    # 'r' suffix — BEFORE the date
    ("göktaşr0728.mp4", "göktaş0728"),
    ("orkider0727.mp4", "orkide0727"),
    ("papatyar0725.mp4", "papatya0725"),
    # 'r' suffix — AFTER the date
    ("göktaşinşaat0801r.mp4", "göktaşinşaat0801"),
    ("İNCİ0725R.mp4", "inci0725"),
    ("KOZA0730R.mp4", "koza0730"),
    # 'rev' suffix
    ("sardunyarev0731.mp4", "sardunya0731"),
])
def test_harf_eki_aday_taban_uretir(yeni, beklenen):
    assert beklenen in rm.base_candidates(yeni)


# --- version_key: numbered version suffixes ---

@pytest.mark.parametrize("ad,taban,no", [
    # TWO-level → version suffix
    ("GÜZEL ÇOCUKLAR - 23 - 2.mp4", "güzel çocuklar - 23", 2),
    ("Örnek Spor Kulübü - 28 - 4.mp4", "örnek spor kulübü - 28", 4),
    ("göktaş0728.2.mp4", "göktaş0728", 2),
    # ONE-level → WEEK video, not a version (no=0)
    ("GÜZEL ÇOCUKLAR - 23.mp4", "güzel çocuklar - 23", 0),
    ("Deniz İnşaat - 28.mp4", "deniz inşaat - 28", 0),
    ("göktaş0728.mp4", "göktaş0728", 0),
])
def test_version_key(ad, taban, no):
    assert rm.version_key(ad) == (taban, no)


def test_kendisi_aday_degildir():
    """A file cannot be its own older version."""
    assert rm.normalize("göktaşr0728.mp4") not in rm.base_candidates("göktaşr0728.mp4")


def test_eksiz_ad_bos_aday_uretir():
    """A name with no r/rev/number suffix must not supersede anything."""
    assert rm.base_candidates("oyun0731.mp4") == set()


# --- find_superseded: which records are older versions ---

class _K:
    """Minimal record for testing (CardUpload in reality)."""
    def __init__(self, ad, sira=0):
        self.file_name = ad
        self.sira = sira


def test_gercek_revize_ciftleri_eslesir():
    eski = _K("göktaş0728.mp4")
    assert rm.find_superseded("göktaşr0728.mp4", [eski]) == [eski]


def test_zincir_ara_surumleri_de_yakalar():
    """When `- 28 - 4` arrives, `- 28`, `- 28 - 2`, `- 28 - 3` should all go together.
    If only exact names were compared, intermediate versions would pile up (a pattern
    seen in production: Örnek Spor Kulübü had four versions sitting side by side)."""
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
    """REGRESSION (2026-08-01 dry run): `MÜŞTERİ - 27` and `MÜŞTERİ - 28` are
    videos from DIFFERENT WEEKS, not a version pair. The first version of the
    rule reduced both to the 'MÜŞTERİ' base and treated them as siblings →
    intact videos would have been deleted (saved only because they were shared)."""
    assert rm.find_superseded(yeni, [_K(eski)]) == []


def test_farkli_taban_eslesmez():
    """`sardunyains0723` and `sardunyarev0731`: both are Sardunya's but different shoots.
    If it matched just because it contains 'rev', an intact video would get deleted."""
    assert rm.find_superseded("sardunyarev0731.mp4", [_K("sardunyains0723.mp4")]) == []


@pytest.mark.parametrize("yeni,eski", [
    ("kzrev.mp4", "KOZA0725.mp4"),          # base 'kz' ≠ 'koza0725'
    ("GUNESRENKREV.mp4", "gunes0725.mp4"),    # base 'gunesrenk' ≠ 'gunes0725'
    ("oyun0731.mp4", "oyun0721.mp4"),         # different date, no suffix
    ("md0727.mp4", "md0724.mp4"),
])
def test_ad_kanitlamayan_dosyalara_dokunulmaz(yeni, eski):
    """Might look like a revision to a human, but the name doesn't prove it → don't touch.
    Missing one is better than deleting an intact video."""
    assert rm.find_superseded(yeni, [_K(eski)]) == []


def test_bos_liste_bos_sonuc():
    assert rm.find_superseded("göktaşr0728.mp4", []) == []


def test_adsiz_kayit_patlatmaz():
    assert rm.find_superseded("göktaşr0728.mp4", [_K(None), _K("")]) == []
    assert rm.base_candidates(None) == set()
