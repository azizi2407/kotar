"""Caption üretimi — sertleştirilmiş ortak runner (ai_claude.run; abonelik auth, worker proje sahibi bağlamında).

Web (svc-agency) job atar; ai_worker (proje sahibi) caption handler'ından bu modülü çağırır. API key YOK.
Bağlam: müşteri adı/sektör + kind + (varsa) haftalık brief intro + kart notu +
(opsiyonel) marka rehberi (ai_context.client_profile) + son kullanılan caption'lar +
global kurallar. Marka bağlamını build_prompt'a taşımak worker'ın işi değil (bkz. Faz 1a Task 2).
"""
import ai_claude

KIND_TR = {'post': 'Instagram post', 'story': 'Instagram story',
           'video': 'Instagram reels/video', 'linkedin': 'LinkedIn gönderisi'}


def build_prompt(client_name, sector, kind, brief_intro=None, note=None, transcript=None, images=False,
                  brand_profile=None, recent_captions=None, global_rules=None, settings=None,
                  special_days=None, feedback=None, previous_caption=None):
    # settings (Faz 1b): üret-anı ayarları. None ise eski davranış BİREBİR korunur
    # (geriye uyum + exact-match test). s boş dict → use_brief varsayılanı True.
    s = settings or {}
    use_brief = s.get('use_brief', True)
    lines = [
        f"Sen deneyimli bir sosyal medya içerik uzmanısın. {KIND_TR.get(kind, kind)} "
        "için Türkçe caption üret.",
        f"Müşteri: {client_name}" + (f" · Sektör: {sector}" if sector else ""),
    ]
    if brief_intro and use_brief:  # use_brief=False → brief intro prompt'a KATILMAZ
        lines.append(f"Bu haftanın brief'i: {brief_intro}")
    if note:
        lines.append(f"Ek not: {note}")
    if special_days:  # o haftanın onaylı özel günleri (07 onay kapısı — draft asla gelmez,
                       # ai_context.week_context çağıranı sağlar); güvenilir veri, delimiter gerekmez.
        madde = "\n".join(
            f"- {d.get('day_name')}" + (f": {d['description']}" if d.get('description') else '')
            for d in special_days)
        lines.append("Bu haftanın özel günleri (uygunsa birine değin):\n" + madde)
    if transcript:  # untrusted (medya) → delimiter'la çerçevele
        lines.append("Videonun ses transkripti:" + ai_claude.wrap_untrusted("TRANSKRİPT", transcript))
    if images:
        lines.append(
            "Ekteki görsel(ler) bu paylaşımın ASIL içeriğidir. Caption ÖNCELİKLE "
            "görselde fiilen görünene dayanmalı (mekan, ürün, sahne, atmosfer). "
            "Brief ve not yalnızca yardımcı bağlamdır; görselle çelişiyorsa görsel "
            "önceliklidir — görselde olmayan bir temayı caption'a dayatma.")
    if feedback:  # yeniden üret: kullanıcı (management) geri bildirimi — talimat konumunda
        lines.append("Kullanıcı geri bildirimi (caption'ı bu yönde düzelt): " + feedback)
    if previous_caption:  # beğenilmeyen önceki caption — untrusted (AI çıktısı), delimiter'lı
        lines.append("Kullanıcı şu önceki caption'ı BEĞENMEDİ — TEKRARLAMA, belirgin biçimde "
                     "FARKLI yaz:" + ai_claude.wrap_untrusted("BEĞENİLMEYEN CAPTION", previous_caption))
    if global_rules:
        lines.append(f"Uyulacak global kurallar:\n{global_rules}")
    if brand_profile:
        rehber = []
        # Ton üret-anı override: settings.tone verilirse brand_voice yerine o kullanılır
        # (çift kaynak çakışması yok — brand_voice varsayılan, ton onu değiştirir).
        voice = s.get('tone') or brand_profile.get('brand_voice')
        if voice:
            rehber.append(f"Marka sesi: {voice}")
        if brand_profile.get('target_audience'):
            rehber.append(f"Hedef kitle: {brand_profile['target_audience']}")
        fb = brand_profile.get('forbidden')
        if fb:
            # forbidden kanonik LİSTE (vault-sema-taslak §2.1); string de gelebilir → dayanıklı
            rehber.append("YASAKLI (kullanma): "
                          + ("; ".join(str(x) for x in fb) if isinstance(fb, (list, tuple)) else str(fb)))
        if brand_profile.get('cta'):
            rehber.append(f"CTA: {brand_profile['cta']}")
        if brand_profile.get('guide_md'):
            rehber.append(brand_profile['guide_md'])
        if rehber:
            lines.append("Marka rehberi:\n" + "\n".join(rehber))
    if recent_captions:  # untrusted (geçmiş kullanıcı verisi) → delimiter'la çerçevele
        madde = "\n".join(f"- {c}" for c in recent_captions)
        lines.append(
            "Bu müşteride daha önce kullanılmış caption'lar — TEKRARLAMA, farklı "
            "açı bul:" + ai_claude.wrap_untrusted("GEÇMİŞ CAPTIONLAR", madde))
    if settings:  # Faz 1b üret-anı ayarları — emoji/karakter sınırları (dil+hashtag aşağıdaki düzende)
        ek = []
        emoji_limit = s.get('emoji_limit')
        if emoji_limit is not None:
            ek.append(f"Caption başına EN FAZLA {emoji_limit} emoji kullan.")
        char_limit = s.get('char_limit')
        if char_limit is not None:
            ek.append(f"Her caption alternatifi EN FAZLA {char_limit} karakter olsun.")
        if ek:
            lines.append("Üretim ayarları:\n" + "\n".join(ek))
    # Dil + hashtag sayısı düzeni belirler. settings verilmese de varsayılan: Türkçe, 14 hashtag
    # (2026-07-18 proje sahibi kararı — İngilizce YALNIZ ayardan lang=EN/TR+EN seçilirse).
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
    else:  # TR — varsayılan
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
    return "\n".join(lines)


def run_claude(prompt, image_paths=None, timeout=240, model=None):
    # Tek sertleştirilmiş runner'a yönlendir (tool kısıtı + strict MCP ai_claude'da).
    return ai_claude.run(prompt, image_paths=image_paths, model=model, timeout=timeout)


def parse(output):
    """(captions[list], hashtags) döndür. [[CAPTION]]/[[HASHTAGS]] marker'larıyla
    bölütle — caption'lar çok satırlı (iki dilli) olabilir."""
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
        elif mode is not None:  # marker öncesi preamble'ı yoksay
            cur.append(line)
    if mode == 'cap' and cur:
        captions.append('\n'.join(cur).strip())
    elif mode == 'tags':
        hashtags = '\n'.join(cur).strip()
    captions = [c for c in captions if c]
    if not captions:  # biçim hiç tutmadıysa tüm çıktıyı tek alternatif say
        captions = [output.strip()]
    return captions, hashtags


def generate(client_name, sector, kind, brief_intro=None, note=None, transcript=None, image_paths=None,
             brand_profile=None, recent_captions=None, global_rules=None, model=None, settings=None,
             special_days=None, feedback=None, previous_caption=None):
    # settings.model per-job model'i belirler (03 → ai_claude.run(model=...)); yoksa `model` argümanı.
    if settings and settings.get('model'):
        model = settings['model']
    prompt = build_prompt(client_name, sector, kind, brief_intro, note, transcript,
                          images=bool(image_paths), brand_profile=brand_profile,
                          recent_captions=recent_captions, global_rules=global_rules,
                          settings=settings, special_days=special_days,
                          feedback=feedback, previous_caption=previous_caption)
    # model verilmezse eski imzayla çağır (geriye-uyum: run_claude'u model'siz
    # monkeypatch'leyen çağrılar/testler kırılmasın); verilirse per-job model akıtılır.
    if model is None:
        return parse(run_claude(prompt, image_paths=image_paths))
    return parse(run_claude(prompt, image_paths=image_paths, model=model))
