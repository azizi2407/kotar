"""AI akışları için ortak bağlam okuma servisi (Faz 0 — Task 2).

6 AI akışının (caption, hashtag, brief özeti vb.) ortak girdisi burada tek yerde
toplanır: müşteri profili, global kurallar, geçmiş caption'lar, hafta bağlamı.
Saf okuma — yan etkisi yoktur, DB'ye yazmaz. `ai_worker.py`'nin caption
handler'ı bugün bu bilgileri inline okuyor; bu modülü ona bağlamak Faz 1 işidir.
"""
from datetime import date

from extensions import db
from models import AppSetting, Client
from models_sharing import Share, SpecialDayEvent

# Ay (1-12) → TR mevsim adı.
_SEASON_BY_MONTH = {
    12: 'kış', 1: 'kış', 2: 'kış',
    3: 'ilkbahar', 4: 'ilkbahar', 5: 'ilkbahar',
    6: 'yaz', 7: 'yaz', 8: 'yaz',
    9: 'sonbahar', 10: 'sonbahar', 11: 'sonbahar',
}

_EMPTY_PROFILE = {'name': '', 'sector': '', 'brand_voice': '', 'target_audience': '',
                  'forbidden': '', 'cta': '', 'guide_md': '', 'color_palette': [],
                  'content_mix': {}, 'content_pillars': '', 'hashtags': {},
                  'ideas_per_week': 5}


def client_profile(client_id):
    """Müşteri temel bilgisi (name, sector) + brand_profile'ı tek dict'te birleştirir.

    Müşteri bulunamazsa veya brand_profile NULL/kısmi ise eksik alanlar boş
    string/liste/dict döner — çağıran patlamaz. `color_palette` (renk paleti),
    `content_mix` (içerik dağılımı: 3 foto+slogan / 1 reel / carousel) ve
    `content_pillars` (içerik sütunları — brief fikirlerinin dağıtıldığı steering
    metni) brief akışının ihtiyacı — brand_profile JSON'unda yoksa boş liste/dict/str.
    `hashtags` (konu/marka/sektor hashtag kümeleri) ve `ideas_per_week` (haftalık
    fikir sayısı, yoksa 5) brief steering alanları — brief_handler `ideas_per_week`'i
    doğrudan tüketir.
    """
    c = db.session.get(Client, client_id)
    if c is None:
        return dict(_EMPTY_PROFILE)
    bp = c.brand_profile if isinstance(c.brand_profile, dict) else {}
    return {
        'name': c.name or '',
        'sector': c.sector or '',
        'brand_voice': bp.get('brand_voice') or '',
        'target_audience': bp.get('target_audience') or '',
        'forbidden': bp.get('forbidden') or '',
        'cta': bp.get('cta') or '',
        'guide_md': bp.get('guide_md') or '',
        'color_palette': bp.get('color_palette') or [],
        'content_mix': bp.get('content_mix') or {},
        'content_pillars': bp.get('content_pillars') or '',
        'hashtags': bp.get('hashtags') or {},
        'ideas_per_week': bp.get('ideas_per_week') or 5,
    }


def global_rules():
    """Tüm müşterilerde geçerli ortak caption/hashtag kuralları (panelden düzenlenir)."""
    return AppSetting.get('caption_global_rules', '') or ''


# Caption üretim ayarları sistem-varsayılanı (Faz 1b — step 09). Tüm alanlar opsiyonel;
# `model=None` verilince ai_claude env/DEFAULT_MODEL'e düşer (per-job model 03 yoluyla).
CAPTION_SETTINGS_DEFAULTS = {
    'model': None,          # None → ai_claude.DEFAULT_MODEL / env CAPTION_MODEL
    'tone': None,           # None → brand_profile.brand_voice'tan türer (override değilse)
    'emoji_limit': 3,       # caption başına makul emoji üst sınırı
    'hashtag_count': 14,    # varsayılan hashtag sayısı (2026-07-18 proje sahibi kararı)
    'lang': 'TR',           # 'TR' | 'EN' | 'TR+EN' (iki dilli)
    'use_brief': False,     # True → brief intro prompt'a katılır (varsayılan kapalı — 2026-07-18 proje sahibi kararı)
    'char_limit': None,     # None → karakter limiti talimatı yok
}


def resolve_caption_settings(client, payload_settings=None):
    """Caption ayarlarını katmanlı çözer: sistem-varsayılanı ⊕ client.caption_settings
    ⊕ payload_settings (sağdan override). Yalnız bilinen şema anahtarları döner
    (bilinmeyen anahtar sonuca sızmaz). `client` None olabilir (varsayılanlar döner)."""
    resolved = dict(CAPTION_SETTINGS_DEFAULTS)
    cs = getattr(client, 'caption_settings', None) if client is not None else None
    if isinstance(cs, dict):
        resolved.update({k: v for k, v in cs.items() if k in CAPTION_SETTINGS_DEFAULTS})
    if isinstance(payload_settings, dict):
        resolved.update({k: v for k, v in payload_settings.items() if k in CAPTION_SETTINGS_DEFAULTS})
    return resolved


def recent_captions(client_id, n=10):
    """Müşterinin en son N dolu caption metni (en yeni önce).

    Kaynak `Share.caption_text` — `CaptionHistory` bu fazda kullanılmaz (YAGNI,
    ölü tablo); yayımlanan/seçilen caption'lar zaten Share'de duruyor. Soft-delete
    edilmiş (`deleted_at` dolu) Share'ler hariç tutulur — codebase konvansiyonu
    (bkz. review.py, sharing.py: her yerde `Share.deleted_at.is_(None)`).
    """
    rows = (Share.query
            .filter(Share.client_id == client_id,
                    Share.caption_text.isnot(None),
                    Share.caption_text != '',
                    Share.deleted_at.is_(None))
            .order_by(Share.created_at.desc(), Share.id.desc())
            .limit(n)
            .all())
    return [r.caption_text for r in rows]


def week_context(week_iso):
    """O ISO haftaya düşen aktif özel günler (global + müşteriye özel, hepsi) + mevsim.

    Brief-ready yapı döner: `{season, special_days, week_iso}` — `week_iso`
    girdiyi aynen yankılar, brief akışı ayrıca taşımak zorunda kalmaz. İmza
    yalnız `week_iso` alır — müşteriden bağımsızdır; sektörel/müşteriye özel
    filtreleme çağıranın işidir. Mevsim, haftanın ISO tanım günü olan
    Perşembe'nin ayına göre belirlenir. `week_iso` parse edilemezse boş sonuç
    döner (patlamaz).
    """
    bounds = _week_bounds(week_iso)
    if bounds is None:
        return {'special_days': [], 'season': '', 'week_iso': week_iso}
    monday, sunday, thursday = bounds

    special_days = []
    # Onay kapısı: downstream AI bağlamı YALNIZ onaylı (approved) özel günleri okur —
    # taslak (AI üretimi, onaysız) etkinlik prompt'a/müşteriye sızmamalı.
    for e in SpecialDayEvent.query.filter_by(active=True, status='approved').all():
        rng = _event_range(e)
        if rng is None:
            continue
        start, end = rng
        if start <= sunday and end >= monday:
            special_days.append(e.to_dict())

    return {'special_days': special_days, 'season': _SEASON_BY_MONTH.get(thursday.month, ''),
            'week_iso': week_iso}


def _week_bounds(week_iso):
    """'YYYY-Www' → (pazartesi, pazar, perşembe) date nesneleri; parse edilemezse None."""
    try:
        year_s, week_s = week_iso.split('-W')
        year, week = int(year_s), int(week_s)
        monday = date.fromisocalendar(year, week, 1)
        sunday = date.fromisocalendar(year, week, 7)
        thursday = date.fromisocalendar(year, week, 4)
        return monday, sunday, thursday
    except (ValueError, AttributeError, TypeError):
        return None


def _event_range(e):
    """SpecialDayEvent'in kapsadığı takvim aralığı (start, end); eksik/bozuk veri → None."""
    if e.year is None or e.month is None:
        return None
    try:
        if e.date_num is not None:
            d = date(e.year, e.month, e.date_num)
            return d, d
        if e.date_start is not None and e.date_end is not None:
            return date(e.year, e.month, e.date_start), date(e.year, e.month, e.date_end)
    except ValueError:
        return None
    return None


# --- AI görsel üretimi: prompt dönüşümü (2026-07-20) ---

def image_prompt_json_instruction(user_prompt):
    """Görsel üretim istemini İngilizce'ye çevirip yapılandırılmış JSON'a dönüştüren
    claude talimatı. Görsel modelleri (Mystic/nano banana/gpt...) İngilizce + alan-bazlı
    JSON istemlerle belirgin daha iyi sonuç verir. Çıktı YALNIZ tek JSON nesnesi —
    panel 'İngilizce JSON'a çevir' butonu textarea'ya koyar; worker refine adımı
    üretime gönderir. Untrusted kullanıcı metni delimiter'la sarılır."""
    import ai_claude
    return (
        'Aşağıdaki görsel üretim istemini İngilizce\'ye çevir ve görsel üretim modeline '
        'uygun yapılandırılmış JSON\'a dönüştür. ÇIKTIN YALNIZ şu anahtarlarla TEK bir '
        'JSON nesnesi olsun (başka hiçbir metin yazma, kod çiti kullanma):\n'
        '{"scene": "...", "subjects": ["..."], "style": "...", "lighting": "...", '
        '"color_palette": ["..."], "composition": "...", "mood": "...", '
        '"camera": "...", "text_elements": [], "negative": ["..."]}\n'
        'Kurallar: tüm değerler İNGİLİZCE; istemde olmayan bilgiyi uydurma, ilgili '
        'anahtarı boş bırak ("" ya da []); marka renkleri/hex verilmişse color_palette\'e '
        'BİREBİR aktar; görselde yazı istenmişse text_elements\'e İngilizce değil '
        'ORİJİNAL metniyle koy (yazılar çevrilmez).'
        + ai_claude.wrap_untrusted('İSTEM', user_prompt))


# --- transkript sözlüğü (2026-08-08) --------------------------------------

# Whisper'ın `initial_prompt`'una geçilecek özel adlar. Ölçümle doğrulandı:
# sözlüksüz "Molo Pantarya", sözlükle "Mall of Antalya" (3/3 tekrarlanabilir,
# maliyeti +0.3 sn). Model büyütmek bunu ÇÖZMÜYOR — model markayı bilmiyor;
# large-v3-turbo da medium da aynı adı farklı şekilde uyduruyordu.
#
# Whisper `initial_prompt`'u önceki-bağlam token'ı olarak yer: faster-whisper
# 224 token'a kırpar. Türkçe özel adlar ~2-4 token → yaklaşık 60-70 ad sığar.
# Sınırı KARAKTERDEN uyguluyoruz (tokenizer'ı buraya taşımamak için) ve
# müşteri adlarını ÖNCE koyuyoruz: kırpılma olursa ekip adları düşsün, marka
# adları kalsın — asıl kazanç orada.
VOCAB_MAX_CHARS = 700

# Modelin hiçbir müşteri kaydından öğrenemeyeceği, ajansa özgü sabit terimler.
_SABIT_TERIMLER = ('Kotar',)


def transcript_vocabulary(extra=None):
    """Whisper'a verilecek özel ad sözlüğü — tek satır, virgülle ayrılmış.

    Kaynak: aktif müşteri adları + panel kullanıcılarının adları + ajans sabitleri.
    `extra`: çağıranın eklemek istediği adlar (ör. o videonun müşterisi başa gelsin).

    Boş dönebilir (DB boşsa) — çağıran `initial_prompt=None` gibi davranmalı."""
    from models import UserRef

    adlar = []
    if extra:
        adlar.extend(str(x).strip() for x in extra if str(x or '').strip())
    adlar.extend(_SABIT_TERIMLER)
    # Aktif müşteriler önce: kırpılma olursa marka adları hayatta kalsın.
    adlar.extend(n for (n,) in db.session.query(Client.name)
                 .filter(Client.deleted_at.is_(None), Client.name.isnot(None))
                 .order_by(Client.name).all())
    adlar.extend(u.name for u in UserRef.query.filter(UserRef.name.isnot(None)).all())

    # Tekrarı ele — Türkçe-duyarlı karşılaştır, yazılan hâli koru.
    gorulen, benzersiz = set(), []
    for ad in adlar:
        ad = ' '.join(str(ad).split())
        if not ad:
            continue
        k = ad.replace('I', 'ı').replace('İ', 'i').casefold()
        if k in gorulen:
            continue
        gorulen.add(k)
        benzersiz.append(ad)

    # Karakter sınırına kadar doldur; sınırı aşan adı YARIM ekleme (kırpılmış bir
    # marka adı modele yanlış ipucu verir).
    out, uzunluk = [], 0
    for ad in benzersiz:
        ek = len(ad) + 2
        if uzunluk + ek > VOCAB_MAX_CHARS:
            break
        out.append(ad)
        uzunluk += ek
    return ', '.join(out) + ('.' if out else '')


# --- sesli not ajanı (2026-08-09) -------------------------------------------

def voice_note_instruction(transcript, bugun=None):
    """Sesli not transkriptini yapılandırılmış nota çeviren claude talimatı.

    Bağlam (müşteri ve ekip listeleri) prompt'a ÖNCEDEN basılır — ajana DB
    erişimi verilmez. Gerekçe: `ai_claude` tool ve MCP'yi bilerek kapatıyor
    (`--strict-mcp-config` + `--disallowedTools`) çünkü `claude -p` proje sahibi'in
    abonelik oturumunda çalışıyor; tool açmak injection yüzeyini büyütürdü.
    Ajanın ihtiyacı olan tek şey bu iki eşleme tablosu.

    Transkript `wrap_untrusted` ile sarılır: kullanıcının kendi sesi de
    untrusted veridir (transkripte 'önceki talimatları unut' geçebilir)."""
    import ai_claude
    from datetime import date as _date

    from models import UserRef
    # PANEL_ROLES'u planning'den import ediyoruz (tekrar TANIMLAMIYORUZ): panoya
    # atanabilir rol kümesi TEK yerde yaşamalı. 2026-08-09 canlı bulgusu — bu
    # süzgeç YOKTU: 'pending' rolündeki kullanıcılar (panosu olmayan, /planlama'nın
    # reddettiği hesaplar) ajana aday olarak veriliyordu; `planning._apply_item`
    # assignee_sub'ı TÜM users_ref'e karşı doğruladığı için yazma başarılı oluyor
    # ama atanan kişi göreve HİÇBİR ZAMAN erişemiyordu (aynı isimde iki kullanıcı,
    # pending/designer rol karışıklığı). İki liste ayrışırsa hata geri gelir.
    from planning import PANEL_ROLES

    bugun = bugun or _date.today().isoformat()
    musteriler = (db.session.query(Client.id, Client.name)
                  .filter(Client.deleted_at.is_(None), Client.name.isnot(None))
                  .order_by(Client.name).all())
    # order_by(name): prompt sırası deterministik olsun (ekip listesi büyüdükçe
    # aynı transkript aynı JSON metnini üretsin — test/ölçüm tekrarlanabilir kalsın).
    ekip = [(u.sub, u.name) for u in UserRef.query
            .filter(UserRef.name.isnot(None), UserRef.role.in_(PANEL_ROLES))
            .order_by(UserRef.name).all()]

    m_satir = '\n'.join(f'  {cid}: {ad}' for cid, ad in musteriler) or '  (müşteri yok)'
    e_satir = '\n'.join(f'  {sub}: {ad}' for sub, ad in ekip) or '  (kullanıcı yok)'

    return (
        'Aşağıdaki sesli not transkriptini yapılandırılmış bir nota dönüştür. '
        'ÇIKTIN YALNIZ şu anahtarlarla TEK bir JSON nesnesi olsun (başka hiçbir '
        'metin yazma, kod çiti kullanma):\n'
        '{"baslik": "...", "ozet": "...", "maddeler": ["..."], '
        '"gorevler": [{"metin": "...", "client_id": null, "assignee_sub": null, '
        '"due_date": null}]}\n\n'
        'Kurallar:\n'
        '- Hepsi TÜRKÇE yaz.\n'
        '- "baslik": notu tek satırda özetleyen kısa bir ad.\n'
        '- "ozet": 1-3 cümle.\n'
        '- "maddeler": konuşulan başlıklar; transkriptte OLMAYAN madde ekleme.\n'
        '- "gorevler": YALNIZ yapılacak iş olarak söylenenler. Konuşmada görev '
        'yoksa boş liste bırak — görev UYDURMA.\n'
        '- "client_id" ve "assignee_sub" YALNIZ aşağıdaki listelerden seçilir; '
        'emin değilsen null bırak. Listede olmayan bir ad geçiyorsa null.\n'
        f'- "due_date" YYYY-MM-DD biçiminde. Bugün {bugun}. "yarın", "önümüzdeki '
        'salı" gibi göreli ifadeleri bu tarihe göre çöz; çözemiyorsan null.\n\n'
        f'MÜŞTERİLER (client_id: ad)\n{m_satir}\n\n'
        f'EKİP (assignee_sub: ad)\n{e_satir}\n'
        + ai_claude.wrap_untrusted('TRANSKRİPT', transcript))


def _marka_baglami(client):
    """`[MARKA BAĞLAMI]` bloğunun satırları — iki görsel prompt kurucusu da bunu kullanır.

    GÜVENİLİR veri (bizim DB'miz) → `wrap_untrusted` ile sarılmaz."""
    prof = client.brand_profile or {}
    satirlar = ['[MARKA BAĞLAMI]',
                f'Müşteri: {client.name} · Sektör: {client.sector or "belirtilmemiş"}']
    if prof.get('brand_voice'):
        satirlar.append(f'Marka sesi: {prof["brand_voice"]}')
    if prof.get('target_audience'):
        satirlar.append(f'Hedef kitle: {prof["target_audience"]}')
    if prof.get('forbidden'):
        satirlar.append(f'Kaçınılacaklar: {prof["forbidden"]}')
    return satirlar


def codex_image_instruction(client, brief, user_prompt, aspect_ratio):
    """Codex `$imagegen` için deterministik resolved prompt (bkz. spec §5).

    Marka bağlamı GÜVENİLİR (bizim DB'miz), brief ve kullanıcı istemi GÜVENİLMEZ:
    ikincisi `wrap_untrusted` ile sarılır ve zorunlu kısıtlar bloğu ondan SONRA gelir —
    sıra önemlidir, sarmalanmış metnin içindeki "önceki talimatları unut" denemesi
    kısıtları ezemesin.

    Kırpma sınırları (brief 4000, istem 2000) prompt'un şişip üretimi bozmasını
    engeller; `$imagegen` uzun bağlamda talimatın sonunu kaçırıyor."""
    import ai_claude
    from models_imagegen import ASPECTS
    w, h = ASPECTS.get(aspect_ratio, ASPECTS['social_post_4_5'])
    parcalar = ['$imagegen', ''] + _marka_baglami(client)
    if brief is not None and (brief.raw_md or '').strip():
        parcalar += ['', '[İÇERİK BRIEFİ]',
                     ai_claude.wrap_untrusted('BRIEF', (brief.raw_md or '')[:4000])]
    parcalar += ['', '[GÖRSEL TALİMATI]',
                 ai_claude.wrap_untrusted('İSTEM', (user_prompt or '')[:2000]),
                 '', '[ZORUNLU KISITLAR]',
                 f'- Görselin en-boy ölçüsü tam olarak {w}x{h} piksel olsun.',
                 '- Yalnızca bulunduğun dizine yaz; tek bir dosya üret: output.png',
                 '- Başka hiçbir dosyaya dokunma, başka dizine yazma.',
                 '- İstenmedikçe logo ya da metin ekleme.',
                 '- İşin sonunda yalnızca dosya yolunu ve kısa bir üretim özeti bildir.']
    return '\n'.join(parcalar)


def image_json_instruction(client, idea):
    """Brief fikrini İngilizce yapılandırılmış görsel JSON'una çeviren claude talimatı.

    Bu SADECE biçim dönüşümü DEĞİL: brief'in susduğu görsel-dil alanlarını (kamera,
    kompozisyon, ışık, teknik) marka bağlamından türetir. Yukarıdaki
    `image_prompt_json_instruction`'ın "istemde olmayanı uydurma" kuralı buraya
    DEVRALINMAZ — devralınsaydı şemanın yarısı boş çıkardı (brief
    `camera.depth_of_field` demiyor).

    Varyant parametresi YOK: çeviri fikir başına bir kez yapılır, iki varyant aynı
    JSON'u paylaşır (spec §3-§4). `clean` farkı kod tarafında uygulanır.

    Fikir alanları UNTRUSTED → `wrap_untrusted` ile sarılır; kurallar ondan ÖNCE
    yazılır ki sarmalanmış metindeki 'önceki talimatları unut' denemesi onları ezmesin.
    """
    import ai_claude
    import imagegen_prompt as ip

    prof = client.brand_profile or {}
    marka = [f'Brand: {client.name}',
             f'Sector: {client.sector or "unspecified"}']
    if prof.get('brand_voice'):
        marka.append(f'Brand voice: {ip.url_temizle(prof["brand_voice"])}')
    if prof.get('target_audience'):
        marka.append(f'Target audience: {ip.url_temizle(prof["target_audience"])}')
    if prof.get('forbidden'):
        marka.append(f'Avoid: {ip.url_temizle(str(prof["forbidden"]))}')

    # Fikirden yalnız prompt'a girecek alanlar; hepsi URL'den arındırılır.
    # `pinterest` BİLEREK YOK — link gönderilmez.
    fikir = []
    for etiket, anahtar in (('Title', 'başlık'), ('Topic', 'içerik'),
                            ('Visual style', 'görsel_tarz'),
                            ('Shot type', 'çekim_tipi')):
        v = ip.url_temizle(str(idea.get(anahtar) or ''))
        if v:
            fikir.append(f'{etiket}: {v}')
    for etiket, anahtar in (('Composition plan', 'plan'),
                            ('Required elements', 'görsel_gerekli')):
        ham = idea.get(anahtar)
        if isinstance(ham, list):
            satirlar = [ip.url_temizle(str(x)) for x in ham]
            satirlar = [s for s in satirlar if s]
            if satirlar:
                fikir.append(f'{etiket}:')
                fikir += [f'- {s}' for s in satirlar]

    return (
        'Aşağıdaki marka bağlamı ve içerik fikrinden, görsel üretim modeline verilecek '
        'YAPILANDIRILMIŞ bir görsel tarifi üret. ÇIKTIN YALNIZ TEK bir JSON nesnesi '
        'olsun (başka hiçbir metin yazma, kod çiti kullanma):\n'
        '{"prompt": "...", "subject": "...", "environment": "...", "style": "...", '
        '"lighting": "...", "camera": {"angle": "...", "distance": "...", '
        '"depth_of_field": "...", "focus": "..."}, "composition": {"framing": "...", '
        '"subject_placement": "...", "foreground": "...", "background": "...", '
        '"negative_space": "..."}, "mood": "...", "color_palette": ["#..."], '
        '"technical": {"render_type": "...", "post_processing": "..."}, '
        '"text_elements": ["..."]}\n\n'
        'KURALLAR\n'
        '1. TÜM değerler İNGİLİZCE yazılır. TEK İSTİSNA: "text_elements" — görselde '
        'yazacak metin TÜRKÇE ve HARFİ HARFİNE korunur, çevrilmez, düzeltilmez.\n'
        '2. DOKUNULMAZ (fikirden birebir aktarılır, değiştirilmez/kısaltılmaz): '
        'renk paleti hex kodları → "color_palette"; başlık → "text_elements"; '
        '"Required elements" maddeleri; fikrin konusu ve mesajı.\n'
        '3. TÜRETİLİR (fikir susuyorsa marka bağlamından çıkarılır): "camera.*", '
        '"composition.*", "lighting", "mood", "technical.*", "environment". Bunları '
        'profesyonel bir görsel yönetmeni gibi, görsel tarza ve sektöre SADIK doldur.\n'
        '4. YASAK: fikirde geçmeyen marka öğesi, olmayan ürün/hizmet, yeni mesaj ya da '
        'iddia ekleme. Türetme YALNIZ görsel dil alanlarında yapılır, içerikte değil.\n'
        '5. Bilgi yoksa ve türetilemiyorsa anahtarı ATLA (boş string yazma).\n'
        '6. Çıktıda URL/link BULUNMAZ.\n\n'
        'MARKA BAĞLAMI\n' + '\n'.join(marka) + '\n'
        + ai_claude.wrap_untrusted('İÇERİK FİKRİ', '\n'.join(fikir)[:3000]))
