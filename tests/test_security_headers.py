"""Güvenlik başlıkları + CSP (report-only) — step 21.

CSP şimdilik `Content-Security-Policy-Report-Only`: enforce edilirse SPA/inline-HTML
public sayfaları (review, özel gün) kırılabilir → önce gözlem modu. Testler hem
başlıkların varlığını hem de enforce YERİNE report-only gönderildiğini doğrular
(un-gameable: `Content-Security-Policy` header'ı — enforce adıyla — YOK olmalı).
"""
from conftest import MANAGER, login_as


def _assert_guvenlik_basliklari(headers):
    assert headers.get('X-Content-Type-Options') == 'nosniff'
    assert 'X-Frame-Options' in headers
    assert 'Referrer-Policy' in headers
    assert 'Permissions-Policy' in headers


def test_health_guvenlik_baslıklari(client):
    resp = client.get('/health')
    _assert_guvenlik_basliklari(resp.headers)


def test_api_guvenlik_baslıklari(client):
    login_as(client, MANAGER)
    resp = client.get('/api/me')
    _assert_guvenlik_basliklari(resp.headers)


def test_spa_guvenlik_baslıklari(client):
    resp = client.get('/panel/')
    try:
        _assert_guvenlik_basliklari(resp.headers)
    finally:
        # send_from_directory dosya tanıtıcısını GC'ye bırakmasın (ResourceWarning →
        # filterwarnings=error testi düşürür); açıkça kapat.
        resp.close()


def test_permissions_policy_mikrofona_kendi_originimize_izin_verir(client):
    """Sesli not sayfası tarayıcıda kayıt yapıyor — `microphone=()` (hiçbir origin)
    `getUserMedia`'yı izin sorulmadan reddediyordu. Bu test o regresyonu kilitler:
    mikrofon kendi origin'imize AÇIK, camera/geolocation KAPALI kalmalı."""
    resp = client.get('/health')
    policy = resp.headers['Permissions-Policy']
    assert 'microphone=(self)' in policy
    assert 'camera=()' in policy
    assert 'geolocation=()' in policy


def test_csp_report_only_modda(client):
    """CSP `Report-Only` header'ıyla gönderiliyor; enforce header'ı (aynı ada, `-Report-Only`
    eksiz) yok — enforce geçişi bu step'in kapsamı dışı (bkz. CUTOVER.md)."""
    resp = client.get('/health')
    assert 'Content-Security-Policy-Report-Only' in resp.headers
    assert 'Content-Security-Policy' not in resp.headers
    policy = resp.headers['Content-Security-Policy-Report-Only']
    assert "default-src 'self'" in policy


def test_csp_ozel_gun_font_kaynaklarini_kapsar(client):
    """special_days.py Cormorant/DM Sans için Google Fonts kullanıyor — policy bunu kapsamalı."""
    resp = client.get('/health')
    policy = resp.headers['Content-Security-Policy-Report-Only']
    assert 'fonts.googleapis.com' in policy
    assert 'fonts.gstatic.com' in policy


def test_review_sayfasi_csp_ile_yukleniyor(client):
    """Public review sayfası (inline stil/script) report-only CSP altında hâlâ 200 döner
    (report-only bloklamaz) ve güvenlik başlıklarını taşır."""
    resp = client.get('/review/olmayan-token')
    assert resp.status_code == 404  # geçersiz token — ama başlıklar yine de eklenmeli
    _assert_guvenlik_basliklari(resp.headers)
    assert 'Content-Security-Policy-Report-Only' in resp.headers


def test_ozel_gun_sayfasi_csp_ile_yukleniyor(client):
    resp = client.get('/special-days/olmayan-token')
    assert resp.status_code == 404
    _assert_guvenlik_basliklari(resp.headers)
    assert 'Content-Security-Policy-Report-Only' in resp.headers
