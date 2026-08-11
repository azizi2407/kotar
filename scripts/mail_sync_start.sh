#!/usr/bin/env bash
# Mail sync poller starter (systemd --user, oneshot, run as the deploy user).
# Example script: injects MAIL_ENC_KEY + MAIL_ALLOWED_DOMAINS via Infisical
# machine identity (for password decryption). DATABASE_URL etc. come from
# systemd's EnvironmentFile. If Infisical is unreachable, the mail module
# fails closed (sync is skipped); the worker still exits cleanly.
set -uo pipefail
cd /srv/apps/agency

INFISICAL_ENV_FILE="${INFISICAL_ENV_FILE:-/home/deploy/.config/agency-worker/infisical.env}"
PY=(/srv/apps/agency/venv/bin/python mail_sync_worker.py)

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
    unset INFISICAL_UNIVERSAL_AUTH_CLIENT_ID INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET
    if [ -n "$TOKEN" ]; then
      export INFISICAL_TOKEN="$TOKEN"
      SECRETS="$(infisical export --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
                   --domain="$INFISICAL_DOMAIN" --path=/ --format=dotenv-export 2>/dev/null)"
      unset INFISICAL_TOKEN
      if [ -n "$SECRETS" ]; then
        eval "$SECRETS"; unset SECRETS
        exec "${PY[@]}"
      fi
    fi
  fi
  echo "[mail_sync_start] ⚠️ Infisical login failed — skipping mail sync" >&2
fi
exec "${PY[@]}"
