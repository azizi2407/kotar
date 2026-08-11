"""Image prompt's JSON schema, validation, and conversion to Codex text (2026-08-10).

Why a separate module: schema validation is a SECURITY layer and must be
testable without HTTP or a DB. The brief is untrusted data; the `claude -p`
translation step is an injection opportunity, and nothing that doesn't fit the schema should reach Codex.
"""
import json
import re

MAX_VALUE_LEN = 400          # prevents a single field from bloating the prompt

# Top-level string fields.
_STR_FIELDS = ('prompt', 'subject', 'environment', 'style', 'lighting', 'mood')
# String-list fields.
_LIST_FIELDS = ('color_palette', 'text_elements')
# Nested dict fields and their ALLOWED inner keys (whitelist).
_DICT_FIELDS = {
    'camera': ('angle', 'distance', 'depth_of_field', 'focus'),
    'composition': ('framing', 'subject_placement', 'foreground', 'background',
                    'negative_space'),
    'technical': ('render_type', 'post_processing'),
}

PROMPT_SCHEMA = {'str': _STR_FIELDS, 'list': _LIST_FIELDS, 'dict': _DICT_FIELDS}

_URL_RE = re.compile(r'https?://\S+')


def url_temizle(metin):
    """Strips URLs from the text and collapses extra whitespace.

    Links aren't sent to the prompt (user rule 2026-08-10). Right now links only
    appear in the brief's `pinterest` field and that field never enters the
    prompt; this is a defense in case a future brief puts a link in another field."""
    if not isinstance(metin, str):
        return ''
    return ' '.join(_URL_RE.sub(' ', metin).split())


def _str_deger(v):
    """Coerces to string, strips URLs, caps the length. None if empty."""
    if not isinstance(v, str):
        return None
    temiz = url_temizle(v)[:MAX_VALUE_LEN].strip()
    return temiz or None


def validate_prompt_json(data):
    """Coerces the JSON returned by Claude down to the schema.

    Unknown keys are DROPPED, nested dicts pass through the whitelist, empty
    values are removed. If the returned dict is EMPTY, the input was invalid and the caller should stop generation."""
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
    """Extracts the first JSON object from Claude's output; None if not found.

    The model sometimes wraps the JSON in explanatory text — the existing
    `prompt_convert_handler` uses the same pattern (`re.search(r'\\{.*\\}', ..., re.DOTALL)`)."""
    if not isinstance(metin, str):
        return None
    m = re.search(r'\{.*\}', metin, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# Constraints sent to Codex. ALL IN ENGLISH (user rule 2026-08-10): the only
# thing that stays in Turkish is the `text_elements` content in the JSON.
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
    """Builds the final text sent to Codex from validated JSON.

    In the `clean` variant, `text_elements` is ALSO removed FROM THE JSON: just
    writing a "no text" constraint isn't enough, the model still tends to draw
    the heading it sees in the specification (2026-08-10 lesson — a contradictory
    prompt was breaking generation)."""
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
