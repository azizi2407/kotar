"""Notification triggers — in-panel notification + (2026-08-05) ntfy push.

Notifications are written to the `Notification` table; the panel's bell
component reads them. Recipient resolution is kept simple (YAGNI): everyone
with the management role + team members assigned to the relevant client
(ClientTeamAssignment), deduplicated.

**Two channels, one source (2026-08-05):** every row lands in the panel;
additionally, if it matches the user's preference, it goes to their phone via
ntfy. The decision is in `notify_rules`, delivery in `ntfy_gateway`, preference
in `NotificationPref` (no record → NO push — opt-in).

**Severity lives in one place: `CATALOG`.** Previously each type's recipient
rule was embedded in its own function; adding severity there too would have
spread the same information across two or three places. Adding a new `kind`
REQUIRES adding a line here — the integrity test (`tests/test_notify_rules.py`)
catches a type with no catalog entry.
"""
import logging
import os
from collections import namedtuple
from datetime import timedelta

import ntfy_gateway
from extensions import db
from models import (Client, ClientTeamAssignment, Notification, NotificationPref,
                    UserRef, utcnow)
from notify_rules import BILGI, KRITIK, NORMAL, should_push_ntfy

log = logging.getLogger('agency.notify')

KIND_LABELS = {'design': 'design', 'video': 'video'}

# audience is for DOCUMENTATION purposes only (recipient resolution happens in
# the trigger functions); severity, however, is actually used — push() takes its default from here.
Kind = namedtuple('Kind', 'severity audience')

CATALOG = {
    # critical — someone needs to do something today
    'revision_requested': Kind(KRITIK, 'client_team'),
    'old_video_removed':  Kind(KRITIK, 'client_team'),   # 30-day rollback window
    # 2026-08-05: had fallen out of the catalog (was written under the wrong
    # name `client_provisioned`) → silently fell back to NORMAL. If the folder
    # doesn't exist, NOTHING can be uploaded for that client.
    'provision_failed':   Kind(KRITIK, 'management'),
    'depot_quota':        Kind(KRITIK, 'management'),    # client uploads also stop at the ceiling
    # normal — needs to be known but doesn't require action
    'client_review':      Kind(NORMAL, 'client_team'),   # escalates to KRITIK on revision
    'pre_approval':       Kind(NORMAL, 'client_team'),   # escalates to KRITIK on revision
    'job_failed':         Kind(NORMAL, 'management'),
    'ops_digest_report':     Kind(NORMAL, 'management'),
    'mail':               Kind(NORMAL, 'owner'),
    'old_video_kept':     Kind(NORMAL, 'client_team'),
    'announcement':       Kind(NORMAL, 'secilen'),       # sender chooses severity
    'priority_marked':    Kind(NORMAL, 'client_team'),
    'planning_changed':   Kind(NORMAL, 'pano_sahibi'),
    'photos_uploaded':    Kind(NORMAL, 'designer_slot'),
    'video_uploaded':     Kind(NORMAL, 'management'),
    'content_uploaded':   Kind(NORMAL, 'management'),
    'special_day_soon':   Kind(NORMAL, 'management'),
    'ad_ending':          Kind(NORMAL, 'management'),
    'approval_note':      Kind(NORMAL, 'client_team'),   # wrote a note on the client approval page
    # info — historical record
    'revision_resolved':  Kind(BILGI, 'client_team'),
    'job_stuck':          Kind(BILGI, 'management'),
    'similarity_report':  Kind(BILGI, 'management'),
}

# No second notification is generated within the window for the same (recipient,
# type, link). Uploads come in batches (a 47-photo shoot, a 10-file design batch)
# — without this, each file would spawn its own notification bell, making it unusable.
COALESCE_MINUTES = {'content_uploaded': 30, 'photos_uploaded': 30, 'video_uploaded': 30,
                    'planning_changed': 15, 'approval_note': 30}


def severity_of(kind):
    """Default severity from the catalog. An unknown type counts as NORMAL — so
    forgetting to update the catalog doesn't drop the notification (a test already catches it)."""
    entry = CATALOG.get(kind)
    return entry.severity if entry else NORMAL


def _deliver_ntfy(notif, severity, now_hour=None):
    """Deliver the notification to the recipient's phone — if their preference
    allows it. Best-effort: nothing here affects the panel flow (the gateway swallows errors too)."""
    if not ntfy_gateway.available():
        return False
    pref = db.session.get(NotificationPref, notif.recipient_sub)
    if now_hour is None:
        now_hour = utcnow().astimezone(_ISTANBUL).hour if _ISTANBUL else utcnow().hour
    if not should_push_ntfy(pref, severity, now_hour):
        return False
    click = f'{_PANEL_ORIGIN}{notif.link}' if notif.link and _PANEL_ORIGIN else None
    return ntfy_gateway.send(pref.ntfy_topic, notif.title, notif.body,
                             severity=severity, click_url=click)


try:                                      # quiet hours are relative to the user's local time
    from zoneinfo import ZoneInfo
    _ISTANBUL = ZoneInfo('Europe/Istanbul')
except Exception:                         # noqa: BLE001 — fall back to UTC if there's no tz data
    _ISTANBUL = None

_PANEL_ORIGIN = os.getenv('AGENCY_BASE_URL', 'https://panel.example.com').rstrip('/')

# job_failed spam-coalesce window: when `claude -p` runs out of quota, many jobs can
# terminal-fail at once and flood the bell — don't create a new notification for the
# same (recipient, job_type) if an unread matching one already exists in the window
# (consistent with the 15-min/ops_digest dedup pattern).
JOB_FAILED_COALESCE_MINUTES = 15


def push(recipient_sub, kind, title, body, link=None, severity=None):
    """Adds one notification row and flushes it (INSERT is sent, id is assigned) —
    BUT DOES NOT COMMIT. Commit responsibility belongs to the caller: push() must
    not prematurely commit a transaction the caller hasn't finished, making
    half-done session state permanent (Phase 0 review I1). Within this module the
    caller is `_push_to_client_team` — it does add+flush for all recipients, then
    issues a SINGLE commit.

    If `severity` isn't given it comes from the catalog; the caller can escalate
    it depending on the situation (e.g. client approval is NORMAL but a revision
    request is KRITIK). ntfy delivery also happens here — IMMEDIATELY after the
    panel row is written, without waiting for commit: the pushed message will
    also sit in the panel, and the rollback chance in between (error path only)
    means an extra phone notification, which is better than a lost one."""
    sev = severity or severity_of(kind)
    n = Notification(recipient_sub=recipient_sub, kind=kind, severity=sev,
                     title=title, body=body, link=link)
    db.session.add(n)
    db.session.flush()
    _deliver_ntfy(n, sev)
    return n


def _recipients(client_id):
    """Everyone with the management role + team members assigned to the client (deduplicated sub set)."""
    subs = {u.sub for u in UserRef.query.filter_by(role='management').all()}
    if client_id is not None:
        assigned = ClientTeamAssignment.query.filter_by(client_id=client_id).all()
        subs.update(a.user_id for a in assigned)
    return subs


def _recipients_slot(client_id, *role_slots):
    """People in ONLY the given role slots (2026-08-05) — management is NOT INCLUDED.

    `_recipients` sends everything to all of management + the whole team; as
    notification volume grows this makes the bell unusable. Targeted
    notifications (shoot photos → that client's designer) use this instead. Can
    return an empty set: if the slot is empty, no one gets it — no made-up recipient is picked."""
    if client_id is None:
        return set()
    rows = (ClientTeamAssignment.query
            .filter(ClientTeamAssignment.client_id == client_id,
                    ClientTeamAssignment.role_slot.in_(role_slots))
            .all())
    return {r.user_id for r in rows if r.user_id}


def _management_subs():
    return {u.sub for u in UserRef.query.filter_by(role='management').all()}


def _coalesced(sub, kind, link):
    """Is there an unread notification in the window for this (recipient, type,
    link)? If so, no new one is generated — the generalized form of `job_failed`'s 15-min spam guard."""
    dakika = COALESCE_MINUTES.get(kind)
    if not dakika:
        return False
    pencere = utcnow() - timedelta(minutes=dakika)
    return (Notification.query
            .filter_by(recipient_sub=sub, kind=kind, link=link, read_at=None)
            .filter(Notification.created_at >= pencere)
            .first()) is not None


def _push_many(subs, kind, title, body, link=None, severity=None):
    """push to the given recipients + a SINGLE commit; applies the coalesce window.
    Does nothing (and doesn't commit) if the recipient set is empty."""
    notifs = [push(sub, kind, title, body, link=link, severity=severity)
              for sub in subs if not _coalesced(sub, kind, link)]
    if notifs:
        db.session.commit()
    return notifs


def _client_name(client_id):
    c = db.session.get(Client, client_id)
    return c.name if c else f'#{client_id}'


def _push_to_client_team(kind, client_id, title, body, link=None, severity=None):
    """Does add+flush for all recipients, then issues a SINGLE commit (instead of a
    per-recipient commit — this is both more efficient and guarantees the
    notification isn't lost when sharing.py callers don't commit after notify_*)."""
    notifs = [push(sub, kind, title, body, link=link, severity=severity)
              for sub in _recipients(client_id)]
    if notifs:
        db.session.commit()
    return notifs


def notify_revision_requested(kind, client_id, week_iso, note=None):
    label = KIND_LABELS.get(kind, kind)
    title = f'Revision request ({label})'
    body = f'{_client_name(client_id)} — revision requested for week {week_iso}.'
    if note:
        body += f' Note: {note}'
    _push_to_client_team('revision_requested', client_id, title, body,
                          link=f'/panel/sharing?client_id={client_id}&week={week_iso}')


def notify_revision_resolved(kind, client_id, week_iso):
    label = KIND_LABELS.get(kind, kind)
    title = f'Revision resolved ({label})'
    body = f'{_client_name(client_id)} — revision closed for week {week_iso}.'
    _push_to_client_team('revision_resolved', client_id, title, body,
                          link=f'/panel/sharing?client_id={client_id}&week={week_iso}')


def notify_client_review(client_id, week_iso, status):
    status_label = 'approved' if status == 'approved' else 'revision requested'
    title = 'Client review'
    body = f'{_client_name(client_id)} — week {week_iso}: {status_label}.'
    link = f'/panel/sharing?client_id={client_id}&week={week_iso}'
    if status == 'approved':
        # An approval doesn't require action from the team (designer/videographer) →
        # management only (2026-07-21). A revision needs the team to see it too (rework needed).
        notifs = [push(sub, 'client_review', title, body, link=link)
                  for sub in _recipients(None)]
        if notifs:
            db.session.commit()
    else:
        # The client requested a revision → action needed, escalate the catalog default to KRITIK.
        _push_to_client_team('client_review', client_id, title, body, link=link,
                             severity=KRITIK)


def notify_pre_approval(client_id, week_iso, status, file_name=None):
    """Pre-approval decision (2026-07-24): a manager approved or requested revision
    on the designer's upload. The TEAM is also notified on revision (rework
    needed); on approval only management is (no designer action needed, they see it from the board badge)."""
    label = 'pre-approval granted' if status == 'approved' else 'revision requested at pre-approval'
    title = 'Pre-approval'
    detail = f' ({file_name})' if file_name else ''
    body = f'{_client_name(client_id)} — week {week_iso}: {label}{detail}.'
    link = f'/panel/designer?week={week_iso}'
    if status == 'approved':
        notifs = [push(sub, 'pre_approval', title, body, link=link)
                  for sub in _recipients(None)]
        if notifs:
            db.session.commit()
    else:
        # Revision at pre-approval → the designer will have to redo it, KRITIK.
        _push_to_client_team('pre_approval', client_id, title, body, link=link,
                             severity=KRITIK)


def notify_approval_note(client_id, ozet):
    """Wrote in the note pad on the client approval page (2026-08-06).

    Called every time the note TEXT changes; consecutive changes are coalesced by
    a 30-min window (see COALESCE_MINUTES).

    **Changed on 2026-08-07:** it used to be sent only on the FIRST write; when
    the client wrote a new note hours later, no one heard about it — caught in a
    live trial. Coalescing does the same job more correctly: one notification per
    writing session, the next note gets a new notification. The note itself is
    feedback → the team should see it too (`_push_to_client_team`), but whether
    it requires action isn't clear → it stays NORMAL."""
    title = 'Client note'
    body = f'{_client_name(client_id)} wrote a note on the approval page: {ozet}'
    # `_push_many`, NOT `_push_to_client_team`: only the latter applies the
    # coalesce window. The recipient set is the same (`_recipients`), the only
    # difference is spam suppression — since this type relies on the 30-min window, this is the right path.
    _push_many(_recipients(client_id), 'approval_note', title, body,
               link=f'/panel/sharing?client_id={client_id}')


def notify_old_video_removed(client_id, week_iso, new_name, removed_names):
    """A revision was uploaded, the old version(s) were auto-deleted (2026-08-01).

    Deletion must NOT be silent: the decision rests on a rule that looks at the
    file name, and the only recovery from a wrong match is the Drive trash (30
    days) — if no one notices, that window slips too."""
    eski = ', '.join(removed_names)
    title = 'Old video removed (revision)'
    body = (f'{_client_name(client_id)} — {week_iso}: "{new_name}" was uploaded as '
            f'a revision, the previous version was removed ({eski}). If this was '
            f'wrong, it can be restored from the Drive trash within 30 days.')
    _push_to_client_team('old_video_removed', client_id, title, body,
                         link=f'/panel/videograf-yukleme?week={week_iso}')


def notify_old_video_kept(client_id, week_iso, new_name, kept_names, reason):
    """A revision came in but the old version was KEPT (in sharing / client approval).

    We leave the decision to a human: deleting a video that's already been shared
    silently breaks the item on the client approval page (`Share.file_id` is plain text, no FK)."""
    eski = ', '.join(kept_names)
    title = 'Revision received — old video not removed'
    body = (f'{_client_name(client_id)} — {week_iso}: "{new_name}" looks like a '
            f'revision, but the previous version ({eski}) was not removed because '
            f'{reason}. Remove it manually if needed.')
    _push_to_client_team('old_video_kept', client_id, title, body,
                         link=f'/panel/videograf-yukleme?week={week_iso}')


def _job_link(job):
    return f'/panel/jobs?type={job.type}'


def notify_job_failed(job):
    """Terminal job failure → notification to management (`kind='job_failed'`).
    `push()` only flushes — commit is the caller's job (jobqueue.fail's terminal
    branch), that branch's existing SINGLE commit persists both the job and the notification.

    Spam-coalesce: don't create a new one for the same (recipient,
    kind='job_failed', job_type) if a matching unread notification exists within
    the last JOB_FAILED_COALESCE_MINUTES — so a batch of terminal fails on quota exhaustion doesn't flood the bell."""
    error = str((job.result or {}).get('error') or '')[:200]
    title = 'AI job failed'
    body = f'{job.type} job failed.' + (f' Error: {error}' if error else '')
    link = _job_link(job)
    window_start = utcnow() - timedelta(minutes=JOB_FAILED_COALESCE_MINUTES)
    notifs = []
    for sub in _recipients(None):
        dupe = (Notification.query
                .filter_by(recipient_sub=sub, kind='job_failed', link=link, read_at=None)
                .filter(Notification.created_at >= window_start)
                .first())
        if dupe is not None:
            continue
        notifs.append(push(sub, 'job_failed', title, body, link=link))
    return notifs


def notify_ops_digest_report(title, body):
    """Ops Digest tracking report → in-panel notification to management only
    (`kind='ops_digest_report'`). Since the report is operational (jobs/approval/
    client gaps), the recipient is NOT client-team, only management
    (`_recipients(None)`). `push()` flushes; commit is the caller's
    (`ai_worker.ops_digest_handler`) job."""
    # The full report is in `body`; there's no separate "jobs" page → NO link
    # (the body reads in full on the notification page; the broken /panel/jobs redirect was removed).
    return [push(sub, 'ops_digest_report', title, body, link=None)
            for sub in _recipients(None)]


def notify_similarity_report(week_iso, title, body):
    """K9 cross-client similarity alert → management only (`kind='similarity_report'`).
    The report is strategic/operational (cross-client theme overlap, NOT
    client-team) → recipient is management only (`_recipients(None)`).

    Idempotent: if an unread similarity_report already exists for the same week,
    a new one is NOT CREATED (`title` carries the week → per-week dedup; re-running
    the same week doesn't flood the bell — consistent with the job_failed coalesce
    pattern). `push()` flushes; commit is the caller's (`ai_worker.similarity_handler`) job."""
    notifs = []
    for sub in _recipients(None):
        dupe = (Notification.query
                .filter_by(recipient_sub=sub, kind='similarity_report',
                           title=title, read_at=None)
                .first())
        if dupe is not None:
            continue
        notifs.append(push(sub, 'similarity_report', title, body, link=None))
    return notifs


def notify_job_stuck(job):
    """`reap_stuck` requeue → notification to management (`kind='job_stuck'`). Same
    single-commit pattern: `push()` flushes, `reap_stuck`'s own commit persists it."""
    title = 'AI job stuck'
    body = f'{job.type} job got stuck and was requeued.'
    link = _job_link(job)
    return [push(sub, 'job_stuck', title, body, link=link) for sub in _recipients(None)]


# --- 2026-08-05: targeted notifications -------------------------------------
# Common principle: the recipient set is NARROW (role slot / board owner /
# management only) and batched events are coalesced into one notification via `COALESCE_MINUTES`.


def notify_announcement(subs, title, body, severity=NORMAL, link=None):
    """Manual announcement (manager → chosen people). The SENDER chooses severity —
    the catalog default is only a fallback. NO coalescing: each announcement is its own message."""
    return _push_many(subs, 'announcement', title, body, link=link, severity=severity)


def notify_priority_marked(client_id, week_iso, by_name=None):
    """A manager marked the client 'priority' for that week → the client's
    PRODUCTION team. The mark means "move this up" — if the team doesn't see it, the mark changes nothing."""
    subs = _recipients_slot(client_id, 'designer', 'content_creator',
                            'videographer_shoot', 'videographer_edit')
    if not subs:
        return []
    kim = f' ({by_name})' if by_name else ''
    return _push_many(subs, 'priority_marked', 'Priority client',
                      f'{_client_name(client_id)} — week {week_iso} was marked '
                      f'priority{kim}. Bump it up in this week\'s work.',
                      link=f'/panel/designer?week={week_iso}')


def notify_planning_changed(owner_sub, actor_name, board_title=None):
    """A manager made a change on SOMEONE ELSE'S planning board → the board owner.
    No notification is sent to someone working on their own board (the caller checks this)."""
    if not owner_sub:
        return []
    return _push_many({owner_sub}, 'planning_changed', 'Your planning board was updated',
                      f'{actor_name} made a change on your board'
                      f'{f" ({board_title})" if board_title else ""}.',
                      link='/panel/planlama')


def notify_photos_uploaded(client_id, adet):
    """A videographer uploaded shoot photos → ONLY that client's designer +
    content creator (they're the ones who use the photos; management isn't in the loop here)."""
    subs = _recipients_slot(client_id, 'designer', 'content_creator')
    if not subs:
        return []
    return _push_many(subs, 'photos_uploaded', 'Shoot photos uploaded',
                      f'{_client_name(client_id)} — {adet} photos uploaded.',
                      link=f'/panel/designer/musteri/{client_id}')


def notify_video_uploaded(client_id, week_iso, yukleyen_rol=None):
    """A video was uploaded (by videographer OR designer) → management. Separate
    type: management routes video differently from content (approval link, sharing card)."""
    kaynak = f' ({yukleyen_rol})' if yukleyen_rol else ''
    return _push_many(_management_subs(), 'video_uploaded', 'Video uploaded',
                      f'{_client_name(client_id)} — video uploaded for week '
                      f'{week_iso}{kaynak}.',
                      link=f'/panel/sharing?week={week_iso}')


def notify_content_uploaded(client_id, week_iso, kategori):
    """A designer uploaded content (post/story/linkedin) → management.

    **The link goes straight to the action (2026-08-07, project owner):** `?onay=<client_id>`
    opens the sharing board and that client's **Client Approval Link** modal opens
    automatically — the work to do after the notification is already "check it and send to the client".
    The link used to be just `?week=`, it didn't even carry client info.

    **The side effect is deliberate:** since the link now includes the client, the
    30-min coalesce window now operates PER CLIENT (it used to lump all clients of
    the same week into one notification). If a notification is going to take you
    to a client-specific page, two clients' uploads can't be merged into one
    notification — it would be unclear which one it should go to."""
    return _push_many(_management_subs(), 'content_uploaded', 'New content uploaded',
                      f'{_client_name(client_id)} — content uploaded for week '
                      f'{week_iso} ({kategori}).',
                      link=f'/panel/sharing?week={week_iso}&onay={client_id}')


def notify_depot_quota(kullanilan_gb, limit_gb, yuzde):
    """The videographer storage threshold was exceeded → management. Above 95% is
    KRITIK: this eats from the SAME Drive quota as client content, and uploads also stop at the ceiling."""
    sev = KRITIK if yuzde >= 95 else NORMAL
    return _push_many(_management_subs(), 'depot_quota', 'Videographer depot filling up',
                      f'Depot is {yuzde}% full ({kullanilan_gb:.1f}/{limit_gb:.0f} GB). '
                      'If it hits the ceiling, client video/image uploads also stop — '
                      'clean up old files.',
                      link='/panel/videograf-deposu', severity=sev)


def notify_special_day_soon(gunler, tarih_metni):
    """Tomorrow's special days — a DAILY reminder. Not called if there are no
    days (generating an empty notification devalues the bell)."""
    if not gunler:
        return []
    liste = ', '.join(gunler)
    return _push_many(_management_subs(), 'special_day_soon', f'Tomorrow: {tarih_metni}',
                      f"Tomorrow's special days: {liste}.",
                      link='/panel/special-days')


def notify_ad_ending(client_id, kampanya_adi, bitis):
    """An ad campaign is ending today → management. Result metrics need to be
    entered and the renewal decision made today; if missed, the campaign dies silently."""
    ad = kampanya_adi or 'Ad campaign'
    return _push_many(_management_subs(), 'ad_ending', 'Ad campaign ends today',
                      f'{_client_name(client_id)} — the "{ad}" campaign ends on '
                      f'{bitis}. Enter results / decide on renewal.',
                      link='/panel/reklam')
