// Ad Tracking data hooks — /api/ads (management only).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson } from "@/lib/api"
import { useI18n } from "@/lib/i18n"

export type AdPlatform = "meta" | "google" | "tiktok" | "other"
export type AdStatus = "planned" | "active" | "finished"

export interface AdCampaign {
  id: number
  client_id: number
  client_name: string | null
  title: string | null
  platform: AdPlatform
  start_date: string
  end_date: string | null
  amount_spent: number
  status: AdStatus
  reach: number | null
  clicks: number | null
  notes: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AdSummary {
  total_amount: number
  count: number
  by_client: { client_id: number; client_name: string; total: number; count: number }[]
}

export interface AdFilters {
  client_id?: number | null
  status?: string
  platform?: string
  from?: string
  to?: string
}

export function usePlatformLabels(): Record<string, string> {
  const { t } = useI18n()
  return {
    meta: t("pages.ads.platform.meta"),
    google: t("pages.ads.platform.google"),
    tiktok: t("pages.ads.platform.tiktok"),
    other: t("pages.ads.platform.other"),
  }
}

export function useStatusLabels(): Record<string, string> {
  const { t } = useI18n()
  return {
    planned: t("pages.ads.status.planned"),
    active: t("pages.ads.status.active"),
    finished: t("pages.ads.status.finished"),
  }
}

function qs(f: AdFilters) {
  const p = new URLSearchParams()
  if (f.client_id) p.set("client_id", String(f.client_id))
  if (f.status) p.set("status", f.status)
  if (f.platform) p.set("platform", f.platform)
  if (f.from) p.set("from", f.from)
  if (f.to) p.set("to", f.to)
  const s = p.toString()
  return s ? `?${s}` : ""
}

export function useAds(filters: AdFilters) {
  return useQuery<{ campaigns: AdCampaign[]; summary: AdSummary }>({
    queryKey: ["ads", filters],
    queryFn: () => apiGet(`/ads${qs(filters)}`),
  })
}

function useAdsInvalidator() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: ["ads"] })
}

export interface AdInput {
  client_id: number
  title?: string
  platform: AdPlatform
  start_date: string
  end_date?: string | null
  amount_spent: string | number
  status: AdStatus
  reach?: string | number | null
  clicks?: string | number | null
  notes?: string
}

export function useCreateAd() {
  const invalidate = useAdsInvalidator()
  return useMutation({
    mutationFn: (body: AdInput) => apiJson("/ads", body).then((r) => r.campaign as AdCampaign),
    onSuccess: invalidate,
  })
}

export function useUpdateAd() {
  const invalidate = useAdsInvalidator()
  return useMutation({
    mutationFn: (v: { id: number; body: Partial<AdInput> }) =>
      apiJson(`/ads/${v.id}`, v.body, "PATCH").then((r) => r.campaign as AdCampaign),
    onSuccess: invalidate,
  })
}

export function useDeleteAd() {
  const invalidate = useAdsInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/ads/${id}`),
    onSuccess: invalidate,
  })
}

// ₺ formatting (TRY currency; the number format follows the UI language)
export function fmtTRY(n: number, lang: "tr" | "en") {
  return new Intl.NumberFormat(lang === "tr" ? "tr-TR" : "en-US", {
    style: "currency",
    currency: "TRY",
    maximumFractionDigits: 2,
  }).format(n || 0)
}

export function fmtDateRange(start: string, end: string | null, ongoingLabel: string, lang: "tr" | "en") {
  const f = (s: string) => new Date(s).toLocaleDateString(lang === "tr" ? "tr-TR" : "en-US", { day: "2-digit", month: "short", year: "numeric" })
  if (!end) return `${f(start)} → ${ongoingLabel}`
  return `${f(start)} → ${f(end)}`
}
