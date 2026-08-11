// Monthly report data hooks (2026-08-07). Contract: reports.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiGet, apiJson, apiUpload } from "./api"

export interface ReportRow {
  id: number
  client_id: number | null
  client_name: string
  period: string
  /** Address while sharing is on; null if revoked (the record stays, the link dies). */
  token: string | null
  revoked: boolean
  created_at: string | null
  warnings: string[]
  ozet: Record<string, number | null>
  reklam_var: boolean
}

export interface SkippedRow {
  client_name: string
  reason: string
  missing: string[]
}

export interface GenerateResult {
  reports: ReportRow[]
  skipped: SkippedRow[]
  found_clients: string[]
}

export function useReports(period?: string) {
  const qs = period ? `?period=${encodeURIComponent(period)}` : ""
  return useQuery<ReportRow[]>({
    queryKey: ["reports", period ?? "hepsi"],
    queryFn: () => apiGet(`/reports${qs}`).then((d) => d.reports),
  })
}

export function useReportPeriods() {
  return useQuery<{ period: string; count: number }[]>({
    queryKey: ["report-periods"],
    queryFn: () => apiGet("/reports/periods").then((d) => d.periods),
  })
}

// CSVs go as multipart. `paths` = the browser's webkitRelativePath (populated on
// folder selection, just the file name on individual file selection) — the backend derives
// the client split from this, so it MUST stay perfectly aligned with the file order.
export function useGenerateReports() {
  const qc = useQueryClient()
  return useMutation<GenerateResult, Error,
    { files: File[]; paths: string[]; period: string; client_name: string }>({
    mutationFn: ({ files, paths, period, client_name }) => {
      const form = new FormData()
      form.append("period", period)
      form.append("client_name", client_name)
      files.forEach((f, i) => {
        form.append("files", f)
        form.append("paths", paths[i] ?? f.name)
      })
      return apiUpload("/reports/generate", form) as Promise<GenerateResult>
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reports"] })
      qc.invalidateQueries({ queryKey: ["report-periods"] })
    },
  })
}

export function useShareReport() {
  const qc = useQueryClient()
  return useMutation<{ report: ReportRow }, Error, { id: number; shared: boolean }>({
    mutationFn: ({ id, shared }) => apiJson(`/reports/${id}/share`, { shared }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reports"] }),
  })
}

export function useDeleteReport() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, number>({
    mutationFn: (id) => apiJson(`/reports/${id}`, {}, "DELETE"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reports"] })
      qc.invalidateQueries({ queryKey: ["report-periods"] })
    },
  })
}

export function reportHtmlUrl(id: number) {
  return `/api/reports/${id}/html`
}

export function reportPdfUrl(id: number) {
  return `/api/reports/${id}/pdf`
}

export function reportPublicUrl(token: string) {
  return `${window.location.origin}/rapor/${token}`
}

// "2026-07" → "Temmuz 2026" (tr) / "July 2026" (en). Since this file can't use a
// React hook, `lang` is taken directly as a parameter — the calling component
// passes in the `lang` it got from `useI18n()`.
const AYLAR_TR = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
const AYLAR_EN = ["", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"]

export function periodLabel(period: string, lang: "tr" | "en" = "tr") {
  const [y, m] = period.split("-")
  const aylar = lang === "en" ? AYLAR_EN : AYLAR_TR
  return `${aylar[Number(m)] ?? m} ${y}`
}

// Default period is LAST MONTH: the report is prepared after the month ends, so
// suggesting the current month would mean a manual correction almost every time.
export function gecenAy() {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}
