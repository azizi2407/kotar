"""brief_markdown.py — pure markdown parser (no DB, no side effects)."""
import json

import brief_markdown


def test_parse_frontmatter_ve_bolumler():
    txt = (
        "---\nclient_id: 1\nhafta: 2026-W30\n---\n"
        "# Örnek Müşteri — 2026-W30 Brief\n> Bu haftanın odağı yaz kampanyası.\n"
        "## 💡 Fikir 1 \"Yaz İndirimi\"\n- **pillar**: kampanya\n- **format**: reels\n"
        "## Hafta Notları\n- **durum**: taslak\n"
    )
    p = brief_markdown.parse_brief(txt, "ornek/2026-W30.md")
    assert p["frontmatter"]["client_id"] == 1
    assert p["week_iso"] == "2026-W30"
    assert p["title"] == "Örnek Müşteri — 2026-W30 Brief"
    assert p["intro"] == "Bu haftanın odağı yaz kampanyası."
    assert len(p["ideas"]) == 1
    assert p["ideas"][0]["ad"] == "Yaz İndirimi"
    assert p["ideas"][0]["pillar"] == "kampanya"
    assert p["week_notes"]["durum"] == "taslak"


def test_frontmatter_yoksa_patlamaz():
    fm, body = brief_markdown.parse_frontmatter("# Başlık\nİçerik")
    assert fm == {}
    assert body == "# Başlık\nİçerik"


# --- regression: YAML date → JSONB-safe --------------------------------------
def test_frontmatter_tarih_json_safe():
    """`üretildi: 2026-07-19` becomes a date in YAML; it needs to be an ISO string to
    be writable to JSONB. Regression: 'Object of type date is not JSON serializable'."""
    txt = (
        "---\nclient_id: 1\nhafta: 2026-W30\nüretildi: 2026-07-19\n"
        "özel_günler: []\n---\n# X\n> intro\n## 💡 Fikir 1\n- **başlık**: a\n"
    )
    p = brief_markdown.parse_brief(txt, "x/2026-W30.md")
    assert p["frontmatter"]["üretildi"] == "2026-07-19"
    assert isinstance(p["frontmatter"]["üretildi"], str)
    json.dumps(p["frontmatter"])   # raises TypeError if a date leaks through
    json.dumps(p["ideas"])
    json.dumps(p["week_notes"])
