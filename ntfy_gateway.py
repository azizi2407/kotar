"""ntfy channel — sends the notification to the user's phone (2026-08-05).

A single job: HTTP POST. The caller makes the decision
(`notify_rules.should_push_ntfy`), only delivery lives here.

**Best-effort and SYNCHRONOUS.** The ntfy server is on localhost (127.0.0.1:2586,
podman) — not an external service like Drive/Magnific, its latency is
milliseconds. Putting it on the job queue would add a delay of the worker's poll
interval, and "instant notification" would lose its meaning. In return, no error
affects the caller: the notification row is already written to the DB, it not
reaching the phone shouldn't break the panel.

Writing requires a token (`NTFY_TOKEN`): ntfy has `auth-default-access:
read-only`, meaning nobody can produce a message without a token. Reading relies
on the secrecy of the topic name — topics are random 32-hex and are shown only
to their owner.
"""
import logging
import os

import requests

from notify_rules import NTFY_PRIORITY, NTFY_TAGS, NORMAL

log = logging.getLogger('agency.ntfy')

TIMEOUT_SECONDS = 3


def base_url():
    return (os.environ.get('NTFY_BASE_URL') or '').rstrip('/')


def available():
    """Is the channel configured? (if the env is missing, the panel silently falls
    back to the in-panel channel)"""
    return bool(base_url() and os.environ.get('NTFY_TOKEN'))


def _baslik(value):
    """Give the title as UTF-8 BYTES.

    If requests receives the header as str it encodes it to latin-1, turning
    "Çekim fotoğrafı" into "?ekim foto?raf?" (seen live on 2026-08-05). The ntfy
    server accepts raw UTF-8 in the header (verified with curl: 'Çekim fotoğrafı
    … İĞÜŞÖÇ' came back unchanged); by passing bytes we bypass requests' own
    encoding. The title is also collapsed to a single line: a line break in an
    HTTP header is a request-splitting risk (header injection), and notification
    titles can carry user text (announcements)."""
    tek_satir = ' '.join((value or '').split())
    return tek_satir.encode('utf-8')


def send(topic, title, body, severity=NORMAL, click_url=None):
    """Send a single notification. True on success; False on any error (swallowed).

    Note: `click_url` is the corresponding panel page — tapping the notification
    on the phone goes straight there (the `Notification.link` field)."""
    if not available() or not topic:
        return False
    headers = {
        'Authorization': f"Bearer {os.environ['NTFY_TOKEN']}",
        'Title': _baslik(title),
        'Priority': str(NTFY_PRIORITY.get(severity, 3)),
        'Tags': NTFY_TAGS.get(severity, 'bell'),
    }
    if click_url:
        headers['Click'] = click_url
    try:
        r = requests.post(f'{base_url()}/{topic}', data=(body or '').encode('utf-8'),
                          headers=headers, timeout=TIMEOUT_SECONDS)
        if r.status_code >= 400:
            log.warning('ntfy gönderilemedi (topic=%s http=%s): %s',
                        topic, r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001 — delivery is best-effort, the panel flow is critical
        log.warning('ntfy isteği başarısız (topic=%s): %s', topic, e)
        return False
