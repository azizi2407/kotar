"""Weekly brief view — /api/sharing/brief (scoped reads)."""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

WK = "2026-W21"
PENDING = {"sub": "9", "email": "p@t.com", "name": "P", "role": "pending"}


@pytest.fixture
def ctx(client):
    login_as(client, MANAGER)
    cid = client.post("/api/clients", json={"name": "Brief Müşteri"},
                      headers=csrf_headers(client)).get_json()["client"]["id"]
    from extensions import db
    from models_sharing import WeeklyBrief
    # Role-visibility/serialization tests run against an approved brief; the brief
    # endpoint now reads approved-only BY DEFAULT (draft only for management + include_draft=1).
    db.session.add(WeeklyBrief(client_id=cid, week_iso=WK, title="Hafta 21 Brief",
                               status="approved",
                               intro="Bu hafta odak: bahar kampanyası.",
                               ideas=[{"number": 1, "title": "Fikir bir"},
                                      {"number": 2, "title": "Fikir iki"}]))
    db.session.commit()
    return cid


def test_brief_pending_403(client, ctx):
    login_as(client, PENDING)
    assert client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={WK}").status_code == 403


def test_brief_management_gorur(client, ctx):
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={WK}")
    assert r.status_code == 200
    b = r.get_json()["brief"]
    assert b["intro"].startswith("Bu hafta")
    assert len(b["ideas"]) == 2


def test_brief_yok_null(client, ctx):
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso=2026-W40")
    assert r.status_code == 200
    assert r.get_json()["brief"] is None


def test_brief_designer_atanmamis_da_gorur(client, ctx):
    # New behavior: the designer also sees the brief of a client they aren't assigned to
    # (intentional — so "Other Clients" tiles can also open Brief).
    login_as(client, DESIGNER)
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={WK}")
    assert r.status_code == 200 and r.get_json()["brief"]["title"] == "Hafta 21 Brief"


def test_brief_designer_atanmis_gorur(client, ctx):
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=ctx, role_slot="designer", user_id=DESIGNER["sub"]))
    db.session.commit()
    login_as(client, DESIGNER)
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={WK}")
    assert r.status_code == 200 and r.get_json()["brief"]["title"] == "Hafta 21 Brief"


# --- approval gate (step 06): status/generated_by + approve/reject ---

def _draft_brief(cid):
    """EXPLICITLY creates a DRAFT brief (status='draft').

    Until 2026-07-30 this just meant `WeeklyBrief(...)` — the ORM default was 'draft'.
    When the approval gate was removed, the default became 'approved', so a draft now
    has to be set up by hand. Even though there's no longer a path that produces a
    draft, these tests STAY: the approved-only filter on the read side was deliberately
    kept (so a leftover/restored old draft doesn't silently enter the flow), and that
    behavior needs to stay covered by tests."""
    from extensions import db
    from models_sharing import WeeklyBrief
    b = WeeklyBrief(client_id=cid, week_iso="2026-W30", title="AI Brief", status="draft")
    db.session.add(b)
    db.session.commit()
    return b.id


def test_brief_orm_default_approved_onay_kapisi_kaldirildi(client, ctx):
    """APPROVAL GATE REMOVED (2026-07-30, project owner's decision): an AI brief (ORM insert,
    no status given) is now born DIRECTLY approved — it used to be born 'draft' and wait for
    manual approval. `generated_by` DID NOT CHANGE: the origin distinction (ai vs import)
    is preserved."""
    from extensions import db
    from models_sharing import WeeklyBrief
    from sqlalchemy import text
    # server_default path (raw SQL, imitating an existing/import row): has always been approved/import
    db.session.execute(text(
        "INSERT INTO weekly_briefs (client_id, week_iso, title) "
        "VALUES (:c, '2026-W31', 'Mevcut')"), {"c": ctx})
    db.session.commit()
    old = WeeklyBrief.query.filter_by(week_iso="2026-W31").first()
    assert old.status == "approved" and old.generated_by == "import"
    # ORM insert path (new AI brief): NOW approved — no gate, origin still 'ai'
    new = WeeklyBrief(client_id=ctx, week_iso="2026-W32", title="AI Brief")
    db.session.add(new)
    db.session.commit()
    assert new.status == "approved" and new.generated_by == "ai"


def test_brief_serialize_status(client, ctx):
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={WK}")
    b = r.get_json()["brief"]
    assert "status" in b and "generated_by" in b


def test_brief_approve_management(client, ctx):
    bid = _draft_brief(ctx)
    r = client.post(f"/api/sharing/brief/{bid}/approve", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["brief"]["status"] == "approved"


def test_brief_approve_non_management_403(client, ctx):
    bid = _draft_brief(ctx)
    login_as(client, DESIGNER)
    r = client.post(f"/api/sharing/brief/{bid}/approve", headers=csrf_headers(client))
    assert r.status_code == 403


def test_brief_reject_taslakta_birakir(client, ctx):
    bid = _draft_brief(ctx)
    client.post(f"/api/sharing/brief/{bid}/approve", headers=csrf_headers(client))
    r = client.post(f"/api/sharing/brief/{bid}/reject", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["brief"]["status"] == "draft"


# --- panel management visibility (step 07 acceptance criterion): approval gate default ---

DW = "2026-W30"  # the week _draft_brief writes to (status='draft')


def test_brief_management_include_draft_gorur(client, ctx):
    """Management SEES the draft with include_draft=1."""
    _draft_brief(ctx)  # draft brief for the DW week
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={DW}&include_draft=1")
    assert r.status_code == 200
    b = r.get_json()["brief"]
    assert b is not None and b["status"] == "draft"


def test_brief_normal_okuma_approved_only(client, ctx):
    """A normal read with no params is approved-only → draft DOES NOT SHOW (negative)."""
    _draft_brief(ctx)  # draft-only brief for the DW week
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={DW}")
    assert r.status_code == 200
    assert r.get_json()["brief"] is None


def test_brief_non_management_include_draft_gormez(client, ctx):
    """A non-management user (assigned designer) DOES NOT see the draft even with include_draft=1 (negative)."""
    from extensions import db
    from models import ClientTeamAssignment
    _draft_brief(ctx)  # draft-only brief for the DW week
    db.session.add(ClientTeamAssignment(client_id=ctx, role_slot="designer",
                                        user_id=DESIGNER["sub"]))
    db.session.commit()
    login_as(client, DESIGNER)
    r = client.get(f"/api/sharing/brief?client_id={ctx}&week_iso={DW}&include_draft=1")
    assert r.status_code == 200
    assert r.get_json()["brief"] is None


# --- Phase 2 (step 13): weekly brief fan-out enqueue ---

def _active_client(name):
    from extensions import db
    from models import Client
    c = Client(name=name, status="active")
    db.session.add(c)
    db.session.commit()
    return c


def test_brief_fan_out_musteri_basi_ayri_job(client):
    """enqueue_briefs.run with 3 active clients → 3 SEPARATE brief jobs (NOT a single job loop);
    each has the correct client_id + a per-client dedup_key (`brief:{cid}:{week}`) + batch priority."""
    import scripts.enqueue_briefs as eb
    from models import Job
    ids = [_active_client(f"Fanout {i}").id for i in range(3)]
    wk = "2026-W25"
    jobs = eb.run(week_iso=wk)

    assert len(jobs) == 3
    assert Job.query.filter_by(type="brief").count() == 3
    by_client = {j.payload["client_id"]: j for j in jobs}
    assert set(by_client) == set(ids)
    for cid, j in by_client.items():
        assert j.type == "brief"
        assert j.priority == 0                              # batch (doesn't block caption)
        assert j.payload["week_iso"] == wk
        assert j.payload["_dedup_key"] == f"brief:{cid}:{wk}"   # per-client key


def test_brief_fan_out_dedup_tekrar_no_op(client):
    """Fan-out twice for the same week → the per-client dedup_key returns the same active
    job (no duplicate job). Un-gameable: 3 clients, still 3 jobs after the second call."""
    import scripts.enqueue_briefs as eb
    from models import Job
    for i in range(3):
        _active_client(f"Dedup {i}")
    wk = "2026-W26"
    first = eb.run(week_iso=wk)
    second = eb.run(week_iso=wk)
    assert Job.query.filter_by(type="brief").count() == 3
    assert {j.id for j in first} == {j.id for j in second}


def test_brief_fan_out_sadece_aktif_musteri(client):
    """Fan-out only covers active clients (a deleted client is skipped)."""
    import scripts.enqueue_briefs as eb
    from extensions import db
    from models import Client, Job
    aktif = _active_client("Aktif Müşteri")
    pasif = Client(name="Silinmiş Müşteri", status="deleted")
    db.session.add(pasif)
    db.session.commit()
    jobs = eb.run(week_iso="2026-W27")
    assert len(jobs) == 1
    assert Job.query.filter_by(type="brief").count() == 1
    assert jobs[0].payload["client_id"] == aktif.id


def test_brief_fan_out_brief_pasif_atlanir(client):
    """Fan-out only covers brief-ENABLED clients (brief_enabled=False is skipped)."""
    import scripts.enqueue_briefs as eb
    from extensions import db
    from models import Client, Job
    acik = _active_client("Brief Açık")
    kapali = Client(name="Brief Pasif", status="active", brief_enabled=False)
    db.session.add(kapali)
    db.session.commit()
    jobs = eb.run(week_iso="2026-W28")
    assert len(jobs) == 1
    assert Job.query.filter_by(type="brief").count() == 1
    assert jobs[0].payload["client_id"] == acik.id


def test_image_gen_briefs_listeler(client, ctx):
    """AI image form's brief list: all drafts + approved, week_iso descending (newest first)."""
    from extensions import db
    from models_sharing import WeeklyBrief
    db.session.add(WeeklyBrief(client_id=ctx, week_iso="2026-W23", title="Yeni", status="draft"))
    db.session.commit()
    r = client.get(f"/api/sharing/image-gen/briefs?client_id={ctx}")
    assert r.status_code == 200
    briefs = r.get_json()["briefs"]
    weeks = [b["week_iso"] for b in briefs]
    assert weeks == sorted(weeks, reverse=True)          # sorted descending
    assert "2026-W23" in weeks and WK in weeks           # draft + approved together
    statuses = {b["week_iso"]: b["status"] for b in briefs}
    assert statuses["2026-W23"] == "draft"
    assert statuses[WK] == "approved"


# --- POST /brief/generate (manual generate / regenerate) ---
# This endpoint previously had NO tests at all; the gap was closed when `force` was added on 2026-07-30.

def test_brief_generate_management_enqueue_eder(client, ctx):
    """Management triggers → 202 + a 'brief' job on the queue; force not given → NOT in the
    payload (the handler stays idempotent, doesn't overwrite an existing brief)."""
    from models import Job
    r = client.post("/api/sharing/brief/generate",
                    json={"client_id": ctx, "week_iso": "2026-W40"},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    j = Job.query.filter_by(type="brief").order_by(Job.id.desc()).first()
    assert j.payload["client_id"] == ctx and j.payload["week_iso"] == "2026-W40"
    assert "force" not in j.payload


def test_brief_generate_force_payloada_gecer(client, ctx):
    """`force: true` → `force=True` is written to the payload (opens the handler's overwrite path)."""
    from models import Job
    r = client.post("/api/sharing/brief/generate",
                    json={"client_id": ctx, "week_iso": "2026-W40", "force": True},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    j = Job.query.filter_by(type="brief").order_by(Job.id.desc()).first()
    assert j.payload["force"] is True


def test_brief_generate_force_bekleyen_normal_joba_TAKILMAZ(client, ctx):
    """Dedup trap: when a NORMAL job is already pending for the same client+week, a force
    request must not get swallowed by its dedup — otherwise "Regenerate" would silently do
    nothing (force is part of the dedup key)."""
    from models import Job
    body = {"client_id": ctx, "week_iso": "2026-W41"}
    client.post("/api/sharing/brief/generate", json=body, headers=csrf_headers(client))
    r = client.post("/api/sharing/brief/generate", json={**body, "force": True},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    jobs = Job.query.filter_by(type="brief").all()
    payloads = [j.payload for j in jobs if j.payload.get("week_iso") == "2026-W41"]
    assert len(payloads) == 2                                  # the two are SEPARATE jobs
    assert [p.get("force") for p in payloads] == [None, True]


def test_brief_generate_ayni_normal_istek_deduplenir(client, ctx):
    """Control check: two identical requests without force become ONE job (dedup still works)."""
    from models import Job
    body = {"client_id": ctx, "week_iso": "2026-W42"}
    client.post("/api/sharing/brief/generate", json=body, headers=csrf_headers(client))
    client.post("/api/sharing/brief/generate", json=body, headers=csrf_headers(client))
    jobs = [j for j in Job.query.filter_by(type="brief").all()
            if j.payload.get("week_iso") == "2026-W42"]
    assert len(jobs) == 1


def test_brief_generate_non_management_403(client, ctx):
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/brief/generate",
                    json={"client_id": ctx, "week_iso": "2026-W40"},
                    headers=csrf_headers(client))
    assert r.status_code == 403


def test_brief_generate_eksik_alan_400(client, ctx):
    r = client.post("/api/sharing/brief/generate", json={"client_id": ctx},
                    headers=csrf_headers(client))
    assert r.status_code == 400
