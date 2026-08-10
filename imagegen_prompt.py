"""Görsel promptunun JSON şeması, doğrulaması ve Codex metnine dönüşümü (2026-08-10).

Neden ayrı modül: şema doğrulaması bir GÜVENLİK katmanıdır ve HTTP'siz, DB'siz test
edilebilmelidir. Brief untrusted veridir; `claude -p` çeviri adımı bir enjeksiyon
fırsatıdır ve şemaya sığmayan hiçbir şey Codex'e geçmemelidir.
"""
import json
import re

MAX_VALUE_LEN = 400          # tek bir alanın prompt'u şişirmesini engeller

# Üst düzey string alanlar.
_STR_FIELDS = ('prompt', 'subject', 'environment', 'style', 'lighting', 'mood')
# String listesi alanlar.
_LIST_FIELDS = ('color_palette', 'text_elements')
# İç dict alanları ve İZİNLİ iç anahtarları (beyaz liste).
_DICT_FIELDS = {
    'camera': ('angle', 'distance', 'depth_of_field', 'focus'),
    'composition': ('framing', 'subject_placement', 'foreground', 'background',
                    'negative_space'),
    'technical': ('render_type', 'post_processing'),
}

PROMPT_SCHEMA = {'str': _STR_FIELDS, 'list': _LIST_FIELDS, 'dict': _DICT_FIELDS}

_URL_RE = re.compile(r'https?://\S+')


def url_temizle(metin):
    """Metinden URL'leri çıkarır ve fazla boşlukları toplar.

    Prompt'a link gönderilmez (kullanıcı kuralı 2026-08-10). Şu an linkler yalnız
    brief'in `pinterest` alanında ve o alan prompt'a hiç girmiyor; bu, brief ileride
    başka alana link koyarsa diye savunmadır."""
    if not isinstance(metin, str):
        return ''
    return ' '.join(_URL_RE.sub(' ', metin).split())


def _str_deger(v):
    """String'e indirger, URL'leri atar, uzunluğu sınırlar. Boşsa None."""
    if not isinstance(v, str):
        return None
    temiz = url_temizle(v)[:MAX_VALUE_LEN].strip()
    return temiz or None


def validate_prompt_json(data):
    """Claude'un döndürdüğü JSON'u şemaya indirger.

    Bilinmeyen anahtarlar ATILIR, iç dict'ler beyaz listeden geçer, boş değerler
    çıkarılır. Dönen dict BOŞSA girdi geçersizdir ve çağıran üretimi durdurmalıdır."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for f in _STR_FIELDS:
        v = _str_deger(data.get(f))
        if v:
            out[f] = v
    for f in _LIST_FIELDS:
        ham = data.get(f)
        if isinstance(ham, list):
            temiz = [x for x in (_str_deger(i) for i in ham) if x]
            if temiz:
                out[f] = temiz
    for f, izinli in _DICT_FIELDS.items():
        ham = data.get(f)
        if isinstance(ham, dict):
            ic = {}
            for k in izinli:
                v = _str_deger(ham.get(k))
                if v:
                    ic[k] = v
            if ic:
                out[f] = ic
    return out


def parse_json_cikti(metin):
    """Claude çıktısından ilk JSON nesnesini ayıklar; bulunamazsa None.

    Model bazen JSON'u açıklama metniyle sarıyor — mevcut `prompt_convert_handler` da
    aynı deseni kullanıyor (`re.search(r'\\{.*\\}', ..., re.DOTALL)`)."""
    if not isinstance(metin, str):
        return None
    m = re.search(r'\{.*\}', metin, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# Codex'e giden kısıtlar. TAMAMI İNGİLİZCE (kullanıcı kuralı 2026-08-10): Türkçe kalan
# tek şey JSON'daki `text_elements` içeriğidir.
_TEXT_CONSTRAINT = (
    '- Render the strings in "text_elements" EXACTLY as written, in Turkish. '
    'The characters ş, ğ, ı, İ, ö, ü, ç must be rendered correctly — no '
    'transliteration, no spelling changes.')
_CLEAN_CONSTRAINT = (
    '- Do NOT add any text, letters, words or numbers to the image.')
_LOGO_WITH_TEXT = (
    '- The attached image is the brand logo. Place it tastefully in the composition '
    '(corner or footer area).')
_LOGO_CLEAN = (
    '- The attached image is the brand logo. Use it ONLY as a brand identity and '
    'color reference — do NOT draw, reproduce or include the logo in the output.')


def build_codex_prompt(prompt_json, variant, aspect_ratio, has_logo):
    """Doğrulanmış JSON'dan Codex'e gönderilecek nihai metni kurar.

    `clean` varyantında `text_elements` JSON'DAN DA çıkarılır: yalnız "metin ekleme"
    kısıtı yazmak yetmez, model spesifikasyonda gördüğü başlığı yine de çizmeye
    eğilimli olur (2026-08-10 dersi — çelişkili prompt üretimi bozuyordu)."""
    from models_imagegen import ASPECTS
    w, h = ASPECTS.get(aspect_ratio, ASPECTS['social_post_4_5'])

    veri = dict(prompt_json or {})
    if variant == 'clean':
        veri.pop('text_elements', None)

    kisitlar = [f'- Output image must be exactly {w}x{h} pixels.']
    kisitlar.append(_CLEAN_CONSTRAINT if variant == 'clean' else _TEXT_CONSTRAINT)
    if has_logo:
        kisitlar.append(_LOGO_CLEAN if variant == 'clean' else _LOGO_WITH_TEXT)
    if veri.get('color_palette'):
        kisitlar.append('- Use ONLY the colors listed in "color_palette"; do not '
                        'introduce other colors.')
    kisitlar += [
        '- Write only to the current directory; produce exactly one file: output.png',
        '- Do not touch any other file or directory.',
        '- When done, report only the file path and a short production summary.']

    return ('$imagegen\n\n[IMAGE SPECIFICATION]\n'
            + json.dumps(veri, ensure_ascii=False, indent=2)
            + '\n\n[MANDATORY CONSTRAINTS]\n' + '\n'.join(kisitlar))
