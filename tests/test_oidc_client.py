"""sso_client.OIDCClient — pure struct/URL tests (no network access: all three endpoints
are given explicitly, discovery is never triggered; PyJWKClient is also lazy and won't
fetch the JWKS until the first `verify()` call — which never happens here)."""
import pytest

from sso_client import OIDCClient


def _client(**overrides):
    kwargs = dict(
        issuer='https://idp.test',
        client_id='kotar-panel',
        client_secret='gizli',
        authorization_endpoint='https://idp.test/authorize',
        token_endpoint='https://idp.test/token',
        jwks_url='https://idp.test/jwks.json',
    )
    kwargs.update(overrides)
    return OIDCClient(**kwargs)


def test_login_url_gerekli_parametreleri_icerir():
    c = _client()
    url = c.login_url('https://panel.test/auth/callback', 'state123')
    assert url.startswith('https://idp.test/authorize?')
    assert 'client_id=kotar-panel' in url
    assert 'response_type=code' in url
    assert 'state=state123' in url
    assert 'redirect_uri=https%3A%2F%2Fpanel.test%2Fauth%2Fcallback' in url
    assert 'scope=openid+email+profile' in url


def test_varsayilan_rol_claim_ve_default_role():
    c = _client()
    assert c.role_claim == 'role'
    assert c.default_role == 'pending'


def test_ozel_rol_claim_ve_default_role_ayarlanabilir():
    c = _client(role_claim='custom:role', default_role='content_creator')
    assert c.role_claim == 'custom:role'
    assert c.default_role == 'content_creator'


def test_ozel_scope_login_url_yansir():
    c = _client(scope='openid email')
    url = c.login_url('https://panel.test/auth/callback', 's')
    assert 'scope=openid+email' in url and 'profile' not in url


# --- lazy discovery (issuer-only config) ---

def _disc_doc():
    return {'authorization_endpoint': 'https://idp.test/authorize',
            'token_endpoint': 'https://idp.test/token',
            'jwks_uri': 'https://idp.test/jwks.json'}


def test_issuer_only_kurulum_boot_aninda_aga_cikmaz(monkeypatch):
    """The constructor must NOT hit the network: it runs inside create_app(),
    and an unreachable IdP at (re)start would otherwise take the whole app
    down — public no-auth pages included."""
    import sso_client

    def boom(*a, **kw):
        raise AssertionError('network call at construction time')
    monkeypatch.setattr(sso_client.requests, 'get', boom)
    c = OIDCClient(issuer='https://idp.test', client_id='x', client_secret='y')
    assert c.authorization_endpoint is None  # not discovered yet


def test_login_url_discovery_hatasinda_OIDCDiscoveryError(monkeypatch):
    import sso_client

    def down(*a, **kw):
        raise sso_client.requests.ConnectionError('idp unreachable')
    monkeypatch.setattr(sso_client.requests, 'get', down)
    c = OIDCClient(issuer='https://idp.test', client_id='x', client_secret='y')
    with pytest.raises(sso_client.OIDCDiscoveryError):
        c.login_url('https://panel.test/auth/callback', 's')


def test_discovery_ilk_login_de_calisir_ve_cachelenir(monkeypatch):
    import sso_client
    calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return _disc_doc()

    def fake_get(url, timeout=None):
        calls.append(url)
        return R()
    monkeypatch.setattr(sso_client.requests, 'get', fake_get)
    c = OIDCClient(issuer='https://idp.test', client_id='x', client_secret='y')
    url = c.login_url('https://panel.test/auth/callback', 's')
    assert url.startswith('https://idp.test/authorize?')
    c.login_url('https://panel.test/auth/callback', 's2')
    assert len(calls) == 1  # discovered once, cached afterwards


def test_discovery_kurtulan_idp_restartsiz_iyilesir(monkeypatch):
    """First attempt fails (IdP down), second succeeds — no process restart
    needed in between."""
    import sso_client
    state = {'down': True}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return _disc_doc()

    def flaky_get(url, timeout=None):
        if state['down']:
            raise sso_client.requests.ConnectionError('idp unreachable')
        return R()
    monkeypatch.setattr(sso_client.requests, 'get', flaky_get)
    c = OIDCClient(issuer='https://idp.test', client_id='x', client_secret='y')
    with pytest.raises(sso_client.OIDCDiscoveryError):
        c.login_url('https://panel.test/auth/callback', 's')
    state['down'] = False
    assert c.login_url('https://panel.test/auth/callback', 's').startswith(
        'https://idp.test/authorize?')
