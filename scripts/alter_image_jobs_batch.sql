-- Codex image pipeline: weekly batch generation fields.
--
-- WHY MANUAL: app.py calls `db.create_all()` at startup; that creates a
-- missing TABLE but does not add a column to an EXISTING table (same lesson
-- as `notifications.severity` and `design_files`).
--
-- ORDER MATTERS: run this script FIRST, THEN restart the app service and the
-- AI worker service. Otherwise the new code reads a column that doesn't exist
-- yet and /api/imagegen/* returns 500.
--
-- Apply:
--   psql -U postgres -d agency -f scripts/alter_image_jobs_batch.sql
-- Verify:
--   psql -U postgres -d agency -c "\d image_jobs"
--
-- Everything is IF NOT EXISTS — running the script twice won't break anything.

BEGIN;

-- The batch's week ('YYYY-Www'). Kept alongside brief_id: if the brief is
-- regenerated (the brief_handler force path overwrites the row), gallery
-- grouping stays intact via week_iso.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS week_iso varchar(16);

-- Index (0-based) within brief.ideas[] — for finding the idea's title in the gallery.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS brief_idea_index integer;

-- with_text | clean. Stays NULL for single-image generation.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS variant varchar(16);

-- Used by the gallery query (client + week) and the idempotency check.
CREATE INDEX IF NOT EXISTS ix_imagejob_week ON image_jobs (client_id, week_iso);

COMMIT;
