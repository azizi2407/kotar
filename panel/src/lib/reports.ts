// Aylık rapor veri hook'ları (2026-08-07). Sözleşme: reports.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiGet, apiJson, apiUpload } from "./api"

export interface ReportRow {
  id: number
  client_id: number | null
  client_name: string
  period: string
  /** Paylaşım açıkken adres; iptal edilmişse null (kayıt durur, link ölür). */
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

// CSV'ler multipart gider. `paths` = tarayıcının webkitRelativePath'i (klasör
// seçiminde dolu, tek tek dosya seçiminde dosya adı) — backend müşteri ayrımını
// buradan yapıyor, dosya sırasıyla birebir hizalı olmak ZORUNDA.
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

// "2026-07" → "Temmuz 2026"
const AYLAR = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

export function periodLabel(period: string) {
  const [y, m] = period.split("-")
  return `${AYLAR[Number(m)] ?? m} ${y}`
}

// Varsayılan dönem GEÇEN AY: rapor ay bittikten sonra hazırlanıyor, içinde
// bulunulan ayı önermek neredeyse her seferinde elle düzeltme demek olurdu.
export function gecenAy() {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}
