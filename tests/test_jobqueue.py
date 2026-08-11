"""Job queue — enqueue / claim / complete / fail / retry-backoff / janitor."""
from datetime import timedelta, timezone

from sqlalchemy.dialects import postgresql

import jobqueue
from conftest import MANAGER, login_as
from models import Job, utcnow
from test_session_csrf import csrf_headers


def _aware(dt):
    """Convert a naive sqlite datetime to UTC-aware (for comparison)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def test_enqueue_queued(client):
    j = jobqueue.enqueue("caption", {"share_id": 5}, created_by="1")
    assert j.id > 0 and j.status == "queued"
    assert j.payload["share_id"] == 5


def test_claim_running(client):
    jobqueue.enqueue("caption", {"share_id": 1})
    j = jobqueue.claim(["caption"])
    assert j is not None and j.status == "running"
    assert j.claimed_at is not None and j.attempts == 1


def test_claim_yanlis_tip_none(client):
    jobqueue.enqueue("caption", {})
    assert jobqueue.claim(["media"]) is None


def test_claim_fifo(client):
    a = jobqueue.enqueue("caption", {"n": 1})
    b = jobqueue.enqueue("caption", {"n": 2})
    assert jobqueue.claim(["caption"]).id == a.id
    assert jobqueue.claim(["caption"]).id == b.id
    assert jobqueue.claim(["caption"]) is None  # queue empty


def test_complete(client):
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    jobqueue.complete(j, {"caption": "merhaba"})
    assert j.status == "done" and j.result["caption"] == "merhaba"
    assert j.finished_at is not None


def test_fail(client):
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    jobqueue.fail(j, "hata oldu")
    assert j.status == "failed" and "hata" in j.result["error"]


def test_fail_rollback_temizler_session(client):
    """fail() does a rollback as its FIRST line: it recovers a dirty/pending-rollback
    session, doesn't raise an exception, and a query works afterward."""
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    # Dirty the session: a NOT NULL violation flush (type=None)
    from extensions import db
    db.session.add(Job(type=None, status="queued"))
    try:
        db.session.flush()
    except Exception:
        pass  # session is now pending-rollback
    jobqueue.fail(j, "patladi")  # must NOT raise an exception
    # session clean → query runs successfully
    assert db.session.query(Job).count() >= 1
    assert j.status == "failed"


def test_fail_transient_requeue_backoff(client):
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])  # attempts=1
    assert j.attempts == 1
    jobqueue.fail(j, "rate limit exceeded", transient=True, max_attempts=3)
    assert j.status == "queued"
    assert j.available_at is not None
    assert _aware(j.available_at) > utcnow()  # backoff is in the future
    assert j.result["retry"] is True


def test_fail_transient_max_attempts_terminal(client):
    from extensions import db
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    j.attempts = 3
    db.session.commit()
    jobqueue.fail(j, "rate limit", transient=True, max_attempts=3)
    assert j.status == "failed"  # NO requeue
    assert j.result.get("retry") is None


def test_claim_available_at_gelecek_atlanir(client):
    from extensions import db
    j = jobqueue.enqueue("caption", {})
    j.available_at = utcnow() + timedelta(minutes=5)
    db.session.commit()
    assert jobqueue.claim(["caption"]) is None


def test_claim_available_at_gecmis_cekilir(client):
    from extensions import db
    j = jobqueue.enqueue("caption", {})
    j.available_at = utcnow() - timedelta(minutes=5)
    db.session.commit()
    assert jobqueue.claim(["caption"]) is not None


def test_claim_available_at_null_cekilir(client):
    jobqueue.enqueue("caption", {})  # available_at NULL (default)
    assert jobqueue.claim(["caption"]) is not None


def test_reap_stuck_requeue(client):
    from extensions import db
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])  # running
    j.claimed_at = utcnow() - timedelta(hours=2)
    db.session.commit()
    reaped = jobqueue.reap_stuck(timeout_seconds=1800)
    assert j.id in [r.id for r in reaped]
    db.session.refresh(j)
    assert j.status == "queued"
    assert j.available_at is None


def test_reap_stuck_timeout_icinde_dokunmaz(client):
    from extensions import db
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])  # claimed_at ~ now
    reaped = jobqueue.reap_stuck(timeout_seconds=1800)
    assert j.id not in [r.id for r in reaped]
    db.session.refresh(j)
    assert j.status == "running"


def test_claim_skip_locked_pg_compile(client):
    """The PG branch shouldn't silently disappear: the compiled SQL must contain 'SKIP LOCKED'."""
    q = jobqueue._select_query(["caption"], utcnow(), for_update=True)
    sql = str(q.statement.compile(dialect=postgresql.dialect()))
    assert "SKIP LOCKED" in sql


def test_is_transient_klasiflendirir(client):
    import ai_worker
    assert ai_worker._is_transient(Exception("rate limit exceeded"))
    assert ai_worker._is_transient(TimeoutError("timed out after 240 seconds"))
    assert ai_worker._is_transient(Exception("Overloaded 429"))
    # NOT every error is transient — guards against infinite retry risk
    assert not ai_worker._is_transient(ValueError("paylaşım yok: 5"))


# --- priority order (step 02) ---

def test_claim_priority_once_id_sonra(client):
    """Un-gameable: the low-priority job is enqueued FIRST (small id), the high-priority
    one SECOND (large id). claim must return the high priority one FIRST, DESPITE the id order."""
    dusuk = jobqueue.enqueue("caption", {"p": "dusuk"}, priority=0)
    yuksek = jobqueue.enqueue("caption", {"p": "yuksek"}, priority=10)
    assert dusuk.id < yuksek.id  # ids are reversed: the low-priority one is older
    ilk = jobqueue.claim(["caption"])
    assert ilk.id == yuksek.id  # priority DESC — high priority first despite the id
    ikinci = jobqueue.claim(["caption"])
    assert ikinci.id == dusuk.id


def test_claim_esit_priority_fifo(client):
    """With equal priority, the smaller id goes first (FIFO is preserved)."""
    a = jobqueue.enqueue("caption", {"n": 1}, priority=5)
    b = jobqueue.enqueue("caption", {"n": 2}, priority=5)
    assert jobqueue.claim(["caption"]).id == a.id
    assert jobqueue.claim(["caption"]).id == b.id


def test_enqueue_priority_default_0(client):
    j = jobqueue.enqueue("caption", {})
    assert j.priority == 0


# --- fan-out (step 02) ---

def test_enqueue_per_client_ayri_joblar(client):
    """EXACTLY 3 separate Jobs; each with the correct client_id payload and the given priority."""
    jobs = jobqueue.enqueue_per_client(
        "brief", [1, 2, 3],
        payload_fn=lambda cid: {"client_id": cid},
        priority=0)
    assert Job.query.count() == 3
    assert len(jobs) == 3
    assert sorted(j.payload["client_id"] for j in jobs) == [1, 2, 3]
    assert all(j.type == "brief" for j in jobs)
    # SEPARATE jobs — different ids
    assert len({j.id for j in jobs}) == 3


def test_enqueue_per_client_priority_iletilir(client):
    jobs = jobqueue.enqueue_per_client(
        "special_days", [7, 8],
        payload_fn=lambda cid: {"client_id": cid},
        priority=3)
    assert all(j.priority == 3 for j in jobs)


def test_enqueue_per_client_per_client_dedup_key(client):
    """dedup_key_fn produces a DIFFERENT key for each client (NOT a single scalar);
    the key shows up in the payload as _dedup_key. Dedup behavior is covered in 04 —
    here only per-client propagation is verified."""
    jobs = jobqueue.enqueue_per_client(
        "brief", [1, 2, 3],
        payload_fn=lambda cid: {"client_id": cid},
        dedup_key_fn=lambda cid: f"brief:{cid}")
    keys = [j.payload["_dedup_key"] for j in jobs]
    assert keys == ["brief:1", "brief:2", "brief:3"]  # DIFFERENT per client
    assert len(set(keys)) == 3


def test_enqueue_dedup_key_yoksa_payload_dokunulmaz(client):
    j = jobqueue.enqueue("caption", {"share_id": 1})
    assert "_dedup_key" not in (j.payload or {})


# --- dedup (step 04) ---

def test_enqueue_dedup_ikinci_cagri_no_op(client):
    """Enqueueing twice with the same (type, dedup_key) → one job; the second call returns the first."""
    a = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    b = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    assert Job.query.count() == 1
    assert b.id == a.id


def test_enqueue_dedup_farkli_key_iki_job(client):
    jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    jobqueue.enqueue("caption", {"share_id": 8}, dedup_key="caption:8")
    assert Job.query.count() == 2


def test_enqueue_dedup_farkli_type_carismaz(client):
    """Dedup is scoped to the same type only: same key but different type → 2 jobs."""
    jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="k")
    jobqueue.enqueue("media", {"share_id": 7}, dedup_key="k")
    assert Job.query.count() == 2


def test_enqueue_dedup_yalniz_aktif_isleri_kapsar(client):
    """Same key AFTER the active job is done → new job (dedup only covers queued|running)."""
    a = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    j = jobqueue.claim(["caption"])
    jobqueue.complete(j, {"ok": True})  # now done
    b = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    assert b.id != a.id
    assert Job.query.count() == 2


def test_enqueue_dedup_running_de_kapsar(client):
    """Same key while the active job is running (not yet finished) → no new job is created."""
    a = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    jobqueue.claim(["caption"])  # running
    b = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    assert b.id == a.id
    assert Job.query.count() == 1


def test_enqueue_dedup_eszamanli_tek_aktif_job(app):
    """TOCTOU race: two threads enqueue SIMULTANEOUSLY with the same (type, dedup_key)
    (a barrier overlaps their checks). If check-then-act were unlocked, both would find
    `_active_dupe` empty and produce 2 jobs (gthread 2x4 production topology).
    Fix (serialization inside enqueue) → only 1 active job should remain, the second
    call should return the first one's job. Un-gameable: real concurrency is set up,
    not sequential."""
    import threading

    from extensions import db

    barrier = threading.Barrier(2)
    ids = []
    errors = []

    def worker():
        try:
            with app.app_context():
                try:
                    barrier.wait(timeout=5)  # so both threads enter the check together
                    j = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
                    ids.append(j.id)
                finally:
                    db.session.remove()  # clean up INSIDE the context
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, errors
    assert Job.query.filter_by(type="caption").count() == 1  # a single active job
    assert len(set(ids)) == 1  # both calls saw the same job


def test_enqueue_dedup_hit_cagiranin_pending_degisikligini_korur(client):
    """When the dedup-HIT branch releases the advisory lock, it MUST preserve an unrelated
    pending change the caller made BEFORE enqueue (not yet committed). Regression: in r1
    the lock was released with `db.session.rollback()` → dedup-hit rolled back the whole
    session and silently erased this change. Fix: commit instead of rollback (SELECT
    changes nothing; commit both releases the lock and preserves pending state). Un-gameable:
    the change must land in the DB."""
    from extensions import db
    from models import Client

    c = Client(name="Once")
    db.session.add(c)
    db.session.commit()
    # active job — the next enqueue will be a dedup-HIT
    jobqueue.enqueue("caption", {"share_id": c.id}, dedup_key=f"caption:{c.id}")
    # the caller changes a field BEFORE enqueue (not yet committed)
    c.name = "SONRA-DEGISTI"
    # dedup-HIT: no new INSERT, the existing job is returned
    jobqueue.enqueue("caption", {"share_id": c.id}, dedup_key=f"caption:{c.id}")
    # the pending change must be preserved and written to the DB
    db.session.refresh(c)
    assert c.name == "SONRA-DEGISTI"


# --- caption enqueue priority (step 02) ---

def test_caption_enqueue_priority_10(client):
    """An interactive caption must enter the queue with high priority (priority=10) — so
    it doesn't wait head-of-line behind batch jobs (step 02). End-to-end: the API call
    that enqueues a caption must create a Job with priority=10."""
    from extensions import db
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "C"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    s = client.post("/api/sharing/shares", json={"client_id": cid, "week_iso": "2026-W21", "kind": "post"},
                    headers=csrf_headers(client)).get_json()["share"]
    r = client.post(f"/api/sharing/shares/{s['id']}/caption", headers=csrf_headers(client))
    assert r.status_code == 202
    jid = r.get_json()["job"]["id"]
    assert db.session.get(Job, jid).priority == 10


def test_caption_ucu_iki_kez_tek_aktif_job(client):
    """Back-to-back POST .../caption on the same share → a single active job (dedup no-op)."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "C"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    s = client.post("/api/sharing/shares", json={"client_id": cid, "week_iso": "2026-W21", "kind": "post"},
                    headers=csrf_headers(client)).get_json()["share"]
    r1 = client.post(f"/api/sharing/shares/{s['id']}/caption", headers=csrf_headers(client))
    r2 = client.post(f"/api/sharing/shares/{s['id']}/caption", headers=csrf_headers(client))
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.get_json()["job"]["id"] == r2.get_json()["job"]["id"]
    assert Job.query.filter_by(type="caption").count() == 1


def test_caption_feedback_fresh_job_dedupsuz(client):
    """Regenerating with feedback creates a new job EVERY TIME (dedup=None), and feedback +
    previous_caption are passed into the payload — independent of the normal caption dedup for the same share."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "C"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    s = client.post("/api/sharing/shares", json={"client_id": cid, "week_iso": "2026-W21", "kind": "post"},
                    headers=csrf_headers(client)).get_json()["share"]
    body = {"feedback": "daha kısa", "previous_caption": "eski caption"}
    r1 = client.post(f"/api/sharing/shares/{s['id']}/caption", json=body, headers=csrf_headers(client))
    r2 = client.post(f"/api/sharing/shares/{s['id']}/caption", json=body, headers=csrf_headers(client))
    from extensions import db
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.get_json()["job"]["id"] != r2.get_json()["job"]["id"]  # fresh every time
    j1 = db.session.get(Job, r1.get_json()["job"]["id"])
    assert j1.payload["feedback"] == "daha kısa"
    assert j1.payload["previous_caption"] == "eski caption"
