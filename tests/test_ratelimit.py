"""Postgres/sqlite fixed-window rate limiter + public uç entegrasyonu."""

import ratelimit
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers


def test_hit_limit_altinda_izin(client):
    # ilk 3 istek izinli (max 3)
    assert ratelimit.hit("b1", 3, 60) is True
    assert ratelimit.hit("b1", 3, 60) is True
    assert ratelimit.hit("b1", 3, 60) is True


def test_hit_limit_ustunde_red(client):
    for _ in range(3):
        ratelimit.hit("b2", 3, 60)
    assert ratelimit.hit("b2", 3, 60) is False  # 4. istek reddedilir


def test_hit_ayri_bucket_bagimsiz(client):
    for _ in range(5):
        ratelimit.hit("bx", 3, 60)
    assert ratelimit.hit("by", 3, 60) is True  # farklı bucket etkilenmez


def test_hit_pencere_sifirlanir(client, monkeypatch):
    """Pencere dolunca sayaç sıfırlanır.

    **Saat SABİT, `sleep` YOK** (2026-07-31 flake düzeltmesi). Eski hâli 1 saniyelik
    pencere + gerçek saat kullanıyordu: `hit` pencereyi `now - now % 1` ile
    hesapladığı için ilk iki çağrı bir SANİYE SINIRINI straddle ederse (ör. t=X.999
    ve t=Y.001) ayrı pencerelere düşüyor, ikincisi de True dönüyor ve
    `assert ... is False` kırılıyordu. `test_review_action_rate_limit` ile aynı
    sınıf hata; orada 60 sn'lik pencerede nadir, burada 1 sn'de sık. Saati elle
    ilerletmek hem deterministik hem 1.1 sn daha hızlı."""
    now = [1_785_000_000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: now[0])
    assert ratelimit.hit("bw", 1, 1) is True     # 1. istek → sınır içinde
    assert ratelimit.hit("bw", 1, 1) is False    # 2. istek → AYNI pencerede, aşıldı
    now[0] += 1.1                               # pencere ilerledi
    assert ratelimit.hit("bw", 1, 1) is True     # yeni pencere → sayaç sıfırlandı


def test_review_action_rate_limit(client, monkeypatch):
    """review /action 429 döndürmeli (limit 40/60sn aşılınca).

    **Saat SABİTLENİR** (2026-07-31, FLAKE düzeltmesi). `ratelimit.hit` fixed-window
    kullanıyor (`now - now % 60`); 42 isteğin arasına bir 60 sn sınırı düşerse sayaç
    SIFIRLANIR — ör. 6 + 36 bölünmesinde iki pencere de 40'ı aşmaz ve 429 HİÇ
    gelmez. O durumda 42 yanıtın hepsi 404 olur (aşağıdaki nota bakın) ve test
    `assert 429 in {404}` ile düşer. GELISTIRME'de "test sırası bağımlılığı"
    hipotezi kayıtlıydı; gerçek neden pencere sınırıydı.

    NOT: limitin altındaki isteklerin 404 dönmesi BEKLENEN — uç `CardUpload`
    arıyor, test ise `Share` kimliği gönderiyor. Hız sınırı kontrolü (satır ~221)
    o 404'ten (satır ~230) ÖNCE koştuğu için test yine de sınırı ölçer."""
    import ratelimit
    monkeypatch.setattr(ratelimit.time, 'time', lambda: 1_785_000_000.0)

    from extensions import db
    from models import Client
    from models_sharing import ReviewLink, Share
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "RL"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    s = client.post("/api/sharing/shares",
                    json={"client_id": cid, "week_iso": "2026-W21", "kind": "post", "file_id": "f"},
                    headers=csrf_headers(client)).get_json()["share"]
    client.post(f"/api/sharing/shares/{s['id']}/publish", headers=csrf_headers(client))
    token = client.post("/api/sharing/review-link",
                        json={"client_id": cid, "week_iso": "2026-W21"},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as sess:
        sess.clear()
    codes = set()
    for _ in range(42):
        r = client.post(f"/review/{token}/action", json={"share_id": s["id"], "action": "approve"})
        codes.add(r.status_code)
    assert 429 in codes  # bir noktada limit devreye girer
