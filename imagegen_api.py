"""Codex görsel üretim uçları (`/api/imagegen/*`) — yalnız management.

Bu uçlar YALNIZCA işi kuyruğa alır ve sonucu okur; hiçbir uzun süren işlem web
sürecinde koşmaz (Codex çağrısı 1-4 dk sürer, gunicorn worker'ı bloklardı).

Üretilen görsel PUBLIC bir route'tan servis EDİLMEZ — `/jobs/<id>/image` rol
kapısının arkasındadır. v1'de onay/red UI'si yok; onay kapısı invaryantı bu hattın
çıktısını hiçbir müşteri yüzeyine bağlamayarak korunur (spec §10).
"""
from datetime import timedelta

from flask import Blueprint, jsonify, request, send_file

import image_providers
import imagegen_batch
import imagegen_store
import jobqueue
from api import csrf_protect
from extensions import db
from models import AppSetting, Client, ClientAsset, utcnow
from models_imagegen import ASPECTS, VARIANTS, ImageJob
from models_sharing import WeeklyBrief
from sso_client import current_user

bp = Blueprint('imagegen', __name__)
bp.before_request(csrf_protect)  # api ile aynı CSRF (session token)

# Kota savunması BİZDE de var: ChatGPT tarafındaki tavana çarpmadan önce kendi
# sınırımıza çarparız — böylece 'quota' hatası istisna olur, günlük rutin değil.
# Tek worker ve ~3 dk/üretim ile günde teorik tavan zaten ~200'dür; bu sayılar
# operasyonun gerçek ihtiyacına göre (40 müşteri) tutuldu.
DAILY_CLIENT_LIMIT = 20        # müşteri başına / gün
DAILY_GLOBAL_LIMIT = 60        # tüm sistem / gün


def _require_management():
    """`sharing._require_management` ile birebir aynı kapı (v1: management-only).

    Görsel üretimi müşteri markasını üçüncü tarafa gönderir ve kota harcar —
    üretim rolleri (designer/content_creator) bu hatta v1'de giremez."""
    u = current_user()
    if not u:
        return None, (jsonify(error='oturum yok'), 401)
    if u.get('role') != 'management':
        return None, (jsonify(error='bu işlem için yetkiniz yok'), 403)
    return u, None


def _bakim_kapali():
    """`AppSetting['codex_image_enabled']` == '0' ise hat bakımda (operatör anahtarı)."""
    return AppSetting.get('codex_image_enabled', '1') == '0'


def _gunluk_sayim(client_id=None):
    """Son 24 saatte açılmış ImageJob sayısı.

    Başarısızlar da SAYILIR: her deneme Codex kotasından yer, bu yüzden sınır
    'başarılı üretim' değil 'deneme' üzerinden işler."""
    q = ImageJob.query.filter(ImageJob.created_at >= utcnow() - timedelta(days=1))
    if client_id is not None:
        q = q.filter_by(client_id=client_id)
    return q.count()


@bp.post('/generate')
def imagegen_generate():
    """Üretim işini kuyruğa al.

    KVKK ön kontrolü burada (kullanıcıya hızlı geri bildirim; ASIL kapı handler'da),
    referans sahipliği burada (handler'da tekrar) — iki katman bilinçli."""
    u, err = _require_management()
    if err:
        return err
    if _bakim_kapali():
        return jsonify(error='Codex görsel üretimi şu anda bakımda.'), 503
    data = request.get_json(silent=True) or {}
    c = db.session.get(Client, data.get('client_id'))
    if c is None or c.status == 'deleted':
        return jsonify(error='müşteri bulunamadı'), 404
    if _gunluk_sayim(c.id) >= DAILY_CLIENT_LIMIT:
        return jsonify(error=f'Bu müşteri için günlük üretim sınırına ulaşıldı '
                             f'({DAILY_CLIENT_LIMIT}). Yarın tekrar deneyin.'), 429
    if _gunluk_sayim() >= DAILY_GLOBAL_LIMIT:
        return jsonify(error=f'Günlük toplam üretim sınırına ulaşıldı '
                             f'({DAILY_GLOBAL_LIMIT}).'), 429
    if not (c.brand_profile or {}).get('ai_image_consent'):
        return jsonify(error='müşteri AI görsel onayı yok (KVKK). Müşteri kaydında '
                             'ai_image_consent onayı gerekli.'), 409
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify(error='istem boş olamaz'), 400
    aspect = data.get('aspect_ratio') or 'social_post_4_5'
    if aspect not in ASPECTS:
        return jsonify(error='geçersiz en-boy oranı'), 400
    ref_ids = data.get('reference_asset_ids') or []
    if not isinstance(ref_ids, list):
        return jsonify(error='reference_asset_ids liste olmalı'), 400
    if len(ref_ids) > image_providers.MAX_REFERENCES:
        return jsonify(error=f'en fazla {image_providers.MAX_REFERENCES} referans '
                             f'seçilebilir'), 400
    if ref_ids:
        sahip = ClientAsset.query.filter(ClientAsset.id.in_(ref_ids),
                                         ClientAsset.client_id == c.id,
                                         ClientAsset.deleted_at.is_(None)).count()
        if sahip != len(set(ref_ids)):
            return jsonify(error='referans görsel bu müşteriye ait değil'), 403

    ij = ImageJob(client_id=c.id, brief_id=data.get('brief_id'),
                  requested_by=u.get('email') or u.get('sub'), provider='codex_exec',
                  original_user_prompt=prompt[:2000], aspect_ratio=aspect,
                  reference_asset_ids=list(ref_ids), status='queued')
    db.session.add(ij)
    db.session.commit()
    # dedup ImageJob id'si üzerinden: panelin çift tıklaması ikinci job üretmez.
    job = jobqueue.enqueue('codex_image', {'image_job_id': ij.id}, priority=0,
                           dedup_key=f'codex_image:{ij.id}', created_by=u.get('sub'))
    return jsonify(image_job=ij.to_dict(), job=job.to_dict()), 202


@bp.get('/jobs')
def imagegen_jobs():
    """Müşterinin son üretimleri (en yeni önce, en fazla 20)."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id zorunlu'), 400
    rows = (ImageJob.query.filter_by(client_id=client_id)
            .order_by(ImageJob.id.desc()).limit(20).all())
    return jsonify(image_jobs=[r.to_dict() for r in rows])


@bp.get('/jobs/<int:job_id>')
def imagegen_job(job_id):
    """Tek işin durumu — panel bunu yoklayarak üretimi bekler."""
    _, err = _require_management()
    if err:
        return err
    ij = db.session.get(ImageJob, job_id)
    if ij is None:
        return jsonify(error='iş bulunamadı'), 404
    return jsonify(image_job=ij.to_dict())


@bp.get('/jobs/<int:job_id>/image')
def imagegen_image(job_id):
    """Görseli rol kapısının arkasından stream et. Lokal depo dışarı açılmaz;
    dosya adı DB'den gelir ve `abs_path` içinde ayrıca doğrulanır."""
    _, err = _require_management()
    if err:
        return err
    ij = db.session.get(ImageJob, job_id)
    if ij is None or not ij.output_path:
        return jsonify(error='görsel yok'), 404
    p = imagegen_store.abs_path(ij.output_path)
    if not p:
        return jsonify(error='görsel dosyası bulunamadı'), 404
    return send_file(p, mimetype='image/png', max_age=0)


@bp.get('/health')
def imagegen_health():
    """Codex kurulumu ve oturum durumu + bakım anahtarı. Sır DÖNMEZ — `health_check`
    auth dosyasının yalnız VARLIĞINA bakar, içeriğini okumaz."""
    _, err = _require_management()
    if err:
        return err
    st = image_providers.get_provider('codex_exec').health_check()
    return jsonify(ok=st['ok'], detail=st['detail'], enabled=not _bakim_kapali())


# --- Haftalık parti üretimi (2026-08-10) ---

@bp.post('/batch')
def imagegen_batch_baslat():
    """Haftanın onaylı brief'inden parti üretimi başlat (spec §3).

    Kontrol sırası: bakım → müşteri → anahtar → KVKK → brief → plan → limit.
    Limit EN SONDA ama satır açılmadan ÖNCE: parti kaç iş isteyeceğini ancak plan
    çıkınca biliriz ve limit aşılıyorsa HİÇBİR satır açılmamalıdır (spec §8)."""
    u, err = _require_management()
    if err:
        return err
    if _bakim_kapali():
        return jsonify(error='Codex görsel üretimi şu anda bakımda.'), 503
    data = request.get_json(silent=True) or {}
    c = db.session.get(Client, data.get('client_id'))
    if c is None or c.status == 'deleted':
        return jsonify(error='müşteri bulunamadı'), 404
    prof = c.brand_profile or {}
    if not prof.get('auto_image_enabled'):
        return jsonify(error='bu müşteride haftalık görsel üretimi kapalı'), 409
    if not prof.get('ai_image_consent'):
        return jsonify(error='müşteri AI görsel onayı yok (KVKK). Müşteri kaydında '
                             'ai_image_consent onayı gerekli.'), 409
    week_iso = (data.get('week_iso') or '').strip()
    if not week_iso:
        return jsonify(error='week_iso zorunlu'), 400
    brief = WeeklyBrief.query.filter_by(client_id=c.id, week_iso=week_iso,
                                        status='approved').first()
    if brief is None:
        return jsonify(error=f'{week_iso} için onaylı brief yok'), 404

    plan = imagegen_batch.parti_plani(brief)
    if not plan['uygun']:
        return jsonify(error="bu brief'te görselleştirilecek fikir yok "
                             '(hepsi video/reel ya da fikir listesi boş)',
                       skipped=plan['skipped']), 409

    mevcut = imagegen_batch.mevcut_isler(c.id, week_iso)
    isler = imagegen_batch.uygulanacaklar(plan, mevcut)
    already = [{'index': u_['index'], 'variant': v, 'baslik': u_['baslik'],
                'image_job_id': mevcut[(u_['index'], v)].id}
               for u_ in plan['uygun'] for v in VARIANTS
               if (u_['index'], v) in mevcut
               and mevcut[(u_['index'], v)].status == 'completed']
    logo_missing = imagegen_batch.musteri_logosu(c.id) is None
    if not isler:
        return jsonify(created=[], skipped=plan['skipped'], already=already,
                       logo_missing=logo_missing), 202

    # Limit: parti TOPLAMI üzerinden. Kısmi üretim yapılmaz.
    if _gunluk_sayim(c.id) + len(isler) > DAILY_CLIENT_LIMIT:
        return jsonify(error=f'Bu parti günlük müşteri sınırını aşıyor '
                             f'({DAILY_CLIENT_LIMIT}). Yarın tekrar deneyin.'), 429
    if _gunluk_sayim() + len(isler) > DAILY_GLOBAL_LIMIT:
        return jsonify(error=f'Bu parti günlük toplam sınırı aşıyor '
                             f'({DAILY_GLOBAL_LIMIT}).'), 429

    sonuc = imagegen_batch.parti_uygula(c, brief, isler,
                                        u.get('email') or u.get('sub'))
    return jsonify(created=sonuc['created'], skipped=plan['skipped'],
                   already=already, logo_missing=logo_missing), 202


@bp.get('/batch')
def imagegen_batch_listele():
    """Haftanın işlerini fikir bazında grupla (galeri)."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    week_iso = (request.args.get('week_iso') or '').strip()
    if not client_id or not week_iso:
        return jsonify(error='client_id ve week_iso zorunlu'), 400
    brief = WeeklyBrief.query.filter_by(client_id=client_id, week_iso=week_iso,
                                        status='approved').first()
    if brief is None:
        return jsonify(groups=[], skipped=[])
    plan = imagegen_batch.parti_plani(brief)
    mevcut = imagegen_batch.mevcut_isler(client_id, week_iso)
    groups = []
    for u_ in plan['uygun']:
        jobs = {v: mevcut[(u_['index'], v)].to_dict()
                for v in VARIANTS if (u_['index'], v) in mevcut}
        groups.append({'index': u_['index'], 'baslik': u_['baslik'], 'jobs': jobs})
    return jsonify(groups=groups, skipped=plan['skipped'])


@bp.get('/weeks')
def imagegen_weeks():
    """Müşterinin onaylı brief'i olan haftalar (hafta seçici; en yeni önce).

    Yalnız `approved`: taslak brief üretime giremez, seçiciye koymak yanıltır."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify(error='client_id zorunlu'), 400
    rows = (WeeklyBrief.query
            .filter_by(client_id=client_id, status='approved')
            .order_by(WeeklyBrief.week_iso.desc()).limit(12).all())
    return jsonify(weeks=[{'week_iso': b.week_iso, 'brief_id': b.id,
                           'title': b.title} for b in rows])


@bp.get('/client-settings')
def imagegen_client_settings_oku():
    """Müşterinin iki kapı bayrağı. Panel bunu sayfa açılışında okur; okumasaydı
    anahtar her yenilemede kapalı görünür, DB'de açık olur ve kullanıcı düğmenin
    neden pasif olduğunu anlayamazdı."""
    _, err = _require_management()
    if err:
        return err
    client_id = request.args.get('client_id', type=int)
    c = db.session.get(Client, client_id) if client_id else None
    if c is None or c.status == 'deleted':
        return jsonify(error='müşteri bulunamadı'), 404
    prof = c.brand_profile or {}
    return jsonify(auto_image_enabled=bool(prof.get('auto_image_enabled')),
                   ai_image_consent=bool(prof.get('ai_image_consent')))


@bp.patch('/client-settings')
def imagegen_client_settings():
    """`auto_image_enabled` anahtarını yaz. `brand_profile` MERGE edilir — üzerine
    yazmak diğer marka alanlarını silerdi."""
    _, err = _require_management()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    c = db.session.get(Client, data.get('client_id'))
    if c is None or c.status == 'deleted':
        return jsonify(error='müşteri bulunamadı'), 404
    if not isinstance(data.get('auto_image_enabled'), bool):
        return jsonify(error='auto_image_enabled bool olmalı'), 400
    bp_ = dict(c.brand_profile) if isinstance(c.brand_profile, dict) else {}
    bp_['auto_image_enabled'] = data['auto_image_enabled']
    c.brand_profile = bp_          # yeni dict — SQLAlchemy mutasyonu böyle algılar
    c.updated_at = utcnow()
    db.session.commit()
    return jsonify(auto_image_enabled=bp_['auto_image_enabled'])
