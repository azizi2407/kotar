-- Codex görsel hattı: İngilizce JSON prompt alanı (2026-08-10)
--
-- NEDEN ELLE: `db.create_all()` eksik TABLOYU yaratır ama var olan tabloya kolon
-- EKLEMEZ (`notifications.severity` ve `design_files` dersleri).
--
-- SIRA KRİTİK: önce bu script, SONRA `sudo systemctl restart agency.service` ve
-- `systemctl --user restart agency-ai-worker.service`.
--
-- Uygulama:
--   sudo podman exec -i platform-pg psql -U postgres -d agency < scripts/alter_image_jobs_prompt_json.sql
-- Doğrulama:
--   sudo podman exec platform-pg psql -U postgres -d agency -c "\d image_jobs" | grep prompt_json

BEGIN;

-- claude -p'nin ürettiği, şemaya göre doğrulanmış İngilizce görsel tarifi.
ALTER TABLE image_jobs ADD COLUMN IF NOT EXISTS prompt_json jsonb;

COMMIT;
