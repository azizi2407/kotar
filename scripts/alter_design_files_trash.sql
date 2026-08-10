-- Tasarım çalışma dosyaları: çöp kutusu + geri alma + yönetim onaylı kalıcı
-- silme (2026-08-08) — bkz. `.superpowers/sdd/2026-08-07-tasarim-calisma-dosyalari/
-- followup-silme-brief.md`
--
-- NEDEN ELLE: app.py başlangıçta `db.create_all()` çağırır; bu eksik TABLOYU
-- yaratır ama VAR OLAN bir tabloya eklenen kolonu yaratmaz (`notifications.severity`
-- dersi). `design_files`/`design_file_versions` prod'da BOŞ (0 satır) → ALTER
-- risksiz.
--
-- SIRA KRİTİK: önce bu script, SONRA `sudo systemctl restart agency.service`.
-- Tersi durumda yeni kod olmayan kolonu okur ve /api/design-files/* 500 verir.
--
-- Uygulama:
--   sudo podman exec -i platform-pg psql -U postgres -d agency < scripts/alter_design_files_trash.sql
-- Doğrulama:
--   sudo podman exec platform-pg psql -U postgres -d agency -c "\d design_files"
--   sudo podman exec platform-pg psql -U postgres -d agency -c "\d design_file_versions"
--
-- Hepsi IF NOT EXISTS — script iki kez koşarsa patlamaz.

BEGIN;

-- Kim sildi (önceden hiç kaydedilmiyordu — soft-delete tek yönlü bir kapıydı).
ALTER TABLE design_files ADD COLUMN IF NOT EXISTS deleted_by varchar(64);

-- Tasarımcının "kalıcı silinsin" işareti — çöp kutusunda yönetime görünen bir
-- uyarı rozeti, TEK BAŞINA hiçbir şeyi silmez (tetik yalnız yönetimde).
ALTER TABLE design_files ADD COLUMN IF NOT EXISTS purge_requested_at timestamptz;
ALTER TABLE design_files ADD COLUMN IF NOT EXISTS purge_requested_by varchar(64);

ALTER TABLE design_file_versions ADD COLUMN IF NOT EXISTS deleted_by varchar(64);

COMMIT;
