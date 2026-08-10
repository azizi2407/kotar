"""Mail hesap parolaları için Fernet şifreleme. Ana anahtar MAIL_ENC_KEY
Infisical'da (base64 urlsafe 32B, Fernet.generate_key formatı). Anahtar yoksa
modül fail-closed: available()=False, encrypt/decrypt MailCryptoError atar.
Anahtar çalışma anında okunur (import değil) → testte monkeypatch/enjeksiyon çalışır."""
import os

from cryptography.fernet import Fernet, InvalidToken


class MailCryptoError(Exception):
    """Şifreleme anahtarı yok veya token geçersiz."""


def _fernet():
    key = (os.environ.get('MAIL_ENC_KEY') or '').strip()
    if not key:
        raise MailCryptoError('MAIL_ENC_KEY yok (Infisical enjeksiyonu?)')
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise MailCryptoError(f'MAIL_ENC_KEY geçersiz: {e}')


def available():
    """Şifreleme anahtarı yapılandırılmış mı (mail modülü çalışabilir mi)?"""
    return bool((os.environ.get('MAIL_ENC_KEY') or '').strip())


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise MailCryptoError(f'token çözülemedi: {e}')
