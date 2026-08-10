"""Haftalık içerik brief'i için saf markdown ayrıştırıcı (yan etkisiz).

`ai_worker.py`'nin brief handler'ı AI'dan markdown üretir, sonra kendi çıktısını
BU modülle ayrıştırıp yapısal alanlara (title/intro/ideas/week_notes) böler —
üretim ile ayrıştırma aynı sözleşmeyi paylaşsın diye (bkz. `ai_worker.brief_handler`).

Format LENIENT: eski/yeni madde başlıkları ikisi de idare edilir. Anahtarlar
İngilizceye zorla çevrilmez (kayıpsız); madde başlığı küçük harfe indirgenip
aynen saklanır, ham blok metni ayrıca `raw` altında tutulur.
"""
import datetime as dt
import re

import yaml


def _json_safe(obj):
    """YAML'ın ürettiği `date`/`datetime` nesnelerini JSONB-yazılabilir ISO
    string'e çevirir (recursive). psycopg JSONB dumper `date`'i serialize edemez."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (dt.date, dt.datetime)):   # datetime, date'in alt sınıfı
        return obj.isoformat()
    return obj


def parse_frontmatter(text):
    """`--- ... ---` YAML bloğunu (dict) + kalan gövdeyi döner.

    Frontmatter yoksa/bozuksa ({}, text) döner (patlamaz)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return _json_safe(fm), m.group(2)


def _sections(body):
    """Gövdeyi `##`/`###` başlıklarına böler.

    Döner: (pre, [(başlık_satırı, bölüm_gövdesi), ...]). `pre` = ilk `##`ten
    önceki kısım (title/intro burada yaşar). Level-1 `#` başlık BÖLMEZ (title)."""
    parts = re.split(r"(?m)^(#{2,3}\s+.*)$", body)
    pre = parts[0]
    secs = [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return pre, secs


def _first_title(body):
    """İlk level-1 `# ...` başlığı (title). Yoksa ''."""
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _first_intro(body):
    """İlk `>` blockquote bloğu (intro). Ardışık `>` satırları birleştirilir."""
    out = []
    started = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">"):
            out.append(s.lstrip(">").strip())
            started = True
        elif started:
            break
    return "\n".join(out).strip()


def _quoted(text):
    """İlk tırnak (düz veya kıvrık) içi metni döner; yoksa ''."""
    m = re.search(r'[""“”](.+?)[""“”]', text)
    return m.group(1).strip() if m else ""


def _parse_kv_bullets(text):
    """`- **anahtar**: değer` madde listesini dict'e ayrıştırır (LENIENT).

    Alt-maddeler (girintili `- ...`) değer listesi olur. Anahtar kaynaktaki HÂLİYLE
    (case dönüşümü YOK) saklanır → eski+yeni format kayıpsız; ayrıca Türkçe `İ`.lower()
    tuzağından (birleşik nokta) kaçınılır. Tüketici gerekirse case-insensitive eşler."""
    lines = text.splitlines()
    result = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^-\s+\*\*(.+?)\*\*\s*[:：]\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        key = m.group(1).strip()
        val = m.group(2).strip()
        subs = []
        j = i + 1
        while j < len(lines) and re.match(r"^\s+[-*]\s+", lines[j]):
            subs.append(re.sub(r"^\s+[-*]\s+", "", lines[j]).strip())
            j += 1
        if subs:
            result[key] = ([val] + subs) if val else subs
        else:
            result[key] = val
        i = j
    return result


def parse_brief(text, source_path):
    """Bir brief markdown'ını yapısal dict'e ayrıştırır (upsert'e hazır)."""
    fm, body = parse_frontmatter(text)
    _, secs = _sections(body)

    ideas = []
    week_notes = {}
    for head, sbody in secs:
        h = head.lstrip("#").strip()
        if "Fikir" in h and "💡" in h:
            idea = {"ad": _quoted(h)}
            idea.update(_parse_kv_bullets(sbody))
            idea["raw"] = (head + "\n" + sbody).strip()
            ideas.append(idea)
        elif "Hafta Notları" in h:
            week_notes.update(_parse_kv_bullets(sbody))

    return {
        "frontmatter": fm,
        "week_iso": str(fm.get("hafta")) if fm.get("hafta") else "",
        "title": _first_title(body),
        "intro": _first_intro(body),
        "ideas": ideas,
        "week_notes": week_notes,
        "raw_md": text,
        "source_path": source_path,
    }
