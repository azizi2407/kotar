"""Selected special days within the week in the board card strip (row.special_days)."""
from datetime import date

from conftest import DESIGNER, MANAGER, login_as
from test_session_csrf import csrf_headers
from sharing import _TR_AY

WK = "2026-W30"


def _client(client, name="Özel Gün Müşteri"):
    return client.post("/api/clients", json={"name": name},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _event(day_name, month, year, *, date_num=None, date_start=None, date_end=None,
           client_id=None, status="approved", active=True):
    from extensions import db
    from models_sharing import SpecialDayEvent
    ev = SpecialDayEvent(day_name=day_name, month=month, year=year, date_num=date_num,
                         date_start=date_start, date_end=date_end,
                         type="day" if date_num else "week", client_id=client_id,
                         status=status, active=active)
    db.session.add(ev)
    db.session.commit()
    return ev.id


def _select(cid, month, year, event_ids):
    from extensions import db
    from models_sharing import SpecialDaySelection
    db.session.add(SpecialDaySelection(client_id=cid, month=month, year=year,
                                       selected_event_ids=event_ids))
    db.session.commit()


def _row(client, cid, week=WK):
    rows = client.get(f"/api/sharing/cards?week_iso={week}").get_json()["rows"]
    return next((r for r in rows if r["client"]["id"] == cid), None)


def test_secili_ozel_gun_haftaya_dusuyorsa_kartta(client):
    login_as(client, MANAGER)
    cid = _client(client)
    d = date.fromisocalendar(2026, 30, 3)  # W30 Wednesday
    eid = _event("Test Özel Günü", d.month, d.year, date_num=d.day)
    _select(cid, d.month, d.year, [eid])
    row = _row(client, cid)
    assert row and len(row["special_days"]) == 1
    sd = row["special_days"][0]
    assert sd["day_name"] == "Test Özel Günü"
    assert sd["date_label"] == f"{d.day} {_TR_AY[d.month]}"
    assert sd["type"] == "day"


def test_secilmemis_ozel_gun_kartta_yok(client):
    login_as(client, MANAGER)
    cid = _client(client)
    d = date.fromisocalendar(2026, 30, 3)
    _event("Seçilmeyen Gün", d.month, d.year, date_num=d.day)  # no selection
    row = _row(client, cid)
    assert row and row["special_days"] == []


def test_secili_ama_hafta_disi_kartta_yok(client):
    login_as(client, MANAGER)
    cid = _client(client)
    d = date.fromisocalendar(2026, 30, 3)
    # a day in the same month but that doesn't fall in the week (pick a day outside W30)
    other = date.fromisocalendar(2026, 34, 3)
    eid = _event("Başka Hafta Günü", other.month, other.year, date_num=other.day)
    _select(cid, other.month, other.year, [eid])
    row = _row(client, cid, week=WK)
    assert row and row["special_days"] == []


def test_aralik_tipi_hafta_ile_ortusurse_kartta(client):
    login_as(client, MANAGER)
    cid = _client(client)
    mon = date.fromisocalendar(2026, 30, 1)
    sun = date.fromisocalendar(2026, 30, 7)
    # a range spanning the middle of the week (mon.day .. sun.day) — same-month assumption
    if mon.month == sun.month:
        eid = _event("Farkındalık Haftası", mon.month, mon.year,
                     date_start=mon.day, date_end=sun.day)
        _select(cid, mon.month, mon.year, [eid])
        row = _row(client, cid)
        assert row and len(row["special_days"]) == 1
        assert row["special_days"][0]["type"] == "week"


def test_taslak_ozel_gun_secili_olsa_da_kartta_yok(client):
    login_as(client, MANAGER)
    cid = _client(client)
    d = date.fromisocalendar(2026, 30, 3)
    eid = _event("Taslak Gün", d.month, d.year, date_num=d.day, status="draft")
    _select(cid, d.month, d.year, [eid])
    row = _row(client, cid)
    assert row and row["special_days"] == []


def test_designer_board_da_ozel_gun_gorunur(client):
    login_as(client, MANAGER)
    cid = _client(client)
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=cid, role_slot="designer", user_id=DESIGNER["sub"]))
    db.session.commit()
    d = date.fromisocalendar(2026, 30, 3)
    eid = _event("Designer Görsün", d.month, d.year, date_num=d.day)
    _select(cid, d.month, d.year, [eid])
    login_as(client, DESIGNER)
    rows = client.get(f"/api/sharing/designer/cards?week_iso={WK}").get_json()["rows"]
    row = next((r for r in rows if r["client"]["id"] == cid), None)
    assert row and len(row["special_days"]) == 1 and row["special_days"][0]["day_name"] == "Designer Görsün"


def test_overview_marka_eslemesi(client):
    """/special-days/overview: **only SELECTED days are returned**, and `client_names`
    is also only the ones that selected.

    Changed DELIBERATELY on 2026-07-31 by the project owner's decision. This test used
    to nail down the old behavior: (a) an unselected general day used to stay in the
    response with `client_names: []` → it became a gray "suggested" chip on the
    calendar, (b) an event's `client_id` alone put the brand on the calendar. New
    rule: the calendar is not a "suggested days" board, but a board of days clients
    have requested content for."""
    login_as(client, MANAGER)
    cid_a = _client(client, "Marka A")
    cid_b = _client(client, "Marka B")
    e_global = _event("Kahve Günü", 7, 2026, date_num=24)
    e_secilmemis = _event("Sessiz Gün", 7, 2026, date_num=25)
    e_ozel = _event("Kuruluş Yıldönümü", 7, 2026, date_num=28, client_id=cid_b)
    _select(cid_a, 7, 2026, [e_global])
    items = client.get("/api/sharing/special-days/overview?month=7&year=2026").get_json()["items"]
    by_id = {i["id"]: i for i in items}
    # Only the selected day came back; the other two are NOT in the response AT ALL.
    assert list(by_id) == [e_global]
    assert by_id[e_global]["client_names"] == ["Marka A"]
    assert e_secilmemis not in by_id, 'seçilmeyen gün takvime girmemeli'
    assert e_ozel not in by_id, 'müşteriye özel ama seçilmemiş gün takvime girmemeli'


def test_overview_rol_kapisi(client):
    login_as(client, DESIGNER)
    assert client.get("/api/sharing/special-days/overview?month=7&year=2026").status_code == 200
    from conftest import login_as as _la
    _la(client, {"sub": "99", "email": "p@t.com", "name": "P", "role": "pending"})
    assert client.get("/api/sharing/special-days/overview?month=7&year=2026").status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# /special-days/overview — CALENDAR view (2026-07-31 rule)
#
# RULE (project owner): the calendar shows ONLY days SELECTED by a client.
# The previous behavior returned the entire month's catalog; unselected days filled
# the grid as gray chips, making real commitments invisible.
# There were NO tests for this endpoint before.
# ─────────────────────────────────────────────────────────────────────────────

def _overview(client, month=8, year=2026):
    return client.get(
        f"/api/sharing/special-days/overview?month={month}&year={year}").get_json()["items"]


def test_overview_secilen_gun_gorunur(client):
    login_as(client, MANAGER)
    cid = _client(client, "Seçen Marka")
    eid = _event("Ağustos Günü", 8, 2026, date_num=5)
    _select(cid, 8, 2026, [eid])
    items = _overview(client)
    assert [it["id"] for it in items] == [eid]
    assert items[0]["client_names"] == ["Seçen Marka"]


def test_overview_SECILMEYEN_gun_GORUNMEZ(client):
    """The core of the rule: a day sitting in the catalog that nobody selected doesn't
    make it onto the calendar."""
    login_as(client, MANAGER)
    _client(client)
    _event("Kimse Seçmedi", 8, 2026, date_num=9)
    assert _overview(client) == []


def test_overview_MUSTERIYE_OZEL_ama_secilmemis_gun_GORUNMEZ(client):
    """BEHAVIOR CHANGE: previously an event's `client_id` alone put the brand on the
    calendar. But the selection page (`special_days._events_for`) also OFFERS
    client-specific days for selection → an unmarked client-specific day is also
    "offered, not selected"; there's no reason for it to behave differently from
    general days."""
    login_as(client, MANAGER)
    cid = _client(client, "Özel Marka")
    _event("Markaya Özel Gün", 8, 2026, date_num=12, client_id=cid)
    assert _overview(client) == []


def test_overview_musteriye_ozel_gun_SECILDIYSE_gorunur(client):
    login_as(client, MANAGER)
    cid = _client(client, "Özel Marka")
    eid = _event("Markaya Özel Gün", 8, 2026, date_num=12, client_id=cid)
    _select(cid, 8, 2026, [eid])
    items = _overview(client)
    assert [it["id"] for it in items] == [eid]
    assert items[0]["client_names"] == ["Özel Marka"]


def test_overview_ayni_gunu_secen_iki_marka_birlikte(client):
    login_as(client, MANAGER)
    a = _client(client, "Aaa Marka")
    b = _client(client, "Bbb Marka")
    eid = _event("Ortak Gün", 8, 2026, date_num=3)
    _select(a, 8, 2026, [eid])
    _select(b, 8, 2026, [eid])
    items = _overview(client)
    assert len(items) == 1
    assert items[0]["client_names"] == ["Aaa Marka", "Bbb Marka"]   # sorted


def test_overview_silinmis_musterinin_secimi_takvimde_kalmaz(client):
    """A soft-deleted client's selection shouldn't keep the day on the calendar —
    otherwise a day with no client anymore would still sit in the grid."""
    login_as(client, MANAGER)
    cid = _client(client, "Gidecek Marka")
    eid = _event("Gün", 8, 2026, date_num=7)
    _select(cid, 8, 2026, [eid])
    assert len(_overview(client)) == 1
    client.delete(f"/api/clients/{cid}", json={"reason": "test"},
                  headers=csrf_headers(client))
    assert _overview(client) == []


def test_overview_baska_ayin_secimi_sizmaz(client):
    login_as(client, MANAGER)
    cid = _client(client)
    eid = _event("Eylül Günü", 9, 2026, date_num=4)
    _select(cid, 9, 2026, [eid])
    assert _overview(client, month=8) == []          # didn't leak into August
    assert len(_overview(client, month=9)) == 1


def test_overview_pasif_etkinlik_secili_olsa_da_gorunmez(client):
    login_as(client, MANAGER)
    cid = _client(client)
    eid = _event("Silinmiş Gün", 8, 2026, date_num=6, active=False)
    _select(cid, 8, 2026, [eid])
    assert _overview(client) == []


def test_overview_TASLAK_gun_secilmisse_gorunur(client):
    """Edge case, deliberate: the selection page only offers `approved` days, so a
    draft normally can't be selected. BUT if an approved day gets pulled back to
    draft by management AFTER it was selected, the selection is left dangling —
    hiding that day from the calendar would make a day the client expects content
    for invisible. The panel shows the draft badge."""
    login_as(client, MANAGER)
    cid = _client(client, "Taslak Marka")
    eid = _event("Sonradan Taslak", 8, 2026, date_num=8, status="draft")
    _select(cid, 8, 2026, [eid])
    items = _overview(client)
    assert [it["id"] for it in items] == [eid]
    assert items[0]["status"] == "draft"


def test_overview_tasarimci_okur_yetkisiz_403(client):
    login_as(client, MANAGER)
    cid = _client(client)
    eid = _event("Gün", 8, 2026, date_num=2)
    _select(cid, 8, 2026, [eid])
    login_as(client, DESIGNER)
    assert client.get(
        "/api/sharing/special-days/overview?month=8&year=2026").status_code == 200
    from conftest import PENDING
    login_as(client, PENDING)
    assert client.get(
        "/api/sharing/special-days/overview?month=8&year=2026").status_code == 403
