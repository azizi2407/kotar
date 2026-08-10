-- Codex görsel hattı: haftalık parti üretimi alanları (2026-08-10)
--
-- NEDEN ELLE: app.py başlangıçta `db.create_all()` çağırır; bu eksik TABLOYU
-- yaratır ama VAR OLAN bir tabloya eklenen kolonu yaratmaz (`notifications.severity`
-- ve `design_files` dersleri).
--
-- SIRA KRİTİK: önce bu script, SONRA `sudo systemctl restart agency.service` ve
-- `systemctl --user restart agency-ai-worker.service`. Tersi durumda yeni kod
-- olmayan kolonu okur ve /api/imagegen/* 500 verir.
--
-- Uygulama:
--   sudo podman exec -i platform-pg psql -U postgres -d agency < scripts/alter_image_jobs_batch.sql
-- Doğrulama:
--   sudo podman exec platform-pg psql -U postgres -d agency -c "\d image_jobs"
--
-- Hepsi IF NOT EXISTS — script iki kez koşarsa patlamaz.

BEGIN;

-- Partinin haftası ('YYYY-Www'). brief_id yanında AYRICA tutulur: brief yeniden
-- üretilirse (brief_handler force yolu satırı üzerine yazar) galeri gruplaması
-- week_iso sayesinde ayakta kalır.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS week_iso varchar(16);

-- brief.ideas[] içindeki sıra (0-tabanlı) — galeride fikir başlığını bulmak için.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS brief_idea_index integer;

-- with_text | clean. Tekil üretimde NULL kalır.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS variant varchar(16);

-- Galeri sorgusu (client + hafta) ve idempotency kontrolü bu index'i kullanır.
CREATE INDEX IF NOT EXISTS ix_imagejob_week ON image_jobs (client_id, week_iso);

COMMIT;
