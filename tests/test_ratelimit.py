"""Postgres/sqlite fixed-window rate limiter + public endpoint integration."""

import ratelimit
from conftest import MANAGER, login_as
from test_session_csrf import csrf_headers


def test_hit_limit_altinda_izin(client):
    # first 3 requests allowed (max 3)
    assert ratelimit.hit("b1", 3, 60) is True
    assert ratelimit.hit("b1", 3, 60) is True
    assert ratelimit.hit("b1", 3, 60) is True


def test_hit_limit_ustunde_red(client):
    for _ in range(3):
        ratelimit.hit("b2", 3, 60)
    assert ratelimit.hit("b2", 3, 60) is False  # 4th request rejected


def test_hit_ayri_bucket_bagimsiz(client):
    for _ in range(5):
        ratelimit.hit("bx", 3, 60)
    assert ratelimit.hit("by", 3, 60) is True  # different bucket unaffected


def test_hit_pencere_sifirlanir(client, monkeypatch):
    """The counter resets when the window fills up.

    **Clock is FIXED, NO `sleep`** (2026-07-31 flake fix). The old version used a
    1-second window + the real clock: since `hit` computes the window as
    `now - now % 1`, if the first two calls straddled a SECOND BOUNDARY (e.g.
    t=X.999 and t=Y.001) they'd fall into separate windows, the second call would
    also return True, and `assert ... is False` would break. Same class of bug as
    `test_review_action_rate_limit`; rare there in a 60-second window, frequent
    here at 1 second. Advancing the clock manually is both deterministic and 1.1
    seconds faster."""
    now = [1_785_000_000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: now[0])
    assert ratelimit.hit("bw", 1, 1) is True     # 1st request → within limit
    assert ratelimit.hit("bw", 1, 1) is False    # 2nd request → SAME window, exceeded
    now[0] += 1.1                               # window advanced
    assert ratelimit.hit("bw", 1, 1) is True     # new window → counter reset


def test_review_action_rate_limit(client, monkeypatch):
    """review /action should return 429 once the limit (40/60s) is exceeded.

    **Clock is FIXED** (2026-07-31, FLAKE fix). `ratelimit.hit` uses a fixed window
    (`now - now % 60`); if a 60-second boundary falls between the 42 requests, the
    counter RESETS — e.g. with a 6 + 36 split neither window exceeds 40 and 429
    NEVER shows up. In that case all 42 responses are 404 (see note below) and the
    test fails with `assert 429 in {404}`. GELISTIRME had a "test order dependency"
    hypothesis on record; the real cause was the window boundary.

    NOTE: requests under the limit returning 404 is EXPECTED — the endpoint looks
    up `CardUpload`, but the test sends a `Share` id. Since the rate-limit check
    (line ~221) runs BEFORE that 404 (line ~230), the test still measures the
    limit."""
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
    assert 429 in codes  # the limit kicks in at some point
