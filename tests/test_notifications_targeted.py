"""Targeted notifications + announcements + daily reminders (2026-08-05).

Focus is in two places: **who receives it** (role-slot targeting excludes management)
and **who does NOT receive it** (the coalesce window, empty slot, the silence rule).
"""
from datetime import date, timedelta

import pytest
from conftest import CONTENT_CREATOR, DESIGNER, MANAGER, VIDEOGRAPHER, login_as
from test_session_csrf import csrf_headers


@pytest.fixture
def kisiler(client):
    """UserRef records — `_recipients`/`_management_subs` read from here."""
    from extensions import db
    from models import UserRef
    for u in (MANAGER, DESIGNER, VIDEOGRAPHER, CONTENT_CREATOR):
        if db.session.get(UserRef, u["sub"]) is None:
            db.session.add(UserRef(sub=u["sub"], email=u["email"], name=u["name"],
                                   role=u["role"]))
    db.session.commit()


@pytest.fixture
def cid(client, kisiler):
    login_as(client, MANAGER)
    return client.post("/api/clients", json={"name": "Bildirim Müşterisi"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]


def _ata(cid, slot, sub):
    from extensions import db
    from models import ClientTeamAssignment
    db.session.add(ClientTeamAssignment(client_id=cid, role_slot=slot, user_id=sub))
    db.session.commit()


def _alicilar(kind):
    from models import Notification
    return {n.recipient_sub for n in Notification.query.filter_by(kind=kind).all()}


# --- role-slot targeting -----------------------------------------------------

def test_cekim_fotografi_yalniz_tasarimci_ve_icerikciye(client, cid):
    """Management does NOT receive this notification — the party using the photos is the production team."""
    import notifications
    _ata(cid, "designer", DESIGNER["sub"])
    _ata(cid, "content_creator", CONTENT_CREATOR["sub"])
    _ata(cid, "videographer_shoot", VIDEOGRAPHER["sub"])
    notifications.notify_photos_uploaded(cid, 12)
    assert _alicilar("photos_uploaded") == {DESIGNER["sub"], CONTENT_CREATOR["sub"]}


def test_slot_bossa_kimseye_gitmez(client, cid):
    """No made-up recipient is picked: without an assignment, there's no notification either."""
    import notifications
    assert notifications.notify_photos_uploaded(cid, 3) == []
    assert _alicilar("photos_uploaded") == set()


def test_oncelik_isareti_uretim_ekibine(client, cid):
    import notifications
    _ata(cid, "designer", DESIGNER["sub"])
    _ata(cid, "videographer_edit", VIDEOGRAPHER["sub"])
    notifications.notify_priority_marked(cid, "2026-W33", "Ece")
    assert _alicilar("priority_marked") == {DESIGNER["sub"], VIDEOGRAPHER["sub"]}


def test_video_ve_icerik_yonetime(client, cid):
    """Upload notifications go to management; the production team already knows what they uploaded."""
    import notifications
    _ata(cid, "designer", DESIGNER["sub"])
    notifications.notify_video_uploaded(cid, "2026-W33", "designer")
    notifications.notify_content_uploaded(cid, "2026-W33", "post")
    assert _alicilar("video_uploaded") == {MANAGER["sub"]}
    assert _alicilar("content_uploaded") == {MANAGER["sub"]}


def test_planlama_degisikligi_pano_sahibine(client, cid):
    import notifications
    notifications.notify_planning_changed(DESIGNER["sub"], "Ece")
    assert _alicilar("planning_changed") == {DESIGNER["sub"]}


# --- coalesce -------------------------------------------------------------

def test_parti_yuklemesi_tek_bildirime_toplanir(client, cid):
    """A 47-photo shoot shouldn't produce 47 notifications."""
    import notifications
    _ata(cid, "designer", DESIGNER["sub"])
    for _ in range(5):
        notifications.notify_photos_uploaded(cid, 1)
    from models import Notification
    assert Notification.query.filter_by(kind="photos_uploaded").count() == 1


def test_okunmus_bildirim_coalesce_i_bitirir(client, cid):
    """The window looks at the unread row: if the user has read it, a new event notifies again."""
    import notifications
    from extensions import db
    from models import Notification, utcnow
    _ata(cid, "designer", DESIGNER["sub"])
    notifications.notify_photos_uploaded(cid, 1)
    n = Notification.query.filter_by(kind="photos_uploaded").first()
    n.read_at = utcnow()
    db.session.commit()
    notifications.notify_photos_uploaded(cid, 1)
    assert Notification.query.filter_by(kind="photos_uploaded").count() == 2


def test_farkli_musteri_ayri_bildirim(client, cid, kisiler):
    """The coalesce key is the LINK — different client, different link, both should be sent."""
    import notifications
    login_as(client, MANAGER)
    cid2 = client.post("/api/clients", json={"name": "İkinci"},
                       headers=csrf_headers(client)).get_json()["client"]["id"]
    _ata(cid, "designer", DESIGNER["sub"])
    _ata(cid2, "designer", DESIGNER["sub"])
    notifications.notify_photos_uploaded(cid, 1)
    notifications.notify_photos_uploaded(cid2, 1)
    from models import Notification
    assert Notification.query.filter_by(kind="photos_uploaded").count() == 2


# --- announcements --------------------------------------------------------

def test_anons_role_gore_gonderilir(client, cid):
    login_as(client, MANAGER)
    r = client.post("/api/announce",
                    json={"title": "Toplantı", "body": "Yarın 10:00",
                          "roles": ["designer", "videographer"], "severity": "kritik"},
                    headers=csrf_headers(client))
    assert r.status_code == 201
    assert _alicilar("announcement") == {DESIGNER["sub"], VIDEOGRAPHER["sub"]}
    from models import Notification
    assert Notification.query.filter_by(kind="announcement").first().severity == "kritik"


def test_anons_gonderene_gitmez(client, cid):
    login_as(client, MANAGER)
    client.post("/api/announce",
                json={"title": "Duyuru", "body": "x", "roles": ["management"]},
                headers=csrf_headers(client))
    assert MANAGER["sub"] not in _alicilar("announcement")


def test_anons_kisi_ve_rol_birlesimi_tekil(client, cid):
    login_as(client, MANAGER)
    r = client.post("/api/announce",
                    json={"title": "Duyuru", "body": "x", "roles": ["designer"],
                          "subs": [DESIGNER["sub"], VIDEOGRAPHER["sub"]]},
                    headers=csrf_headers(client))
    assert r.get_json()["sent"] == 2


def test_anons_alicisiz_400(client, cid):
    login_as(client, MANAGER)
    r = client.post("/api/announce", json={"title": "Duyuru", "body": "x"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_anons_basliksiz_400(client, cid):
    login_as(client, MANAGER)
    r = client.post("/api/announce", json={"body": "x", "roles": ["designer"]},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_anons_gecersiz_severity_400(client, cid):
    login_as(client, MANAGER)
    r = client.post("/api/announce",
                    json={"title": "x", "roles": ["designer"], "severity": "acil"},
                    headers=csrf_headers(client))
    assert r.status_code == 400


def test_anons_yalniz_yonetim(client, cid):
    login_as(client, DESIGNER)
    r = client.post("/api/announce", json={"title": "x", "roles": ["designer"]},
                    headers=csrf_headers(client))
    assert r.status_code == 403
    login_as(client, MANAGER)
    assert client.get("/api/announce/recipients").status_code == 200
    login_as(client, VIDEOGRAPHER)
    assert client.get("/api/announce/recipients").status_code == 403


# --- daily reminders --------------------------------------------------------

def _ozel_gun(day_name, month, day, year=None, status="approved"):
    from extensions import db
    from models_sharing import SpecialDayEvent
    db.session.add(SpecialDayEvent(day_name=day_name, month=month, date_num=day,
                                   year=year, active=True, status=status))
    db.session.commit()


def test_yarinin_ozel_gunleri(client, cid):
    from scripts.daily_reminders import yarinin_ozel_gunleri
    hedef = date(2026, 9, 10)
    _ozel_gun("Dünya Kahve Günü", 9, 10)
    _ozel_gun("Başka Gün", 9, 11)
    assert yarinin_ozel_gunleri(hedef) == ["Dünya Kahve Günü"]


def test_taslak_ozel_gun_hatirlatilmaz(client, cid):
    """This is exactly what the approval gate exists for: an AI-generated draft day is not announced."""
    from scripts.daily_reminders import yarinin_ozel_gunleri
    _ozel_gun("Taslak Gün", 9, 10, status="draft")
    assert yarinin_ozel_gunleri(date(2026, 9, 10)) == []


def test_aralikli_ozel_gun_kapsanir(client, cid):
    from extensions import db
    from models_sharing import SpecialDayEvent
    from scripts.daily_reminders import yarinin_ozel_gunleri
    db.session.add(SpecialDayEvent(day_name="Bilim Haftası", month=3, date_start=8,
                                   date_end=14, active=True, status="approved"))
    db.session.commit()
    assert yarinin_ozel_gunleri(date(2026, 3, 10)) == ["Bilim Haftası"]
    assert yarinin_ozel_gunleri(date(2026, 3, 20)) == []


def test_biten_reklam_bulunur(client, cid):
    from extensions import db
    from models import AdCampaign
    from scripts.daily_reminders import biten_reklamlar
    bugun = date(2026, 8, 20)
    db.session.add(AdCampaign(client_id=cid, title="Yaz kampanyası",
                              start_date=bugun - timedelta(days=10), end_date=bugun))
    db.session.add(AdCampaign(client_id=cid, title="Devam eden",
                              start_date=bugun, end_date=bugun + timedelta(days=5)))
    db.session.commit()
    bulunan = biten_reklamlar(bugun)
    assert [k.title for k in bulunan] == ["Yaz kampanyası"]


def test_silinmis_reklam_hatirlatilmaz(client, cid):
    from extensions import db
    from models import AdCampaign, utcnow
    from scripts.daily_reminders import biten_reklamlar
    bugun = date(2026, 8, 20)
    db.session.add(AdCampaign(client_id=cid, title="Silinmiş", start_date=bugun,
                              end_date=bugun, deleted_at=utcnow()))
    db.session.commit()
    assert biten_reklamlar(bugun) == []


def test_sessizlik_kurali(client, cid):
    """If there's no special day tomorrow and no ending campaign, NO notification is produced."""
    from models import Notification
    from scripts.daily_reminders import biten_reklamlar, yarinin_ozel_gunleri
    hedef = date(2031, 4, 17)
    assert yarinin_ozel_gunleri(hedef) == []
    assert biten_reklamlar(hedef) == []
    assert Notification.query.filter_by(kind="special_day_soon").count() == 0
    assert Notification.query.filter_by(kind="ad_ending").count() == 0


def test_ozel_gun_bildirimi_yonetime(client, cid):
    import notifications
    notifications.notify_special_day_soon(["Dünya Kahve Günü"], "10 Eylül")
    assert _alicilar("special_day_soon") == {MANAGER["sub"]}


def test_depo_kotasi_esige_gore_severity(client, cid):
    import notifications
    from models import Notification
    notifications.notify_depot_quota(4.1, 5, 82)
    assert Notification.query.filter_by(kind="depot_quota").first().severity == "normal"
    Notification.query.delete()
    from extensions import db
    db.session.commit()
    notifications.notify_depot_quota(4.8, 5, 96)
    assert Notification.query.filter_by(kind="depot_quota").first().severity == "kritik"


# --- deep link (2026-08-07) --------------------------------------------------

def test_icerik_bildirimi_onay_modalina_gotururur(client, cid):
    """The link carries `?onay=<client_id>` → the panel auto-opens that client's Client
    Approval Link modal. The link used to be just `?week=`; whoever clicked the
    notification landed on the right week but had to find the client by hand."""
    import notifications
    from models import Notification
    notifications.notify_content_uploaded(cid, "2026-W33", "post")
    n = Notification.query.filter_by(kind="content_uploaded").first()
    assert n.link == f"/panel/sharing?week=2026-W33&onay={cid}"


def test_farkli_musteriler_ayri_bildirim_alir(client, cid, kisiler):
    """The coalesce key is the LINK; since the link contains the client, two clients
    in the same week can't merge into one notification — if they did, clicking it
    would leave which client it goes to ambiguous."""
    import notifications
    from models import Notification
    ikinci = client.post("/api/clients", json={"name": "İkinci Müşteri"},
                         headers=csrf_headers(client)).get_json()["client"]["id"]
    notifications.notify_content_uploaded(cid, "2026-W33", "post")
    notifications.notify_content_uploaded(ikinci, "2026-W33", "post")
    linkler = {n.link for n in Notification.query.filter_by(kind="content_uploaded").all()}
    assert linkler == {f"/panel/sharing?week=2026-W33&onay={cid}",
                       f"/panel/sharing?week=2026-W33&onay={ikinci}"}


def test_ayni_musteri_partisi_hala_tek_bildirim(client, cid):
    """Going down to a per-client basis must NOT break batch coalescing: a 10-file design
    batch is still a single notification."""
    import notifications
    from models import Notification
    for _ in range(4):
        notifications.notify_content_uploaded(cid, "2026-W33", "post")
    assert Notification.query.filter_by(kind="content_uploaded").count() == 1
