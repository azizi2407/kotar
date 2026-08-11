#!/usr/bin/env bash
# Media worker starter (systemd --user, run as the deploy user). Example
# script: injects GOOGLE_SA_JSON via Infisical machine identity (to download
# video from Drive). DATABASE_URL/WHISPER_URL come from systemd's
# EnvironmentFile. If Infisical is unreachable, it still starts (Drive
# downloads fail, the job goes to failed).
set -uo pipefail
cd /srv/apps/agency

INFISICAL_ENV_FILE="${INFISICAL_ENV_FILE:-/home/deploy/.config/agency-worker/infisical.env}"
PY=(/srv/apps/agency/venv/bin/python media_worker.py)

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
      # EXPORT instead of infisical RUN: pulls secrets into the process env
      # without keeping infisical resident (~62MB Go runtime saved). dotenv-export
      # is single-quoted; verified round-trip including JSON secrets (GOOGLE_SA_JSON).
      SECRETS="$(infisical export --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
                   --domain="$INFISICAL_DOMAIN" --path=/ --format=dotenv-export 2>/dev/null)"
      unset INFISICAL_TOKEN
      if [ -n "$SECRETS" ]; then
        eval "$SECRETS"; unset SECRETS
        exec "${PY[@]}"
      fi
    fi
  fi
  echo "[media_worker_start] ⚠️ Infisical login failed — starting without Drive" >&2
fi
exec "${PY[@]}"
