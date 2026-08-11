"""Drive Gateway — units that don't require network (env parsing, error mapping)."""
import json

import pytest

import drive_gateway as dg


def test_available_toggle(monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_TOKEN_JSON", raising=False)
    assert dg.available() is False
    monkeypatch.setenv("GOOGLE_SA_JSON", "{}")
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_JSON", "{}")
    assert dg.available() is True


def test_sa_json_yoksa_auth_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    with pytest.raises(dg.DriveAuthError):
        dg._sa_info()


def test_sa_info_newline_duzeltme(monkeypatch):
    # private_key may contain literal \n in env round-trip → should become an actual newline
    monkeypatch.setenv("GOOGLE_SA_JSON", json.dumps({
        "type": "service_account",
        "private_key": "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n",
    }))
    info = dg._sa_info()
    assert "\n" in info["private_key"]
    assert "\\n" not in info["private_key"]


def test_token_json_yoksa_auth_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_DRIVE_TOKEN_JSON", raising=False)
    with pytest.raises(dg.DriveAuthError):
        dg._token_info()


class _FakeResp:
    def __init__(self, status):
        self.status = status


class _FakeHttpError(Exception):
    def __init__(self, status):
        self.resp = _FakeResp(status)


def test_wrap_401_auth_error():
    assert isinstance(dg._wrap(_FakeHttpError(401)), dg.DriveAuthError)
    assert isinstance(dg._wrap(_FakeHttpError(403)), dg.DriveAuthError)


def test_wrap_500_generic_error():
    e = dg._wrap(_FakeHttpError(500))
    assert isinstance(e, dg.DriveError)
    assert not isinstance(e, dg.DriveAuthError)


def test_count_files_bos_folder_none():
    assert dg.count_files(None) is None
    assert dg.count_files("") is None


def test_trash_file_trashed_true_gonderir(monkeypatch):
    """Trash, NOT permanent delete — a wrong click gets a 30-day undo window."""
    seen = {}

    class _Files:
        def update(self, **kw):
            seen.update(kw)
            return self

        def execute(self):
            return {"id": seen.get("fileId"), "trashed": True}

    class _Svc:
        def files(self):
            return _Files()

    monkeypatch.setattr(dg, "_service", lambda kind: _Svc())
    out = dg.trash_file("FID1")
    assert seen["fileId"] == "FID1"
    assert seen["body"] == {"trashed": True}
    assert out["trashed"] is True


def test_trash_file_hatasi_drive_error(monkeypatch):
    class _Svc:
        def files(self):
            raise RuntimeError("ağ yok")

    monkeypatch.setattr(dg, "_service", lambda kind: _Svc())
    with pytest.raises(dg.DriveError):
        dg.trash_file("FID1")
