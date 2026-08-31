"""Caption generation — the hardened shared runner (ai_claude.run; subscription auth, worker runs in the project owner's context).

The web (svc-agency) enqueues the job; ai_worker (project owner) calls this
module from the caption handler. NO API key. Context: client name/sector +
kind + (if any) the weekly brief intro + card note + (optional) brand guide
(ai_context.client_profile) + recently used captions + global rules.
Carrying the brand context into build_prompt is not the worker's job (see
Phase 1a Task 2).
"""
import os

import ai_claude

KIND_TR = {'post': 'Instagram post', 'story': 'Instagram story',
           'video': 'Instagram reels/video', 'linkedin': 'LinkedIn gönderisi'}

# Optional style-skill trigger — appended at the VERY END of the prompt.
#
# WHY: the upstream agency wires a locally installed Claude Code skill into
# caption generation (theirs naturalizes Turkish copy while preserving facts;
# they A/B-measured it on real posts: it eliminated the boilerplate "As
# <brand>, we…" openers entirely and improved factual-reference retention in a
# regulated-sector client from 1/3 to 3/3 alternatives, at ~1.8× tokens and
# ~+25s per caption). In kotar this is opt-in configuration: set
# CAPTION_STYLE_SKILL to the name of a skill installed in the worker's Claude
# environment to enable it; unset (the default) leaves the prompt untouched.
# Also measured upstream and rejected: asking the model to read the skill's
# references/*.md too — cost rose to 2.9× while factual references DROPPED.
#
# The second sentence is NON-NEGOTIABLE: a skill may impose its own delivery
# format (text + change notes), while this prompt is bound to the
# [[CAPTION]]/[[HASHTAGS]] contract — if that breaks, rationale prose ends up
# in the caption field (this exact failure class happened once in production
# upstream). The trigger therefore comes AFTER the format instructions and
# restates that the format must not change. If the named skill isn't
# installed, the trigger is harmless: the model ignores it.
_SKILL_TRIGGER = (
    "Use the `{skill}` skill while producing these captions.\n"
    "The response format is unaffected: output only the [[CAPTION]] / "
    "[[HASHTAGS]] blocks requested above; do not add edit notes, rationale, "
    "or any other commentary.")


def style_skill_trigger():
    """The configured trigger text, or None when CAPTION_STYLE_SKILL is unset.

    Reads the env at call time (not import time) so tests and late dotenv
    loading both behave."""
    skill = (os.getenv('CAPTION_STYLE_SKILL') or '').strip()
    return _SKILL_TRIGGER.format(skill=skill) if skill else None


def build_prompt(client_name, sector, kind, brief_intro=None, note=None, transcript=None, images=False,
                  brand_profile=None, recent_captions=None, global_rules=None, settings=None,
                  special_days=None, feedback=None, previous_caption=None):
    # settings (Phase 1b): generation-time settings. If None, the old
    # behavior is preserved EXACTLY (backward compat + exact-match test).
    # s is an empty dict -> use_brief defaults to True.
    s = settings or {}
    use_brief = s.get('use_brief', True)
    lines = [
        f"Sen deneyimli bir sosyal medya içerik uzmanısın. {KIND_TR.get(kind, kind)} "
        "için Türkçe caption üret.",
        f"Müşteri: {client_name}" + (f" · Sektör: {sector}" if sector else ""),
    ]
    if brief_intro and use_brief:  # use_brief=False -> brief intro is NOT included in the prompt
        lines.append(f"Bu haftanın brief'i: {brief_intro}")
    if note:
        lines.append(f"Ek not: {note}")
    if special_days:  # that week's approved special days (the 07 approval
                       # gate — a draft never arrives here, guaranteed by
                       # the ai_context.week_context caller); trusted data, no delimiter needed.
        madde = "\n".join(
            f"- {d.get('day_name')}" + (f": {d['description']}" if d.get('description') else '')
            for d in special_days)
        lines.append("Bu haftanın özel günleri (uygunsa birine değin):\n" + madde)
    if transcript:  # untrusted (media) -> frame it with a delimiter
        lines.append("Videonun ses transkripti:" + ai_claude.wrap_untrusted("TRANSKRİPT", transcript))
    if images:
        lines.append(
            "Ekteki görsel(ler) bu paylaşımın ASIL içeriğidir. Caption ÖNCELİKLE "
            "görselde fiilen görünene dayanmalı (mekan, ürün, sahne, atmosfer). "
            "Brief ve not yalnızca yardımcı bağlamdır; görselle çelişiyorsa görsel "
            "önceliklidir — görselde olmayan bir temayı caption'a dayatma.")
    if feedback:  # regenerate: user (management) feedback — treated as an instruction
        lines.append("Kullanıcı geri bildirimi (caption'ı bu yönde düzelt): " + feedback)
    if previous_caption:  # disliked previous caption — untrusted (AI output), delimited
        lines.append("Kullanıcı şu önceki caption'ı BEĞENMEDİ — TEKRARLAMA, belirgin biçimde "
                     "FARKLI yaz:" + ai_claude.wrap_untrusted("BEĞENİLMEYEN CAPTION", previous_caption))
    if global_rules:
        lines.append(f"Uyulacak global kurallar:\n{global_rules}")
    if brand_profile:
        rehber = []
        # Tone generation-time override: if settings.tone is given, it's used
        # instead of brand_voice (no dual-source conflict — brand_voice is
        # the default, tone overrides it).
        voice = s.get('tone') or brand_profile.get('brand_voice')
        if voice:
            rehber.append(f"Marka sesi: {voice}")
        if brand_profile.get('target_audience'):
            rehber.append(f"Hedef kitle: {brand_profile['target_audience']}")
        fb = brand_profile.get('forbidden')
        if fb:
            # forbidden's canonical shape is a LIST (vault-sema-taslak §2.1);
            # a string can also come through -> handle it robustly
            rehber.append("YASAKLI (kullanma): "
                          + ("; ".join(str(x) for x in fb) if isinstance(fb, (list, tuple)) else str(fb)))
        if brand_profile.get('cta'):
            rehber.append(f"CTA: {brand_profile['cta']}")
        if brand_profile.get('guide_md'):
            rehber.append(brand_profile['guide_md'])
        if rehber:
            lines.append("Marka rehberi:\n" + "\n".join(rehber))
    if recent_captions:  # untrusted (past user data) -> frame it with a delimiter
        madde = "\n".join(f"- {c}" for c in recent_captions)
        lines.append(
            "Bu müşteride daha önce kullanılmış caption'lar — TEKRARLAMA, farklı "
            "açı bul:" + ai_claude.wrap_untrusted("GEÇMİŞ CAPTIONLAR", madde))
    if settings:  # Phase 1b generation-time settings — emoji/character limits (lang+hashtags are in the format below)
        ek = []
        emoji_limit = s.get('emoji_limit')
        if emoji_limit is not None:
            ek.append(f"Caption başına EN FAZLA {emoji_limit} emoji kullan.")
        char_limit = s.get('char_limit')
        if char_limit is not None:
            ek.append(f"Her caption alternatifi EN FAZLA {char_limit} karakter olsun.")
        if ek:
            lines.append("Üretim ayarları:\n" + "\n".join(ek))
    # Language + hashtag count determine the format. Default even without
    # settings: Turkish, 14 hashtags (2026-07-18 project owner's decision —
    # English ONLY if lang=EN/TR+EN is selected via settings).
    lang = str(s.get('lang') or 'TR').upper()
    htag = s.get('hashtag_count') or 14
    yapi = (
        "Her caption alternatifini şu düzende yaz — HER paragraf arasında BİR BOŞ SATIR:\n"
        "1) Vurucu TEK açılış cümlesi (istersen 1 emoji).\n"
        "2) Konuyu açan 2-3 cümlelik gövde (uygun yerde emoji).\n"
        "3) Kapanış: varsa CTA / adres / web sitesi + kapanış emojisi. Bu bilgileri "
        "YALNIZCA sana verilen marka bağlamında (marka rehberi/CTA) açıkça geçiyorsa "
        "kullan; YOKSA UYDURMA, o kısmı atla.\n")
    if lang in ('EN', 'İNGİLİZCE', 'INGILIZCE'):
        dil = "Caption'ları YALNIZCA İngilizce yaz; Türkçe metin veya '---' EKLEME.\n"
        alt0 = "<birinci alternatif — yukarıdaki 3 paragraf düzeninde, İngilizce>"
    elif lang in ('TR+EN', 'TREN', 'İKİSİ', 'IKISI', 'İKİ DİLLİ', 'IKI DILLI'):
        dil = ("Her alternatifi İKİ DİLLİ yaz: önce Türkçe (3 paragraf), sonra tek başına "
               "bir satırda üç tire (---), sonra aynı metnin İngilizcesi (aynı 3 paragraf).\n")
        alt0 = "<birinci alternatif — Türkçe 3 paragraf, ---, İngilizce 3 paragraf>"
    else:  # TR — default
        dil = ("Caption'ları YALNIZCA Türkçe yaz; İngilizce çeviri veya '---' ayıracı "
               "EKLEME.\n")
        alt0 = "<birinci alternatif — yukarıdaki 3 paragraf düzeninde, Türkçe>"
    lines.append(
        yapi + dil +
        "Doğal ve akıcı, marka sesine uygun, ölçülü emoji; klişe ve doldurmadan kaçın.\n"
        "BİRBİRİNDEN FARKLI 3 alternatif üret. Ayrıca 3 alternatifin HEPSİNE uyan TEK "
        f"hashtag seti: tam {htag} adet, konuyla ALAKALI, boşlukla ayrılmış "
        "(hashtag'i alternatiflerin İÇİNE koyma).\n"
        "Yanıtı TAM olarak şu biçimde ver, başka HİÇBİR açıklama yazma "
        "(her alternatif çok satırlıdır):\n"
        "[[CAPTION]]\n" + alt0 + "\n"
        "[[CAPTION]]\n<ikinci alternatif>\n"
        "[[CAPTION]]\n<üçüncü alternatif>\n"
        "[[HASHTAGS]]\n<hashtag seti>")
    trigger = style_skill_trigger()
    if trigger:
        lines.append(trigger)  # last — AFTER the format contract (see _SKILL_TRIGGER's note)
    return "\n".join(lines)


def run_claude(prompt, image_paths=None, timeout=240, model=None):
    # Route to the single hardened runner (tool restriction + strict MCP live in ai_claude).
    return ai_claude.run(prompt, image_paths=image_paths, model=model, timeout=timeout)


def parse(output):
    """Return (captions[list], hashtags). Splits on [[CAPTION]]/[[HASHTAGS]]
    markers — captions can be multi-line (bilingual)."""
    captions, hashtags, cur, mode = [], '', [], None
    for line in output.splitlines():
        tag = line.strip().upper().replace(' ', '')
        if tag == '[[CAPTION]]':
            if mode == 'cap' and cur:
                captions.append('\n'.join(cur).strip())
            cur, mode = [], 'cap'
        elif tag == '[[HASHTAGS]]':
            if mode == 'cap' and cur:
                captions.append('\n'.join(cur).strip())
            cur, mode = [], 'tags'
        elif mode is not None:  # ignore any preamble before the first marker
            cur.append(line)
    if mode == 'cap' and cur:
        captions.append('\n'.join(cur).strip())
    elif mode == 'tags':
        hashtags = '\n'.join(cur).strip()
    captions = [c for c in captions if c]
    if not captions:  # if the format didn't match at all, treat the whole output as one alternative
        captions = [output.strip()]
    return captions, hashtags


def generate(client_name, sector, kind, brief_intro=None, note=None, transcript=None, image_paths=None,
             brand_profile=None, recent_captions=None, global_rules=None, model=None, settings=None,
             special_days=None, feedback=None, previous_caption=None):
    # settings.model determines the per-job model (03 -> ai_claude.run(model=...)); otherwise the `model` argument.
    if settings and settings.get('model'):
        model = settings['model']
    prompt = build_prompt(client_name, sector, kind, brief_intro, note, transcript,
                          images=bool(image_paths), brand_profile=brand_profile,
                          recent_captions=recent_captions, global_rules=global_rules,
                          settings=settings, special_days=special_days,
                          feedback=feedback, previous_caption=previous_caption)
    # If model isn't given, call with the old signature (backward compat: so
    # calls/tests that monkeypatch run_claude without a model don't break);
    # if given, the per-job model flows through.
    if model is None:
        return parse(run_claude(prompt, image_paths=image_paths))
    return parse(run_claude(prompt, image_paths=image_paths, model=model))
