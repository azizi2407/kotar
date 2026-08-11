"""Pure markdown parser for the weekly content brief (no side effects).

`ai_worker.py`'s brief handler generates markdown from the AI, then parses its own
output with THIS module into structured fields (title/intro/ideas/week_notes) — so
generation and parsing share the same contract (see `ai_worker.brief_handler`).

The format is LENIENT: both old and new item headings are handled. Keys aren't
force-translated to English (lossless); the item heading is lowercased and kept
as-is, the raw block text is also kept under `raw`.
"""
import datetime as dt
import re

import yaml


def _json_safe(obj):
    """Converts `date`/`datetime` objects produced by YAML into a JSONB-writable ISO
    string (recursive). The psycopg JSONB dumper can't serialize `date`."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (dt.date, dt.datetime)):   # datetime is a subclass of date
        return obj.isoformat()
    return obj


def parse_frontmatter(text):
    """Returns the `--- ... ---` YAML block (dict) + the remaining body.

    If there's no frontmatter/it's broken, returns ({}, text) (doesn't blow up)."""
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
    """Splits the body on `##`/`###` headings.

    Returns: (pre, [(heading_line, section_body), ...]). `pre` = the part before
    the first `##` (title/intro live here). A level-1 `#` heading does NOT split
    (title)."""
    parts = re.split(r"(?m)^(#{2,3}\s+.*)$", body)
    pre = parts[0]
    secs = [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return pre, secs


def _first_title(body):
    """The first level-1 `# ...` heading (title). '' if none."""
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _first_intro(body):
    """The first `>` blockquote block (intro). Consecutive `>` lines are merged."""
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
    """Returns the text inside the first quote (straight or curly); '' if none."""
    m = re.search(r'[""“”](.+?)[""“”]', text)
    return m.group(1).strip() if m else ""


def _parse_kv_bullets(text):
    """Parses a `- **key**: value` bullet list into a dict (LENIENT).

    Sub-bullets (indented `- ...`) become a list of values. The key is kept AS
    FOUND in the source (NO case conversion) → old+new format stays lossless; this
    also avoids the Turkish `İ`.lower() trap (combining dot). The consumer can
    match case-insensitively if needed."""
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
    """Parses a brief markdown into a structural dict (ready for upsert)."""
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
