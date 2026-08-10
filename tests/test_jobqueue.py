"""Job queue — enqueue / claim / complete / fail / retry-backoff / janitor."""
from datetime import timedelta, timezone

from sqlalchemy.dialects import postgresql

import jobqueue
from conftest import MANAGER, login_as
from models import Job, utcnow
from test_session_csrf import csrf_headers


def _aware(dt):
    """sqlite naive datetime'ı UTC-aware'e çevir (karşılaştırma için)."""
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
    assert jobqueue.claim(["caption"]) is None  # kuyruk boş


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
    """fail() İLK satırda rollback yapar: kirli/pending-rollback session'ı toparlar,
    istisna fırlatmaz ve sonrasında sorgu çalışır."""
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    # Session'ı kirlet: NOT NULL ihlali flush'ı (type=None)
    from extensions import db
    db.session.add(Job(type=None, status="queued"))
    try:
        db.session.flush()
    except Exception:
        pass  # session artık pending-rollback
    jobqueue.fail(j, "patladi")  # istisna FIRLATMAMALI
    # session temiz → sorgu başarıyla çalışır
    assert db.session.query(Job).count() >= 1
    assert j.status == "failed"


def test_fail_transient_requeue_backoff(client):
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])  # attempts=1
    assert j.attempts == 1
    jobqueue.fail(j, "rate limit exceeded", transient=True, max_attempts=3)
    assert j.status == "queued"
    assert j.available_at is not None
    assert _aware(j.available_at) > utcnow()  # backoff gelecekte
    assert j.result["retry"] is True


def test_fail_transient_max_attempts_terminal(client):
    from extensions import db
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    j.attempts = 3
    db.session.commit()
    jobqueue.fail(j, "rate limit", transient=True, max_attempts=3)
    assert j.status == "failed"  # requeue YOK
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
    j = jobqueue.claim(["caption"])  # claimed_at ~ şimdi
    reaped = jobqueue.reap_stuck(timeout_seconds=1800)
    assert j.id not in [r.id for r in reaped]
    db.session.refresh(j)
    assert j.status == "running"


def test_claim_skip_locked_pg_compile(client):
    """PG dalı sessizce kaybolmasın: derlenmiş SQL 'SKIP LOCKED' içermeli."""
    q = jobqueue._select_query(["caption"], utcnow(), for_update=True)
    sql = str(q.statement.compile(dialect=postgresql.dialect()))
    assert "SKIP LOCKED" in sql


def test_is_transient_klasiflendirir(client):
    import ai_worker
    assert ai_worker._is_transient(Exception("rate limit exceeded"))
    assert ai_worker._is_transient(TimeoutError("timed out after 240 seconds"))
    assert ai_worker._is_transient(Exception("Overloaded 429"))
    # Her hata transient DEĞİL — sonsuz retry riskine karşı
    assert not ai_worker._is_transient(ValueError("paylaşım yok: 5"))


# --- priority sırası (step 02) ---

def test_claim_priority_once_id_sonra(client):
    """Un-gameable: düşük-priority job ÖNCE (küçük id) enqueue edilir, yüksek-priority
    SONRA (büyük id). claim id sırasına RAĞMEN yüksek priority'yi ÖNCE döndürmeli."""
    dusuk = jobqueue.enqueue("caption", {"p": "dusuk"}, priority=0)
    yuksek = jobqueue.enqueue("caption", {"p": "yuksek"}, priority=10)
    assert dusuk.id < yuksek.id  # id ters: düşük öncelikli olan daha eski
    ilk = jobqueue.claim(["caption"])
    assert ilk.id == yuksek.id  # priority DESC — id'ye rağmen yüksek önce
    ikinci = jobqueue.claim(["caption"])
    assert ikinci.id == dusuk.id


def test_claim_esit_priority_fifo(client):
    """Eşit priority'de küçük id önce (FIFO korunur)."""
    a = jobqueue.enqueue("caption", {"n": 1}, priority=5)
    b = jobqueue.enqueue("caption", {"n": 2}, priority=5)
    assert jobqueue.claim(["caption"]).id == a.id
    assert jobqueue.claim(["caption"]).id == b.id


def test_enqueue_priority_default_0(client):
    j = jobqueue.enqueue("caption", {})
    assert j.priority == 0


# --- fan-out (step 02) ---

def test_enqueue_per_client_ayri_joblar(client):
    """TAM 3 ayrı Job; her biri doğru client_id payload'ı ve verilen priority."""
    jobs = jobqueue.enqueue_per_client(
        "brief", [1, 2, 3],
        payload_fn=lambda cid: {"client_id": cid},
        priority=0)
    assert Job.query.count() == 3
    assert len(jobs) == 3
    assert sorted(j.payload["client_id"] for j in jobs) == [1, 2, 3]
    assert all(j.type == "brief" for j in jobs)
    # AYRI job'lar — farklı id
    assert len({j.id for j in jobs}) == 3


def test_enqueue_per_client_priority_iletilir(client):
    jobs = jobqueue.enqueue_per_client(
        "special_days", [7, 8],
        payload_fn=lambda cid: {"client_id": cid},
        priority=3)
    assert all(j.priority == 3 for j in jobs)


def test_enqueue_per_client_per_client_dedup_key(client):
    """dedup_key_fn her müşteri için FARKLI anahtar üretir (tek skaler DEĞİL);
    anahtar payload'da _dedup_key olarak görünür. Dedup davranışı 04'te — burada
    sadece per-client iletim doğrulanır."""
    jobs = jobqueue.enqueue_per_client(
        "brief", [1, 2, 3],
        payload_fn=lambda cid: {"client_id": cid},
        dedup_key_fn=lambda cid: f"brief:{cid}")
    keys = [j.payload["_dedup_key"] for j in jobs]
    assert keys == ["brief:1", "brief:2", "brief:3"]  # müşteri-başı FARKLI
    assert len(set(keys)) == 3


def test_enqueue_dedup_key_yoksa_payload_dokunulmaz(client):
    j = jobqueue.enqueue("caption", {"share_id": 1})
    assert "_dedup_key" not in (j.payload or {})


# --- dedup (step 04) ---

def test_enqueue_dedup_ikinci_cagri_no_op(client):
    """Aynı (type, dedup_key) ile iki kez enqueue → tek job; ikinci çağrı birinciyi döndürür."""
    a = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    b = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    assert Job.query.count() == 1
    assert b.id == a.id


def test_enqueue_dedup_farkli_key_iki_job(client):
    jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    jobqueue.enqueue("caption", {"share_id": 8}, dedup_key="caption:8")
    assert Job.query.count() == 2


def test_enqueue_dedup_farkli_type_carismaz(client):
    """Dedup yalnız aynı type içinde: aynı key ama farklı type → 2 job."""
    jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="k")
    jobqueue.enqueue("media", {"share_id": 7}, dedup_key="k")
    assert Job.query.count() == 2


def test_enqueue_dedup_yalniz_aktif_isleri_kapsar(client):
    """Aktif job done olduktan SONRA aynı key → yeni job (dedup yalnız queued|running)."""
    a = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    j = jobqueue.claim(["caption"])
    jobqueue.complete(j, {"ok": True})  # artık done
    b = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    assert b.id != a.id
    assert Job.query.count() == 2


def test_enqueue_dedup_running_de_kapsar(client):
    """Aktif job running iken (henüz bitmemiş) aynı key → yeni job atılmaz."""
    a = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    jobqueue.claim(["caption"])  # running
    b = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
    assert b.id == a.id
    assert Job.query.count() == 1


def test_enqueue_dedup_eszamanli_tek_aktif_job(app):
    """TOCTOU yarışı: iki thread AYNI ANDA aynı (type, dedup_key) ile enqueue eder
    (barrier ile check'leri örtüşür). check-then-act kilitsiz olsaydı ikisi de
    `_active_dupe`'u boş bulup 2 job üretirdi (gthread 2×4 production topolojisi).
    Fix (enqueue içinde serileştirme) → yalnız 1 aktif job kalmalı, ikinci çağrı
    birincinin job'unu döndürmeli. Un-gameable: gerçek eşzamanlılık kurulur, sıralı
    değil."""
    import threading

    from extensions import db

    barrier = threading.Barrier(2)
    ids = []
    errors = []

    def worker():
        try:
            with app.app_context():
                try:
                    barrier.wait(timeout=5)  # iki thread check'e beraber girsin
                    j = jobqueue.enqueue("caption", {"share_id": 7}, dedup_key="caption:7")
                    ids.append(j.id)
                finally:
                    db.session.remove()  # context İÇİNDE temizle
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, errors
    assert Job.query.filter_by(type="caption").count() == 1  # tek aktif job
    assert len(set(ids)) == 1  # iki çağrı aynı job'u gördü


def test_enqueue_dedup_hit_cagiranin_pending_degisikligini_korur(client):
    """Dedup-HIT dalı advisory lock'u bırakırken çağıranın enqueue'dan ÖNCE yaptığı
    (henüz commit edilmemiş) ilgisiz pending değişikliği KORUMALI. Regresyon: r1'de
    kilit `db.session.rollback()` ile bırakılıyordu → dedup-hit tüm session'ı geri
    alıp bu değişikliği sessizce siliyordu. Fix: rollback yerine commit (SELECT hiçbir
    şey değiştirmez; commit hem lock'u bırakır hem pending state'i korur). Un-gameable:
    değişiklik DB'ye yansımalı."""
    from extensions import db
    from models import Client

    c = Client(name="Once")
    db.session.add(c)
    db.session.commit()
    # aktif job — sonraki enqueue dedup-HIT olacak
    jobqueue.enqueue("caption", {"share_id": c.id}, dedup_key=f"caption:{c.id}")
    # çağıran enqueue'dan ÖNCE bir alan değiştirir (henüz commit edilmemiş)
    c.name = "SONRA-DEGISTI"
    # dedup-HIT: yeni INSERT yok, mevcut job döner
    jobqueue.enqueue("caption", {"share_id": c.id}, dedup_key=f"caption:{c.id}")
    # pending değişiklik korunmalı ve DB'ye yazılmış olmalı
    db.session.refresh(c)
    assert c.name == "SONRA-DEGISTI"


# --- caption enqueue priority (step 02) ---

def test_caption_enqueue_priority_10(client):
    """İnteraktif caption yüksek öncelikle (priority=10) kuyruğa girmeli — batch
    işlerin arkasında head-of-line beklememesi için (step 02). Uçtan uca: caption
    enqueue eden API çağrısı priority=10'lu Job yaratmalı."""
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
    """Aynı share'e arka arkaya POST .../caption → tek aktif job (dedup no-op)."""
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
    """Feedback'li yeniden üret HER SEFERİNDE yeni job üretir (dedup=None) ve payload'a
    feedback + previous_caption geçer — aynı share'e normal caption dedup'undan bağımsız."""
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "C"}, headers=csrf_headers(client)).get_json()["client"]["id"]
    s = client.post("/api/sharing/shares", json={"client_id": cid, "week_iso": "2026-W21", "kind": "post"},
                    headers=csrf_headers(client)).get_json()["share"]
    body = {"feedback": "daha kısa", "previous_caption": "eski caption"}
    r1 = client.post(f"/api/sharing/shares/{s['id']}/caption", json=body, headers=csrf_headers(client))
    r2 = client.post(f"/api/sharing/shares/{s['id']}/caption", json=body, headers=csrf_headers(client))
    from extensions import db
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.get_json()["job"]["id"] != r2.get_json()["job"]["id"]  # fresh her seferinde
    j1 = db.session.get(Job, r1.get_json()["job"]["id"])
    assert j1.payload["feedback"] == "daha kısa"
    assert j1.payload["previous_caption"] == "eski caption"
