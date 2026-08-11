"""Public special-day selection page (/special-days/<token>).

NO auth: token = authorization. The client sees the month's special days
(global + their own) and picks which ones they want content produced for. The
selection is written to `selected_event_ids` (used as a special card on the
management board). noindex, mobile.
"""
from flask import Blueprint, Response, abort, jsonify, request

import ratelimit
from extensions import db
from models import Client
from models_sharing import SpecialDayEvent, SpecialDaySelection

bp = Blueprint('special_days', __name__)


def _sel_or_404(token):
    # In old data the same token can appear on multiple selections (inconsistency);
    # prefer the newest record with month/year populated.
    sel = (SpecialDaySelection.query.filter_by(token=token)
           .order_by(SpecialDaySelection.month.desc().nullslast(),
                     SpecialDaySelection.id.desc()).first())
    if sel is None:
        abort(404)
    return sel


def _events_for(sel):
    """Global + client-specific events in the selection's month/year.

    `active=False` = deleted (soft delete) — not shown to the client.
    Approval gate: this endpoint is CLIENT-FACING (public selection page) — only
    `approved` events are returned; unapproved/draft (AI-generated) special days never leak to the client.
    """
    q = SpecialDayEvent.query.filter_by(month=sel.month, year=sel.year, active=True,
                                        status='approved')
    return [e for e in q.order_by(SpecialDayEvent.date_num, SpecialDayEvent.day_name).all()
            if e.client_id is None or e.client_id == sel.client_id]


@bp.get('/special-days/<token>/events')
def events(token):
    sel = _sel_or_404(token)
    client = db.session.get(Client, sel.client_id)
    selected = set(sel.selected_event_ids or [])
    evs = [{'id': e.id, 'day_name': e.day_name, 'description': e.description,
            'date_num': e.date_num, 'date_start': e.date_start, 'date_end': e.date_end,
            'selected': e.id in selected} for e in _events_for(sel)]
    return jsonify(client_name=client.name if client else '', month=sel.month,
                   year=sel.year, events=evs)


@bp.post('/special-days/<token>/select')
def select(token):
    sel = _sel_or_404(token)
    if not ratelimit.hit(f'sd:{token}', 60, 60):
        return jsonify(error='too many requests, please wait a moment'), 429
    incoming = (request.get_json(silent=True) or {}).get('event_ids', [])
    valid = {e.id for e in _events_for(sel)}
    sel.selected_event_ids = [i for i in incoming if i in valid]
    db.session.commit()
    return jsonify(ok=True, selected=sel.selected_event_ids)


@bp.get('/special-days/<token>')
def page(token):
    _sel_or_404(token)
    return Response(_PAGE.replace('__TOKEN__', token), mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow'})


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>Special Days</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,300;1,400&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
  :root{
    --cream:#FAFAF8; --cream-mid:#F0EDE6; --ink:#1A1A1A; --ink-soft:#3D3D3D;
    --ink-muted:#7A7670; --gold:#C9A96E; --gold-light:#E8D9BB;
    --border:rgba(26,26,26,0.10); --radius:16px;
    --font-body:'DM Sans',sans-serif; --font-disp:'Cormorant Garamond',serif;
  }
  html{scroll-behavior:smooth;}
  body{background:var(--cream);color:var(--ink);font-family:var(--font-body);font-size:15px;
    line-height:1.5;min-height:100dvh;padding-bottom:120px;-webkit-font-smoothing:antialiased;}
  body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.025;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");}
  .page-header{position:relative;z-index:1;padding:48px 24px 36px;max-width:480px;margin:0 auto;}
  .logo-lockup{display:flex;align-items:center;gap:10px;margin-bottom:40px;opacity:0;animation:fadeUp .6s ease forwards;}
  .logo-img{height:104px;width:auto;display:block;}
  @media (max-width:400px){ .logo-img{height:84px;} }
  .header-eyebrow{font-size:11px;font-weight:500;letter-spacing:.18em;color:var(--gold);margin-bottom:12px;opacity:0;animation:fadeUp .6s .1s ease forwards;}
  .header-title{font-family:var(--font-disp);font-size:clamp(36px,10vw,52px);font-weight:300;line-height:1.05;letter-spacing:-.01em;color:var(--ink);margin-bottom:6px;opacity:0;animation:fadeUp .6s .15s ease forwards;}
  .header-title em{font-style:italic;color:var(--ink-soft);}
  .header-client{font-size:14px;color:var(--ink-muted);margin-top:4px;margin-bottom:20px;opacity:0;animation:fadeUp .6s .2s ease forwards;}
  .header-desc{font-size:14px;font-weight:300;color:var(--ink-muted);line-height:1.65;max-width:320px;opacity:0;animation:fadeUp .6s .25s ease forwards;}
  .divider{max-width:480px;margin:0 auto 32px;padding:0 24px;opacity:0;animation:fadeUp .5s .3s ease forwards;}
  .divider-line{height:1px;background:linear-gradient(to right,transparent,var(--border) 30%,var(--border) 70%,transparent);}
  .counter-chip{max-width:480px;margin:0 auto 20px;padding:0 20px;opacity:0;animation:fadeUp .5s .35s ease forwards;}
  .counter-inner{display:inline-flex;align-items:center;gap:6px;padding:6px 12px 6px 8px;border-radius:999px;background:var(--cream-mid);border:1px solid var(--border);}
  .counter-dot{width:7px;height:7px;border-radius:50%;background:var(--gold);}
  .counter-text{font-size:12px;color:var(--ink-muted);}
  .counter-text span{font-weight:600;color:var(--ink);}
  .cards-container{max-width:480px;margin:0 auto;padding:0 20px;display:flex;flex-direction:column;gap:12px;}
  .day-card{position:relative;border-radius:var(--radius);cursor:pointer;user-select:none;-webkit-tap-highlight-color:transparent;overflow:hidden;
    transition:background-color .22s ease,border-color .22s ease,transform .15s ease,box-shadow .22s ease;
    background:#FFF;border:1px solid var(--border);box-shadow:0 1px 3px rgba(26,26,26,.04);opacity:0;transform:translateY(14px);}
  .day-card.visible{animation:cardReveal .5s ease forwards;}
  .day-card:active{transform:scale(.985);}
  .day-card.selected{background:var(--ink);border-color:var(--ink);box-shadow:0 8px 24px rgba(26,26,26,.18);}
  .card-inner{display:flex;align-items:center;gap:20px;padding:20px 22px;}
  .card-date{flex-shrink:0;text-align:center;width:52px;}
  .card-date-num{font-family:var(--font-disp);font-size:42px;font-weight:300;line-height:1;color:var(--ink);transition:color .22s ease;letter-spacing:-.02em;}
  .day-card.selected .card-date-num{color:var(--cream);}
  .card-date-range{font-family:var(--font-disp);font-size:22px;font-weight:300;line-height:1.1;color:var(--ink);transition:color .22s ease;letter-spacing:-.01em;white-space:nowrap;}
  .day-card.selected .card-date-range{color:var(--cream);}
  .card-type-badge{font-size:8.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin-top:3px;transition:color .22s ease;}
  .day-card.selected .card-type-badge{color:var(--gold-light);}
  .card-date-month{font-size:9.5px;font-weight:500;letter-spacing:.14em;color:var(--gold);margin-top:1px;transition:color .22s ease;}
  .day-card.selected .card-date-month{color:var(--gold-light);}
  .card-sep{width:1px;height:44px;background:var(--border);flex-shrink:0;transition:background .22s ease;}
  .day-card.selected .card-sep{background:rgba(255,255,255,.12);}
  .card-text{flex:1;min-width:0;}
  .card-name{font-size:15px;font-weight:500;color:var(--ink);line-height:1.3;transition:color .22s ease;}
  .day-card.selected .card-name{color:var(--cream);}
  .card-desc{font-size:12.5px;font-weight:300;color:var(--ink-muted);margin-top:3px;line-height:1.4;transition:color .22s ease;}
  .day-card.selected .card-desc{color:rgba(250,250,248,.5);}
  .card-check{flex-shrink:0;width:26px;height:26px;border-radius:50%;border:1.5px solid var(--border);display:flex;align-items:center;justify-content:center;
    transition:background .22s ease,border-color .22s ease,transform .22s cubic-bezier(.34,1.56,.64,1);}
  .day-card.selected .card-check{background:var(--gold);border-color:var(--gold);transform:scale(1.08);}
  .card-check svg{opacity:0;transform:scale(.5);transition:opacity .18s ease,transform .22s cubic-bezier(.34,1.56,.64,1);}
  .day-card.selected .card-check svg{opacity:1;transform:scale(1);}
  .save-bar{position:fixed;bottom:0;left:0;right:0;z-index:100;padding:20px 20px calc(20px + env(safe-area-inset-bottom));background:linear-gradient(to top,var(--cream) 65%,transparent);display:flex;justify-content:center;}
  .save-btn{width:100%;max-width:440px;height:54px;border-radius:999px;border:none;cursor:pointer;font-family:var(--font-body);font-size:14px;font-weight:500;letter-spacing:.06em;
    display:flex;align-items:center;justify-content:center;gap:10px;transition:transform .15s ease,box-shadow .15s ease,background .3s ease;
    background:var(--ink);color:var(--cream);box-shadow:0 4px 20px rgba(26,26,26,.22);-webkit-tap-highlight-color:transparent;}
  .save-btn:hover{box-shadow:0 6px 28px rgba(26,26,26,.30);transform:translateY(-1px);}
  .save-btn:active{transform:scale(.975);}
  .save-btn.loading{pointer-events:none;opacity:.75;}
  .save-btn.success{background:#2C6B45;pointer-events:none;}
  .save-btn .spinner{width:18px;height:18px;border:2px solid rgba(250,250,248,.3);border-top-color:var(--cream);border-radius:50%;animation:spin .7s linear infinite;display:none;}
  .save-btn.loading .spinner{display:block;}
  .save-btn.loading .btn-text{display:none;}
  .empty-state{text-align:center;padding:60px 24px;color:var(--ink-muted);font-size:14px;font-weight:300;}
  @keyframes fadeUp{from{opacity:0;transform:translateY(10px);}to{opacity:1;transform:translateY(0);}}
  @keyframes cardReveal{from{opacity:0;transform:translateY(14px);}to{opacity:1;transform:translateY(0);}}
  @keyframes spin{to{transform:rotate(360deg);}}
</style></head>
<body>
  <header class="page-header">
    <div class="logo-lockup"><img class="logo-img" src="/panel/kotar-logo.png" alt="Kotar — digital media agency"></div>
    <p class="header-eyebrow" id="eyebrow"></p>
    <h1 class="header-title">Special<br><em>Days</em></h1>
    <p class="header-client" id="client"></p>
    <p class="header-desc" id="desc">Mark the special days below that you'd like us to create content for. Once you save your selection, our team will start planning.</p>
  </header>
  <div class="divider"><div class="divider-line"></div></div>
  <div class="counter-chip"><div class="counter-inner"><div class="counter-dot"></div>
    <p class="counter-text"><span id="countNum">0</span> days selected</p></div></div>
  <div class="cards-container" id="cards"><div class="empty-state">Loading…</div></div>
  <div class="save-bar">
    <button class="save-btn" id="saveBtn" type="button">
      <div class="spinner"></div><span class="btn-text">Save Selection</span>
    </button>
  </div>
<script>
const TOKEN="__TOKEN__";
const MONTHS=["","January","February","March","April","May","June","July","August","September","October","November","December"];
const trUpper=s=>(s||"").toUpperCase();
const cardsEl=document.getElementById("cards"), saveBtn=document.getElementById("saveBtn"),
      countEl=document.getElementById("countNum"), btnText=saveBtn.querySelector(".btn-text");
let selected=new Set();
function esc(s){const d=document.createElement("div");d.textContent=s||"";return d.innerHTML;}
function updateCounter(){countEl.textContent=selected.size;}
function resetSaveBtn(){ if(saveBtn.classList.contains("success")){saveBtn.classList.remove("success");btnText.textContent="Save Selection";} }
function card(e,monthUpper){
  const el=document.createElement("div");
  el.className="day-card"+(selected.has(e.id)?" selected":"");
  el.dataset.id=e.id; el.setAttribute("role","checkbox"); el.tabIndex=0;
  el.setAttribute("aria-checked",selected.has(e.id)?"true":"false");
  const isWeek=!e.date_num&&e.date_start&&e.date_end;
  const dateBlock=isWeek
    ? `<div class="card-date-range">${e.date_start}–${e.date_end}</div><div class="card-date-month">${monthUpper}</div><div class="card-type-badge">Week</div>`
    : `<div class="card-date-num">${e.date_num||""}</div><div class="card-date-month">${monthUpper}</div>`;
  el.innerHTML=`<div class="card-inner"><div class="card-date">${dateBlock}</div>
    <div class="card-sep"></div>
    <div class="card-text"><div class="card-name">${esc(e.day_name)}</div>${e.description?`<div class="card-desc">${esc(e.description)}</div>`:""}</div>
    <div class="card-check"><svg width="13" height="10" viewBox="0 0 13 10" fill="none"><path d="M1.5 5L5 8.5L11.5 1.5" stroke="#FAFAF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div></div>`;
  function toggle(){ const on=el.classList.toggle("selected"); el.setAttribute("aria-checked",on?"true":"false");
    if(on)selected.add(e.id); else selected.delete(e.id); updateCounter(); resetSaveBtn(); }
  el.addEventListener("click",toggle);
  el.addEventListener("keydown",ev=>{ if(ev.key===" "||ev.key==="Enter"){ev.preventDefault();toggle();} });
  return el;
}
async function load(){
  let d;
  try{ const r=await fetch(`/special-days/${TOKEN}/events`); if(!r.ok)throw 0; d=await r.json(); }
  catch{ cardsEl.innerHTML='<div class="empty-state">This link is invalid.</div>'; return; }
  const monthName=MONTHS[d.month]||"", monthUpper=trUpper(monthName);
  document.getElementById("eyebrow").textContent=monthUpper+" "+d.year;
  document.getElementById("client").textContent=d.client_name||"";
  document.getElementById("desc").textContent="In "+monthName+", mark the special days below that you'd like us to create content for. Once you save your selection, our team will start planning.";
  selected=new Set(d.events.filter(e=>e.selected).map(e=>e.id));
  updateCounter();
  if(!d.events.length){ cardsEl.innerHTML='<div class="empty-state">There are no special days for this month.</div>'; return; }
  cardsEl.innerHTML="";
  d.events.forEach((e,i)=>{ const el=card(e,monthUpper); cardsEl.appendChild(el);
    setTimeout(()=>el.classList.add("visible"),150+i*60); });
}
saveBtn.addEventListener("click",async()=>{
  if(saveBtn.classList.contains("loading")) return;
  saveBtn.classList.add("loading");
  try{
    const r=await fetch(`/special-days/${TOKEN}/select`,{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({event_ids:[...selected]})});
    saveBtn.classList.remove("loading");
    if(r.ok){ saveBtn.classList.add("success"); btnText.textContent="Saved"; }
    else{ btnText.textContent="Error, try again"; setTimeout(()=>btnText.textContent="Save Selection",3000); }
  }catch{ saveBtn.classList.remove("loading"); btnText.textContent="Error, try again";
    setTimeout(()=>btnText.textContent="Save Selection",3000); }
});
load();
</script></body></html>"""
