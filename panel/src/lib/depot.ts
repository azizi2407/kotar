// Videographer Depot — /api/depot. SHARED 5 GB space (all videographers +
// management), 500 MB per file. The quota is always read from the SERVER
// response; the constants below are for client-side pre-checks only (saves
// the user from a pointless upload).
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

/** Mirrors backend `depot.BLOCKED_EXT` — executable files don't get into the
 *  depot (files are publicly accessible via link; a placed .exe would turn into a phishing tool). */
export const BLOCKED_EXT = new Set([
  "exe", "msi", "bat", "cmd", "com", "scr", "pif", "ps1", "sh", "bash",
  "apk", "jar", "vbs", "wsf", "lnk", "dll", "deb", "rpm",
])

export function isBlockedFile(name: string): boolean {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : ""
  return BLOCKED_EXT.has(ext)
}

// Note: "0 MB"/"GB"/"MB" units are already language-independent (Latin
// abbreviations) — only the number format is localized, following the UI language.
export function fmtBytes(n: number, lang: "tr" | "en"): string {
  if (!n) return "0 MB"
  const locale = lang === "tr" ? "tr-TR" : "en-US"
  if (n >= 1024 ** 3) {
    return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(n / 1024 ** 3)} GB`
  }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(n / 1024 ** 2)} MB`
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
