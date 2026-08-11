"""Notification severity levels and the "should it go to the phone" decision
(2026-08-05).

This module is DELIBERATELY pure: it doesn't touch the DB, HTTP, or the Flask
request. So the decision logic lives in one place and is testable on its own —
delivery (`ntfy_gateway`) and triggers (`notifications`) follow this function's
answer.

Three levels (project owner decision 2026-08-05). The criterion isn't "how
interested is who", it's ACTION:
  kritik (critical) — someone needs to do something today (revision request, ops alert)
  normal            — needs to be known but doesn't create work (client approved, mail arrived)
  bilgi (info)       — historical record (revision resolved, job requeued itself)
"""

KRITIK = 'kritik'
NORMAL = 'normal'
BILGI = 'bilgi'

# The ordering is for comparison (higher = more important). The threshold picker in
# the panel also uses this order: someone who picks "kritik" gets only critical,
# someone who picks "bilgi" gets everything.
SEVERITY_ORDER = {BILGI: 0, NORMAL: 1, KRITIK: 2}
SEVERITIES = tuple(SEVERITY_ORDER)

# ntfy priority mapping (1..5). Critical is 5 → an audible/persistent notification
# on the phone.
NTFY_PRIORITY = {KRITIK: 5, NORMAL: 3, BILGI: 2}
NTFY_TAGS = {KRITIK: 'rotating_light', NORMAL: 'bell', BILGI: 'information_source'}


def rank(severity):
    """An unknown severity is treated as NORMAL — if a new type is missing from the
    catalog, the notification shouldn't silently disappear, it should stay visible."""
    return SEVERITY_ORDER.get(severity, SEVERITY_ORDER[NORMAL])


def in_quiet_hours(start_hour, end_hour, now_hour):
    """Are we inside the quiet-hours window? The range CAN wrap past midnight (22→08).

    Bounds: start inclusive, end exclusive (22–08 → 22:xx is quiet, 08:xx isn't).
    Missing/equal values mean "no quiet hours"."""
    if start_hour is None or end_hour is None or start_hour == end_hour:
        return False
    if start_hour < end_hour:                      # a plain range like 09–18
        return start_hour <= now_hour < end_hour
    return now_hour >= start_hour or now_hour < end_hour   # a wrapping range like 22–08


def should_push_ntfy(pref, severity, now_hour):
    """Should this notification go to this user's phone?

    `pref`: a `NotificationPref`-like object or None (if there's no record, nothing
    goes out — it's OPT-IN; the in-panel bell already works for everyone).

    Order matters: the quiet-hours check comes AFTER the threshold check, and
    critical punches through it (project owner decision: a 03:00 ops alert
    shouldn't wait)."""
    if pref is None or not getattr(pref, 'ntfy_enabled', False):
        return False
    if not getattr(pref, 'ntfy_topic', None):
        return False
    if rank(severity) < rank(getattr(pref, 'min_severity', KRITIK)):
        return False
    if severity != KRITIK and in_quiet_hours(
            getattr(pref, 'quiet_start', None), getattr(pref, 'quiet_end', None), now_hour):
        return False
    return True
