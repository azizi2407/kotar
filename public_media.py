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
        return 'too many requests', 429
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


_PLACEHOLDER_RE = re.compile(r'__([A-Z_]+)__')


def _fill(template, **values):
    """Fills the placeholders in a SINGLE pass.

    With chained `replace` calls, a placeholder string embedded in the file
    name (like `__DL_HREF__.mp4`) could get turned into a real value by a
    later step."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


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
    is_video = kind == 'video'
    # file_id was validated with `_ID_RE` (only [A-Za-z0-9_-]) → safely embedded in the URL.
    if local:
        src = f'/m/{file_id}?raw=1&amp;web=1' if web else f'/m/{file_id}?raw=1'
        media = (f'<video class="media" src="{src}" controls playsinline '
                 f'preload="metadata"></video>' if is_video
                 else f'<img class="media" src="/m/{file_id}?raw=1" alt="{safe_name}">')
        dl_href, dl_label = f'/m/{file_id}?dl=1', 'Download'
        note = ''
    else:
        media = (f'<iframe class="media frame" src="https://drive.google.com/file/d/'
                 f'{file_id}/preview" allow="autoplay; fullscreen" title="{safe_name}">'
                 f'</iframe>')
        dl_href, dl_label = DRIVE_DOWNLOAD.format(file_id), 'Download from Drive'
        note = ('<p class="note">The server copy has expired — '
                'showing via Drive.</p>')

    page = _fill(
        _PAGE,
        MEDIA=media,
        NAME=safe_name,
        BADGE='Video' if is_video else 'Photo',
        DESC=('You can watch the video on this page and download it to your '
              'device if you like.' if is_video else
              'You can view the photo on this page and download it to your '
              'device if you like.'),
        # The `&` in the Drive download address must be `&amp;` in the HTML attribute.
        DL_HREF=html.escape(dl_href, quote=True),
        DL_LABEL=dl_label,
        NOTE=note,
    )
    return Response(page, mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow',
                             'Cache-Control': 'no-store'})


# Palette and typography use the same cream/gold language as
# `client_approval.py`/`special_days.py` — every public page that goes out to
# a client should look like it came from the same agency. The previous dark
# teal palette was retired for that reason. Single-file page, inline CSS/JS.
_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>__NAME__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,300;1,400&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
  :root{
    --cream:#FAFAF8; --cream-mid:#F0EDE6; --ink:#1A1A1A; --ink-soft:#3D3D3D;
    --ink-muted:#7A7670; --gold:#C9A96E;
    --border:rgba(26,26,26,0.10); --radius:16px;
    --font-body:'DM Sans',sans-serif; --font-disp:'Cormorant Garamond',serif;
  }
  body{background:var(--cream);color:var(--ink);font-family:var(--font-body);font-size:15px;
    line-height:1.5;min-height:100dvh;padding-bottom:60px;-webkit-font-smoothing:antialiased;}
  body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.025;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");}
  .page-header{position:relative;z-index:1;padding:48px 24px 32px;max-width:880px;margin:0 auto;}
  .logo-lockup{display:flex;align-items:center;gap:10px;margin-bottom:36px;opacity:0;animation:fadeUp .6s ease forwards;}
  .logo-img{height:104px;width:auto;display:block;}
  @media (max-width:400px){ .logo-img{height:84px;} }
  .header-eyebrow{font-size:11px;font-weight:500;letter-spacing:.18em;color:var(--gold);margin-bottom:12px;opacity:0;animation:fadeUp .6s .1s ease forwards;}
  .header-title{font-family:var(--font-disp);font-size:clamp(36px,10vw,52px);font-weight:300;line-height:1.05;letter-spacing:-.01em;color:var(--ink);margin-bottom:14px;opacity:0;animation:fadeUp .6s .15s ease forwards;}
  .header-title em{font-style:italic;color:var(--ink-soft);}
  .header-desc{font-size:14px;font-weight:300;color:var(--ink-muted);line-height:1.65;max-width:420px;opacity:0;animation:fadeUp .6s .25s ease forwards;}
  .divider{max-width:880px;margin:0 auto 32px;padding:0 24px;opacity:0;animation:fadeUp .5s .3s ease forwards;}
  .divider-line{height:1px;background:linear-gradient(to right,transparent,var(--border) 30%,var(--border) 70%,transparent);}
  .layout{position:relative;z-index:1;max-width:880px;margin:0 auto;padding:0 20px;}
  .item{background:#FFF;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;
    box-shadow:0 1px 3px rgba(26,26,26,.04);opacity:0;animation:cardReveal .5s .35s ease forwards;}
  .media-wrap{background:var(--cream-mid);display:flex;align-items:center;justify-content:center;min-height:180px;}
  /* The width is NOT pinned (no `width:100%`): with a portrait video/photo the
     element would fill the box and leave black bars on the sides. The media
     keeps its own aspect ratio, centered in a cream-backed box. */
  .media{display:block;max-width:100%;max-height:72dvh;}
  .media-wrap img{cursor:zoom-in;}
  .media-wrap video{background:#000;}
  .frame{width:100%;height:72dvh;border:0;}
  .item-body{padding:18px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}
  .meta{min-width:0;flex:1;}
  .name{font-size:13px;color:var(--ink-muted);word-break:break-word;}
  .badge{font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin-bottom:4px;}
  .note{font-size:12.5px;color:var(--ink-muted);margin-top:6px;line-height:1.6;}
  .dl{flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;gap:8px;
    height:46px;padding:0 24px;border-radius:999px;background:var(--ink);color:var(--cream);
    font-family:var(--font-body);font-size:13.5px;font-weight:500;letter-spacing:.03em;
    text-decoration:none;-webkit-tap-highlight-color:transparent;
    transition:background .2s ease,transform .12s ease;}
  .dl:hover{background:var(--ink-soft);}
  .dl:active{transform:scale(.98);}
  @media (max-width:480px){ .dl{width:100%;} }
  .lightbox{position:fixed;inset:0;z-index:200;background:rgba(26,26,26,.92);display:none;
    align-items:center;justify-content:center;padding:24px;cursor:zoom-out;}
  .lightbox.open{display:flex;}
  .lightbox img{max-width:100%;max-height:100%;object-fit:contain;}
  @keyframes fadeUp{from{opacity:0;transform:translateY(10px);}to{opacity:1;transform:translateY(0);}}
  @keyframes cardReveal{from{opacity:0;transform:translateY(14px);}to{opacity:1;transform:translateY(0);}}
</style>
</head><body>
  <header class="page-header">
    <div class="logo-lockup"><img class="logo-img" src="/panel/kotar-logo.png" alt="Kotar — digital media agency"></div>
    <p class="header-eyebrow">MEDIA SHARE</p>
    <h1 class="header-title">Shared<br><em>Content</em></h1>
    <p class="header-desc">__DESC__</p>
  </header>
  <div class="divider"><div class="divider-line"></div></div>
  <main class="layout">
    <article class="item">
      <div class="media-wrap">__MEDIA__</div>
      <div class="item-body">
        <div class="meta">
          <div class="badge">__BADGE__</div>
          <div class="name">__NAME__</div>
          __NOTE__
        </div>
        <a class="dl" href="__DL_HREF__" download>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/>
          </svg>
          __DL_LABEL__
        </a>
      </div>
    </article>
  </main>
  <div class="lightbox" id="lightbox"><img id="lightboxImg" src="" alt=""></div>
<script>
// Image zoom — same lightbox as the approval page. A video/iframe has no img,
// so nothing gets attached in that case.
const lightbox=document.getElementById("lightbox"), lightboxImg=document.getElementById("lightboxImg"),
      image=document.querySelector(".media-wrap img");
if(image){
  image.addEventListener("click",()=>{ lightboxImg.src=image.src; lightbox.classList.add("open"); });
  lightbox.addEventListener("click",()=>lightbox.classList.remove("open"));
  document.addEventListener("keydown",e=>{ if(e.key==="Escape") lightbox.classList.remove("open"); });
}
</script>
</body></html>"""
