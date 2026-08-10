#!/usr/bin/env bash
# agency Postgres gecelik yedek — pg_dump custom format (-Fc, sıkıştırılmış,
# pg_restore ile geri yüklenebilir) + 14 gün retention. systemd agency-backup
# tarafından çağrılır (root; rootful platform-pg'ye podman exec eder).
# YALNIZ agency DB'si (sso/diğer DB'ler kapsam dışı).
set -euo pipefail

BACKUP_DIR="/srv/apps/agency/data/backups"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/agency-$STAMP.dump"

# Yerel soket + superuser (parolasız); custom format stdout'a → dosyaya
podman exec platform-pg pg_dump -U postgres -Fc -d agency > "$OUT"

# Boş/bozuk dump koruması: pg_restore --list çalışmalı
if ! pg_restore --list "$OUT" >/dev/null 2>&1; then
  # host'ta pg_restore yoksa boyut kontrolüne düş
  if [ ! -s "$OUT" ]; then
    echo "HATA: yedek boş: $OUT" >&2
    rm -f "$OUT"
    exit 1
  fi
fi

# Retention
find "$BACKUP_DIR" -maxdepth 1 -name 'agency-*.dump' -mtime "+$RETENTION_DAYS" -delete

echo "yedek alındı: $OUT ($(du -h "$OUT" | cut -f1))"
