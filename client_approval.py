"""Public müşteri onay sayfası — ELLE SEÇİLMİŞ içerikler (/onay/<token>).

`review.py`'nin ikizi DEĞİL, yanında duran ikinci bir akış (proje sahibi 2026-08-06:
"oradakine dokunmayalım"). Farklar:

- **Kapsam:** `/review/<token>` (müşteri, hafta) çiftini gösterir; burada linkin
  `upload_ids` listesi dondurulmuştur — üç haftalık pencereden elle seçilmiş
  dosyalar. Sonradan yüklenen hiçbir şey linke sızmaz.
- **Not defteri:** sayfanın sağında müşterinin serbestçe yazdığı, otomatik
  kaydedilen alan. Metin her değiştiğinde ekibe bildirim düşer; art arda gelen
  değişiklikleri 30 dk'lık coalesce penceresi toplar.
- **Görsel dil:** özel gün seçim sayfasıyla (`special_days.py`) aynı krem/altın
  palet ve tipografi — müşteriye giden iki sayfa aynı ajanstan gelmiş görünsün.

Kararlar AYRI tablo tutmaz: `card_upload_reviews`'a yazılır → sharing board'daki
✅/📝 rozetleri ve `client_review` bildirimleri bu akışta da çalışır.

Auth YOK: token'ın kendisi yetkidir (32 bayt). Medya proxy ve karar uçları yalnız
linkin kendi `upload_ids`'ine bakar; keyfi file_id / başka müşterinin dosyası
reddedilir. CSRF yok (mutasyon token'la korunur), token başına hız sınırı var.
"""
import mimetypes

from flask import (Blueprint, Response, abort, jsonify, request, send_file)

import drive_gateway as dg
import media_store
import ratelimit
from extensions import db
from models import Client, utcnow
from models_sharing import (CardUpload, ClientApprovalLink, DriveThumbnail,
                            UploadReview)
from notifications import notify_approval_note, notify_client_review
from sharing import REVIEW_CATEGORIES

bp = Blueprint('client_approval', __name__)

# Not defterinin üst sınırı — DB'de Text ama sayfadan gelen veri sınırsız olamaz.
NOTE_MAX = 4000
# Bildirim gövdesindeki not özeti.
NOTE_SUMMARY = 120
MEDIA_WIDTHS = {'thumb': 600, 'full': 2048}


def _link_or_404(token):
    link = ClientApprovalLink.query.filter_by(token=token, revoked=False).first()
    if link is None:
        abort(404)
    return link


def _uploads(link):
    """Linkin içerikleri — `upload_ids` SIRASIYLA (tasarımcının seçim sırası).

    Aradan silinmiş dosya sessizce düşer: link üretildikten sonra yükleme
    kalıcı silinebiliyor (videografın silme ucu), sayfa bunun için ölmemeli."""
    ids = [i for i in (link.upload_ids or []) if isinstance(i, int)]
    if not ids:
        return []
    rows = {u.id: u for u in CardUpload.query.filter(
        CardUpload.id.in_(ids), CardUpload.deleted_at.is_(None),
        CardUpload.client_id == link.client_id,
        CardUpload.category.in_(REVIEW_CATEGORIES)).all()}
    return [rows[i] for i in ids if i in rows]


def _item(token, up):
    is_video = up.category == 'video'
    return {
        'id': up.id, 'kind': up.category, 'file_name': up.file_name,
        'week_iso': up.week_iso,
        'media_url': f'/onay/{token}/media/{up.file_id}' if up.file_id else None,
        # Lokal kopya varken video sayfa içinde oynar (21 günlük pencere).
        'video_url': (f'/onay/{token}/stream/{up.file_id}'
                      if is_video and up.file_id
                      and media_store.has_original(up.file_id) else None),
        'drive_url': (f'https://drive.google.com/file/d/{up.file_id}/view'
                      if is_video and up.file_id else None),
    }


@bp.get('/onay/<token>/items')
def items(token):
    link = _link_or_404(token)
    client = db.session.get(Client, link.client_id)
    ups = _uploads(link)
    reviews = ({r.upload_id: r for r in UploadReview.query.filter(
        UploadReview.upload_id.in_([u.id for u in ups])).all()} if ups else {})
    out = []
    for up in ups:
        rv = reviews.get(up.id)
        out.append(dict(_item(token, up), review=rv.to_dict() if rv else None))
    return jsonify(client_name=client.name if client else '', items=out,
                   note=link.note or '')


@bp.get('/onay/<token>/media/<file_id>')
def media(token, file_id):
    """Görsel proxy — yalnız bu linkin dosyaları. Sıra: lokal orijinal (full ve
    görselse) → lokal önizleme → DB thumbnail cache → Drive."""
    link = _link_or_404(token)
    allowed = {u.file_id for u in _uploads(link) if u.file_id}
    if file_id not in allowed:
        abort(404)
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
            pass  # lokal okunamadıysa Drive yoluna düş
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


@bp.get('/onay/<token>/stream/<file_id>')
def stream(token, file_id):
    """Lokal orijinal akışı (Range destekli). Lokal kopya süresi dolduysa 404 →
    sayfa Drive bağlantısına düşer."""
    link = _link_or_404(token)
    if not ratelimit.hit(f'onaystream:{token}', 120, 60):
        abort(429)
    allowed = {u.file_id for u in _uploads(link) if u.file_id}
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


@bp.post('/onay/<token>/decision')
def decision(token):
    """Müşterinin içerik bazlı kararı → `card_upload_reviews` (ortak tablo).

    Revize için not ZORUNLU (mevcut onay akışıyla aynı sözleşme): "revize" tek
    başına tasarımcıya ne yapacağını söylemez."""
    link = _link_or_404(token)
    if not ratelimit.hit(f'onay:{token}', 40, 60):
        return jsonify(error='çok fazla istek, biraz bekleyin'), 429
    data = request.get_json(silent=True) or {}
    act = data.get('action')
    if act not in ('approve', 'revise'):
        return jsonify(error='geçersiz işlem'), 400
    note = (data.get('note') or '').strip()
    if act == 'revise' and not note:
        return jsonify(error='revize için not zorunlu'), 400
    upload_id = data.get('upload_id')
    up = next((u for u in _uploads(link) if u.id == upload_id), None)
    if up is None:
        abort(404)
    status = 'approved' if act == 'approve' else 'revision_requested'
    rv = UploadReview.query.filter_by(upload_id=up.id).first()
    if rv is None:
        rv = UploadReview(upload_id=up.id)
        db.session.add(rv)
    rv.status = status
    rv.note = note[:1000] or None
    rv.at = utcnow()
    db.session.flush()
    # Yazımdan SONRA, commit'ten ÖNCE (review.py ile aynı sıra).
    notify_client_review(link.client_id, up.week_iso, status)
    db.session.commit()
    return jsonify(ok=True, review=rv.to_dict())


@bp.post('/onay/<token>/note')
def note(token):
    """Not defteri — otomatik kaydedilir (sayfa debounce'lar).

    Bildirim **not METNİ değiştiğinde** gider; aynı içerik tekrar POST edilirse
    (alan her duraklamada gönderiyor) bildirim üretilmez, art arda gelen gerçek
    değişiklikleri de `approval_note` coalesce penceresi (30 dk) toplar.
    **2026-08-07'de değişti:** önce yalnız ilk yazımda bildiriliyordu, müşteri
    saatler sonra yeni not yazınca kimse haber alamıyordu (canlı denemede çıktı).
    `note_notified_at` artık kapı değil, SON bildirim zamanının izi."""
    link = _link_or_404(token)
    if not ratelimit.hit(f'onaynot:{token}', 60, 60):
        return jsonify(error='çok fazla istek, biraz bekleyin'), 429
    metin = (request.get_json(silent=True) or {}).get('note')
    if not isinstance(metin, str):
        return jsonify(error='note zorunlu'), 400
    metin = metin.strip()[:NOTE_MAX]
    degisti = bool(metin) and metin != (link.note or '')
    link.note = metin or None
    link.note_at = utcnow() if metin else None
    if degisti:
        link.note_notified_at = utcnow()
    db.session.flush()
    if degisti:
        ozet = metin[:NOTE_SUMMARY] + ('…' if len(metin) > NOTE_SUMMARY else '')
        notify_approval_note(link.client_id, ozet)
    db.session.commit()
    return jsonify(ok=True, saved=len(metin))


@bp.get('/onay/<token>')
def page(token):
    _link_or_404(token)
    return Response(_PAGE.replace('__TOKEN__', token), mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow'})


# Palet ve tipografi `special_days.py` ile aynı (proje sahibi 2026-08-06: "görsel tasarımı
# özel günler sayfalarındaki gibi olsun"). Sayfa tek dosya, inline CSS/JS.
_PAGE = """<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>İçerik Onayı</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,300;1,400&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
  :root{
    --cream:#FAFAF8; --cream-mid:#F0EDE6; --ink:#1A1A1A; --ink-soft:#3D3D3D;
    --ink-muted:#7A7670; --gold:#C9A96E; --gold-light:#E8D9BB;
    --green:#2C6B45; --amber:#A9652B;
    --border:rgba(26,26,26,0.10); --radius:16px;
    --font-body:'DM Sans',sans-serif; --font-disp:'Cormorant Garamond',serif;
  }
  html{scroll-behavior:smooth;}
  body{background:var(--cream);color:var(--ink);font-family:var(--font-body);font-size:15px;
    line-height:1.5;min-height:100dvh;padding-bottom:60px;-webkit-font-smoothing:antialiased;}
  body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.025;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");}
  .page-header{position:relative;z-index:1;padding:48px 24px 32px;max-width:1080px;margin:0 auto;}
  .logo-lockup{display:flex;align-items:center;gap:10px;margin-bottom:36px;opacity:0;animation:fadeUp .6s ease forwards;}
  .logo-img{height:104px;width:auto;display:block;}
  @media (max-width:400px){ .logo-img{height:84px;} }
  .header-eyebrow{font-size:11px;font-weight:500;letter-spacing:.18em;color:var(--gold);margin-bottom:12px;opacity:0;animation:fadeUp .6s .1s ease forwards;}
  .header-title{font-family:var(--font-disp);font-size:clamp(36px,10vw,52px);font-weight:300;line-height:1.05;letter-spacing:-.01em;color:var(--ink);margin-bottom:6px;opacity:0;animation:fadeUp .6s .15s ease forwards;}
  .header-title em{font-style:italic;color:var(--ink-soft);}
  .header-client{font-size:14px;color:var(--ink-muted);margin-top:4px;margin-bottom:20px;opacity:0;animation:fadeUp .6s .2s ease forwards;}
  .header-desc{font-size:14px;font-weight:300;color:var(--ink-muted);line-height:1.65;max-width:420px;opacity:0;animation:fadeUp .6s .25s ease forwards;}
  .divider{max-width:1080px;margin:0 auto 32px;padding:0 24px;opacity:0;animation:fadeUp .5s .3s ease forwards;}
  .divider-line{height:1px;background:linear-gradient(to right,transparent,var(--border) 30%,var(--border) 70%,transparent);}
  .layout{position:relative;z-index:1;max-width:1080px;margin:0 auto;padding:0 20px;
    display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:28px;align-items:start;}
  @media (max-width:900px){ .layout{grid-template-columns:minmax(0,1fr);} }
  .counter-chip{margin-bottom:18px;opacity:0;animation:fadeUp .5s .35s ease forwards;}
  .counter-inner{display:inline-flex;align-items:center;gap:6px;padding:6px 12px 6px 8px;border-radius:999px;background:var(--cream-mid);border:1px solid var(--border);}
  .counter-dot{width:7px;height:7px;border-radius:50%;background:var(--gold);}
  .counter-text{font-size:12px;color:var(--ink-muted);}
  .counter-text span{font-weight:600;color:var(--ink);}
  .cards{display:flex;flex-direction:column;gap:20px;}
  .item{background:#FFF;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;
    box-shadow:0 1px 3px rgba(26,26,26,.04);opacity:0;transform:translateY(14px);
    transition:border-color .22s ease,box-shadow .22s ease;}
  .item.visible{animation:cardReveal .5s ease forwards;}
  .item.decided-approve{border-color:rgba(44,107,69,.35);}
  .item.decided-revise{border-color:rgba(169,101,43,.35);}
  .media-wrap{background:var(--cream-mid);display:flex;align-items:center;justify-content:center;min-height:180px;}
  .media-wrap img{width:100%;height:auto;display:block;cursor:zoom-in;}
  .media-wrap video{width:100%;height:auto;display:block;background:#000;}
  .media-fallback{padding:36px 20px;text-align:center;font-size:13px;color:var(--ink-muted);}
  .media-fallback a{color:var(--ink);text-decoration:underline;text-underline-offset:3px;}
  .item-body{padding:16px 18px 18px;}
  .item-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;}
  .item-name{font-size:13px;color:var(--ink-muted);word-break:break-word;}
  .item-badge{flex-shrink:0;font-size:9px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--gold);}
  .actions{display:flex;gap:10px;}
  .btn{flex:1;height:46px;border-radius:999px;border:1px solid var(--border);cursor:pointer;background:#FFF;
    font-family:var(--font-body);font-size:13.5px;font-weight:500;letter-spacing:.03em;color:var(--ink-soft);
    display:flex;align-items:center;justify-content:center;gap:8px;-webkit-tap-highlight-color:transparent;
    transition:background .2s ease,color .2s ease,border-color .2s ease,transform .12s ease;}
  .btn:hover{border-color:rgba(26,26,26,.25);}
  .btn:active{transform:scale(.98);}
  .btn.on-approve{background:var(--green);border-color:var(--green);color:#FFF;}
  .btn.on-revise{background:var(--amber);border-color:var(--amber);color:#FFF;}
  .btn[disabled]{opacity:.5;pointer-events:none;}
  .revise-box{margin-top:12px;display:none;}
  .revise-box.open{display:block;animation:fadeUp .3s ease;}
  .revise-box textarea{width:100%;min-height:84px;resize:vertical;padding:12px 14px;border-radius:12px;
    border:1px solid var(--border);background:var(--cream);font-family:var(--font-body);font-size:14px;
    color:var(--ink);line-height:1.5;}
  .revise-box textarea:focus{outline:none;border-color:var(--gold);}
  .revise-send{margin-top:10px;height:42px;width:100%;border-radius:999px;border:none;cursor:pointer;
    background:var(--ink);color:var(--cream);font-family:var(--font-body);font-size:13.5px;font-weight:500;}
  .revise-send[disabled]{opacity:.5;pointer-events:none;}
  .state-line{margin-top:12px;font-size:12.5px;color:var(--ink-muted);display:none;}
  .state-line.show{display:block;}
  .state-line b{font-weight:600;color:var(--ink-soft);}
  .notepad{position:sticky;top:20px;background:#FFF;border:1px solid var(--border);border-radius:var(--radius);
    padding:20px;box-shadow:0 1px 3px rgba(26,26,26,.04);opacity:0;animation:fadeUp .6s .4s ease forwards;}
  @media (max-width:900px){ .notepad{position:static;} }
  .notepad h2{font-family:var(--font-disp);font-size:26px;font-weight:300;letter-spacing:-.01em;margin-bottom:6px;}
  .notepad p{font-size:12.5px;font-weight:300;color:var(--ink-muted);line-height:1.6;margin-bottom:14px;}
  .notepad textarea{width:100%;min-height:260px;resize:vertical;padding:14px;border-radius:12px;
    border:1px solid var(--border);background:var(--cream);font-family:var(--font-body);font-size:14px;
    color:var(--ink);line-height:1.6;}
  .notepad textarea:focus{outline:none;border-color:var(--gold);}
  .note-state{margin-top:10px;font-size:11.5px;color:var(--ink-muted);min-height:16px;letter-spacing:.02em;}
  /* Not alanının yönü metinde geçiyor ("sağdaki" / "aşağıdaki") — layout 900px'te
     tek kolona düşüp not defterini alta aldığı için metin AYNI breakpoint'e bağlı.
     JS ile cihaz tespiti yapılmadı: pencere yeniden boyutlandırılınca ya da telefon
     yan çevrilince yalan söylerdi. */
  .yon-genis{display:inline;}
  .yon-dar{display:none;}
  @media (max-width:900px){ .yon-genis{display:none;} .yon-dar{display:inline;} }
  .empty-state{text-align:center;padding:60px 24px;color:var(--ink-muted);font-size:14px;font-weight:300;}
  .lightbox{position:fixed;inset:0;z-index:200;background:rgba(26,26,26,.92);display:none;
    align-items:center;justify-content:center;padding:24px;cursor:zoom-out;}
  .lightbox.open{display:flex;}
  .lightbox img{max-width:100%;max-height:100%;object-fit:contain;}
  @keyframes fadeUp{from{opacity:0;transform:translateY(10px);}to{opacity:1;transform:translateY(0);}}
  @keyframes cardReveal{from{opacity:0;transform:translateY(14px);}to{opacity:1;transform:translateY(0);}}
</style></head>
<body>
  <header class="page-header">
    <div class="logo-lockup"><img class="logo-img" src="/panel/kotar-logo.png" alt="Kotar — dijital medya ajansı"></div>
    <p class="header-eyebrow">İÇERİK ONAYI</p>
    <h1 class="header-title">Hazırladığımız<br><em>İçerikler</em></h1>
    <p class="header-client" id="client"></p>
    <p class="header-desc">Aşağıdaki içerikleri tek tek onaylayabilir ya da revize isteyebilirsiniz. Aklınıza takılan her şeyi <span class="yon-genis">sağdaki</span><span class="yon-dar">sayfanın altındaki</span> not alanına yazabilirsiniz — otomatik kaydedilir.</p>
  </header>
  <div class="divider"><div class="divider-line"></div></div>
  <div class="layout">
    <main>
      <div class="counter-chip"><div class="counter-inner"><div class="counter-dot"></div>
        <p class="counter-text"><span id="countNum">0</span>/<span id="countTotal">0</span> içerik yanıtlandı</p></div></div>
      <div class="cards" id="cards"><div class="empty-state">Yükleniyor…</div></div>
    </main>
    <aside class="notepad">
      <h2>Notlarınız</h2>
      <p>Genel görüşleriniz, istekleriniz veya sonraki içerikler için fikirleriniz.</p>
      <textarea id="note" placeholder="Buraya yazabilirsiniz…"></textarea>
      <div class="note-state" id="noteState"></div>
    </aside>
  </div>
  <div class="lightbox" id="lightbox"><img id="lightboxImg" src="" alt=""></div>
<script>
const TOKEN="__TOKEN__";
const cardsEl=document.getElementById("cards"), countEl=document.getElementById("countNum"),
      totalEl=document.getElementById("countTotal"), noteEl=document.getElementById("note"),
      noteState=document.getElementById("noteState"), lightbox=document.getElementById("lightbox"),
      lightboxImg=document.getElementById("lightboxImg");
const KINDS={post:"Tasarım",video:"Video"};
let items=[];
function esc(s){const d=document.createElement("div");d.textContent=s||"";return d.innerHTML;}
function updateCounter(){
  countEl.textContent=items.filter(i=>i.review&&i.review.status).length;
  totalEl.textContent=items.length;
}
function stateText(rv){
  if(!rv||!rv.status) return "";
  if(rv.status==="approved") return "<b>Onayladınız.</b> Dilerseniz kararınızı değiştirebilirsiniz.";
  return "<b>Revize istediniz.</b> Notunuz ekibimize iletildi."+(rv.note?" ("+esc(rv.note)+")":"");
}
function mediaBlock(it){
  if(it.video_url) return `<div class="media-wrap"><video controls preload="metadata" playsinline src="${it.video_url}"></video></div>`;
  if(it.kind==="video"&&it.drive_url) return `<div class="media-wrap"><div class="media-fallback">Videoyu görüntülemek için <a href="${it.drive_url}" target="_blank" rel="noreferrer">buraya tıklayın</a>.</div></div>`;
  if(it.media_url) return `<div class="media-wrap"><img loading="lazy" src="${it.media_url}?size=full" alt="${esc(it.file_name)}"></div>`;
  return `<div class="media-wrap"><div class="media-fallback">Önizleme yok.</div></div>`;
}
function card(it){
  const el=document.createElement("article");
  el.className="item";
  const rv=it.review;
  if(rv&&rv.status==="approved") el.classList.add("decided-approve");
  if(rv&&rv.status==="revision_requested") el.classList.add("decided-revise");
  el.innerHTML=`${mediaBlock(it)}
    <div class="item-body">
      <div class="item-top">
        <div class="item-name">${esc(it.file_name)||"İçerik"}</div>
        <div class="item-badge">${KINDS[it.kind]||it.kind}</div>
      </div>
      <div class="actions">
        <button class="btn btn-approve${rv&&rv.status==="approved"?" on-approve":""}" type="button">Onaylıyorum</button>
        <button class="btn btn-revise${rv&&rv.status==="revision_requested"?" on-revise":""}" type="button">Revize istiyorum</button>
      </div>
      <div class="revise-box">
        <textarea placeholder="Neyin değişmesini istersiniz?">${esc(rv&&rv.status==="revision_requested"?rv.note:"")}</textarea>
        <button class="revise-send" type="button">Revize talebini gönder</button>
      </div>
      <div class="state-line${rv&&rv.status?" show":""}">${stateText(rv)}</div>
    </div>`;
  const approveBtn=el.querySelector(".btn-approve"), reviseBtn=el.querySelector(".btn-revise"),
        box=el.querySelector(".revise-box"), ta=box.querySelector("textarea"),
        send=box.querySelector(".revise-send"), line=el.querySelector(".state-line");
  const img=el.querySelector(".media-wrap img");
  if(img) img.addEventListener("click",()=>{ lightboxImg.src=img.src; lightbox.classList.add("open"); });
  async function decide(action,note,btn){
    btn.disabled=true;
    try{
      const r=await fetch(`/onay/${TOKEN}/decision`,{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({upload_id:it.id,action,note:note||""})});
      const d=await r.json().catch(()=>({}));
      if(!r.ok){ line.innerHTML="Gönderilemedi, tekrar deneyin."; line.classList.add("show"); return; }
      it.review=d.review;
      approveBtn.classList.toggle("on-approve",action==="approve");
      reviseBtn.classList.toggle("on-revise",action==="revise");
      el.classList.toggle("decided-approve",action==="approve");
      el.classList.toggle("decided-revise",action==="revise");
      box.classList.remove("open");
      line.innerHTML=stateText(it.review); line.classList.add("show");
      updateCounter();
    }catch{ line.innerHTML="Bağlantı hatası, tekrar deneyin."; line.classList.add("show"); }
    finally{ btn.disabled=false; }
  }
  approveBtn.addEventListener("click",()=>{ box.classList.remove("open"); decide("approve","",approveBtn); });
  reviseBtn.addEventListener("click",()=>{ box.classList.add("open"); ta.focus(); });
  send.addEventListener("click",()=>{
    const note=ta.value.trim();
    if(!note){ ta.focus(); return; }
    decide("revise",note,send);
  });
  return el;
}
lightbox.addEventListener("click",()=>lightbox.classList.remove("open"));
document.addEventListener("keydown",e=>{ if(e.key==="Escape") lightbox.classList.remove("open"); });
async function load(){
  let d;
  try{ const r=await fetch(`/onay/${TOKEN}/items`); if(!r.ok)throw 0; d=await r.json(); }
  catch{ cardsEl.innerHTML='<div class="empty-state">Bu bağlantı geçersiz.</div>'; return; }
  document.getElementById("client").textContent=d.client_name||"";
  noteEl.value=d.note||"";
  items=d.items||[];
  updateCounter();
  if(!items.length){ cardsEl.innerHTML='<div class="empty-state">Bu bağlantıda içerik kalmamış.</div>'; return; }
  cardsEl.innerHTML="";
  items.forEach((it,i)=>{ const el=card(it); cardsEl.appendChild(el);
    setTimeout(()=>el.classList.add("visible"),120+i*70); });
}
// Not defteri: yazma durduktan 900ms sonra kaydeder (her tuşta istek atmaz).
let noteTimer=null, noteSon="";
noteEl.addEventListener("input",()=>{
  noteState.textContent="yazılıyor…";
  clearTimeout(noteTimer);
  noteTimer=setTimeout(async()=>{
    const val=noteEl.value;
    if(val===noteSon){ noteState.textContent="kaydedildi"; return; }
    try{
      const r=await fetch(`/onay/${TOKEN}/note`,{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify({note:val})});
      if(r.ok){ noteSon=val; noteState.textContent="kaydedildi"; }
      else noteState.textContent="kaydedilemedi, tekrar deneyin";
    }catch{ noteState.textContent="bağlantı hatası"; }
  },900);
});
load();
</script></body></html>"""
