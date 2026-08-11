-- Codex image pipeline: structured English JSON prompt field.
--
-- WHY MANUAL: `db.create_all()` creates a missing TABLE but does not ADD a
-- column to an existing table (same lesson as `notifications.severity` and
-- `design_files`).
--
-- ORDER MATTERS: run this script FIRST, THEN restart the app service and the
-- AI worker service.
--
-- Apply:
--   psql -U postgres -d agency -f scripts/alter_image_jobs_prompt_json.sql
-- Verify:
--   psql -U postgres -d agency -c "\d image_jobs" | grep prompt_json

BEGIN;

-- The schema-validated English image description produced by `claude -p`.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS prompt_json jsonb;

COMMIT;
