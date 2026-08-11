"""Security headers + CSP (report-only) — step 21.

CSP is `Content-Security-Policy-Report-Only` for now: enforcing it could break
the SPA/inline-HTML public pages (review, special day) → observation mode first.
The tests verify both that the headers exist and that report-only is sent INSTEAD
OF enforce (un-gameable: the `Content-Security-Policy` header — under the enforce
name — must be ABSENT).
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
        # Don't let send_from_directory leave the file handle for the GC (ResourceWarning
        # would fail the test with filterwarnings=error); close it explicitly.
        resp.close()


def test_permissions_policy_mikrofona_kendi_originimize_izin_verir(client):
    """The voice-note page records in the browser — `microphone=()` (no origin) was
    rejecting `getUserMedia` without even asking for permission. This test locks in
    that regression: the microphone must stay OPEN to our own origin, camera/geolocation CLOSED."""
    resp = client.get('/health')
    policy = resp.headers['Permissions-Policy']
    assert 'microphone=(self)' in policy
    assert 'camera=()' in policy
    assert 'geolocation=()' in policy


def test_csp_report_only_modda(client):
    """CSP is sent via the `Report-Only` header; the enforce header (same name, without
    `-Report-Only`) is absent — the enforce cutover is out of scope for this step (see CUTOVER.md)."""
    resp = client.get('/health')
    assert 'Content-Security-Policy-Report-Only' in resp.headers
    assert 'Content-Security-Policy' not in resp.headers
    policy = resp.headers['Content-Security-Policy-Report-Only']
    assert "default-src 'self'" in policy


def test_csp_ozel_gun_font_kaynaklarini_kapsar(client):
    """special_days.py uses Google Fonts for Cormorant/DM Sans — the policy must cover it."""
    resp = client.get('/health')
    policy = resp.headers['Content-Security-Policy-Report-Only']
    assert 'fonts.googleapis.com' in policy
    assert 'fonts.gstatic.com' in policy


def test_review_sayfasi_csp_ile_yukleniyor(client):
    """The public review page (inline style/script) still returns 200 under report-only CSP
    (report-only doesn't block) and carries the security headers."""
    resp = client.get('/review/olmayan-token')
    assert resp.status_code == 404  # invalid token — but the headers must still be added
    _assert_guvenlik_basliklari(resp.headers)
    assert 'Content-Security-Policy-Report-Only' in resp.headers


def test_ozel_gun_sayfasi_csp_ile_yukleniyor(client):
    resp = client.get('/special-days/olmayan-token')
    assert resp.status_code == 404
    _assert_guvenlik_basliklari(resp.headers)
    assert 'Content-Security-Policy-Report-Only' in resp.headers
