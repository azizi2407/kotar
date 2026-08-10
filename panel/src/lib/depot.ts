// Videograf Deposu — /api/depot. ORTAK 5 GB alan (tüm videograflar + yönetim),
// dosya başına 500 MB. Kota her zaman SUNUCU yanıtından okunur; aşağıdaki sabitler
// yalnız istemci ön-kontrolü içindir (kullanıcıyı boşuna yükleme yapmaktan kurtarır).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiUpload } from "@/lib/api"

export interface DepotFile {
  id: number
  file_id: string
  file_name: string
  mime_type: string | null
  file_size: number
  note: string | null
  uploaded_by: string | null
  uploader_name: string | null
  uploaded_at: string | null
}

export interface DepotQuota {
  used: number
  limit: number
  remaining: number
  pct: number
  file_limit: number
  over_quota: boolean
}

/** Backend `depot.BLOCKED_EXT` aynası — çalıştırılabilir dosyalar depoya girmez
 *  (dosyalar bağlantıyla herkese açık; konan bir .exe kimlik avı aracına döner). */
export const BLOCKED_EXT = new Set([
  "exe", "msi", "bat", "cmd", "com", "scr", "pif", "ps1", "sh", "bash",
  "apk", "jar", "vbs", "wsf", "lnk", "dll", "deb", "rpm",
])

export function isBlockedFile(name: string): boolean {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""
  return BLOCKED_EXT.has(ext)
}

export function fmtBytes(n: number): string {
  if (!n) return "0 MB"
  if (n >= 1024 ** 3) {
    return `${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 }).format(n / 1024 ** 3)} GB`
  }
  return `${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(n / 1024 ** 2)} MB`
}

export function useDepotFiles(params: { q?: string } = {}) {
  const qs = params.q ? `?q=${encodeURIComponent(params.q)}` : ""
  return useQuery<{ files: DepotFile[]; quota: DepotQuota }>({
    queryKey: ["depot-files", params.q ?? ""],
    queryFn: () => apiGet(`/depot/files${qs}`),
  })
}

export function useDepotQuota() {
  return useQuery<DepotQuota>({
    queryKey: ["depot-quota"],
    queryFn: () => apiGet("/depot/quota").then((d) => d.quota),
  })
}

function useDepotInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["depot-files"] })
    qc.invalidateQueries({ queryKey: ["depot-quota"] })
  }
}

export function useUploadDepotFile() {
  const invalidate = useDepotInvalidator()
  return useMutation({
    mutationFn: (v: { file: File; note?: string; onProgress?: (pct: number) => void }) => {
      const form = new FormData()
      form.append("file", v.file)
      if (v.note) form.append("note", v.note)
      return apiUpload("/depot/upload", form, v.onProgress)
    },
    onSuccess: invalidate,
  })
}

export function useDeleteDepotFile() {
  const invalidate = useDepotInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/depot/files/${id}`),
    onSuccess: invalidate,
  })
}
