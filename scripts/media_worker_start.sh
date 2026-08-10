#!/usr/bin/env bash
# Media worker başlatıcı (proje sahibi systemd --user). Infisical machine identity ile
# GOOGLE_SA_JSON'u enjekte eder (Drive'dan video indirmek için). DATABASE_URL/
# WHISPER_URL systemd EnvironmentFile'dan gelir. Infisical erişilemezse yine
# başlar (Drive indirmesi başarısız olur, job fail'e düşer).
set -uo pipefail
cd /srv/apps/agency

INFISICAL_ENV_FILE="${INFISICAL_ENV_FILE:-/home/proje sahibi/.config/agency-worker/infisical.env}"
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
      # infisical RUN yerine EXPORT: sırları sürecin env'ine alıp infisical'ı resident
      # bırakma (~62MB Go runtime tasarrufu). dotenv-export tek-tırnaklı; JSON sırları
      # (GOOGLE_SA_JSON) dahil round-trip doğrulandı (2026-07-13).
      SECRETS="$(infisical export --projectId="$INFISICAL_PROJECT_ID" --env="$INFISICAL_ENV" \
                   --domain="$INFISICAL_DOMAIN" --path=/ --format=dotenv-export 2>/dev/null)"
      unset INFISICAL_TOKEN
      if [ -n "$SECRETS" ]; then
        eval "$SECRETS"; unset SECRETS
        exec "${PY[@]}"
      fi
    fi
  fi
  echo "[media_worker_start] ⚠️ Infisical login başarısız — Drive olmadan başlıyor" >&2
fi
exec "${PY[@]}"
