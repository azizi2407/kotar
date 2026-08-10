"""Bildirim önem dereceleri + ntfy gönderim kararı (2026-08-05).

Ağırlık saf karar fonksiyonunda (`should_push_ntfy`): eşik, sessiz saat (gece
yarısını saran aralık dahil) ve kritik istisnası. Kanalın kendisi (HTTP) mock'lu.
"""
from types import SimpleNamespace

import pytest

from notify_rules import (BILGI, KRITIK, NORMAL, SEVERITIES, in_quiet_hours,
                          rank, should_push_ntfy)


def _pref(**kw):
    base = dict(ntfy_enabled=True, ntfy_topic='a' * 32, min_severity=KRITIK,
                quiet_start=None, quiet_end=None)
    base.update(kw)
    return SimpleNamespace(**base)


# --- katalog bütünlüğü ------------------------------------------------------

def test_katalogdaki_her_turun_gecerli_severity_si_var():
    """Yeni bir `kind` eklenip katalog güncellenmezse burası patlar."""
    from notifications import CATALOG
    assert CATALOG, 'katalog boş olamaz'
    for kind, entry in CATALOG.items():
        assert entry.severity in SEVERITIES, f'{kind}: geçersiz severity'
        assert entry.audience, f'{kind}: audience boş'


def test_tetikleyicilerin_kullandigi_her_kind_katalogda():
    """TÜM modüllerde push edilen tür adları ile katalog ayrışmasın.

    Tarama bilerek `notifications.py` ile sınırlı DEĞİL: `provision_failed`
    (client_provision.py) ve `mail` (mail_service.py) gibi türler dışarıdan
    push ediliyor ve ilk sürümde biri katalogda yanlış adla durup sessizce
    NORMAL'e düşmüştü (2026-08-05). Bu test o sınıf hatayı yakalar."""
    import re
    from pathlib import Path

    from notifications import CATALOG
    kok = Path(__file__).resolve().parent.parent
    # push(<herhangi bir alıcı ifadesi>, 'kind', ...) ve _push_to_client_team('kind', ...)
    desenler = (r"push\([^,]+,\s*'([a-z_]+)'", r"_push_to_client_team\('([a-z_]+)'",
                r"_push_many\([^,]+,\s*'([a-z_]+)'")
    kullanilan = set()
    for yol in kok.glob('*.py'):
        metin = yol.read_text(encoding='utf-8')
        for desen in desenler:
            kullanilan |= set(re.findall(desen, metin))
    eksik = kullanilan - set(CATALOG)
    assert not eksik, f'katalogda olmayan tür(ler): {eksik}'


def test_katalogda_olup_hic_kullanilmayan_tur_yok():
    """Ters yön: katalogda ölü satır birikmesin (tür adı değişince eskisi kalır)."""
    import re
    from pathlib import Path

    from notifications import CATALOG
    kok = Path(__file__).resolve().parent.parent
    metin = '\n'.join(y.read_text(encoding='utf-8') for y in kok.glob('*.py'))
    olu = {k for k in CATALOG if f"'{k}'" not in metin.replace(f"'{k}':", '')}
    assert not olu, f'katalogda olup hiç push edilmeyen tür(ler): {olu}'


def test_bilinmeyen_tur_normal_sayilir():
    from notifications import severity_of
    assert severity_of('boyle_bir_tur_yok') == NORMAL


def test_rank_siralamasi():
    assert rank(KRITIK) > rank(NORMAL) > rank(BILGI)


# --- eşik -------------------------------------------------------------------

def test_kayit_yoksa_gonderilmez():
    """ntfy OPT-IN: tercih kaydı olmayan kullanıcıya push YOK."""
    assert should_push_ntfy(None, KRITIK, 12) is False


def test_kapaliysa_gonderilmez():
    assert should_push_ntfy(_pref(ntfy_enabled=False), KRITIK, 12) is False


def test_topic_yoksa_gonderilmez():
    assert should_push_ntfy(_pref(ntfy_topic=''), KRITIK, 12) is False


def test_esik_altindaki_gonderilmez():
    pref = _pref(min_severity=KRITIK)
    assert should_push_ntfy(pref, NORMAL, 12) is False
    assert should_push_ntfy(pref, BILGI, 12) is False
    assert should_push_ntfy(pref, KRITIK, 12) is True


def test_esik_gevsetilince_hepsi_gecer():
    pref = _pref(min_severity=BILGI)
    assert all(should_push_ntfy(pref, s, 12) for s in (BILGI, NORMAL, KRITIK))


# --- sessiz saat ------------------------------------------------------------

@pytest.mark.parametrize('now,beklenen', [(21, False), (22, True), (23, True),
                                          (3, True), (7, True), (8, False), (12, False)])
def test_gece_yarisini_saran_aralik(now, beklenen):
    assert in_quiet_hours(22, 8, now) is beklenen


@pytest.mark.parametrize('now,beklenen', [(8, False), (9, True), (17, True), (18, False)])
def test_duz_aralik(now, beklenen):
    assert in_quiet_hours(9, 18, now) is beklenen


def test_eksik_veya_esit_uclar_sessiz_saat_yok():
    assert in_quiet_hours(None, 8, 3) is False
    assert in_quiet_hours(22, None, 3) is False
    assert in_quiet_hours(10, 10, 10) is False


def test_sessiz_saatte_normal_beklenir_kritik_gecer():
    """Proje sahibi kararı: sessiz saat kritik bildirimi DURDURMAZ."""
    pref = _pref(min_severity=BILGI, quiet_start=22, quiet_end=8)
    assert should_push_ntfy(pref, NORMAL, 3) is False
    assert should_push_ntfy(pref, BILGI, 3) is False
    assert should_push_ntfy(pref, KRITIK, 3) is True
    # aralık dışında normal de geçer
    assert should_push_ntfy(pref, NORMAL, 12) is True


def test_sessiz_saat_esikten_sonra_degerlendirilir():
    """Eşik zaten elemişse sessiz saatin bir önemi yok — sıra karışırsa kritik
    olmayan bir bildirim sessiz saat dışında sızabilirdi."""
    pref = _pref(min_severity=KRITIK, quiet_start=22, quiet_end=8)
    assert should_push_ntfy(pref, NORMAL, 12) is False
