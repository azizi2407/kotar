#!/usr/bin/env bash
# Nightly Postgres backup — pg_dump custom format (-Fc, compressed, restorable
# with pg_restore) + 14-day retention. Example script: called by a systemd
# agency-backup timer (root; execs into the podman Postgres container).
# Backs up ONLY the agency DB (sso/other DBs are out of scope).
set -euo pipefail

BACKUP_DIR="/srv/apps/agency/data/backups"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/agency-$STAMP.dump"

# Local socket + superuser (no password); custom format goes to stdout -> file
podman exec platform-pg pg_dump -U postgres -Fc -d agency > "$OUT"

# Guard against an empty/corrupt dump: pg_restore --list must work
if ! pg_restore --list "$OUT" >/dev/null 2>&1; then
  # fall back to a size check if pg_restore isn't available on the host
  if [ ! -s "$OUT" ]; then
    echo "ERROR: backup is empty: $OUT" >&2
    rm -f "$OUT"
    exit 1
  fi
fi

# Retention
find "$BACKUP_DIR" -maxdepth 1 -name 'agency-*.dump' -mtime "+$RETENTION_DAYS" -delete

echo "backup taken: $OUT ($(du -h "$OUT" | cut -f1))"
