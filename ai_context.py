"""Shared context-reading service for AI flows (Phase 0 — Task 2).

The shared input of the 6 AI flows (caption, hashtag, brief summary etc.) is
gathered here in one place: client profile, global rules, past captions, week
context. Pure reads — no side effects, doesn't write to the DB. Today
`ai_worker.py`'s caption handler reads this information inline; wiring this module
into it is Phase 1 work.
"""
from datetime import date

from extensions import db
from models import AppSetting, Client
from models_sharing import Share, SpecialDayEvent

# Month (1-12) → Turkish season name.
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
    """Merges the client's basic info (name, sector) + brand_profile into a single dict.

    If the client isn't found or brand_profile is NULL/partial, missing fields
    return an empty string/list/dict — the caller doesn't blow up. `color_palette`
    (color palette), `content_mix` (content distribution: 3 photo+slogan / 1 reel /
    carousel) and `content_pillars` (content pillars — the steering text ideas are
    distributed against) are what the brief flow needs — an empty list/dict/str if
    not in the brand_profile JSON. `hashtags` (topic/brand/sector hashtag sets) and
    `ideas_per_week` (ideas per week, 5 if unset) are brief steering fields —
    brief_handler consumes `ideas_per_week` directly.
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
    """Shared caption/hashtag rules applying to all clients (edited from the panel)."""
    return AppSetting.get('caption_global_rules', '') or ''


# System default for caption generation settings (Phase 1b — step 09). All fields
# are optional; when `model=None` is given, ai_claude falls back to
# env/DEFAULT_MODEL (via the per-job model, 03).
CAPTION_SETTINGS_DEFAULTS = {
    'model': None,          # None → ai_claude.DEFAULT_MODEL / env CAPTION_MODEL
    'tone': None,           # None → derived from brand_profile.brand_voice (unless overridden)
    'emoji_limit': 3,       # reasonable emoji ceiling per caption
    'hashtag_count': 14,    # default hashtag count (2026-07-18 project owner decision)
    'lang': 'TR',           # 'TR' | 'EN' | 'TR+EN' (bilingual)
    'use_brief': False,     # True → brief intro is included in the prompt (off by default — 2026-07-18 project owner decision)
    'char_limit': None,     # None → no character-limit instruction
}


def resolve_caption_settings(client, payload_settings=None):
    """Resolves caption settings in layers: system default ⊕ client.caption_settings
    ⊕ payload_settings (rightmost overrides). Only known schema keys are returned
    (an unknown key doesn't leak into the result). `client` can be None (defaults
    are returned)."""
    resolved = dict(CAPTION_SETTINGS_DEFAULTS)
    cs = getattr(client, 'caption_settings', None) if client is not None else None
    if isinstance(cs, dict):
        resolved.update({k: v for k, v in cs.items() if k in CAPTION_SETTINGS_DEFAULTS})
    if isinstance(payload_settings, dict):
        resolved.update({k: v for k, v in payload_settings.items() if k in CAPTION_SETTINGS_DEFAULTS})
    return resolved


def recent_captions(client_id, n=10):
    """The client's last N non-empty caption texts (newest first).

    Source is `Share.caption_text` — `CaptionHistory` isn't used in this phase
    (YAGNI, a dead table); published/selected captions already live in Share.
    Soft-deleted (`deleted_at` set) Shares are excluded — codebase convention (see
    review.py, sharing.py: `Share.deleted_at.is_(None)` everywhere).
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
    """Active special days falling in that ISO week (global + client-specific, all
    of them) + season.

    Returns a brief-ready structure: `{season, special_days, week_iso}` —
    `week_iso` echoes the input as-is, so the brief flow doesn't have to carry it
    separately. The signature only takes `week_iso` — it's client-independent;
    sector/client-specific filtering is the caller's job. The season is determined
    by the month of the week's ISO reference day, Thursday. If `week_iso` can't be
    parsed, an empty result is returned (doesn't blow up).
    """
    bounds = _week_bounds(week_iso)
    if bounds is None:
        return {'special_days': [], 'season': '', 'week_iso': week_iso}
    monday, sunday, thursday = bounds

    special_days = []
    # Approval gate: the downstream AI context reads ONLY approved special days —
    # a draft (AI-generated, unapproved) event must not leak into the prompt/to the client.
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
    """'YYYY-Www' → (Monday, Sunday, Thursday) date objects; None if unparseable."""
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
    """The calendar range (start, end) a SpecialDayEvent covers; missing/broken data → None."""
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


# --- AI image generation: prompt transformation (2026-07-20) ---

def image_prompt_json_instruction(user_prompt):
    """Claude instruction that translates an image-generation prompt into English and
    turns it into structured JSON. Image models (Mystic/nano banana/gpt...) give
    noticeably better results with English + field-based JSON prompts. Output is
    ONLY a single JSON object — the panel's 'Translate to English JSON' button puts
    it into the textarea; the worker's refine step sends it to generation.
    Untrusted user text is wrapped with a delimiter."""
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


# --- transcript vocabulary (2026-08-08) --------------------------------------

# Proper names to pass into Whisper's `initial_prompt`. Verified by measurement:
# without the vocabulary "Molo Pantarya", with the vocabulary "Mall of Antalya"
# (3/3 reproducible, cost +0.3 sec). Scaling up the model does NOT fix this — the
# model doesn't know the brand; large-v3-turbo and medium both mangled the same
# name differently.
#
# Whisper consumes `initial_prompt` as a previous-context token: faster-whisper
# truncates it to 224 tokens. Turkish proper names are ~2-4 tokens → roughly 60-70
# names fit. We enforce the limit in CHARACTERS (to avoid pulling the tokenizer in
# here) and put client names FIRST: if truncation happens, team names should drop,
# brand names should survive — that's where the real payoff is.
VOCAB_MAX_CHARS = 700

# Agency-specific fixed terms the model can never learn from any client record.
_SABIT_TERIMLER = ('Kotar',)


def transcript_vocabulary(extra=None):
    """Proper-name vocabulary to give Whisper — a single line, comma-separated.

    Source: active client names + panel user names + agency constants.
    `extra`: names the caller wants added (e.g. so that video's client comes first).

    Can return empty (if the DB is empty) — the caller should treat that like
    `initial_prompt=None`."""
    from models import UserRef

    adlar = []
    if extra:
        adlar.extend(str(x).strip() for x in extra if str(x or '').strip())
    adlar.extend(_SABIT_TERIMLER)
    # Active clients first: if truncation happens, brand names should survive.
    adlar.extend(n for (n,) in db.session.query(Client.name)
                 .filter(Client.deleted_at.is_(None), Client.name.isnot(None))
                 .order_by(Client.name).all())
    adlar.extend(u.name for u in UserRef.query.filter(UserRef.name.isnot(None)).all())

    # Dedupe — compare Turkish-aware, keep the as-written form.
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

    # Fill up to the character limit; don't add a name HALFWAY past the limit (a
    # truncated brand name gives the model a wrong hint).
    out, uzunluk = [], 0
    for ad in benzersiz:
        ek = len(ad) + 2
        if uzunluk + ek > VOCAB_MAX_CHARS:
            break
        out.append(ad)
        uzunluk += ek
    return ', '.join(out) + ('.' if out else '')


# --- voice note agent (2026-08-09) -------------------------------------------

def voice_note_instruction(transcript, bugun=None):
    """Claude instruction that turns a voice note transcript into a structured note.

    Context (client and team lists) is baked into the prompt UPFRONT — the agent
    isn't given DB access. Rationale: `ai_claude` deliberately disables tools and
    MCP (`--strict-mcp-config` + `--disallowedTools`) because `claude -p` runs
    under the project owner's subscription session; opening up tools would widen
    the injection surface. The only thing the agent needs is these two lookup
    tables.

    The transcript is wrapped with `wrap_untrusted`: the user's own voice is also
    untrusted data (the transcript could contain "forget previous instructions")."""
    import ai_claude
    from datetime import date as _date

    from models import UserRef
    # We import PANEL_ROLES from planning (we do NOT redefine it): the set of
    # roles a board can be assigned to must live in ONE place. 2026-08-09 live
    # finding — this filter was MISSING: users with the 'pending' role (accounts
    # with no board, rejected by /planlama) were being offered to the agent as
    # candidates; since `planning._apply_item` validates assignee_sub against ALL
    # of users_ref, the write succeeded but the assigned person could NEVER access
    # the task (two users with the same name, pending/designer role confusion). If
    # the two lists diverge, the error comes back.
    from planning import PANEL_ROLES

    bugun = bugun or _date.today().isoformat()
    musteriler = (db.session.query(Client.id, Client.name)
                  .filter(Client.deleted_at.is_(None), Client.name.isnot(None))
                  .order_by(Client.name).all())
    # order_by(name): keep the prompt order deterministic (as the team list grows,
    # the same transcript should produce the same JSON text — tests/measurements
    # stay reproducible).
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
    """Lines of the `[MARKA BAĞLAMI]` block — both image prompt builders use this.

    TRUSTED data (our own DB) → not wrapped with `wrap_untrusted`."""
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
    """Deterministic resolved prompt for Codex `$imagegen` (see spec §5).

    The brand context is TRUSTED (our own DB), the brief and the user prompt are
    UNTRUSTED: the latter are wrapped with `wrap_untrusted` and the mandatory
    constraints block comes AFTER them — order matters, so that a "forget previous
    instructions" attempt inside the wrapped text can't override the constraints.

    The truncation limits (brief 4000, prompt 2000) keep the prompt from bloating
    and breaking generation; `$imagegen` misses the end of the instruction in a
    long context."""
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
    """Claude instruction that translates a brief idea into structured English image JSON.

    This is NOT just a format conversion: it derives the visual-language fields the
    brief stays silent on (camera, composition, lighting, technical) from the brand
    context. The "don't invent what's not in the prompt" rule from
    `image_prompt_json_instruction` above does NOT carry over here — if it did,
    half the schema would come out empty (the brief doesn't say
    `camera.depth_of_field`).

    There's NO variant parameter: the translation is done once per idea, both
    variants share the same JSON (spec §3-§4). The `clean` difference is applied
    on the code side.

    Idea fields are UNTRUSTED → wrapped with `wrap_untrusted`; the rules are written
    BEFORE them so a "forget previous instructions" attempt inside the wrapped text
    can't override them.
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

    # Only the fields from the idea that go into the prompt; all are stripped of URLs.
    # `pinterest` is DELIBERATELY MISSING — no link is sent.
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
