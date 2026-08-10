"""Kalıcı doğrudan medya bağlantısı — public `GET /m/<file_id>` (2026-07-30).

NEDEN VAR: videograf sayfalarındaki "Kopyala" düğmesi Drive dosya sayfasının linkini
veriyordu (`drive.google.com/file/d/<id>/view`) — Drive'ın görüntüleyici SAYFASI, dosyanın
kendisi değil. Bu uç dosyanın KENDİSİNİ servis eder ve link **kalıcıdır**:

    lokal kopya varsa (21 günlük `media_store` penceresi) → dosya doğrudan servis edilir
    lokal kopya yoksa (pencere doldu)                    → Drive linkine 302 yönlendirme

Yani aynı link ömür boyu çalışır; sunucu kopyası silinince sessizce Drive'a düşer.
Drive linki AYRICA saklanmıyor — `file_id` zaten Drive dosya kimliği, yönlendirme
adresi ondan türetiliyor (yeni kolon gerekmedi).

ÜÇ MOD (2026-07-31 — "linki alan kişi indirebilsin" isteği):

    /m/<id>          → mini HTML sayfası: oynatıcı/görsel + "İndir" düğmesi
    /m/<id>?raw=1    → dosyanın kendisi, inline (Range destekli — video seek)
    /m/<id>?dl=1     → dosyanın kendisi, attachment (tarayıcı kaydeder)

Ham dosya niye artık varsayılan DEĞİL: kopyalanan link genelde ajans dışına gidiyor ve
ham `video/mp4` yanıtında indirme, tarayıcının yerleşik oynatıcı menüsüne gömülü kalıyordu
(mobilde çoğu zaman hiç yok). Sayfa, indirmeyi görünür bir düğme yapıyor. `?raw=1` ham
davranışı aynen koruyor → `<video src>`/gömme kullanan mevcut tüketiciler bozulmaz.

AUTH YOK — bilinçli (proje sahibi kararı 2026-07-30: "linki bilen herkes açabilsin"). Yeni bir
açıklık DEĞİL: bu uç YALNIZ Drive'da zaten `grant_anyone_reader` ile "bağlantıya sahip
herkes okuyabilir" yapılmış iki dosya sınıfını servis eder —
  * videograf VİDEO yüklemeleri (`CardUpload.category='video'`, sharing.upload izni verir)
  * çekim FOTOĞRAFLARI (`VideographerPhoto`, vg_photo_upload izni verir)
Bu ikisi zaten linki bilen herkese açık; aynı dosyayı kendi sunucumuzdan vermek kitleyi
genişletmiyor. `media_store` içindeki DİĞER her şey (müşteri tasarım yüklemeleri, share
görselleri — bunların Drive'da public izni YOK) burada **404** döner. Uygunluk DB'den
sorulur, dosya sisteminden değil: `media_store.find_original` yalnız uygunluk geçtikten
sonra çağrılır, aksi halde uç `media_store`'un tamamı için okuma oracle'ı olurdu.

Soft-delete edilen kayıt (`deleted_at`) da 404 verir → panelden silinen dosyanın linki ölür.
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
# Drive'ın doğrudan indirme adresi — lokal kopya süresi dolmuş dosyada `?dl=1`
# buraya yönlenir (görüntüleyici sayfasına değil): niyet indirmeydi.
DRIVE_DOWNLOAD = 'https://drive.google.com/uc?export=download&id={}'

# Drive dosya kimliği alfabesi. Uzunluk aralığı geniş tutuldu (Drive id boyu sabit
# değil); asıl kapı bu değil, aşağıdaki DB uygunluk sorgusu.
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{10,128}$')

# Aynı IP'den dakikada izin verilen istek. Video seek'i Range istekleriyle çok sayıda
# istek üretebildiği için normal public uçlardan (40/dk) cömert tutuldu.
RATE_MAX, RATE_WINDOW = 240, 60


def _eligible(file_id):
    """Uygunsa `(dosya_adı, tür)` döndürür, değilse `(None, None)`.

    Tür ('video' | 'foto') sayfanın `<video>` mi `<img>` mi basacağını belirler;
    ad mime tahmini ve indirme adı için kullanılır.
    Sıra önemsiz — bir file_id iki sınıfta birden olamaz (ayrı Drive yüklemeleri)."""
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
        # Uygun değil VEYA silinmiş. İkisini ayırmıyoruz: ayırmak, hangi file_id'lerin
        # sistemde var olduğunu sızdırırdı.
        return '', 404

    dl = request.args.get('dl') == '1'
    path = media_store.find_original(file_id)

    # `?web=1` → tarayıcı uyumlu türev (1080p H.264). Yalnız OYNATMA yolunda:
    # indirme her zaman orijinali verir, yoksa 4K çeken videografın dosyası
    # linkten 1080p olarak dönerdi (2026-08-01).
    web_served = False
    if not dl and request.args.get('web') == '1':
        web = media_store.find_web(file_id)
        if web:
            path, web_served = web, True

    if not (dl or request.args.get('raw') == '1'):
        return _viewer_page(file_id, name, kind, local=bool(path),
                            web=bool(media_store.find_web(file_id)))

    if path:
        # Türev DAİMA mp4/H.264'tür; mime'ı dosya ADINDAN türetmek `.mov`
        # orijinallerde `video/quicktime` verirdi ve tarayıcı türevi reddederdi.
        mime = ('video/mp4' if web_served else
                (mimetypes.guess_type(name)[0] or mimetypes.guess_type(path)[0]
                 or 'application/octet-stream'))
        try:
            # conditional=True → Range destekli (videoda ileri sarma çalışır).
            resp = send_file(path, mimetype=mime, conditional=True,
                             as_attachment=dl, download_name=(name if dl else None))
            resp.headers['Cache-Control'] = 'public, max-age=3600'
            return resp
        except OSError:
            log.warning('lokal kopya okunamadı, Drive\'a düşülüyor: %s', file_id)
    # Lokal yok/okunamadı → Drive. 302 (301 DEĞİL): kalıcı yönlendirme tarayıcıda
    # süresiz cache'lenir, lokal kopya politikası değişirse geri dönemezdik.
    target = (DRIVE_DOWNLOAD if dl else DRIVE_VIEW).format(file_id)
    return redirect(target, code=302)


def _viewer_page(file_id, name, kind, local, web=False):
    """Mini izleme/indirme sayfası.

    `local` ise medya kendi sunucumuzdan (`?raw=1`) gösterilir ve "İndir" düğmesi
    `?dl=1`'e gider. Lokal kopya süresi dolmuşsa sayfa ÖLMEZ: Drive'ın gömülü
    oynatıcısına düşer ve indirme Drive'a yönlenir — link kalıcılığı korunur.

    `web` (2026-08-01): videonun tarayıcı uyumlu türevi hazır → oynatıcı ONU
    kullanır (`?raw=1&web=1`). Türev yoksa orijinal denenir; 4K/HEVC ise mobilde
    oynamayabilir, ama sayfa yine de indirme yolunu sunar."""
    safe_name = html.escape(name)
    # file_id `_ID_RE` ile doğrulandı (yalnız [A-Za-z0-9_-]) → URL'ye güvenle gömülür.
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
                 # Drive indirme adresindeki `&` HTML özniteliğinde `&amp;` olmalı.
                 .replace('__DL_HREF__', html.escape(dl_href, quote=True))
                 .replace('__DL_LABEL__', dl_label)
                 .replace('__NOTE__', note))
    return Response(page, mimetype='text/html',
                    headers={'X-Robots-Tag': 'noindex, nofollow',
                             'Cache-Control': 'no-store'})


# Palet review.py'nin onay sayfasıyla aynı (koyu turkuaz) — medya odaklı sayfada
# açık zemin videoyu bastırıyordu.
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
