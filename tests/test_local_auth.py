"""local_auth.authenticate() — AUTH_MODE=local giriş doğrulaması (DB'li, ağsız)."""
import local_auth
from extensions import db
from models_auth import LocalUser


def _make(email='kullanici@test.com', password='dogru-parola', role='designer',
         status='active'):
    u = LocalUser(email=email, role=role, status=status)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


def test_dogru_parola_kullaniciyi_doner(app):
    with app.app_context():
        _make()
        u = local_auth.authenticate('kullanici@test.com', 'dogru-parola')
        assert u is not None
        assert u.email == 'kullanici@test.com'


def test_yanlis_parola_none_doner(app):
    with app.app_context():
        _make()
        assert local_auth.authenticate('kullanici@test.com', 'yanlis') is None


def test_olmayan_email_none_doner(app):
    with app.app_context():
        assert local_auth.authenticate('yok@test.com', 'ne-olursa') is None


def test_email_buyuk_kucuk_harf_duyarsiz(app):
    with app.app_context():
        _make(email='kullanici@test.com')
        u = local_auth.authenticate('KULLANICI@Test.com', 'dogru-parola')
        assert u is not None


def test_disabled_kullanici_giremez(app):
    with app.app_context():
        _make(status='disabled')
        assert local_auth.authenticate('kullanici@test.com', 'dogru-parola') is None
