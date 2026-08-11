"""Fernet encryption for mail account passwords. The master key MAIL_ENC_KEY lives
in Infisical (base64 urlsafe 32B, Fernet.generate_key format). If the key is
missing, the module fails closed: available()=False, encrypt/decrypt raise
MailCryptoError. The key is read at call time (not at import) → monkeypatch/injection
works in tests."""
import os

from cryptography.fernet import Fernet, InvalidToken


class MailCryptoError(Exception):
    """The encryption key is missing, or the token is invalid."""


def _fernet():
    key = (os.environ.get('MAIL_ENC_KEY') or '').strip()
    if not key:
        raise MailCryptoError('MAIL_ENC_KEY yok (Infisical enjeksiyonu?)')
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise MailCryptoError(f'MAIL_ENC_KEY geçersiz: {e}')


def available():
    """Is the encryption key configured (can the mail module work)?"""
    return bool((os.environ.get('MAIL_ENC_KEY') or '').strip())


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise MailCryptoError(f'token çözülemedi: {e}')
