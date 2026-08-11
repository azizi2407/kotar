"""mail_crypto — Fernet round-trip and behavior when no key is present."""
import importlib

import pytest
from cryptography.fernet import Fernet


def _reload(monkeypatch, key):
    if key is None:
        monkeypatch.delenv('MAIL_ENC_KEY', raising=False)
    else:
        monkeypatch.setenv('MAIL_ENC_KEY', key)
    import mail_crypto
    return importlib.reload(mail_crypto)


def test_roundtrip(monkeypatch):
    mc = _reload(monkeypatch, Fernet.generate_key().decode())
    assert mc.available() is True
    assert mc.decrypt(mc.encrypt('45124512*Talat')) == '45124512*Talat'


def test_missing_key_unavailable(monkeypatch):
    mc = _reload(monkeypatch, None)
    assert mc.available() is False
    with pytest.raises(mc.MailCryptoError):
        mc.encrypt('x')


def test_bad_token_raises(monkeypatch):
    mc = _reload(monkeypatch, Fernet.generate_key().decode())
    with pytest.raises(mc.MailCryptoError):
        mc.decrypt('not-a-valid-token')
