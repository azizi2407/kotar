# Architecture

## Backend (Flask, repo root)

- `app.py` — application factory (`create_app`). Reads config from env
  (`load_dotenv`; in production, point real secrets at a file outside the
  repo). Registers blueprints, defines `/health` and SPA serving.
  `db.create_all()` runs at startup — there is no migration tool, so a new
  *table* is free but a new *column* on an existing table needs a manual
  `ALTER TABLE` (see `scripts/alter_*.sql` for the pattern).
- `extensions.py` — the single `db` (SQLAlchemy) instance, imported from here
  everywhere to avoid circular imports.
- `auth.py` + `sso_client.py` — authentication. Two modes, chosen by
  `AUTH_MODE`:
  - `oidc` (default): standard OpenID Connect Authorization Code flow.
    `/auth/login` → provider → `/auth/callback?code` → exchange code for
    tokens → verify `id_token` against the provider's JWKS → claims go into
    the Flask session. See `sso_client.OIDCClient`.
  - `local`: `/auth/login` redirects to the SPA's own `/login` page, which
    POSTs to `/auth/local-login` (see `local_auth.py`, `models_auth.py`).
  Either way, subsequent requests trust only the signed session cookie —
  there is no server-side session table.
- `api.py` — the main `/api/*` JSON surface: clients, sharing board metadata,
  session info, notifications, etc. Mutating endpoints require CSRF: a token
  handed out by `GET /api/session` must be echoed back in the
  `X-CSRFToken` header (`api.csrf_protect`, shared by `sharing.py`,
  `tools.py`, `mail_api.py`, `admin_api.py`).
- Feature blueprints (registered in `app.py`): `/api/sharing`, `/api/tools`,
  `/api/mail`, `/api/admin`, `/api/ads`, `/api/client-tracking`,
  `/api/planning`, `/api/depot`, `/api/design-files`, `/api/voice-notes`,
  `/api/reports`, `/api/imagegen`, plus public token-gated pages:
  `/review/<token>`, `/rapor/<token>`, `/onay/<token>`,
  `/special-days/<token>`, `/m/<file_id>`.
- `mail_gateway.py` / `mail_crypto.py` / `mail_service.py` / `mail_api.py` —
  an embedded webmail client (IMAP read; account passwords Fernet-encrypted
  in the DB, key in `MAIL_ENC_KEY`). `mail_sync_worker.py` polls inboxes
  periodically (wire it to a cron/systemd timer). Outbound SMTP depends on
  your network allowing it out.
- `admin_api.py` / `sso_admin.py` / `local_admin.py` — user/role management.
  In `AUTH_MODE=local` this manages the local user table directly; in
  `AUTH_MODE=oidc` it proxies to whatever admin API you configure via
  `SSO_BASE_URL`/`SSO_ADMIN_TOKEN` (optional — if unset, the Users page
  simply reports "not configured" and you manage users at your IdP instead).
- `ai_worker.py` / `ai_claude.py` / `ai_context.py` / `codex_runner.py` — the
  AI layer. `jobqueue.py` is a simple Postgres-backed job queue; workers
  (`ai_worker.py`, `media_worker.py`) poll it. `ai_context.py` assembles the
  "trusted" (system) vs "untrusted" (client-supplied) context split used in
  prompts to reduce prompt-injection risk.
- `brief_markdown.py` — a pure markdown parser (frontmatter + sections) that
  the AI-generated weekly brief is parsed back into structured fields with;
  kept separate from `ai_worker.py` because it has no side effects and is
  easy to unit test on its own.

## Frontend (`panel/` — Vite + React 19 + Tailwind v4 + shadcn)

- Build output `panel/dist` is served by Flask under `/panel/*` (SPA
  fallback to `index.html`; `vite.config.ts` sets `base: "/panel/"`).
- `src/lib/auth.tsx` — `AuthProvider`/`useAuth`. Session, CSRF token, and
  `auth_mode` all come from one `GET /api/session` call. `Protected` (in
  `App.tsx`) redirects unauthenticated users to `/auth/login`, which the
  backend routes to either the OIDC provider or the panel's own `/login`
  page depending on `AUTH_MODE`.
- `src/lib/api.ts` — fetch wrappers (`apiGet`, `apiJson`, `apiUpload`,
  `apiDelete`, `apiBlob`); errors surface the backend's `error`/`message`
  field.
- `src/components/ui/` — shadcn components; add new ones with
  `npx shadcn add`, don't hand-edit generated primitives.
- Routes live in `src/App.tsx`; pages in `src/pages/`; the shell (sidebar +
  user menu) is `src/components/AppLayout.tsx`.

## Data model

No migration tool — schema changes are: add a model class, import it in
`app.py`'s model-import block (so `create_all` sees it), and for new columns
on existing tables, write and run a manual `ALTER TABLE` (see
`scripts/alter_design_files_trash.sql` etc. for the pattern; there's no
tracking of which ALTERs have run — track that yourself, e.g. in your deploy
notes).

Two authority levels for roles:
- `role_required('management', ...)` — ordinary feature access; `management`
  always passes.
- `is_superadmin()` (`sso_client.py`, gated by `SUPERADMIN_EMAILS`) —
  destructive/identity-adjacent actions (impersonation, user management,
  hard-deleting uploads). Checked against the **real** identity, never the
  impersonated one, so impersonation can't be used to escalate.

## Conventions

- Code comments are in English throughout. The panel UI defaults to English,
  with Turkish available as a language toggle (see `panel/src/lib/i18n.tsx`
  and `panel/src/lib/dictionaries/`) — a holdover from the team that
  originally built this. Some route segments (e.g. `/musteri-takip`) and a
  few internal identifiers are still Turkish-named; PRs adding more
  languages to the dictionary set are welcome — see `CONTRIBUTING.md`.
- Roles come from the auth session (`sub`, `email`, `name`, `role`); `UserRef`
  is a read-only display projection synced at login, not the source of
  truth.
- Uploads/soft-deletes: most list queries filter `deleted_at.is_(None)`
  explicitly rather than relying on a global query filter — check the
  existing pattern in a neighboring query before adding a new one.
