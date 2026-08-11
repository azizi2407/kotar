# Kotar

[![CI](https://github.com/azizi2407/kotar/actions/workflows/ci.yml/badge.svg)](https://github.com/azizi2407/kotar/actions/workflows/ci.yml)

A content operations system for social media agencies, inspired by curiosity.

Kotar is a self-hosted web app that helps a small agency run the day-to-day
work of managing social media clients: a shared content calendar, an approval
workflow clients can use without an account, AI-assisted captions and briefs,
video/photo intake from shooting days, brand guideline storage, font pooling,
monthly ad reports, and a lightweight inbox for the studio's shared mailbox.

It grew out of a real production system and has been stripped of anything
specific to the company that built it (branding, real client data, internal
ops tooling) so it can be run by anyone.

## Background

This started as a Cursor side project to help a friend run their social
media agency, and grew over time — with substantial help from Claude Code —
into what's in this repository. Development is ongoing.

To get real use out of the AI-assisted features, you'll need:
- **An auth provider** for the panel (I use Google via `AUTH_MODE=oidc`; the
  built-in local login also works)
- **Magnific MCP**, authenticated with your own Magnific account (for AI
  image generation/upscaling)
- **Claude Code**, authenticated via subscription session (no API key needed)
- **ChatGPT**, via the Codex CLI, authenticated via subscription session (no
  API key needed)

Fill in your clients' brief inputs, set up your team's roles, and let Kotar
kotar your day.

## Features

- **Sharing Board** — the weekly planning surface: a client-by-row grid of
  content cards, drag-and-drop scheduling, and a client-facing approval link
  that needs no login (`/onay/<token>`).
- **Clients** — client records, brand guidelines, contacts, contracts,
  Google Drive folder provisioning.
- **Designer board** — task queue for designers, assignment tracking.
- **Brief generation** — AI-drafted weekly content briefs from a client's
  brand profile and upcoming special days.
- **Captions & AI tools** — Claude/Codex-backed caption writing, image
  generation, image splitting.
- **Video/photo intake** — a videographer workflow: upload, review, revision
  matching (detects when a re-shoot supersedes an old file by filename).
- **Planning board** — a React Flow canvas for freeform content planning.
- **Voice notes** — record a note, get a transcript + structured summary.
- **Fonts** — a shared font pool per client.
- **Monthly reports** — turns Meta Ads CSV exports into a client-facing report
  page.
- **Mail** — a minimal embedded webmail (IMAP read; SMTP send depends on your
  network allowing outbound mail).
- **Notifications** — in-panel + optional push (via [ntfy](https://ntfy.sh)).
- **Special days** — a shared calendar of occasions worth posting about,
  with per-client approval.

## Stack

- **Backend**: Flask (Python), SQLAlchemy, PostgreSQL. No migration tool —
  `db.create_all()` creates missing tables at startup; new columns need a
  manual `ALTER TABLE` (see `scripts/alter_*.sql` for examples).
- **Frontend**: React 19 + Vite + Tailwind v4 + shadcn/ui, served by Flask
  from `panel/dist` at `/panel/*`.
- **Auth**: pluggable — generic OpenID Connect (works with Keycloak,
  Authentik, Auth0, Google Workspace, ...) or a built-in local email/password
  login. See [Authentication](#authentication) below.

## Quick start

```bash
# 1. Backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in SECRET_KEY, DATABASE_URL at minimum
venv/bin/python -c "from app import app"   # creates tables on first run

# 2. Frontend
cd panel
npm install
npm run build                # or `npm run dev` for a hot-reloading dev server

# 3. Run
cd ..
venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
# or, for local dev:
FLASK_ENV=testing venv/bin/python -c "from app import app; app.run(port=5030, debug=True)"

# 4. Create your first login (AUTH_MODE=local only — every other way to create
#    a user requires already being logged in as a superadmin, so this is the
#    one time you need it)
venv/bin/python scripts/create_local_user.py you@example.com --role management
```

Then open `http://localhost:5030/panel/` and log in with the email + temporary
password `create_local_user.py` printed.

You'll also need PostgreSQL running and `DATABASE_URL` pointing at it. Most
individual features (mail, AI captions, Drive sync, push notifications) are
optional and simply stay inactive until their env vars are configured — see
`.env.example` for the full list with explanations.

## Authentication

Kotar never stores its own session table — identity is a signed cookie, and
who's allowed to do what comes from a `role` (`management`, `designer`,
`content_creator`, `videographer`, or `pending`) attached to the session.
`management` can access everything; other roles are scoped per-page.

**`AUTH_MODE=local`** (default) — the panel has its own login page. A
superadmin (see `SUPERADMIN_EMAILS`) creates accounts from the Users page;
each gets a one-time temporary password to hand to the new user, who can
change it after logging in. This is the fastest way to get a working
deployment with zero external dependencies. The very first account has to be
created with `scripts/create_local_user.py` (see Quick start) — the Users
page itself requires being logged in as a superadmin already, so there's no
account to log in with otherwise.

**`AUTH_MODE=oidc`** — identity is delegated to an external OpenID Connect
provider via the standard Authorization Code flow. Set `OIDC_ISSUER`,
`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` (discovery fetches the rest, or set
`OIDC_AUTHORIZATION_ENDPOINT`/`OIDC_TOKEN_ENDPOINT`/`OIDC_JWKS_URL` directly).
Since a `role` claim isn't a standard OIDC field, `OIDC_ROLE_CLAIM` names
whatever custom claim/attribute your provider uses to carry it (falls back to
`OIDC_DEFAULT_ROLE`, `pending`, if absent). User/role management in this mode
happens at your identity provider by default; `admin_api.py`/`sso_admin.py`
can optionally proxy to a compatible admin API if you build one against that
provider (see `sso_admin.py`'s docstring for the expected contract).

Either mode, impersonation ("view as another user", for support/debugging)
and destructive superadmin actions are gated by `SUPERADMIN_EMAILS`
independent of role — set it to the emails you trust with that.

## Project layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for a fuller tour. In short:

- Backend modules live at the repo root as flat `*.py` files (Flask
  blueprints), one per feature area — `sharing.py`, `client_*.py`,
  `ai_worker.py`, `mail_*.py`, etc.
- `models*.py` — SQLAlchemy models, split by feature.
- `panel/` — the React SPA. Pages in `panel/src/pages/`, shared UI in
  `panel/src/components/`.
- `scripts/` — one-off and periodic maintenance scripts (cron/systemd-timer
  targets), invoked as `python scripts/whatever.py`.
- `tests/` — pytest suite, SQLite + mocks, no network/DB dependency.

## Testing

```bash
venv/bin/python -m pytest tests/ -q     # backend — ~1500 tests, sqlite + mocks
cd panel && npm run test                # frontend — vitest, pure logic only
cd panel && npm run lint                # oxlint
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
