# Upstream sync state

Kotar is an open-source derivative of a private upstream repository. The two repositories share **no git history** — kotar was
born as a single clean commit — so upstream changes are brought over by
*semantic porting*: reading the upstream commit range since the last sync and
re-applying each change under kotar's conventions (see `docs/PORTING.md`).

## Baseline

| | commit | date |
|---|---|---|
| Fork base (kotar was cut from here) | `d312c1f` | 2026-08-10 |
| **Last synced upstream commit** | `322a68f` | 2026-08-11 |

The next sync covers `git log <last synced>..origin/main` in the upstream
repository. Every sync PR MUST update the "Last synced" row above and list
the ported/skipped upstream commits in its description.

## Sync log

| date | upstream range | result |
|---|---|---|
| 2026-08-31 | `d312c1f..322a68f` (7 commits) | Ported: public-media redesign, caption skill trigger (as opt-in `CAPTION_STYLE_SKILL`), reels triple cover, mail/users management-only gates. Skipped: `docs/harita/*`, design docs. |

## Reverse-port candidates (kotar → upstream)

Generic fixes made in kotar that the upstream would benefit from:

- `app.py`: `load_dotenv()` must run **before** project imports —
  `sso_client` reads `AUTH_MODE` from the environment at import time.
  Invisible in production (systemd `EnvironmentFile`) but breaks any
  plain-`.env` local run.
- `scripts/create_local_user.py`: first-user bootstrap pattern (upstream
  equivalent would be seeding outside the admin-API gate).
- Locale bug: several formatters hardcoded `tr-TR`; kotar threads a `lang`
  parameter through plain helpers (`AdsGantt`, `planlama.ts`, `depot.ts`, …).
- `.github/workflows/ci.yml`: backend pytest + frontend tsc/lint/vitest/build
  on push and PR; `@pytest.mark.integration` excludes environment-dependent
  tests (headless Chromium PDF render).
