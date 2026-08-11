-- Design working files: trash + restore + management-approved permanent
-- delete.
--
-- WHY MANUAL: app.py calls `db.create_all()` at startup; that creates a
-- missing TABLE but does not create a column added to an EXISTING table
-- (same lesson as `notifications.severity`). `design_files`/
-- `design_file_versions` are EMPTY (0 rows) in a fresh deploy, so this ALTER
-- is risk-free there.
--
-- ORDER MATTERS: run this script FIRST, THEN restart the app service.
-- Otherwise the new code reads a column that doesn't exist yet and
-- /api/design-files/* returns 500.
--
-- Apply:
--   psql -U postgres -d agency -f scripts/alter_design_files_trash.sql
-- Verify:
--   psql -U postgres -d agency -c "\d design_files"
--   psql -U postgres -d agency -c "\d design_file_versions"
--
-- Everything is IF NOT EXISTS — running the script twice won't break anything.

BEGIN;

-- Who deleted it (never recorded before — soft-delete used to be a one-way door).
ALTER TABLE design_files ADD COLUMN IF NOT EXISTS deleted_by varchar(64);

-- The designer's "please permanently delete" flag — a warning badge visible to
-- management in the trash view, does NOT delete anything by itself (the
-- actual trigger is management-only).
ALTER TABLE design_files ADD COLUMN IF NOT EXISTS purge_requested_at timestamptz;
ALTER TABLE design_files ADD COLUMN IF NOT EXISTS purge_requested_by varchar(64);

ALTER TABLE design_file_versions ADD COLUMN IF NOT EXISTS deleted_by varchar(64);

COMMIT;
