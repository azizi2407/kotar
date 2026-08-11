"""Special Days — event CRUD (management) + public selection page (token)."""
import pytest
from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def cid(client):
    login_as(client, MANAGER)
    return client.post("/api/clients", json={"name": "SD Müşteri"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _event(client, **kw):
    body = {"day_name": "Anneler Günü", "month": 5, "year": 2026, "type": "day", "date_num": 11}
    body.update(kw)
    return client.post("/api/sharing/special-days/events", json=body, headers=csrf_headers(client))


# --- event CRUD ---

def test_event_create_designer_403(client, cid):
    login_as(client, DESIGNER)
    assert _event(client).status_code == 403


def test_event_create_ve_liste(client, cid):
    e = _event(client).get_json()["event"]
    assert e["day_name"] == "Anneler Günü"
    evs = client.get("/api/sharing/special-days/events?month=5&year=2026").get_json()["events"]
    assert any(x["id"] == e["id"] for x in evs)


def test_event_liste_global_ve_musteriye_ozel(client, cid):
    g = _event(client, day_name="Global Gün").get_json()["event"]
    s = _event(client, day_name="Müşteri Günü", client_id=cid).get_json()["event"]
    # without client_id, only global
    evs = client.get("/api/sharing/special-days/events?month=5&year=2026").get_json()["events"]
    ids = [x["id"] for x in evs]
    assert g["id"] in ids and s["id"] not in ids
    # with client_id, global + client-specific
    evs2 = client.get(f"/api/sharing/special-days/events?month=5&year=2026&client_id={cid}").get_json()["events"]
    ids2 = [x["id"] for x in evs2]
    assert g["id"] in ids2 and s["id"] in ids2


def test_event_delete(client, cid):
    e = _event(client).get_json()["event"]
    assert client.delete(f"/api/sharing/special-days/events/{e['id']}",
                         headers=csrf_headers(client)).status_code == 200
    evs = client.get("/api/sharing/special-days/events?month=5&year=2026").get_json()["events"]
    assert e["id"] not in [x["id"] for x in evs]


def test_pasif_event_yonetim_listesinde_yok(client, cid):
    """active=False = deleted (soft delete from the old side, present in migrated data)."""
    from extensions import db
    from models_sharing import SpecialDayEvent
    e = _event(client, day_name="Pasif Gün").get_json()["event"]
    db.session.get(SpecialDayEvent, e["id"]).active = False
    db.session.commit()
    evs = client.get("/api/sharing/special-days/events?month=5&year=2026").get_json()["events"]
    assert e["id"] not in [x["id"] for x in evs]


# --- selection link + public ---

def test_selection_link_token_uretir(client, cid):
    r = client.post("/api/sharing/special-days/selection-link",
                    json={"client_id": cid, "month": 5, "year": 2026}, headers=csrf_headers(client))
    assert r.status_code == 200
    t1 = r.get_json()["token"]
    assert len(t1) >= 16
    # idempotent (same month → same token)
    t2 = client.post("/api/sharing/special-days/selection-link",
                     json={"client_id": cid, "month": 5, "year": 2026},
                     headers=csrf_headers(client)).get_json()["token"]
    assert t1 == t2


def test_public_gecersiz_token_404(client):
    assert client.get("/special-days/yok/events").status_code == 404


def test_public_events_ve_secim(client, cid):
    g = _event(client, day_name="Bayram").get_json()["event"]
    _event(client, day_name="Müşteriye Özel", client_id=cid)
    token = client.post("/api/sharing/special-days/selection-link",
                        json={"client_id": cid, "month": 5, "year": 2026},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    # public event list (global + client-specific)
    d = client.get(f"/special-days/{token}/events").get_json()
    assert d["client_name"] == "SD Müşteri"
    assert len(d["events"]) == 2
    assert all(e["selected"] is False for e in d["events"])
    # select
    r = client.post(f"/special-days/{token}/select", json={"event_ids": [g["id"]]})
    assert r.status_code == 200
    d2 = client.get(f"/special-days/{token}/events").get_json()
    assert next(e for e in d2["events"] if e["id"] == g["id"])["selected"] is True


def test_public_pasif_event_musteriye_gorunmez(client, cid):
    """An inactive event must not be listed or selectable on the client page."""
    from extensions import db
    from models_sharing import SpecialDayEvent
    g = _event(client, day_name="Aktif").get_json()["event"]
    p = _event(client, day_name="Pasif").get_json()["event"]
    db.session.get(SpecialDayEvent, p["id"]).active = False
    db.session.commit()
    token = client.post("/api/sharing/special-days/selection-link",
                        json={"client_id": cid, "month": 5, "year": 2026},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    d = client.get(f"/special-days/{token}/events").get_json()
    ids = [e["id"] for e in d["events"]]
    assert g["id"] in ids and p["id"] not in ids
    # even if an inactive event is manually POSTed, it must not be written to the selection
    r = client.post(f"/special-days/{token}/select", json={"event_ids": [g["id"], p["id"]]})
    assert r.get_json()["selected"] == [g["id"]]


def test_public_select_gecersiz_event_filtrelenir(client, cid):
    g = _event(client, day_name="Geçerli").get_json()["event"]
    token = client.post("/api/sharing/special-days/selection-link",
                        json={"client_id": cid, "month": 5, "year": 2026},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    # 99999 invalid event id → gets filtered, only g remains
    client.post(f"/special-days/{token}/select", json={"event_ids": [g["id"], 99999]})
    d = client.get(f"/special-days/{token}/events").get_json()
    selected = [e["id"] for e in d["events"] if e["selected"]]
    assert selected == [g["id"]]


def test_public_sayfa_html(client, cid):
    token = client.post("/api/sharing/special-days/selection-link",
                        json={"client_id": cid, "month": 5, "year": 2026},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    r = client.get(f"/special-days/{token}")
    assert r.status_code == 200 and r.mimetype == "text/html"
    assert "noindex" in r.headers.get("X-Robots-Tag", "")


# --- approval gate (step 06): status/generated_by + approve/reject ---

def _draft_event(**kw):
    """AI-generated draft special day (ORM insert) — default status='draft', generated_by='ai'."""
    from extensions import db
    from models_sharing import SpecialDayEvent
    body = {"day_name": "AI Gün", "month": 5, "year": 2026, "date_num": 3}
    body.update(kw)
    e = SpecialDayEvent(active=True, **body)
    db.session.add(e)
    db.session.commit()
    return e.id


def test_sd_elle_girme_approved_manual(client, cid):
    """A special day management enters manually should be approved/manual (not AI)."""
    e = _event(client).get_json()["event"]
    assert e["status"] == "approved" and e["generated_by"] == "manual"


def test_sd_orm_insert_draft_ai(client, cid):
    """ORM insert (new AI) should default to draft/ai."""
    from extensions import db
    from models_sharing import SpecialDayEvent
    e = db.session.get(SpecialDayEvent, _draft_event())
    assert e.status == "draft" and e.generated_by == "ai"


def test_sd_serialize_status(client, cid):
    e = _event(client).get_json()["event"]
    assert "status" in e and "generated_by" in e


def test_sd_approve_management(client, cid):
    eid = _draft_event()
    r = client.post(f"/api/sharing/special-day-events/{eid}/approve",
                    headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["event"]["status"] == "approved"


def test_sd_approve_designer_403(client, cid):
    eid = _draft_event()
    login_as(client, DESIGNER)
    r = client.post(f"/api/sharing/special-day-events/{eid}/approve",
                    headers=csrf_headers(client))
    assert r.status_code == 403


def test_sd_reject_taslakta_birakir(client, cid):
    eid = _draft_event()
    client.post(f"/api/sharing/special-day-events/{eid}/approve", headers=csrf_headers(client))
    r = client.post(f"/api/sharing/special-day-events/{eid}/reject", headers=csrf_headers(client))
    assert r.status_code == 200
    assert r.get_json()["event"]["status"] == "draft"


# --- approval gate (step 07): public client-facing view only shows approved ---

def test_public_taslak_event_musteriye_gorunmez(client, cid):
    """NEGATIVE: a draft (unapproved, AI-generated) special day must NOT appear on the public
    selection endpoint; approved ones DO appear. Unapproved content must not leak into the client-facing surface."""
    onayli = _event(client, day_name="Onaylı Bayram").get_json()["event"]  # API → approved
    taslak_id = _draft_event()  # ORM → draft, month=5/year=2026, global
    token = client.post("/api/sharing/special-days/selection-link",
                        json={"client_id": cid, "month": 5, "year": 2026},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    d = client.get(f"/special-days/{token}/events").get_json()
    ids = [e["id"] for e in d["events"]]
    assert onayli["id"] in ids            # approved is visible
    assert taslak_id not in ids           # draft did NOT leak


def test_public_taslak_event_secilemez(client, cid):
    """NEGATIVE: even if a draft event is manually POSTed, it must not be written to the selection (not approved)."""
    onayli = _event(client, day_name="Onaylı").get_json()["event"]
    taslak_id = _draft_event()
    token = client.post("/api/sharing/special-days/selection-link",
                        json={"client_id": cid, "month": 5, "year": 2026},
                        headers=csrf_headers(client)).get_json()["token"]
    with client.session_transaction() as s:
        s.clear()
    r = client.post(f"/special-days/{token}/select",
                    json={"event_ids": [onayli["id"], taslak_id]})
    assert r.get_json()["selected"] == [onayli["id"]]   # draft was filtered out


# --- Phase 3 (step 11): special day bot enqueue + manual trigger endpoint ---

def test_enqueue_special_days_dedup_ayni_ay(client):
    """scripts/enqueue_special_days.run called twice for the same month → a single job (dedup_key)."""
    import scripts.enqueue_special_days as esd
    from models import Job
    j1 = esd.run(month=6, year=2026)
    j2 = esd.run(month=6, year=2026)
    assert j1.id == j2.id                       # no new INSERT while an active job exists
    assert Job.query.filter_by(type="special_days").count() == 1
    assert j1.priority == 0                      # batch (low priority, doesn't block captioning)
    assert j1.payload["month"] == 6 and j1.payload["year"] == 2026


def test_enqueue_special_days_payloadsuz_sonraki_ay(client):
    """If month/year aren't given, the next month is calculated."""
    import ai_worker
    import scripts.enqueue_special_days as esd
    j = esd.run()
    m, y = ai_worker._next_month()
    assert j.payload["month"] == m and j.payload["year"] == y


def test_sd_generate_ucu_management_gate(client, cid):
    """The manual trigger endpoint is management-gated: designer gets 403."""
    login_as(client, DESIGNER)
    r = client.post("/api/sharing/special-day-events/generate",
                    json={"month": 6, "year": 2026}, headers=csrf_headers(client))
    assert r.status_code == 403


def test_sd_generate_ucu_job_atar(client, cid):
    """The manual trigger endpoint assigns a job as management (special_days, low priority)."""
    from models import Job
    r = client.post("/api/sharing/special-day-events/generate",
                    json={"month": 6, "year": 2026, "prompt": "yerel festivalleri de ekle"},
                    headers=csrf_headers(client))
    assert r.status_code == 202
    job = r.get_json()["job"]
    assert job["type"] == "special_days"
    j = db_get_job(Job, job["id"])
    assert j.priority == 0
    assert j.payload["month"] == 6 and j.payload["prompt"] == "yerel festivalleri de ekle"


def db_get_job(Job, jid):
    from extensions import db
    return db.session.get(Job, jid)
