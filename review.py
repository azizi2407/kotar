"""Public review page — client approval flow (/review/<token>).

NO auth: the token itself is the authorization (32 bytes, unguessable).
Independent of the panel, a single-file HTML page (inline CSS/JS), mobile-
first, noindex. The client sees the shares SELECTED for that week (a share's
existence = management's selection; draft/published doesn't matter), and
approves or requests a revision. Approval happens BEFORE publishing — same
visibility rule as the board (`_visible_shares`).

Security: the media proxy + action are restricted to shares within the
link's (client_id, week_iso) scope; an arbitrary file_id / another client's
share is rejected. No CSRF (mutation is protected by the token), a simple
per-token rate limit.
"""
import mimetypes

import hmac

from flask import (Blueprint, Response, abort, jsonify, request,
                   send_file, session)

import drive_gateway as dg
import media_store
import ratelimit
from extensions import db
from models import Client, utcnow
from models_sharing import (CardUpload, DriveThumbnail, PreApprovalLink,
                            ReviewExcludedUpload, ReviewLink, UploadPreApproval,
                            UploadReview)
from notifications import notify_client_review, notify_pre_approval
from sharing import REVIEW_CATEGORIES, pre_approval_map, review_visible_uploads
from sso_client import current_user

bp = Blueprint('review', __name__)


def _link_or_404(token):
    """Client approval link (ReviewLink). For the other type, see `_scope_or_404`."""
    link = ReviewLink.query.filter_by(token=token, revoked=False).first()
    if link is None:
        abort(404)
    return link


def _scope_or_404(token):
    """Token -> (link, mode). mode: 'client' (client approval link) or 'pre'
    (pre-approval link, 2026-07-24). The same /review/<token> route resolves
    both types; the page and endpoints behave according to the mode."""
    link = ReviewLink.query.filter_by(token=token, revoked=False).first()
    if link is not None:
        return link, 'client'
    pre = PreApprovalLink.query.filter_by(token=token, revoked=False).first()
    if pre is not None:
        return pre, 'pre'
    abort(404)


# When staff (management/designer) opens the page, removal buttons show up.
STAFF_ROLES = ('management', 'designer')


def _is_staff():
    u = current_user()
    return bool(u and u.get('role') in STAFF_ROLES)


def _review_uploads(link, include_excluded=False):
    """Uploads within the link's scope — the rule is defined in one place, in
    `sharing.review_visible_uploads` (same source as the `share_count` used
    at link generation)."""
    return review_visible_uploads(link.client_id, link.week_iso,
                                  include_excluded=include_excluded)


def _excluded_ids(upload_ids):
    if not upload_ids:
        return set()
    return {e.upload_id for e in ReviewExcludedUpload.query
            .filter(ReviewExcludedUpload.upload_id.in_(upload_ids)).all()}


def _reviews_for(upload_ids):
    if not upload_ids:
        return {}
    return {r.upload_id: r for r in UploadReview.query
            .filter(UploadReview.upload_id.in_(upload_ids)).all()}


@bp.get('/review/<token>/shares')
def shares(token):
    """Content list. Two modes:
    - client: client approval link (public). If staff is logged in, adds can_manage + excluded.
    - pre:    pre-approval link (2026-07-24) - staff ONLY; decision rests
              solely with management (can_decide). Removal/staged gating does
              not apply: management sees EVERYTHING the designer uploaded
              that week."""
    link, mode = _scope_or_404(token)
    client = db.session.get(Client, link.client_id)
    staff = _is_staff()
    u = current_user()

    if mode == 'pre':
        if not staff:
            return jsonify(error='This link is for management and designers only. '
                                 'Please log in to the panel and try again.'), 403
        rows = review_visible_uploads(link.client_id, link.week_iso, include_excluded=True)
        pre = pre_approval_map([r.id for r in rows])
        out = [dict(_item(token, up), pre=(pre[up.id].to_dict() if up.id in pre else None))
               for up in rows]
        return jsonify(client_name=client.name if client else '', week_iso=link.week_iso,
                       shares=out, mode='pre', can_manage=False,
                       can_decide=bool(u and u.get('role') == 'management'))

    rows = _review_uploads(link, include_excluded=staff)
    ids = [r.id for r in rows]
    excluded = _excluded_ids(ids) if staff else set()
    reviews = _reviews_for(ids)
    pre = pre_approval_map(ids) if staff else {}
    out = []
    for up in rows:
        rv = reviews.get(up.id)
        item = dict(_item(token, up), review=rv.to_dict() if rv else None)
        if staff:
            item['excluded'] = up.id in excluded
            item['pre'] = pre[up.id].to_dict() if up.id in pre else None
        out.append(item)
    return jsonify(client_name=client.name if client else '', week_iso=link.week_iso,
                   shares=out, mode='client', can_manage=staff, can_decide=False)


def _item(token, up):
    """A single upload's shared (mode-independent) media fields."""
    is_video = up.category == 'video'
    return {
        'id': up.id, 'kind': up.category, 'file_name': up.file_name,
        'media_url': f'/review/{token}/media/{up.file_id}' if up.file_id else None,
        # While a local copy exists, the video plays in-page (21-day window).
        'video_url': (f'/review/{token}/stream/{up.file_id}'
                      if is_video and up.file_id
                      and media_store.has_original(up.file_id) else None),
        'drive_url': (f'https://drive.google.com/file/d/{up.file_id}/view'
                      if is_video and up.file_id else None),
    }


# Preview widths. `full` = Drive's s2048 render — so caption/detail is
# readable on a retina phone (equivalent of the old `?size=full`). The cache
# is keyed by (file_id, width), so 2048 lands in its own row.
MEDIA_WIDTHS = {'thumb': 600, 'full': 2048}


@bp.get('/review/<token>/media/<file_id>')
def media(token, file_id):
    link, _mode = _scope_or_404(token)
    # Only proxy file_id if it's one of THIS link's UPLOADS (blocks arbitrary files).
    # Includes removed ones too: so staff can preview before restoring.
    allowed = {u.file_id for u in _review_uploads(link, include_excluded=True) if u.file_id}
    if file_id not in allowed:
        abort(404)
    # Local copy (first 21 days): full=original (if it's an image), thumb=preview.
    if request.args.get('size') == 'full':
        path = media_store.find_original(file_id)
        mime = mimetypes.guess_type(path)[0] if path else None
        if path and (mime or '').startswith('image/'):
            try:
                resp = send_file(path, mimetype=mime, conditional=True)
                resp.headers['Cache-Control'] = 'private, max-age=86400'
                return resp
            except OSError:
                pass
    prev = media_store.find_preview(file_id)
    if prev:
        try:
            resp = send_file(prev, mimetype='image/jpeg', conditional=True)
            resp.headers['Cache-Control'] = 'private, max-age=86400'
            return resp
        except OSError:
            pass  # fall through to Drive if local read fails
    width = MEDIA_WIDTHS.get(request.args.get('size'), MEDIA_WIDTHS['thumb'])
    cached = db.session.get(DriveThumbnail, (file_id, width))
    if cached:
        return Response(bytes(cached.data), mimetype=cached.mime,
                        headers={'Cache-Control': 'private, max-age=86400'})
    if not dg.available():
        abort(404)
    try:
        data, mime = dg.thumbnail_bytes(file_id, width)
    except dg.DriveError:
        abort(502)
    if not data:
        abort(404)
    db.session.merge(DriveThumbnail(file_id=file_id, width=width, data=data, mime=mime))
    db.session.commit()
    return Response(data, mimetype=mime, headers={'Cache-Control': 'private, max-age=86400'})


@bp.get('/review/<token>/stream/<file_id>')
def stream(token, file_id):
    """Local original streaming (Range-supported) — only files within the link's scope.

    404 if the local copy has expired: the page falls back to drive_url."""
    link, _mode = _scope_or_404(token)
    if not ratelimit.hit(f'stream:{token}', 120, 60):
        abort(429)
    allowed = {u.file_id for u in _review_uploads(link, include_excluded=True) if u.file_id}
    if file_id not in allowed:
        abort(404)
    path = media_store.find_original(file_id)
    if not path:
        abort(404)
    mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    try:
        resp = send_file(path, mimetype=mime, conditional=True)
        resp.headers['Cache-Control'] = 'private, max-age=3600'
        return resp
    except OSError:
        abort(404)


@bp.post('/review/<token>/action')
def action(token):
    link, mode = _scope_or_404(token)
    if mode == 'pre':
        return _pre_action(link)
    if not ratelimit.hit(f'review:{token}', 40, 60):
        return jsonify(error='too many requests, please wait a moment'), 429
    data = request.get_json(silent=True) or {}
    act = data.get('action')
    if act not in ('approve', 'revise'):
        return jsonify(error='invalid action'), 400
    note = (data.get('note') or '').strip()
    if act == 'revise' and not note:
        return jsonify(error='a note is required for a revision'), 400
    up = db.session.get(CardUpload, data.get('upload_id') or data.get('share_id'))
    if (up is None or up.deleted_at is not None
            or up.client_id != link.client_id or up.week_iso != link.week_iso
            or up.category not in REVIEW_CATEGORIES):
        abort(404)
    if _excluded_ids([up.id]):
        abort(404)  # can't decide on content that's been removed from the page
    status = 'approved' if act == 'approve' else 'revision_requested'
    rv = UploadReview.query.filter_by(upload_id=up.id).first()
    if rv is None:
        rv = UploadReview(upload_id=up.id)
        db.session.add(rv)
    rv.status = status
    rv.note = note[:1000] or None
    rv.at = utcnow()
    db.session.flush()
    # AFTER the write, BEFORE the commit: notify the client team.
    notify_client_review(link.client_id, link.week_iso, status)
    db.session.commit()
    return jsonify(ok=True, review=rv.to_dict())


def _pre_action(link):
    """Pre-approval decision - management ONLY (the designer sees the page
    but cannot decide). The decision is written to `card_upload_pre_approvals`;
    thanks to staged gating, only `approved` ones show up on the client
    approval link."""
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    if u.get('role') != 'management':
        return jsonify(error='only management can make pre-approval decisions'), 403
    data = request.get_json(silent=True) or {}
    act = data.get('action')
    if act not in ('approve', 'revise'):
        return jsonify(error='invalid action'), 400
    note = (data.get('note') or '').strip()
    if act == 'revise' and not note:
        return jsonify(error='a note is required for a revision'), 400
    up = db.session.get(CardUpload, data.get('upload_id'))
    if (up is None or up.deleted_at is not None
            or up.client_id != link.client_id or up.week_iso != link.week_iso
            or up.category not in REVIEW_CATEGORIES):
        abort(404)
    status = 'approved' if act == 'approve' else 'revision_requested'
    pa = UploadPreApproval.query.filter_by(upload_id=up.id).first()
    if pa is None:
        pa = UploadPreApproval(upload_id=up.id)
        db.session.add(pa)
    pa.status = status
    pa.note = note[:1000] or None
    pa.decided_by = u.get('sub')
    pa.at = utcnow()
    db.session.flush()
    notify_pre_approval(link.client_id, link.week_iso, status, up.file_name)
    db.session.commit()
    return jsonify(ok=True, pre=pa.to_dict())


@bp.post('/review/<token>/exclude')
def exclude(token):
    """Remove / restore an upload from the approval page — STAFF ONLY
    (management or designer, logged in). Not destructive: the `card_uploads`
    row is untouched, only visibility on this page changes.

    CSRF: since this blueprint is public, `api.csrf_protect` isn't attached;
    the token is verified manually (the page gets it from `/api/session`)."""
    link = _link_or_404(token)
    u = current_user()
    if not u:
        return jsonify(error='no active session'), 401
    if u.get('role') not in STAFF_ROLES:
        return jsonify(error='you do not have permission'), 403
    sess_token = session.get('csrf')
    if not sess_token or not hmac.compare_digest(
            sess_token, request.headers.get('X-CSRFToken', '')):
        return jsonify(error='CSRF verification failed'), 403
    data = request.get_json(silent=True) or {}
    up = db.session.get(CardUpload, data.get('upload_id'))
    if (up is None or up.deleted_at is not None
            or up.client_id != link.client_id or up.week_iso != link.week_iso
            or up.category not in REVIEW_CATEGORIES):
        abort(404)
    want_excluded = bool(data.get('excluded', True))
    row = db.session.get(ReviewExcludedUpload, up.id)
    if want_excluded and row is None:
        db.session.add(ReviewExcludedUpload(upload_id=up.id, excluded_by=u.get('sub')))
    elif not want_excluded and row is not None:
        db.session.delete(row)
    db.session.commit()
    return jsonify(ok=True, upload_id=up.id, excluded=want_excluded)


@bp.get('/review/<token>')
def page(token):
    _scope_or_404(token)  # 404 if invalid (client OR pre-approval link)
    html = _PAGE.replace('__TOKEN__', token)
    return Response(html, mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow'})


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Content Approval</title>
<style>
  :root { --bg:#0b1220; --card:#131c2e; --fg:#e6edf6; --mut:#8ea2bd; --acc:#14b8a6; --dan:#ef4444; --line:#22304a; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:20px 16px; border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:18px; } .sub { color:var(--mut); font-size:13px; margin-top:2px; }
  main { max-width:960px; margin:0 auto; padding:16px; display:flex; flex-direction:column; gap:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; }
  .media { width:100%; height:auto; display:block; background:#0a0f1a; }
  .mwrap { position:relative; display:block; }
  .play { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; }
  .play span { width:60px; height:60px; border-radius:50%; background:rgba(0,0,0,.55); border:2px solid #fff;
               color:#fff; font-size:24px; display:flex; align-items:center; justify-content:center; padding-left:4px; }
  .mwrap:hover .play span { background:rgba(0,0,0,.75); }
  .dnote { font-size:12px; color:var(--mut); margin-top:8px; }
  .ph { background:#0a0f1a; color:var(--mut); font-size:13px; text-align:center; padding:36px 12px; }
  .body { padding:14px; }
  .kind { display:inline-block; font-size:11px; color:var(--mut); border:1px solid var(--line); border-radius:999px; padding:2px 8px; }
  .status { margin-top:8px; font-size:13px; } .status.ok { color:var(--acc); } .status.rev { color:var(--dan); }
  .row { display:flex; gap:8px; margin-top:12px; }
  button { flex:1; padding:10px; border-radius:10px; border:1px solid var(--line); background:transparent; color:var(--fg); font-size:14px; cursor:pointer; }
  button.ok { background:var(--acc); border-color:var(--acc); color:#04211d; font-weight:600; }
  button.rev { color:var(--dan); border-color:var(--dan); }
  textarea { width:100%; margin-top:8px; background:#0a0f1a; color:var(--fg); border:1px solid var(--line); border-radius:10px; padding:8px; font:inherit; }
  .empty,.err { color:var(--mut); text-align:center; padding:40px 16px; }
  .staff { display:flex; align-items:center; gap:8px; padding:10px 14px; border-top:1px solid var(--line);
           background:rgba(255,255,255,.02); font-size:13px; color:var(--mut); }
  .staff button { flex:0 0 auto; padding:6px 10px; font-size:13px; }
  .card.off { opacity:.45; }
  .card.off .media { filter:grayscale(1); }
  .offtag { display:inline-block; font-size:11px; color:var(--dan); border:1px solid var(--dan);
            border-radius:999px; padding:2px 8px; }
  .banner { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 14px;
            font-size:13px; color:var(--mut); }
  .toast { position:fixed; left:50%; bottom:20px; transform:translateX(-50%); background:var(--card); border:1px solid var(--line); padding:10px 16px; border-radius:10px; opacity:0; transition:opacity .2s; }
  .toast.show { opacity:1; }
</style></head>
<body>
<header><h1 id="title">Content Approval</h1><div class="sub" id="sub"></div></header>
<main id="main"><div class="empty">Loading…</div></main>
<div class="toast" id="toast"></div>
<script>
const TOKEN = "__TOKEN__";
const main = document.getElementById("main");
let CSRF = "";          // CSRF token from the staff session (/api/session)
let CAN_MANAGE = false; // management/designer → removal buttons are shown
let MODE = "client";    // "pre" = pre-approval link (internal flow)
let CAN_DECIDE = false; // decision authority on pre-approval (management only)
function toast(m){ const t=document.getElementById("toast"); t.textContent=m; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),2200); }
function esc(s){ const d=document.createElement("div"); d.textContent=s||""; return d.innerHTML; }
// If media fails to load, don't hide it silently — the client shouldn't think there's no content.
function ph(){ const d=document.createElement("div"); d.className="ph"; d.textContent="Preview unavailable"; return d; }
const KIND={post:"Post",story:"Story",video:"Video",linkedin:"LinkedIn"};

async function load(){
  // If staff has a session in the same browser, grab the CSRF token (needed for removal).
  try { const s = await fetch("/api/session"); if(s.ok){ CSRF = (await s.json()).csrf || ""; } } catch(e){}
  const r = await fetch(`/review/${TOKEN}/shares`);
  if(r.status===403){
    let m="This link is for management and designers only.";
    try{ m = (await r.json()).error || m; }catch(e){}
    main.innerHTML='<div class="err">'+esc(m)+'</div>'; return;
  }
  if(!r.ok){ main.innerHTML='<div class="err">This link is invalid or has expired.</div>'; return; }
  const d = await r.json();
  CAN_MANAGE = !!d.can_manage;
  MODE = d.mode || "client";
  CAN_DECIDE = !!d.can_decide;
  document.getElementById("title").textContent =
    d.client_name + (MODE==="pre" ? " — Pre-Approval (management)" : " — Content Approval");
  const shown = d.shares.filter(x=>!x.excluded).length;
  document.getElementById("sub").textContent = d.week_iso + " week · " + shown + " item(s)";
  if(!d.shares.length){ main.innerHTML='<div class="empty">No content has been uploaded for this week.</div>'; return; }
  main.innerHTML="";
  if(MODE==="pre"){
    const b=document.createElement("div"); b.className="banner";
    b.textContent = CAN_DECIDE
      ? "Pre-approval: items you approve can be sent to the client. Content you request a revision for will not go to the client."
      : "Pre-approval status (view only) — the decision is made by management.";
    main.appendChild(b);
  } else if(CAN_MANAGE){
    const b=document.createElement("div"); b.className="banner";
    b.textContent="Management view: you can remove or restore content from this page. The client only sees items that haven't been removed.";
    main.appendChild(b);
  }
  for(const s of d.shares) main.appendChild(card(s));
}
async function toggleExclude(s, btn){
  if(!CSRF){ toast("Session not found — please log in to the panel"); return; }
  btn.disabled = true;
  try{
    const r = await fetch(`/review/${TOKEN}/exclude`, {
      method:"POST", credentials:"same-origin",
      headers:{"Content-Type":"application/json","X-CSRFToken":CSRF},
      body: JSON.stringify({upload_id:s.id, excluded: !s.excluded}),
    });
    if(!r.ok){ toast("Action failed"); btn.disabled=false; return; }
    toast(s.excluded ? "Restored" : "Removed from page");
    await load();   // refresh the list (count + view)
  }catch(e){ toast("Action failed"); btn.disabled=false; }
}
function statusHtml(rv, prefix){
  if(!rv) return "";
  const p = prefix || "";
  if(rv.status==="approved") return '<div class="status ok">✓ '+p+'Approved</div>';
  if(rv.status==="revision_requested") return '<div class="status rev">✎ '+p+'Revision requested'+(rv.note?': '+esc(rv.note):'')+'</div>';
  return "";
}
function media(s){
  // While the local copy exists (first 3 weeks), the video plays inline on the page.
  if(s.video_url) return `<video class="media" controls playsinline preload="metadata" src="${s.video_url}"></video>`;
  if(!s.media_url) return "";
  // Full size (2026-07-24): not a preview — full resolution is served directly.
  const img=`<img class="media" loading="lazy" src="${s.media_url}?size=full"`+
    ` alt="content preview" onerror="this.replaceWith(ph())">`;
  // Video (local copy expired): tap the poster, opens in Drive.
  if(s.drive_url) return `<a class="mwrap" href="${s.drive_url}" target="_blank" rel="noopener noreferrer">${img}<span class="play"><span>▶</span></span></a>`;
  // Image: tap for full resolution (original if local, Drive s2048 otherwise).
  return `<a class="mwrap" href="${s.media_url}?size=full" target="_blank" rel="noopener noreferrer">${img}</a>`;
}
function card(s){
  const el=document.createElement("div"); el.className="card"+(s.excluded?" off":"");
  el.innerHTML = media(s)+
    `<div class="body">
       <span class="kind">${KIND[s.kind]||s.kind}</span>
       ${s.drive_url&&!s.video_url?'<div class="dnote">Tap the image to watch the video — it opens in Google Drive.</div>':""}
       ${MODE==="pre"
          ? `<div class="st">${statusHtml(s.pre,"Pre-approval: ")}</div>
             ${CAN_DECIDE?`<div class="row">
               <button class="ok">Give Pre-Approval</button>
               <button class="rev">Request Revision</button>
             </div>`:""}`
          : `<div class="st">${statusHtml(s.review)}${CAN_MANAGE&&s.pre?statusHtml(s.pre,"Pre-approval: "):""}</div>
             ${s.excluded?'<div class="row"><span class="offtag">This content has been removed from the page — the client doesn\\'t see it</span></div>':`<div class="row">
               <button class="ok">Approve</button>
               <button class="rev">Request Revision</button>
             </div>`}`}
     </div>`+
    ((MODE!=="pre"&&CAN_MANAGE)?`<div class="staff"><span>${esc(s.file_name||"")}</span>
       <button class="excl" style="margin-left:auto">${s.excluded?"Restore":"Remove from page"}</button></div>`:"")+
    (MODE==="pre"?`<div class="staff"><span>${esc(s.file_name||"")}</span></div>`:"");
  const st=el.querySelector(".st");
  const ex=el.querySelector(".excl");
  if(ex) ex.onclick=()=>toggleExclude(s,ex);
  if(MODE!=="pre" && s.excluded) return el;   // no approve/revise buttons once removed
  if(MODE==="pre" && !CAN_DECIDE) return el;  // designer is view-only
  el.querySelector(".ok").onclick=()=>act(s.id,"approve",null,st);
  el.querySelector(".rev").onclick=()=>{
    if(el.querySelector("textarea")) return;
    const ta=document.createElement("textarea"); ta.placeholder="What would you like changed?"; ta.maxLength=1000;
    const send=document.createElement("button"); send.className="rev"; send.textContent="Send"; send.style.marginTop="8px";
    st.after(ta); ta.after(send);
    send.onclick=()=>{ if(!ta.value.trim()){toast("Note required");return;} act(s.id,"revise",ta.value.trim(),st,[ta,send]); };
  };
  return el;
}
async function act(id,action,note,st,extra){
  const r=await fetch(`/review/${TOKEN}/action`,{method:"POST",
    credentials:"same-origin",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({upload_id:id,action,note})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){ toast(d.error||"Error"); return; }
  st.innerHTML = MODE==="pre" ? statusHtml(d.pre,"Pre-approval: ") : statusHtml(d.review);
  (extra||[]).forEach(e=>e.remove());
  toast(action==="approve" ? (MODE==="pre"?"Pre-approval given":"Approved") : "Revision request sent");
}
load();
</script></body></html>"""
