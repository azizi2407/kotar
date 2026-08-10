"""sso_client.OIDCClient — saf yapı/URL testleri (ağa çıkmaz: üç uç da elle verilir,
discovery tetiklenmez; PyJWKClient de tembel, JWKS'i ilk `verify()` çağrısına kadar
çekmez — burada hiç çağrılmıyor)."""
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
