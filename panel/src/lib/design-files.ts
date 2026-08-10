// Tasarım çalışma dosyaları (2026-08-07) — müşteri bazlı, sürümlü kaynak dosyalar.
// Dosyalar sunucuda kanonik; indirme /api üzerinden (Drive linki DEĞİL) — yetki
// her indirmede doğrulanıyor.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson, apiUpload } from "./api"

export interface DesignVersion {
  id: number
  file_id: number
  version_no: number
  file_name: string
  mime_type: string | null
  file_size: number
  note: string | null
  sha256: string
  /** Drive kopyası oluştu mu — false ise panelde "Drive'a kopyalanmadı" rozeti. */
  drive_ok: boolean
  uploader_name: string | null
  uploaded_at: string | null
  can_delete: boolean
  // Çöp kutusu alanları (2026-08-08) — canlı listede daima null/false döner,
  // yalnız çöp kutusu yanıtında (`useTrash`) anlamlı.
  deleted_at: string | null
  deleted_by: string | null
  deleter_name: string | null
  /** Geri alma yetkisi BACKEND'te hesaplanır — panel kuralı yeniden kurmaz. */
  can_restore: boolean
  /** Kalıcı silme yetkisi yalnız yönetimde — BACKEND'te hesaplanır. */
  can_purge: boolean
}

export interface DesignFileItem {
  id: number
  client_id: number
  title: string
  tags: string[]
  creator_name: string | null
  created_at: string | null
  current: DesignVersion | null
  version_count: number
  /** Silme yetkisi BACKEND'te hesaplanır — panel kuralı yeniden kurmaz. */
  can_delete: boolean
  // Çöp kutusu alanları (2026-08-08) — canlı listede daima null/false döner.
  deleted_at: string | null
  deleted_by: string | null
  deleter_name: string | null
  can_restore: boolean
  can_purge: boolean
  /** Tasarımcının "kalıcı silinsin" talebi — yalnız işaret, hiçbir şeyi silmez. */
  purge_requested_at: string | null
  purge_requested_by: string | null
  purge_requester_name: string | null
}

export interface DesignTrash {
  /** Silinmiş DOSYALAR (tüm sürümleriyle birlikte çöpte). */
  files: DesignFileItem[]
  /** Hâlâ yaşayan dosyaların TEKİL silinmiş sürümleri. */
  versions: DesignVersion[]
  /** Çöpteki TOPLAM boyut (bayt) — tamamen silinmiş dosyalar + tekil silinmiş sürümler dahil. */
  trash_bytes: number
}

export interface Quota {
  used: number
  limit: number
  remaining: number
  pct: number
}

export function useDesignFiles(clientId: number | null) {
  return useQuery<{ files: DesignFileItem[]; quota: Quota }>({
    queryKey: ["design-files", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/design-files/client/${clientId}`),
  })
}

export function useFileVersions(fileId: number | null) {
  return useQuery<DesignVersion[]>({
    queryKey: ["design-file-versions", fileId],
    enabled: fileId != null,
    queryFn: () => apiGet(`/design-files/${fileId}/versions`).then((d) => d.versions),
  })
}

// Çöp kutusu — yalnız bölüm açılınca mount edilecek bileşenden çağrılır
// (`SurumGecmisi` ile aynı desen), kapalıyken sorgu atmaz.
export function useTrash(clientId: number | null) {
  return useQuery<DesignTrash>({
    queryKey: ["design-files-trash", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/design-files/client/${clientId}/trash`),
  })
}

function useInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["design-files"] })
    qc.invalidateQueries({ queryKey: ["design-file-versions"] })
    qc.invalidateQueries({ queryKey: ["design-files-trash"] })
  }
}

export function useUploadDesignFile(clientId: number) {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ file, title, tags, note, onProgress }: {
      file: File; title: string; tags: string[]; note?: string
      onProgress?: (pct: number) => void
    }) => {
      const form = new FormData()
      form.append("file", file)
      form.append("title", title)
      form.append("tags", JSON.stringify(tags))
      if (note) form.append("note", note)
      // retry KAPALI: bu uç IDEMPOTENT DEĞİL — her POST yeni bir dosya (v1)
      // yaratır. Bağlantı, sunucu commit'i bitirip yanıt dönerken koparsa
      // (apiUpload'taki not) status 0 görünür ama yükleme ZATEN olmuştur;
      // otomatik retry aynı içeriği ikinci kez commit'leyip mükerrer dosya +
      // çift kota düşümü üretir.
      return apiUpload(`/design-files/client/${clientId}`, form, onProgress, false)
    },
    onSuccess: tazele,
  })
}

export function useUploadVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ fileId, file, note, onProgress }: {
      fileId: number; file: File; note?: string
      onProgress?: (pct: number) => void
    }) => {
      const form = new FormData()
      form.append("file", file)
      if (note) form.append("note", note)
      // retry KAPALI — aynı gerekçe: her POST yeni bir sürüm yaratır (idempotent
      // değil), yeniden deneme mükerrer sürüm + çift kota düşümüne yol açar.
      return apiUpload(`/design-files/${fileId}/versions`, form, onProgress, false)
    },
    onSuccess: tazele,
  })
}

export function usePatchDesignFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ fileId, title, tags }: { fileId: number; title?: string; tags?: string[] }) =>
      apiJson(`/design-files/${fileId}`, { title, tags }, "PATCH"),
    onSuccess: tazele,
  })
}

export function useDeleteDesignFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (fileId: number) => apiDelete(`/design-files/${fileId}`),
    onSuccess: tazele,
  })
}

export function useDeleteVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (versionId: number) => apiDelete(`/design-files/versions/${versionId}`),
    onSuccess: tazele,
  })
}

// --- çöp kutusu: geri alma / kalıcı silme --------------------------------

export function useRestoreFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (fileId: number) => apiJson(`/design-files/${fileId}/restore`, {}),
    onSuccess: tazele,
  })
}

export function useRestoreVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (versionId: number) => apiJson(`/design-files/versions/${versionId}/restore`, {}),
    onSuccess: tazele,
  })
}

/** Tasarımcının "kalıcı silinsin" işaretini koy/kaldır — hiçbir şeyi silmez. */
export function usePurgeRequest() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ fileId, requested }: { fileId: number; requested: boolean }) =>
      apiJson(`/design-files/${fileId}/purge-request`, { requested }),
    onSuccess: tazele,
  })
}

/** Kalıcı sil — yalnız yönetim, geri alınamaz. */
export function usePurgeFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (fileId: number) => apiDelete(`/design-files/${fileId}/purge`, { confirm: true }),
    onSuccess: tazele,
  })
}

export function usePurgeVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (versionId: number) =>
      apiDelete(`/design-files/versions/${versionId}/purge`, { confirm: true }),
    onSuccess: tazele,
  })
}

export function designFileDownloadUrl(versionId: number) {
  return `/api/design-files/versions/${versionId}/download`
}

export function formatBytes(n: number) {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

// Uzantıya göre renk — tasarımcı listeyi tarayarak "psd nerede" diye aramasın.
export function extBadge(fileName: string) {
  const ext = (fileName.split(".").pop() || "").toLowerCase()
  const renk: Record<string, string> = {
    psd: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
    ai: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
    indd: "bg-pink-500/15 text-pink-700 dark:text-pink-400",
    aep: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
    zip: "bg-muted text-muted-foreground",
  }
  return { ext: ext.toUpperCase() || "DOSYA", cls: renk[ext] ?? "bg-muted text-muted-foreground" }
}
