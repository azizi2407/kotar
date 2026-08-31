# Porting guide: upstream → kotar

How to bring upstream changes into kotar. The repos share no
git history (see `docs/UPSTREAM.md`), so a sync is a *semantic* port of the
upstream commit range since the last recorded baseline — never a git merge.

## Procedure

1. Read the baseline SHA from `docs/UPSTREAM.md`, then in the upstream repo:
   `git log --oneline <baseline>..origin/main`.
2. Classify each commit (or coherent commit cluster) with the skip list and
   policies below. Upstream commits tagged `[no-port]` are skipped outright;
   a `kotar: <hint>` line in an upstream commit body is an adaptation note
   from the author — follow it.
3. Before touching code, read the upstream diff of `docs/harita/` for the
   range — upstream's map rule means it is a reliable behavioral summary of
   the delta.
4. Port cluster by cluster, applying the transformation policies below.
   Port the accompanying tests with the same adaptations.
5. Screen every ported hunk for leaks: real client names, real emails,
   internal hostnames/domains, secrets. Upstream comments and fixtures may
   legitimately contain them; kotar must not.
6. Run the full gate: `venv/bin/python -m pytest tests/ -q` and
   `cd panel && npm run build && npm run lint && npm run test`.
7. Open one PR titled "Upstream sync through `<sha>`", list ported and
   skipped commits in the body, and update `docs/UPSTREAM.md` in the same PR.

## Skip list (never ported)

- `docs/harita/*`, `docs/tasarimlar/*`, `GELISTIRME.md` — internal living
  docs (kotar's docs are `README.md`/`ARCHITECTURE.md`/`CONTRIBUTING.md`).
- Modules removed from kotar: `opsd.py` (+ `/server` page), pastebin,
  lente-influencer, vault-sync.
- Anything carrying real client data, credentials, or org-internal
  operational detail.

## Transformation policies

**Language.**
- Code comments and docstrings → English.
- Backend user-facing strings (API errors, generated file names) → English;
  update the ported tests' assertions to match.
- Frontend user-facing strings → never hardcoded: add a dictionary key in
  `panel/src/lib/dictionaries/` with the upstream Turkish text as the `tr`
  value and an English translation as the `en` value.
- Deliberately NOT translated: AI content-generation prompt bodies (they
  produce Turkish content by design, governed by the caption `lang`
  setting), DB enum/status identifiers, notify-rule severities
  (`kritik`/`normal`/`bilgi`), data-contract dict keys.

**Branding.** upstream brand name → `kotar` (logo: `/panel/kotar-logo.png`,
alt text "Kotar — digital media agency"); public pages use `lang="en"`.

**Auth.** Upstream is SSO-only; kotar has `AUTH_MODE=local|oidc`. Upstream
changes to `auth.py`/`sso_client.py`/`sso_admin.py` usually need rethinking
against kotar's `local_admin.py`/`models_auth.py` split rather than a port.

**Org-specific integrations.** Upstream may hardwire tools from its own
environment (e.g. a house-style Claude skill in caption generation). In kotar
these become opt-in configuration (e.g. `CAPTION_STYLE_SKILL`) defaulting
to off.

## Rename map (upstream name → kotar name)

| upstream | kotar |
|---|---|
| `/panel/<upstream-brand>-logo.png` | `/panel/kotar-logo.png` |
| `instagram_video_kapagi_N.<ext>` | `instagram_video_cover_N.<ext>` |
| `bolunmus_gorsel_N.<ext>` | `split_image_N.<ext>` |
| `<base>-parcalar.zip` | `<base>-pieces.zip` |
| skill trigger, unconditional | `_SKILL_TRIGGER` via `CAPTION_STYLE_SKILL` (opt-in) |
| "Vault Ayar" client-detail tab label | "Brand Profile" / "Marka Profili" (dictionary values only; `vaultAyar` keys and `/vault-ayar` API paths unchanged) |
| OIDC discovery at startup | lazy discovery on first login (`_ensure_endpoints`, 503 on failure) |

Add a row here whenever a sync introduces a new deliberate divergence — this
table is what keeps the next sync cheap.
