"""mail_service — yetki, domain allowlist, Fernet round-trip, senkron idempotentliği."""
import pytest

import mail_gateway as gw
import mail_service as svc
from extensions import db
from models_mail import MailAccount, MailMessage

NORMAL = {'sub': 's1', 'email': 'mert@example.com', 'name': 'Mert', 'role': 'management'}
OTHER = {'sub': 's2', 'email': 'talu@example.com', 'name': 'Talu', 'role': 'management'}
SUPER = {'sub': 's0', 'email': 'superadmin@example.com', 'name': 'Superadmin', 'role': 'management'}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(gw, 'test_imap', lambda conn: None)
    monkeypatch.setattr(gw, 'test_smtp', lambda conn: None)
    monkeypatch.setattr(gw, 'test_connection', lambda conn: None)


def _mk(owner_sub='s1', email='mert@example.com', is_shared=False, secret='pw'):
    a = MailAccount(owner_sub=owner_sub, email=email, imap_host='h', imap_port=993,
                    imap_ssl=True, smtp_host='h', smtp_port=465, smtp_security='ssl',
                    secret_enc=svc.mail_crypto.encrypt(secret), is_shared=is_shared, active=True)
    db.session.add(a)
    db.session.commit()
    return a


def test_create_rejects_foreign_domain():
    with pytest.raises(svc.MailConfigError):
        svc.create_account(NORMAL, {'email': 'x@gmail.com', 'password': 'p'})


def test_create_own_account_ok():
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'gizli'})
    assert acc.owner_sub == 's1' and acc.id
    # secret şifreli saklandı, çözülebiliyor
    assert svc.mail_crypto.decrypt(acc.secret_enc) == 'gizli'


def test_create_succeeds_when_smtp_blocked(monkeypatch):
    # SMTP erişilemez olsa da IMAP OK ise hesap oluşur, uyarı yazılır.
    # (2026-07-31: metin "SMTP doğrulanamadı" → "SMTP sunucusuna erişilemedi";
    #  kimlik hatasından ayırt edilebilsin diye bilinçli değiştirildi.)
    monkeypatch.setattr(gw, 'test_smtp', lambda conn: (_ for _ in ()).throw(gw.MailError('timed out')))
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})
    assert acc.id
    assert 'erişilemedi' in (acc.last_error or '')
    assert 'parola' not in (acc.last_error or '')     # ağ sorunu, kimlik sorunu DEĞİL


def test_create_smtp_KIMLIK_hatasini_agdan_AYIRIR(monkeypatch):
    """`MailAuthError` `MailError`'ın alt sınıfı; tek `except gw.MailError`
    ikisini de yakalıyordu ve yanlış SMTP parolası "gönderim şimdilik kapalı"
    (ağ engeli) gibi görünüyordu. Ağ engeli kalktıktan sonra bu metin aktif
    olarak yanlış yönlendirir: düzeltilebilir bir parola sorununu "sunucu
    engeli" sanıp kimse dokunmaz."""
    monkeypatch.setattr(gw, 'test_smtp',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535 bad password')))
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})
    assert acc.id
    assert 'parola' in (acc.last_error or '')
    assert 'erişilemedi' not in (acc.last_error or '')


def test_test_account_hata_SINIFINI_raporlar(monkeypatch):
    """Panelde "parolayı düzelt" ile "sunucuya ulaşılamıyor" farklı işler →
    uç hatanın sınıfını da döndürür."""
    a = _mk()
    monkeypatch.setattr(gw, 'test_smtp',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535')))
    r = svc.test_account(NORMAL, a.id)
    assert r['imap_ok'] is True and r['smtp_ok'] is False
    assert r['smtp_error_kind'] == 'auth'

    monkeypatch.setattr(gw, 'test_smtp',
                        lambda conn: (_ for _ in ()).throw(gw.MailError('timed out')))
    assert svc.test_account(NORMAL, a.id)['smtp_error_kind'] == 'connect'


def test_create_fails_when_imap_bad(monkeypatch):
    monkeypatch.setattr(gw, 'test_imap', lambda conn: (_ for _ in ()).throw(gw.MailAuthError('bad')))
    with pytest.raises(gw.MailAuthError):
        svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})


def test_shared_account_superadmin_only():
    with pytest.raises(svc.MailAccessError):
        svc.create_account(NORMAL, {'email': 'info@example.com', 'password': 'p',
                                    'is_shared': True})
    acc = svc.create_account(SUPER, {'email': 'info@example.com', 'password': 'p',
                                     'is_shared': True})
    assert acc.is_shared is True


def test_require_account_blocks_foreign():
    a = _mk(owner_sub='s1', email='mert@example.com')
    with pytest.raises(svc.MailAccessError):
        svc.require_account(OTHER, a.id)  # s2 başkasının özel hesabı


def test_accessible_accounts_own_plus_shared():
    _mk(owner_sub='s1', email='mert@example.com')
    _mk(owner_sub='s2', email='talu@example.com')            # başkasının özeli — görünmez
    _mk(owner_sub='s0', email='info@example.com', is_shared=True)  # ortak — görünür
    emails = {a.email for a in svc.accessible_accounts(NORMAL)}
    assert emails == {'mert@example.com', 'info@example.com'}


# --- parola güncelleme (2026-07-31) -----------------------------------------
# Kullanıcı posta sunucusunda parolasını değiştirdi → panel bağlantısı koptu →
# "Şifreyi güncelle" 500 verdi. Kök neden: panel var olan hesabı GÜNCELLEMİYOR,
# yeni hesap OLUŞTURMAYI deniyordu (`uq_mail_owner_email` ihlali). Bu blok o
# akışın backend tarafını çiviliyor.

def test_parola_guncelleme_IMAP_ile_DOGRULANIR(monkeypatch):
    """Yanlış parolayı sessizce kaydetmek kullanıcıyı bozuk durumda bırakır —
    tam olarak düzeltmeye çalıştığı durumda. Doğrulanmadan yazılmamalı."""
    a = _mk(secret='eski')
    monkeypatch.setattr(gw, 'test_imap',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535 bad')))
    with pytest.raises(gw.MailAuthError):
        svc.update_account(NORMAL, a.id, {'password': 'yanlis'})
    db.session.rollback()
    assert svc.mail_crypto.decrypt(db.session.get(MailAccount, a.id).secret_enc) == 'eski'


def test_parola_guncelleme_basarilida_saglik_kaydini_TEMIZLER(monkeypatch):
    """Şerit `last_error`'a bakıyor; temizlenmezse parola düzelse bile hesap
    "bozuk" görünmeye devam eder (2026-07-31 gönderim yolunda düzeltilen aynı sınıf)."""
    a = _mk(secret='eski')
    a.last_error = 'IMAP kimliği reddedildi'
    db.session.commit()
    out = svc.update_account(NORMAL, a.id, {'password': 'yeni'})
    assert svc.mail_crypto.decrypt(out.secret_enc) == 'yeni'
    assert out.last_error is None and out.last_ok_at is not None


def test_parolasiz_guncelleme_IMAP_TESTI_YAPMAZ(monkeypatch):
    """Yalnız display_name değiştiren istek ağa çıkmamalı."""
    a = _mk()
    monkeypatch.setattr(gw, 'test_imap',
                        lambda conn: (_ for _ in ()).throw(AssertionError('ağa çıkıldı')))
    assert svc.update_account(NORMAL, a.id, {'display_name': 'Mert Y'}).display_name == 'Mert Y'


# --- aynı e-postayı ikinci kez bağlama --------------------------------------

def test_ayni_hesabi_tekrar_baglamak_ANLASILIR_hata_verir():
    """Ham `IntegrityError` 500 veriyordu ve panel bunu "Bağlanamadı (e-posta/şifre?)"
    diye gösteriyordu — parola DOĞRUYKEN bile. Mesaj tam ters yönü işaret ediyordu."""
    svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p'})
    with pytest.raises(svc.MailConfigError) as e:
        svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'p2'})
    assert 'zaten bağlı' in str(e.value)


def test_silinmis_hesap_yeniden_baglanabilir():
    """`delete_account` soft-delete (`active=False`) ama unique kısıt satırı görüyor →
    "kaldır, yeniden bağla" yolu kısıta çarpıyordu. Yeniden bağlama satırı canlandırır."""
    acc = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'eski'})
    acc_id = acc.id
    svc.delete_account(NORMAL, acc_id)
    again = svc.create_account(NORMAL, {'email': 'mert@example.com', 'password': 'yeni'})
    assert again.id == acc_id                 # YENİ satır değil — aynı satır canlandı
    assert again.active is True
    assert svc.mail_crypto.decrypt(again.secret_enc) == 'yeni'


# --- okuma yolu sağlık kaydını güncelliyor mu? ------------------------------

def test_klasor_tazelemede_kimlik_hatasi_SAGLIGA_YAZILIR(monkeypatch):
    """Parola sunucuda değişince ilk belirti klasör listesinin 502 vermesi. Sağlık
    kaydı yazılmazsa şerit "Bağlantı sorunu bildirilmedi" (yeşil) demeye devam eder
    ve "Şifreyi güncelle" düğmesi HİÇ görünmez — kullanıcı elle test etmek zorunda
    kalır. Bugün tam olarak bu oldu."""
    a = _mk()
    monkeypatch.setattr(gw, 'list_folders',
                        lambda conn: (_ for _ in ()).throw(gw.MailAuthError('535 auth')))
    with pytest.raises(gw.MailAuthError):
        svc.refresh_folders(a)
    # Metin `create_account` ile aynı kelimeleri taşımalı: şerit "parola sorunu"
    # ile "sunucuya ulaşılamıyor"u bu metinden ayırt ediyor ve gateway'in ham
    # "IMAP giriş başarısız" metni o ayrımı vermiyor.
    err = db.session.get(MailAccount, a.id).last_error or ''
    assert '535 auth' in err and 'parola' in err and 'kimliği' in err


def test_klasor_tazelemede_AG_hatasi_parola_hatasi_gibi_YAZILMAZ(monkeypatch):
    """Karşıt kontrol: erişim hatası "parolayı kontrol edin" dememeli, yoksa şerit
    kullanıcıyı düzeltemeyeceği bir işe yollar."""
    a = _mk()
    monkeypatch.setattr(gw, 'list_folders',
                        lambda conn: (_ for _ in ()).throw(gw.MailError('timed out')))
    with pytest.raises(gw.MailError):
        svc.refresh_folders(a)
    err = db.session.get(MailAccount, a.id).last_error or ''
    assert 'erişilemedi' in err and 'parola' not in err


def test_conn_for_decrypts():
    a = _mk(secret='45124512*Talat')
    conn = svc.conn_for(a)
    assert conn.password == '45124512*Talat' and conn.username == 'mert@example.com'


def test_sync_folder_idempotent(monkeypatch):
    a = _mk()
    monkeypatch.setattr(gw, 'list_folders', lambda conn: [
        {'name': 'INBOX', 'path': 'INBOX', 'flags': '\\Inbox', 'special_use': 'inbox',
         'uidvalidity': 10}])
    msgs = [gw.MailMsg(uid=1, subject='bir', message_id='<1@t>', flags=set()),
            gw.MailMsg(uid=2, subject='iki', message_id='<2@t>', flags={'\\Seen'})]

    def fake_fetch(conn, folder, since_uid=None, limit=50):
        return [m for m in msgs if not since_uid or m.uid > since_uid]

    monkeypatch.setattr(gw, 'fetch_headers', fake_fetch)
    assert svc.sync_folder(a, 'INBOX') == 2
    assert svc.sync_folder(a, 'INBOX') == 0          # idempotent
    assert MailMessage.query.filter_by(account_id=a.id).count() == 2


def test_send_sets_reply_and_answered(monkeypatch):
    a = _mk()
    # kaynak mesaj (yanıtlanacak)
    monkeypatch.setattr(gw, 'list_folders', lambda conn: [
        {'name': 'INBOX', 'path': 'INBOX', 'flags': '', 'special_use': 'inbox',
         'uidvalidity': 5}])
    monkeypatch.setattr(gw, 'fetch_headers', lambda conn, folder, since_uid=None, limit=50: [
        gw.MailMsg(uid=7, subject='soru', message_id='<src@t>', flags=set())])
    svc.sync_folder(a, 'INBOX')
    src = MailMessage.query.filter_by(account_id=a.id, uid=7).one()

    captured = {}
    def fake_send(conn, **kw):
        captured.update(kw)
        return '<new@t>', b'RAW'
    monkeypatch.setattr(gw, 'send', fake_send)
    monkeypatch.setattr(gw, 'append_sent', lambda conn, raw, folder='Sent': None)
    monkeypatch.setattr(gw, 'store_flags', lambda *a, **k: None)

    out = svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'subject': 'Ynt',
                                          'body_text': 'cevap', 'reply_to_message_id': src.id})
    assert captured['in_reply_to'] == '<src@t>'
    assert out['message_id'] == '<new@t>'
    db.session.refresh(src)
    assert src.answered is True


def test_send_requires_recipient():
    a = _mk()
    with pytest.raises(svc.MailConfigError):
        svc.send_message(NORMAL, a.id, {'to': [], 'subject': 's', 'body_text': 'b'})


# --- gönderim hesabın SAĞLIK durumunu günceller (2026-07-31) ----------------
# Eskiden bunu yalnız `test_account` yapıyordu ve o uç panelden HİÇ çağrılmıyor
# (`lib/mail.ts`'te `testAccount` var, hiçbir bileşen kullanmıyor). Sonuç: SMTP
# ağ engeli döneminde yazılan "gönderim kapalı" notu, engel kalktıktan ve
# gönderim çalışmaya başladıktan sonra da kayıtta kalıyordu — kendiliğinden
# temizlenen bir yol yoktu.

def test_basarili_gonderim_BAYAT_hatayi_temizler(monkeypatch):
    a = _mk()
    a.last_error = 'IMAP OK · SMTP sunucusuna erişilemedi (gönderim kapalı): timed out'
    a.last_ok_at = None
    db.session.commit()

    monkeypatch.setattr(gw, 'send', lambda conn, **kw: ('<m@t>', b'RAW'))
    monkeypatch.setattr(gw, 'append_sent', lambda conn, raw, folder='Sent': None)
    svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})

    db.session.refresh(a)
    assert a.last_error is None, 'başarılı gönderim bayat "kapalı" notunu silmedi'
    assert a.last_ok_at is not None


def test_basarisiz_gonderim_sebebi_yazar_ve_hatayi_YUKSELTIR(monkeypatch):
    a = _mk()
    monkeypatch.setattr(gw, 'send',
                        lambda conn, **kw: (_ for _ in ()).throw(gw.MailError('timed out')))
    with pytest.raises(gw.MailError):
        svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})
    db.session.refresh(a)
    assert 'erişilemedi' in (a.last_error or '')
    assert 'parola' not in (a.last_error or '')


def test_gonderim_kimlik_hatasi_parolayi_isaret_eder(monkeypatch):
    a = _mk()
    monkeypatch.setattr(gw, 'send',
                        lambda conn, **kw: (_ for _ in ()).throw(gw.MailAuthError('535')))
    with pytest.raises(gw.MailAuthError):
        svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})
    db.session.refresh(a)
    assert 'parola' in (a.last_error or '')


def test_basarisiz_gonderim_Sent_APPEND_etmez(monkeypatch):
    """Gönderilemeyen mesaj Gönderilmiş klasörüne yazılmamalı — kullanıcı
    gitmediği bir maili gitmiş sanır."""
    a = _mk()
    appended = []
    monkeypatch.setattr(gw, 'send',
                        lambda conn, **kw: (_ for _ in ()).throw(gw.MailError('x')))
    monkeypatch.setattr(gw, 'append_sent',
                        lambda conn, raw, folder='Sent': appended.append(raw))
    with pytest.raises(gw.MailError):
        svc.send_message(NORMAL, a.id, {'to': ['x@example.com'], 'body_text': 'b'})
    assert appended == []
