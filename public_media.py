"""Permanent direct media link — public `GET /m/<file_id>` (2026-07-30).

WHY THIS EXISTS: the "Copy" button on videographer pages was giving out the link to
Drive's file page (`drive.google.com/file/d/<id>/view`) — Drive's viewer PAGE, not
the file itself. This endpoint serves the file ITSELF and the link is **permanent**:

    if a local copy exists (the 21-day `media_store` window) → the file is served directly
    if there's no local copy (the window has closed)         → 302 redirect to the Drive link

So the same link works forever; when the server copy is deleted it silently falls
back to Drive. The Drive link isn't stored SEPARATELY either — `file_id` is already
the Drive file id, the redirect address is derived from it (no new column needed).

THREE MODES (2026-07-31 — the "let whoever gets the link download it" request):

    /m/<id>          → mini HTML page: player/image + "İndir" (Download) button
    /m/<id>?raw=1    → the file itself, inline (Range supported — video seek)
    /m/<id>?dl=1     → the file itself, as an attachment (browser saves it)

Why the raw file is no longer the default: a copied link usually goes outside the
agency, and downloading from a raw `video/mp4` response stayed buried in the
browser's built-in player menu (often missing entirely on mobile). The page turns
downloading into a visible button. `?raw=1` preserves the raw behavior exactly →
existing consumers using `<video src>`/embeds aren't broken.

NO AUTH — deliberate (project owner decision 2026-07-30: "anyone who knows the link
should be able to open it"). This is NOT a new hole: this endpoint serves ONLY the
two file classes already made "anyone with the link can read" in Drive via
`grant_anyone_reader` —
  * videographer VIDEO uploads (`CardUpload.category='video'`, granted by sharing.upload)
  * shoot PHOTOS (`VideographerPhoto`, granted by vg_photo_upload)
These two are already open to anyone who knows the link; serving the same file from
our own server doesn't widen the audience. EVERYTHING ELSE in `media_store` (client
design uploads, share images — these have NO public permission in Drive) returns
**404** here. Eligibility is queried from the DB, not the filesystem:
`media_store.find_original` is called only after eligibility passes; otherwise the
endpoint would become a read oracle for the whole of `media_store`.

A soft-deleted record (`deleted_at`) also returns 404 → a file deleted from the
panel has its link die.
"""
import html
import logging
import mimetypes
import re

from flask import Blueprint, Response, redirect, request, send_file

import media_store
import ratelimit
from models_sharing import CardUpload, VideographerPhoto

log = logging.getLogger(__name__)

bp = Blueprint('public_media', __name__)

DRIVE_VIEW = 'https://drive.google.com/file/d/{}/view'
# Drive's direct download address — for a file whose local copy has expired,
# `?dl=1` redirects here (not to the viewer page): the intent was downloading.
DRIVE_DOWNLOAD = 'https://drive.google.com/uc?export=download&id={}'

# Drive file id alphabet. The length range is kept wide (Drive id length isn't
# fixed); the real gate isn't this, it's the DB eligibility query below.
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{10,128}$')

# Requests allowed per minute from the same IP. Kept more generous than normal
# public endpoints (40/min) since video seeking can generate many Range requests.
RATE_MAX, RATE_WINDOW = 240, 60


def _eligible(file_id):
    """Returns `(file_name, type)` if eligible, `(None, None)` otherwise.

    The type ('video' | 'foto') determines whether the page renders `<video>` or
    `<img>`; the name is used for mime guessing and the download name.
    Order doesn't matter — a file_id can't be in both classes (separate Drive
    uploads)."""
    vu = (CardUpload.query
          .filter_by(file_id=file_id, category='video', deleted_at=None)
          .first())
    if vu is not None:
        return (vu.file_name or file_id), 'video'
    p = VideographerPhoto.query.filter_by(file_id=file_id, deleted_at=None).first()
    if p is not None:
        return (p.file_name or file_id), 'foto'
    return None, None


@bp.get('/m/<file_id>')
def public_media(file_id):
    if not _ID_RE.match(file_id):
        return '', 404
    if not ratelimit.hit(f'pubmedia:{request.remote_addr}', RATE_MAX, RATE_WINDOW):
        return 'çok fazla istek', 429
    name, kind = _eligible(file_id)
    if name is None:
        # Not eligible OR deleted. We don't distinguish the two: doing so would
        # leak which file_ids exist in the system.
        return '', 404

    dl = request.args.get('dl') == '1'
    path = media_store.find_original(file_id)

    # `?web=1` → browser-compatible variant (1080p H.264). PLAYBACK path only:
    # downloading always returns the original, otherwise a 4K-shooting
    # videographer's file would come back as 1080p from the link (2026-08-01).
    web_served = False
    if not dl and request.args.get('web') == '1':
        web = media_store.find_web(file_id)
        if web:
            path, web_served = web, True

    if not (dl or request.args.get('raw') == '1'):
        return _viewer_page(file_id, name, kind, local=bool(path),
                            web=bool(media_store.find_web(file_id)))

    if path:
        # The variant is ALWAYS mp4/H.264; deriving the mime from the file NAME
        # would give `video/quicktime` for `.mov` originals and the browser would
        # reject the variant.
        mime = ('video/mp4' if web_served else
                (mimetypes.guess_type(name)[0] or mimetypes.guess_type(path)[0]
                 or 'application/octet-stream'))
        try:
            # conditional=True → Range supported (seeking forward in video works).
            resp = send_file(path, mimetype=mime, conditional=True,
                             as_attachment=dl, download_name=(name if dl else None))
            resp.headers['Cache-Control'] = 'public, max-age=3600'
            return resp
        except OSError:
            log.warning('lokal kopya okunamadı, Drive\'a düşülüyor: %s', file_id)
    # No local copy / unreadable → Drive. 302 (NOT 301): a permanent redirect
    # would be cached indefinitely by the browser, and we couldn't go back if the
    # local-copy policy changed.
    target = (DRIVE_DOWNLOAD if dl else DRIVE_VIEW).format(file_id)
    return redirect(target, code=302)


def _viewer_page(file_id, name, kind, local, web=False):
    """Mini viewing/download page.

    If `local`, the media is shown from our own server (`?raw=1`) and the
    "İndir" (Download) button goes to `?dl=1`. If the local copy has expired, the
    page DOESN'T DIE: it falls back to Drive's embedded player and downloading
    redirects to Drive — link permanence is preserved.

    `web` (2026-08-01): if the video's browser-compatible variant is ready → the
    player uses IT (`?raw=1&web=1`). If there's no variant, the original is tried;
    if it's 4K/HEVC it may not play on mobile, but the page still offers the
    download path."""
    safe_name = html.escape(name)
    # file_id was validated with `_ID_RE` (only [A-Za-z0-9_-]) → safely embedded in the URL.
    if local:
        src = f'/m/{file_id}?raw=1&amp;web=1' if web else f'/m/{file_id}?raw=1'
        media = (f'<video class="media" src="{src}" controls playsinline '
                 f'preload="metadata"></video>' if kind == 'video'
                 else f'<img class="media" src="/m/{file_id}?raw=1" alt="{safe_name}">')
        dl_href, dl_label = f'/m/{file_id}?dl=1', 'İndir'
        note = ''
    else:
        media = (f'<iframe class="media frame" src="https://drive.google.com/file/d/'
                 f'{file_id}/preview" allow="autoplay; fullscreen" title="{safe_name}">'
                 f'</iframe>')
        dl_href, dl_label = DRIVE_DOWNLOAD.format(file_id), 'Drive\'dan indir'
        note = '<p class="note">Sunucu kopyası süresi doldu — Drive üzerinden gösteriliyor.</p>'

    page = (_PAGE.replace('__MEDIA__', media)
                 .replace('__NAME__', safe_name)
                 # The `&` in the Drive download address must be `&amp;` in the HTML attribute.
                 .replace('__DL_HREF__', html.escape(dl_href, quote=True))
                 .replace('__DL_LABEL__', dl_label)
                 .replace('__NOTE__', note))
    return Response(page, mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow',
                             'Cache-Control': 'no-store'})


# Palette is the same as review.py's approval page (dark teal) — on a media-focused
# page a light background washed out the video.
_PAGE = """<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>__NAME__</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
  :root{--bg:#0b1220;--card:#131c2e;--fg:#e6edf6;--mut:#8ea2bd;--acc:#14b8a6;--line:#22304a;}
  body{background:var(--bg);color:var(--fg);min-height:100dvh;display:flex;
    align-items:center;justify-content:center;padding:16px;
    font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;}
  .wrap{width:100%;max-width:880px;background:var(--card);border:1px solid var(--line);
    border-radius:16px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.45);}
  .media{display:block;width:100%;max-height:72dvh;background:#0a0f1a;object-fit:contain;}
  .frame{height:72dvh;border:0;}
  .bar{display:flex;align-items:center;gap:12px;padding:14px 16px;border-top:1px solid var(--line);}
  .meta{min-width:0;flex:1;}
  .name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .note{font-size:12px;color:var(--mut);margin-top:2px;}
  .dl{flex-shrink:0;display:inline-flex;align-items:center;gap:8px;background:var(--acc);
    color:#04231f;font-weight:600;text-decoration:none;padding:10px 18px;border-radius:999px;
    transition:filter .15s ease;}
  .dl:hover{filter:brightness(1.08);}
  @media (max-width:480px){ .bar{flex-wrap:wrap;} .dl{width:100%;justify-content:center;} }
</style>
</head><body>
  <div class="wrap">
    __MEDIA__
    <div class="bar">
      <div class="meta"><div class="name">__NAME__</div>__NOTE__</div>
      <a class="dl" href="__DL_HREF__" download>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/>
        </svg>
        __DL_LABEL__
      </a>
    </div>
  </div>
</body></html>"""
