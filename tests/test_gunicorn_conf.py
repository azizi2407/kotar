"""gunicorn.conf.py — DB pool guard after fork.

`preload_app=True` builds the app in the master process; `create_app()` at the
end of `app.py`'s module calls `db.create_all()` there, leaving a real Postgres
connection in the pool. On fork, this socket is inherited by both workers at
once, and when two processes talk over the same TCP socket the psycopg protocol
breaks (in production 2026-08-09 18:09-18:15: IndexError / ResourceClosedError /
"server closed the connection"). The fix is `engine.dispose(close=False)` inside
`post_fork`. The tests here guard against that fix silently breaking again.
"""
import importlib.util
import pathlib

import pytest

# Loading it under the name `gunicorn.conf` clashes with the installed `gunicorn` package → distinct name.
_KONF_YOLU = pathlib.Path(__file__).resolve().parent.parent / 'gunicorn.conf.py'


@pytest.fixture(scope='module')
def konf():
    spec = importlib.util.spec_from_file_location('agency_gunicorn_conf', _KONF_YOLU)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_preload_acikken_post_fork_zorunlu(konf):
    """The actual guard: if `preload_app` is on, a hook that drops the pool on fork is MANDATORY.

    If someone leaves `preload_app=True` and deletes `post_fork` (or reverses it
    and removes the hook thinking it's unnecessary), this test fails.
    """
    if getattr(konf, 'preload_app', False):
        assert callable(getattr(konf, 'post_fork', None)), (
            'preload_app=True iken post_fork kancası olmadan DB bağlantısı '
            'worker\'lara miras kalır — bkz. gunicorn.conf.py yorumu.'
        )


def test_post_fork_havuzu_close_false_ile_atar(konf, app, monkeypatch):
    """`close=False` is critical: the socket isn't closed, it's only dropped from this process's pool.

    If `close=True`, the child would also close the socket the sibling worker is
    still using — the fix itself would create a new race condition.
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


# --- accidental config sharing with whisper-service -------------------------
#
# `whisper-service.service` runs with `WorkingDirectory=/srv/apps/agency`.
# When gunicorn isn't given `-c`, it silently loads the `gunicorn.conf.py` in
# the cwd → whisper had been inheriting the PANEL's config for years. When
# `post_fork` was added to the panel on 2026-08-09, the whisper worker's
# `from app import app` failed to boot with `KeyError: 'SECRET_KEY'` and the
# service dropped to `failed`: transcription stopped entirely, and silently
# too (the socket stayed up, requests just got reset).

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
    """Whisper's own config must NEVER carry the panel's DB hook.

    The whisper process never imports the Flask app (`app.py`); panel env like
    SECRET_KEY doesn't exist there. If a `post_fork` gets copied in here, the
    service can't boot.
    """
    assert not hasattr(whisper_konf, 'post_fork')
    assert whisper_konf.workers == 1, 'ikinci worker modeli RAM\'de ikiye katlar'


@pytest.mark.skipif(not _WHISPER_UNIT_YOLU.exists(),
                    reason='whisper unit dosyası yalnız bu sunucuda var')
def test_whisper_uniti_kendi_configini_acikca_veriyor():
    """The unit must give its own config via `-c` — otherwise it inherits the panel's."""
    icerik = _WHISPER_UNIT_YOLU.read_text(encoding='utf-8')
    exec_satiri = next(
        (s for s in icerik.splitlines() if s.startswith('ExecStart=')), '')
    assert exec_satiri, 'unit dosyasında ExecStart yok'
    assert ('-c ' in exec_satiri or '--config' in exec_satiri), (
        f'whisper gunicorn\'u -c olmadan başlatılıyor → cwd\'deki panel '
        f'config\'ini sessizce yükler: {exec_satiri}'
    )
    assert 'whisper_gunicorn.conf.py' in exec_satiri
