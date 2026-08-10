"""gunicorn.conf.py — fork sonrası DB havuzu muhafızı.

`preload_app=True` uygulamayı master süreçte kurar; `app.py` modül sonundaki
`create_app()` orada `db.create_all()` çağırıp havuzda gerçek bir Postgres
bağlantısı bırakır. Fork'ta bu soket her iki worker'a birden miras kalır ve iki
süreç aynı TCP soketinde konuşunca psycopg protokolü bozulur (canlıda 2026-08-09
18:09-18:15: IndexError / ResourceClosedError / "server closed the connection").
Çözüm `post_fork` içinde `engine.dispose(close=False)`. Buradaki testler o
eşleşmenin sessizce bozulmasını engeller.
"""
import importlib.util
import pathlib

import pytest

# `gunicorn.conf` adıyla yüklemek kurulu `gunicorn` paketiyle çakışır → ayrık ad.
_KONF_YOLU = pathlib.Path(__file__).resolve().parent.parent / 'gunicorn.conf.py'


@pytest.fixture(scope='module')
def konf():
    spec = importlib.util.spec_from_file_location('agency_gunicorn_conf', _KONF_YOLU)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_preload_acikken_post_fork_zorunlu(konf):
    """Asıl muhafız: `preload_app` açıksa fork'ta havuzu atan bir kanca ŞART.

    Biri `preload_app=True` bırakıp `post_fork`'u silerse (ya da tersine çevirip
    kancayı gereksiz sanıp kaldırırsa) bu test düşer.
    """
    if getattr(konf, 'preload_app', False):
        assert callable(getattr(konf, 'post_fork', None)), (
            'preload_app=True iken post_fork kancası olmadan DB bağlantısı '
            'worker\'lara miras kalır — bkz. gunicorn.conf.py yorumu.'
        )


def test_post_fork_havuzu_close_false_ile_atar(konf, app, monkeypatch):
    """`close=False` kritik: soket kapatılmaz, yalnız bu sürecin havuzundan düşer.

    `close=True` olsaydı çocuk, kardeş worker'ın hâlâ kullandığı soketi de
    kapatırdı — düzeltmenin kendisi yeni bir yarış yaratırdı.
    """
    from extensions import db

    cagrilar = []
    monkeypatch.setattr(
        type(db.engine), 'dispose',
        lambda self, close=True: cagrilar.append(close),
        raising=True,
    )

    konf.post_fork(server=None, worker=None)

    assert cagrilar == [False], f'dispose çağrıları beklenenden farklı: {cagrilar}'


# --- whisper-service ile kazara config paylaşımı ---------------------------
#
# `whisper-service.service` `WorkingDirectory=/srv/apps/agency` ile koşuyor.
# Gunicorn `-c` verilmediğinde cwd'deki `gunicorn.conf.py`'yi sessizce yükler →
# whisper yıllardır PANELİN config'ini devralıyormuş. 2026-08-09'da panele
# `post_fork` eklenince whisper worker'ı `from app import app` → `KeyError:
# 'SECRET_KEY'` ile boot edemedi ve servis `failed`'a düştü: transkripsiyon
# tümden durdu, üstelik sessizce (socket ayakta, istek reset yiyor).

_WHISPER_KONF_YOLU = pathlib.Path(__file__).resolve().parent.parent / 'whisper_gunicorn.conf.py'
_WHISPER_UNIT_YOLU = pathlib.Path.home() / '.config/systemd/user/whisper-service.service'


@pytest.fixture(scope='module')
def whisper_konf():
    spec = importlib.util.spec_from_file_location(
        'agency_whisper_gunicorn_conf', _WHISPER_KONF_YOLU)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_whisper_konfu_panelin_kancasini_tasimaz(whisper_konf):
    """Whisper'ın kendi config'i panele ait DB kancasını ASLA taşımamalı.

    Whisper süreci Flask app'ini (`app.py`) hiç import etmez; SECRET_KEY gibi
    panel env'i orada yoktur. Buraya bir `post_fork` kopyalanırsa servis boot
    edemez.
    """
    assert not hasattr(whisper_konf, 'post_fork')
    assert whisper_konf.workers == 1, 'ikinci worker modeli RAM\'de ikiye katlar'


@pytest.mark.skipif(not _WHISPER_UNIT_YOLU.exists(),
                    reason='whisper unit dosyası yalnız bu sunucuda var')
def test_whisper_uniti_kendi_configini_acikca_veriyor():
    """Unit `-c` ile kendi config'ini vermeli — yoksa panelinkini devralır."""
    icerik = _WHISPER_UNIT_YOLU.read_text(encoding='utf-8')
    exec_satiri = next(
        (s for s in icerik.splitlines() if s.startswith('ExecStart=')), '')
    assert exec_satiri, 'unit dosyasında ExecStart yok'
    assert ('-c ' in exec_satiri or '--config' in exec_satiri), (
        f'whisper gunicorn\'u -c olmadan başlatılıyor → cwd\'deki panel '
        f'config\'ini sessizce yükler: {exec_satiri}'
    )
    assert 'whisper_gunicorn.conf.py' in exec_satiri
