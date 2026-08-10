#!/usr/bin/env bash
# svc-agency başlatıcı (systemd ExecStart).
# Infisical (kotar-secrets) machine identity ile Google Drive sırlarını
# (GOOGLE_SA_JSON, GOOGLE_DRIVE_TOKEN_JSON) sürece env olarak enjekte eder;
# diske yazmaz. Diğer config (DATABASE_URL, SECRET_KEY, SSO_*) systemd
# EnvironmentFile=/etc/kotar/agency/env'den gelir. Infisical erişilemezse
# gunicorn yine başlar (Drive özellikleri devre dışı, panel çalışır).
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
    # Bootstrap kimliğini süreçten temizle
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
        exec "${GUNICORN[@]}"
      fi
    fi
  fi
  echo "[start.sh] ⚠️ Infisical login başarısız/eksik — Google Drive sırları olmadan başlıyor (Drive çalışmaz)" >&2
fi

exec "${GUNICORN[@]}"
