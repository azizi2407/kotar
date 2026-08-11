# Contributing

Thanks for taking a look at Kotar. This started as a real agency's internal
tool and was open-sourced by stripping out anything company-specific — so
expect a few rough edges from that process. Contributions that smooth those
out are especially welcome.

## Setup

See the [Quick start](README.md#quick-start) in the README. In short:
Python 3.11+, PostgreSQL, Node 20+.

```bash
venv/bin/python -m pytest tests/ -q     # backend tests (sqlite + mocks, no network)
cd panel && npm run test                # frontend tests (vitest, pure logic)
cd panel && npm run lint                # oxlint
cd panel && npx tsc -b --noEmit         # type-check
```

Run the full backend test suite and, if you touched `panel/`, the frontend
build/lint before opening a PR.

## Where things live

See [ARCHITECTURE.md](ARCHITECTURE.md) for the map. Quick pointers:

- New backend feature → a new `*.py` blueprint at the repo root, registered
  in `app.py`, models in a matching `models_*.py` if it needs its own table.
- New DB column on an *existing* table → `db.create_all()` won't add it.
  Write the model change **and** a `scripts/alter_*.sql` (or your own
  migration mechanism) and apply it before deploying.
- New frontend page → `panel/src/pages/`, route it in `panel/src/App.tsx`,
  add it to the nav table in `panel/src/components/AppLayout.tsx`.

## Known gaps (good first contributions)

- **More languages**: the panel ships with English (default) and Turkish via
  the i18n layer in `panel/src/lib/i18n.tsx` and `panel/src/lib/dictionaries/`.
  Adding another language means adding a new dictionary file per feature area
  and merging it in `dictionaries/index.ts`.
- **`sso_admin.py`** assumes a specific `list_users`/`create_user`/
  `update_user` REST contract on the OIDC side; it's a reference
  implementation, not a real integration with any specific provider
  (Keycloak, Authentik, Auth0, ...). A provider-specific adapter (or several)
  would help `AUTH_MODE=oidc` deployments manage users from the panel
  instead of the IdP console.
- **Outbound mail** (`mail_gateway.py` SMTP send) works but depends entirely
  on your network allowing outbound SMTP — no code changes needed, just a
  reachable mail host.

## Style

- Code comments are in English. New UI copy goes through the i18n layer
  (`t("...")` with an entry in both the `en` and `tr` dictionaries) rather
  than being hardcoded — see `panel/src/lib/dictionaries/`.
- Match the surrounding file's naming conventions: some route segments are
  still Turkish (e.g. `/musteri-takip`), internal identifiers are English.
- Backend: plain Flask blueprints + SQLAlchemy, no ORM magic beyond what's
  already in use. Keep new modules flat at the repo root, matching the
  existing layout (no `src/` nesting).
- Frontend: functional components, `@tanstack/react-query` for server state,
  shadcn/ui primitives — add new shadcn components via `npx shadcn add`
  rather than hand-rolling.
- Tests accompany behavior changes. The backend suite runs against SQLite
  with no network access — mock anything that would hit a real service
  (Drive, IMAP/SMTP, the AI CLI, ntfy).

## Reporting issues

Open a GitHub issue. Include repro steps and, for backend bugs, the relevant
`pytest` output if you have it.
