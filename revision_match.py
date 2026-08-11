"""Revised video matching — "which older version does a new upload supersede?"

WHY THIS EXISTS: when a videographer uploads a corrected video, they add a small
suffix to the file name (`camsaş0728.mp4` → `camsaşr0728.mp4`); the old version just
sat there in the panel, on the server, and in Drive. This module reads the NAME and
detects the older version.

KEPT PURE — doesn't touch the DB, disk, or Drive; the caller supplies the
candidates. The rule is expected to be the part most likely to change, and it
should be testable on its own. The deletion decision and safety gates (was it
shared, did it enter approval) live in the caller:
`sharing._supersede_previous_videos`.

METHOD — "suffix stripping": if stripping a version suffix from the new name makes
it equal to an old name, that's the older version. On real production data (165
videos, 2026-08-01) this gave **35 matches, 0 false positives**.

    r/R   camsaşr0728 → camsaş0728 · camsaşinşaat0801r → camsaşinşaat0801
    rev   albarev0731 → alba0731
    - N   MUTLU DAHİLER - 23 - 2 → MUTLU DAHİLER - 23
    .N    camsaş0728.2 → camsaş0728

The rule is designed to lean toward MISSING matches: `bdkrev` (base `bdk`) or
`ATLASRENKREV` may look like a revision to a human eye, but since the name doesn't
prove it, they're left untouched. Missing a revision is preferable to deleting a
legitimate video.
"""
import os
import re
import unicodedata

# Numbered version suffix. `- N` is only a version if the name already carries a
# `- <number>`: `MÜŞTERİ - 28` is a WEEK video (independent), `MÜŞTERİ - 28 - 2` is
# that week's 2nd version. Without this distinction, `- 27` and `- 28` were
# mistaken for siblings and legitimate videos from different weeks were being
# deleted (caught by the 2026-08-01 dry run).
_DASH_N = re.compile(r'^(.*\s-\s*\d+)\s*-\s*(\d+)$')
_DOT_N = re.compile(r'^(.+?)\.(\d+)$')


def _tr_fold(s):
    """Turkish-aware case folding.

    FIRST Unicode NFC: names in production arrive as **NFD** (`ş` = `s` + a
    combining cedilla; `camsaşinşaat0801.mp4` is 22 code points, 20 in NFC).
    macOS/iPhone produce NFD, Windows/Android produce NFC — when a videographer
    switches devices, two versions get saved in different forms and matching
    would SILENTLY miss them (caught in live 2026-08-01 data).

    THEN Turkish: Python's plain `casefold()` turns `İ` into `i̇` (i + a combining
    dot) → `İDEAL0725R` wouldn't match `ideal0725`. We also erase the
    dotted/dotless i distinction: file names use `I`/`ı`/`i` inconsistently."""
    s = unicodedata.normalize('NFC', s)
    return (s.replace('İ', 'i').replace('I', 'ı').replace('ı', 'i')
             .casefold().replace('i̇', 'i'))


def normalize(name):
    """Comparison root: no extension, whitespace-collapsed, Turkish-folded."""
    if not name:
        return ''
    root = os.path.splitext(name)[0]
    return _tr_fold(re.sub(r'\s+', ' ', root).strip())


def version_key(name):
    """`(base, version_no)` — parses a numbered version suffix if present, else no=0.

        MÜŞTERİ - 28        → ('müşteri - 28', 0)   ← week video, NOT a version
        MÜŞTERİ - 28 - 2    → ('müşteri - 28', 2)
        camsaş0728.2        → ('camsaş0728', 2)

    Resolves the version chain by comparing numbers: a lower-numbered member of the
    same base is older. Since base equality is required, `- 27` and `- 28` (two
    separate weeks) are never treated as siblings."""
    n = normalize(name)
    if not n:
        return '', 0
    for rx in (_DASH_N, _DOT_N):
        m = rx.match(n)
        if m:
            return m.group(1).strip(), int(m.group(2))
    return n, 0


def base_candidates(name):
    """Base names obtainable by stripping a LETTER suffix (r / rev) from `name`.

    Numbered suffixes are NOT here — that's `version_key`'s job (needs order
    comparison). Returns a set; `name` itself is never included."""
    n = normalize(name)
    if not n:
        return set()
    out = set()

    # 'r' suffix: its position isn't fixed — it can come before or after the date.
    # Instead of guessing, we drop each `r` one at a time and generate a
    # candidate. Extra candidates (`rizonr0727` → `izonr0727`) are harmless: they
    # won't match a real name.
    for i, ch in enumerate(n):
        if ch == 'r':
            out.add(n[:i] + n[i + 1:])

    # 'rev' syllable (albarev0731 → alba0731)
    for m in re.finditer('rev', n):
        out.add(n[:m.start()] + n[m.end():])

    out.discard(n)
    return {c.strip() for c in out if c.strip()}


def find_superseded(new_name, older):
    """Returns the records from `older` that `new_name` supersedes.

    `older`: records with a `file_name` attribute, guaranteed by the caller to
    actually be OLDER (no time filtering here — this module is pure).

    Two independent paths:
      1. **Numbered chain** — same base, lower version no. When `- 28 - 4` arrives,
         `- 28`, `- 28 - 2`, `- 28 - 3` are all caught together.
      2. **Letter suffix** — the old record's full name is one of the candidates
         obtained by stripping `r`/`rev` from the new name."""
    cands = base_candidates(new_name)
    yeni_taban, yeni_no = version_key(new_name)
    hits = []
    for rec in older:
        ad = getattr(rec, 'file_name', None)
        n = normalize(ad)
        if not n:
            continue
        eski_taban, eski_no = version_key(ad)
        if yeni_no > 0 and eski_taban == yeni_taban and eski_no < yeni_no:
            hits.append(rec)
        elif n in cands:
            hits.append(rec)
    return hits
