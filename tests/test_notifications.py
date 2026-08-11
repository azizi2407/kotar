"""In-panel notifications: push (DB write), GET/read endpoints, authorization boundaries."""
from datetime import timedelta

from sqlalchemy import event

from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers

import jobqueue
import notifications
from api import NOTIFICATION_LIST_LIMIT
from extensions import db
from models import Notification, UserRef, utcnow


def test_push_bir_satir_yazar(client):
    n = notifications.push(MANAGER["sub"], "revision_requested", "Başlık", "Gövde",
                            link="/panel/x")
    db.session.commit()  # push doesn't commit — persisting the row is the caller's job
    assert Notification.query.count() == 1
    row = db.session.get(Notification, n.id)
    assert row.recipient_sub == MANAGER["sub"]
    assert row.kind == "revision_requested"
    assert row.title == "Başlık"
    assert row.body == "Gövde"
    assert row.link == "/panel/x"
    assert row.read_at is None
    assert row.created_at is not None


def test_push_commit_etmez_caller_rollback_ederse_bildirim_kaybolur(client):
    """push() only does add+flush; if the caller rolls back without committing,
    the notification row must not persist (Faz 0 review I1)."""
    notifications.push(MANAGER["sub"], "revision_requested", "Başlık", "x")
    # thanks to flush the row is visible within the same session but not yet committed
    assert Notification.query.count() == 1
    db.session.rollback()
    assert Notification.query.count() == 0


def test_push_to_client_team_coklu_alicida_tek_commit_atar(client):
    """_push_to_client_team should commit exactly once overall even when writing to N
    recipients (instead of a separate commit per recipient — see Faz 0 review M1)."""
    from models import UserRef

    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                           name=MANAGER["name"], role="management"))
    db.session.add(UserRef(sub="99", email="ikinci@test.com", name="İkinci Yönetici",
                           role="management"))
    db.session.commit()

    commit_count = {"n": 0}

    def on_commit(_session):
        commit_count["n"] += 1

    event.listen(db.session, "after_commit", on_commit)
    try:
        notifications._push_to_client_team("revision_requested", None, "Başlık", "x")
    finally:
        event.remove(db.session, "after_commit", on_commit)

    assert commit_count["n"] == 1
    assert Notification.query.count() == 2


def test_get_notifications_yalniz_alicinin_satirlarini_doner(client):
    notifications.push(MANAGER["sub"], "revision_requested", "Yönetici için", "x")
    notifications.push(DESIGNER["sub"], "revision_requested", "Tasarımcı için", "y")

    login_as(client, MANAGER)
    r = client.get("/api/notifications")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["notifications"]) == 1
    assert data["notifications"][0]["title"] == "Yönetici için"
    assert data["unread_count"] == 1


def test_get_notifications_oturumsuz_401(client):
    r = client.get("/api/notifications")
    assert r.status_code == 401


def test_read_kendi_bildirimini_isaretler(client):
    n = notifications.push(MANAGER["sub"], "revision_requested", "Başlık", "x")
    login_as(client, MANAGER)
    r = client.post(f"/api/notifications/{n.id}/read", headers=csrf_headers(client))
    assert r.status_code == 200
    row = db.session.get(Notification, n.id)
    assert row.read_at is not None

    # the read one must no longer show up among the unread
    r2 = client.get("/api/notifications")
    assert r2.get_json()["unread_count"] == 0


def test_baska_kullanicinin_bildirimini_isaretleyemez(client):
    n = notifications.push(DESIGNER["sub"], "revision_requested", "Başlık", "x")
    login_as(client, MANAGER)
    r = client.post(f"/api/notifications/{n.id}/read", headers=csrf_headers(client))
    assert r.status_code == 404
    row = db.session.get(Notification, n.id)
    assert row.read_at is None


def test_read_all_tumunu_isaretler(client):
    notifications.push(MANAGER["sub"], "revision_requested", "Bir", "x")
    notifications.push(MANAGER["sub"], "revision_requested", "İki", "x")
    notifications.push(DESIGNER["sub"], "revision_requested", "Başkasının", "x")

    login_as(client, MANAGER)
    r = client.post("/api/notifications/read-all", headers=csrf_headers(client))
    assert r.status_code == 200

    unread = Notification.query.filter_by(recipient_sub=MANAGER["sub"], read_at=None).count()
    assert unread == 0
    # someone else's notification must not be affected
    other_unread = Notification.query.filter_by(recipient_sub=DESIGNER["sub"], read_at=None).count()
    assert other_unread == 1


def test_unread_count_limit_disinda_kalan_okunmamislari_da_sayar(client):
    """unread_count is computed with SQL COUNT — even though the list is capped at
    the last N, the badge count must show the real total unread count (see Faz 0 review M4)."""
    for i in range(NOTIFICATION_LIST_LIMIT + 5):
        notifications.push(MANAGER["sub"], "revision_requested", f"Bildirim {i}", "x")
    db.session.commit()

    login_as(client, MANAGER)
    r = client.get("/api/notifications")
    data = r.get_json()
    assert len(data["notifications"]) == NOTIFICATION_LIST_LIMIT
    assert data["unread_count"] == NOTIFICATION_LIST_LIMIT + 5


def test_revizyon_talebi_yoneticilere_ve_atanan_ekibe_bildirim_yazar(client, app):
    from extensions import db as _db
    from models import Client, ClientTeamAssignment, UserRef

    # manager UserRef + designer assigned to the client
    _db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                            name=MANAGER["name"], role="management"))
    _db.session.add(UserRef(sub=DESIGNER["sub"], email=DESIGNER["email"],
                            name=DESIGNER["name"], role="designer"))
    c = Client(name="Test Müşteri")
    _db.session.add(c)
    _db.session.flush()
    _db.session.add(ClientTeamAssignment(client_id=c.id, role_slot="designer",
                                         user_id=DESIGNER["sub"]))
    _db.session.commit()

    notifications.notify_revision_requested("design", c.id, "2026-W29", note="not")

    manager_notifs = Notification.query.filter_by(recipient_sub=MANAGER["sub"]).all()
    designer_notifs = Notification.query.filter_by(recipient_sub=DESIGNER["sub"]).all()
    assert len(manager_notifs) == 1
    assert len(designer_notifs) == 1


def _mgmt_ve_atanan_designer(client):
    from extensions import db as _db
    from models import Client, ClientTeamAssignment, UserRef
    _db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                            name=MANAGER["name"], role="management"))
    _db.session.add(UserRef(sub=DESIGNER["sub"], email=DESIGNER["email"],
                            name=DESIGNER["name"], role="designer"))
    c = Client(name="İnceleme Müşteri")
    _db.session.add(c)
    _db.session.flush()
    _db.session.add(ClientTeamAssignment(client_id=c.id, role_slot="designer",
                                         user_id=DESIGNER["sub"]))
    _db.session.commit()
    return c


def test_client_review_onaylandi_yalniz_yonetime(client, app):
    """Client 'approved' → management only; does NOT go to the assigned designer (2026-07-21)."""
    c = _mgmt_ve_atanan_designer(client)
    notifications.notify_client_review(c.id, "2026-W29", "approved")
    assert Notification.query.filter_by(recipient_sub=MANAGER["sub"],
                                        kind="client_review").count() == 1
    assert Notification.query.filter_by(recipient_sub=DESIGNER["sub"],
                                        kind="client_review").count() == 0


def test_client_review_revizyon_ekibe_de(client, app):
    """Client 'requested revision' → management + assigned team (rework is needed)."""
    c = _mgmt_ve_atanan_designer(client)
    notifications.notify_client_review(c.id, "2026-W29", "revision_requested")
    assert Notification.query.filter_by(recipient_sub=MANAGER["sub"],
                                        kind="client_review").count() == 1
    assert Notification.query.filter_by(recipient_sub=DESIGNER["sub"],
                                        kind="client_review").count() == 1


# --- job-failure / stuck notification wiring (step 05) ---

def _yonetici_user_ref():
    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                           name=MANAGER["name"], role="management"))
    db.session.commit()


def test_job_terminal_fail_yonetime_bildirim_yazar(client):
    """Un-gameable: job_failed count is 0 BEFORE the terminal fail, ≥1 AFTER, with the right recipient."""
    _yonetici_user_ref()
    jobqueue.enqueue("caption", {"share_id": 1})
    j = jobqueue.claim(["caption"])
    assert Notification.query.filter_by(kind="job_failed").count() == 0
    jobqueue.fail(j, "kalıcı hata oldu", transient=False)
    notifs = Notification.query.filter_by(kind="job_failed").all()
    assert len(notifs) == 1
    assert notifs[0].recipient_sub == MANAGER["sub"]
    assert "caption" in notifs[0].body


def test_job_transient_requeue_bildirim_atmaz(client):
    """Negative check: transient + attempts<max → only requeue, no notification is CREATED."""
    _yonetici_user_ref()
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])  # attempts=1
    jobqueue.fail(j, "rate limit exceeded", transient=True, max_attempts=3)
    assert j.status == "queued"
    assert Notification.query.filter_by(kind="job_failed").count() == 0


def test_reap_stuck_yonetime_bildirim_yazar(client):
    _yonetici_user_ref()
    jobqueue.enqueue("caption", {})
    j = jobqueue.claim(["caption"])
    j.claimed_at = utcnow() - timedelta(hours=1)
    db.session.commit()
    jobqueue.reap_stuck(timeout_seconds=1800)
    notifs = Notification.query.filter_by(kind="job_stuck").all()
    assert len(notifs) == 1
    assert notifs[0].recipient_sub == MANAGER["sub"]


def test_ops_digest_report_yalniz_yonetime_yazar(client):
    """notify_ops_digest_report writes ops_digest_report notifications only to management
    recipients (not to designer). push() flushes; the test persists it with commit."""
    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"],
                           name=MANAGER["name"], role="management"))
    db.session.add(UserRef(sub=DESIGNER["sub"], email=DESIGNER["email"],
                           name=DESIGNER["name"], role="designer"))
    db.session.commit()

    notifs = notifications.notify_ops_digest_report("Ops Digest takip raporu", "gövde metni")
    db.session.commit()

    rows = Notification.query.filter_by(kind="ops_digest_report").all()
    assert len(notifs) == 1                       # management recipient only
    assert len(rows) == 1
    assert rows[0].recipient_sub == MANAGER["sub"]
    assert rows[0].title == "Ops Digest takip raporu"
    assert rows[0].body == "gövde metni"


def test_job_failed_spam_coalesce_ikinci_bildirim_olusmaz(client):
    """TWO terminal-fails for the same job_type in a short window → the SECOND notification
    is NOT created (skipped while a matching unread one exists). Un-gameable: after two
    fails, the matching unread notification count is 1."""
    _yonetici_user_ref()
    j1 = jobqueue.enqueue("caption", {"n": 1})
    j1 = jobqueue.claim(["caption"])
    jobqueue.fail(j1, "hata bir", transient=False)
    j2 = jobqueue.enqueue("caption", {"n": 2})
    j2 = jobqueue.claim(["caption"])
    jobqueue.fail(j2, "hata iki", transient=False)
    notifs = Notification.query.filter_by(kind="job_failed", recipient_sub=MANAGER["sub"]).all()
    assert len(notifs) == 1


def test_get_notifications_sayfalama_has_more(client):
    # 3 notifications; requesting limit=2 returns 2 rows on the first page + has_more True.
    for i in range(3):
        notifications.push(MANAGER["sub"], "revision_requested", f"Bildirim {i}", "x")
    db.session.commit()
    login_as(client, MANAGER)

    r = client.get("/api/notifications?limit=2")
    d = r.get_json()
    assert len(d["notifications"]) == 2
    assert d["has_more"] is True
    assert d["unread_count"] == 3  # unread_count is independent of pagination (global)

    # second page via offset: the remaining 1 row, has_more False.
    r2 = client.get("/api/notifications?limit=2&offset=2")
    d2 = r2.get_json()
    assert len(d2["notifications"]) == 1
    assert d2["has_more"] is False


def test_ops_digest_report_link_yok(client):
    # The entire Ops Digest report is in the body; link=None since there's no /panel/jobs page.
    db.session.add(UserRef(sub=MANAGER["sub"], email=MANAGER["email"], name="M", role="management"))
    db.session.commit()
    notifications.notify_ops_digest_report("Ops Digest takip raporu", "tüm rapor gövdede")
    db.session.commit()
    rows = Notification.query.filter_by(kind="ops_digest_report").all()
    assert rows and all(r.link is None for r in rows)
