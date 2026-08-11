#!/usr/bin/env bash
# Kotar starter (systemd ExecStart). Example deployment script — adapt the
# paths/service names below to your own setup.
# Injects the Google Drive secrets (GOOGLE_SA_JSON, GOOGLE_DRIVE_TOKEN_JSON)
# into the process env via Infisical machine identity, without writing them to
# disk. Other config (DATABASE_URL, SECRET_KEY, SSO_*) comes from systemd's
# EnvironmentFile=/etc/kotar/agency/env. If Infisical is unreachable, gunicorn
# still starts (Drive features are disabled, the panel still works).
set -uo pipefail
cd /srv/apps/agency

INFISICAL_ENV_FILE="${INFISICAL_ENV_FILE:-/etc/kotar/agency/infisical.env}"
GUNICORN=(/srv/apps/agency/venv/bin/gunicorn --config gunicorn.conf.py wsgi:app)

if [ -r "$INFISICAL_ENV_FILE" ]; then
  set -a; # shellcheck disable=SC1090
  source "$INFISICAL_ENV_FILE"; set +a
  : "${INFISICAL_DOMAIN:=https://app.infisical.com}"
  : "${INFISICAL_ENV:=prod}"
  if [ -n "${INFISICAL_UNIVERSAL_AUTH_CLIENT_ID:-}" ] \
     && [ -n "${INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET:-}" ] \
     && [ -n "${INFISICAL_PROJECT_ID:-}" ]; then
    TOKEN="$(infisical login --method=universal-auth --plain --silent \
               --domain="$INFISICAL_DOMAIN" 2>/dev/null)"
    # Clear the bootstrap identity from the process
    unset INFISICAL_UNIVERSAL_AUTH_CLIENT_ID INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET
    if [ -n "$TOKEN" ]; then
      export INFISICAL_TOKEN="$TOKEN"
      # EXPORT instead of infisical RUN: pulls secrets into the process env
      # without keeping infisical resident (~62MB Go runtime saved). dotenv-export
      # is single-quoted; verified round-trip including JSON secrets (GOOGLE_SA_JSON).
      SECRETS="$(infisical export --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
                   --domain="$INFISICAL_DOMAIN" --path=/ --format=dotenv-export 2>/dev/null)"
      unset INFISICAL_TOKEN
      if [ -n "$SECRETS" ]; then
        eval "$SECRETS"; unset SECRETS
        exec "${GUNICORN[@]}"
      fi
    fi
  fi
  echo "[start.sh] ⚠️ Infisical login failed/missing — starting without Google Drive secrets (Drive won't work)" >&2
fi

exec "${GUNICORN[@]}"
