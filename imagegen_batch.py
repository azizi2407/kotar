"""Haftalık brief fikirlerinden parti görsel üretimi (2026-08-10).

Mevcut Codex hattının (`codex_image`) üzerine oturur: bu modül üretim YAPMAZ, yalnız
hangi fikirlerin hangi varyantlarla kuyruğa gireceğine karar verir ve `ImageJob`
satırlarını açar. Üretimin kendisi `ai_worker.codex_image_handler`'ın işidir ve o
handler bu iş için DEĞİŞMEZ (yalnız prompt kurucusu seçimi eklenir).

Neden ayrı modül: `imagegen_api.py` bir HTTP katmanı; fikir süzgeci ve idempotency
kuralları saf mantıktır ve HTTP olmadan test edilebilmelidir.
"""
import jobqueue
from extensions import db
from models_imagegen import VARIANTS, ImageJob

# Video işareti taşıyan fikirler tek kare görsele dönüştürülmez. `format` alanı bazı
# brief'lerde boş geliyor, bu yüzden `çekim_tipi` de taranır.
REEL_ISARETLERI = ('reel', 'video')


def gorsele_uygun(idea):
    """Bu fikir tek kare görsele dönüştürülebilir mi? (reel/video ise HAYIR)"""
    if not isinstance(idea, dict):
        return False
    metin = f"{idea.get('format') or ''} {idea.get('çekim_tipi') or ''}".lower()
    return not any(k in metin for k in REEL_ISARETLERI)


def fikir_basligi(idea, index):
    """Galeride ve hata mesajlarında kullanılacak insan-okur ad.

    `başlık` yoksa `ad`a, o da yoksa sıra numarasına düşer — brief AI üretimi,
    alanların dolu geleceği garanti değil."""
    if isinstance(idea, dict):
        for k in ('başlık', 'ad'):
            v = (idea.get(k) or '').strip()
            if v:
                return v
    return f'Fikir {index + 1}'


def parti_plani(brief):
    """Brief'in fikirlerini süz → `{'uygun': [...], 'skipped': [...]}`.

    DB'ye HİÇBİR ŞEY yazmaz — çağıran önce limit kontrolü yapabilsin diye plan ve
    uygulama ayrı. `ideas` bozuk/eksikse boş plan döner (500 DEĞİL: brief AI üretimi,
    şekli bozulabilir ve bu kullanıcıya 'bu brief'te görselleştirilecek fikir yok'
    olarak gösterilmelidir)."""
    ideas = getattr(brief, 'ideas', None)
    if not isinstance(ideas, list):
        return {'uygun': [], 'skipped': []}
    uygun, skipped = [], []
    for i, idea in enumerate(ideas):
        if not isinstance(idea, dict):
            continue        # düz metin/sayı — kullanıcıya gösterilecek bir şey yok
        baslik = fikir_basligi(idea, i)
        if gorsele_uygun(idea):
            uygun.append({'index': i, 'idea': idea, 'baslik': baslik})
        else:
            skipped.append({'index': i, 'baslik': baslik,
                            'sebep': 'video/reel fikri — görsel üretilmedi'})
    return {'uygun': uygun, 'skipped': skipped}


def mevcut_isler(client_id, week_iso):
    """O hafta için açılmış işler → `{(brief_idea_index, variant): ImageJob}`.

    Idempotency'nin temeli: parti ikinci kez tetiklenirse `completed` olanlar atlanır,
    `failed` olanlar yeniden üretilir (bkz. `uygulanacaklar`)."""
    rows = (ImageJob.query
            .filter(ImageJob.client_id == client_id,
                    ImageJob.week_iso == week_iso,
                    ImageJob.brief_idea_index.isnot(None))
            .all())
    return {(r.brief_idea_index, r.variant): r for r in rows}





def musteri_logosu(client_id):
    """Müşterinin aktif logo asset id'si; yoksa None.

    Kullanıcı kuralı (2026-08-10): her görsel üretimine marka logosu referans olarak
    gider. Logosu olmayan müşteride üretim ENGELLENMEZ — 29/32 müşteride logo var,
    3 müşteri yüzünden hattı kilitlemenin karşılığı yok; panel uyarı gösterir."""
    from models import ClientAsset
    row = (ClientAsset.query
           .filter(ClientAsset.client_id == client_id,
                   ClientAsset.kind == 'logo',
                   ClientAsset.deleted_at.is_(None))
           .order_by(ClientAsset.id.desc())
           .first())
    return row.id if row is not None else None

# Parti üretimi v1'de tek oran kullanır. `çekim_tipi`'nden oran türetmek v2 işidir
# (spec §5); şimdi türetmek, brief metnine dayalı kırılgan bir tahmin olurdu.
BATCH_ASPECT = 'social_post_4_5'

# Tamamlanmış iş yeniden üretilmez; bunun dışındaki her durum (failed/cancelled/yarım
# kalmış queued) yeniden kuyruğa alınabilir.
_ATLANACAK_DURUMLAR = ('completed',)


def uygulanacaklar(plan, mevcut):
    """Plandaki fikir × varyant kombinasyonlarından GERÇEKTEN üretilecek olanlar.

    `mevcut` (bkz. `mevcut_isler`) idempotency sağlar: `completed` bir satır varsa o
    kombinasyon atlanır, `failed` varsa satır yeniden kullanılır. Böylece düğmeye
    ikinci kez basmak kalanı tamamlar, olanı ikizlemez."""
    isler = []
    for u in plan['uygun']:
        for variant in VARIANTS:
            var_olan = mevcut.get((u['index'], variant))
            if var_olan is not None and var_olan.status in _ATLANACAK_DURUMLAR:
                continue
            isler.append({'index': u['index'], 'idea': u['idea'],
                          'baslik': u['baslik'], 'variant': variant,
                          'mevcut': var_olan})
    return isler


def parti_uygula(client, brief, isler, requested_by):
    """`ImageJob` satırlarını aç/sıfırla ve `codex_image` işlerini kuyruğa bas.

    `resolved_prompt` BURADA KURULMAZ: onu handler kurar (`codex_image_handler`), çünkü
    iş kuyrukta beklerken brief güncellenebilir ve üretim anındaki brief geçerli olmalı.
    Handler'ın hangi kurucuyu çağıracağını `brief_idea_index`'in dolu olması belirler.

    Her işe müşterinin logosu referans olarak eklenir (kullanıcı kuralı 2026-08-10);
    logo yoksa liste boş kalır ve prompt'ta logo kısıtı yazılmaz."""
    logo_id = musteri_logosu(client.id)
    created = []
    for is_ in isler:
        row = is_['mevcut']
        if row is None:
            row = ImageJob(client_id=client.id, provider='codex_exec')
            db.session.add(row)
        # Yeniden üretimde eski hatayı ve çıktıyı temizle — panelde bayat veri kalmasın.
        row.brief_id = brief.id
        row.week_iso = brief.week_iso
        row.brief_idea_index = is_['index']
        row.variant = is_['variant']
        row.requested_by = requested_by
        row.aspect_ratio = BATCH_ASPECT
        row.original_user_prompt = is_['baslik'][:2000]
        row.reference_asset_ids = [logo_id] if logo_id else []
        row.status = 'queued'
        row.resolved_prompt = None
        row.prompt_json = None          # yeniden üretimde çeviri de tazelensin
        row.output_path = row.output_meta = None
        row.error_code = row.error_public = row.error_internal = None
        db.session.flush()          # id gerek: dedup anahtarı ondan türüyor
        created.append(row)
    db.session.commit()

    for row in created:
        jobqueue.enqueue('codex_image', {'image_job_id': row.id}, priority=0,
                         dedup_key=f'codex_image:{row.id}', created_by=requested_by)
    return {'created': [r.to_dict() for r in created]}
