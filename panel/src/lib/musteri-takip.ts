// Client Tracking data hooks — /api/client-tracking (management only).
// Item catalog + client × item status cells + a dated activity log;
// last ad / last shoot / responsible team are DERIVED on the backend (cannot be written from here).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  BookOpen, Camera, Clapperboard, Globe, MapPin, Megaphone, Palette, Printer,
  Share2, ShieldCheck, ShoppingCart, Sparkles, Tag, type LucideIcon,
} from "lucide-react"

import { apiDelete, apiGet, apiJson } from "@/lib/api"
import { useI18n } from "@/lib/i18n"

export type EntryStatus = "var" | "yok" | "surecte" | "ilgilenmiyor"

export interface TrackingItem {
  id: number
  key: string | null
  name: string
  category: string
  icon: string | null
  position: number
  active: boolean
}

export interface TrackingEntry {
  id: number
  client_id: number
  item_id: number
  status: EntryStatus
  status_date: string | null
  note: string | null
  url: string | null
  updated_at: string | null
  updated_by: string | null
}

export interface ActivityNote {
  id: number
  client_id: number
  item_id: number | null
  happened_on: string
  text: string
  created_by: string | null
  author_name: string | null
  created_at?: string | null
}

export interface TrackingSignals {
  last_ad_date: string | null
  ad_active: boolean
  ad_count: number
  last_shoot_date: string | null
  next_shoot_date: string | null
  team: { role_slot: string; user_id: string; name: string | null }[]
}

export interface TrackingRow {
  client_id: number
  client_name: string
  sector: string | null
  /** Key = item_id but as a STRING (JSON object key). Access via: entries[String(item.id)] */
  entries: Record<string, TrackingEntry>
  signals: TrackingSignals
  last_note: ActivityNote | null
  summary: Record<string, number> & { opportunity: number }
}

export interface TrackingDetail {
  client: { id: number; name: string; sector: string | null; client_email: string | null; instagram_url: string | null }
  notes: ActivityNote[]
  ads: { id: number; title: string | null; platform: string; status: string; start_date: string; end_date: string | null }[]
  shoots: { id: number; title: string | null; status: string; scheduled_date: string }[]
}

// --- labels & tones --------------------------------------------------

export function useEntryStatusLabels(): Record<string, string> {
  const { t } = useI18n()
  return {
    var: t("pages.clientTracking.entryStatus.var"),
    surecte: t("pages.clientTracking.entryStatus.surecte"),
    yok: t("pages.clientTracking.entryStatus.yok"),
    ilgilenmiyor: t("pages.clientTracking.entryStatus.ilgilenmiyor"),
  }
}

/** Badge tones — same class language as AdsPage's STATUS_TONE dictionary. */
export const ENTRY_STATUS_TONE: Record<string, string> = {
  var: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  surecte: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  yok: "bg-muted text-muted-foreground",
  ilgilenmiyor: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
}

/** Dot colors for the row's scope strip (the page's signature element). */
export const ENTRY_STATUS_DOT: Record<string, string> = {
  var: "bg-emerald-500",
  surecte: "bg-amber-500",
  yok: "bg-muted-foreground/20",
  ilgilenmiyor: "bg-rose-400/50",
}

export function useCategoryLabels(): Record<string, string> {
  const { t } = useI18n()
  return {
    hukuki: t("pages.clientTracking.category.hukuki"),
    dijital: t("pages.clientTracking.category.dijital"),
    tasarim: t("pages.clientTracking.category.tasarim"),
    uretim: t("pages.clientTracking.category.uretim"),
    reklam: t("pages.clientTracking.category.reklam"),
    diger: t("pages.clientTracking.category.diger"),
  }
}

export function useRoleSlotLabels(): Record<string, string> {
  const { t } = useI18n()
  return {
    designer: t("pages.clientTracking.roleSlot.designer"),
    content_creator: t("pages.clientTracking.roleSlot.contentCreator"),
    videographer_shoot: t("pages.clientTracking.roleSlot.videographerShoot"),
    videographer_edit: t("pages.clientTracking.roleSlot.videographerEdit"),
    manager: t("pages.clientTracking.roleSlot.manager"),
  }
}

/** The catalog's `icon` string is resolved via an ALLOWLIST — dynamically importing
 *  all of lucide would bloat the bundle. An unknown name silently falls back to `Tag`. */
export const ITEM_ICONS: Record<string, LucideIcon> = {
  ShieldCheck, Palette, Globe, ShoppingCart, MapPin, Share2,
  Megaphone, BookOpen, Printer, Camera, Clapperboard, Sparkles, Tag,
}

export function itemIcon(name: string | null): LucideIcon {
  return (name && ITEM_ICONS[name]) || Tag
}

// --- formatting -------------------------------------------------------

export function fmtDay(iso: string | null): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("tr-TR", {
    day: "2-digit", month: "short", year: "numeric",
  })
}

/** How many days ago from today? Returns negative for a future date. null = no date. */
export function daysSince(iso: string | null): number | null {
  if (!iso) return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  const today = new Date()
  return Math.floor((today.setHours(0, 0, 0, 0) - then.setHours(0, 0, 0, 0)) / 86_400_000)
}

/** Staleness color: none at all or 180+ days → red, 90-180 → amber, otherwise → normal.
 *  This is what visually carries the page's "what can we sell to whom" purpose. */
export function stalenessTone(iso: string | null): string {
  const days = daysSince(iso)
  if (days == null || days > 180) return "text-red-600 dark:text-red-400"
  if (days > 90) return "text-amber-600 dark:text-amber-400"
  return ""
}

// --- hooks ------------------------------------------------------------

export function useClientTracking(params: { q?: string } = {}) {
  const qs = params.q ? `?q=${encodeURIComponent(params.q)}` : ""
  return useQuery<{ items: TrackingItem[]; clients: TrackingRow[] }>({
    queryKey: ["client-tracking", params.q ?? ""],
    queryFn: () => apiGet(`/client-tracking${qs}`),
  })
}

export function useClientTrackingDetail(clientId: number | null) {
  return useQuery<TrackingDetail>({
    queryKey: ["client-tracking-detail", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/client-tracking/clients/${clientId}`),
  })
}

function useTrackingInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["client-tracking"] })
    qc.invalidateQueries({ queryKey: ["client-tracking-detail"] })
    qc.invalidateQueries({ queryKey: ["tracking-items"] })
  }
}

export interface EntryInput {
  status: EntryStatus
  status_date?: string | null
  note?: string | null
  url?: string | null
}

export function useSaveTrackingEntry() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (v: { clientId: number; itemId: number; body: EntryInput }) =>
      apiJson(`/client-tracking/clients/${v.clientId}/entries/${v.itemId}`, v.body, "PUT")
        .then((r) => r.entry as TrackingEntry),
    onSuccess: invalidate,
  })
}

export function useAddActivityNote() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (v: { clientId: number; text: string; happened_on?: string; item_id?: number | null }) =>
      apiJson(`/client-tracking/clients/${v.clientId}/notes`, {
        text: v.text, happened_on: v.happened_on, item_id: v.item_id ?? null,
      }).then((r) => r.note as ActivityNote),
    onSuccess: invalidate,
  })
}

export function useDeleteActivityNote() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/client-tracking/notes/${id}`),
    onSuccess: invalidate,
  })
}

export function useTrackingItems() {
  return useQuery<TrackingItem[]>({
    queryKey: ["tracking-items"],
    queryFn: () => apiGet("/client-tracking/items").then((d) => d.items),
  })
}

export interface ItemInput {
  name: string
  category: string
  icon?: string | null
  active?: boolean
}

export function useCreateTrackingItem() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (body: ItemInput) =>
      apiJson("/client-tracking/items", body).then((r) => r.item as TrackingItem),
    onSuccess: invalidate,
  })
}

export function useUpdateTrackingItem() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (v: { id: number; body: Partial<ItemInput> }) =>
      apiJson(`/client-tracking/items/${v.id}`, v.body, "PATCH").then((r) => r.item as TrackingItem),
    onSuccess: invalidate,
  })
}

export function useDeleteTrackingItem() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/client-tracking/items/${id}`),
    onSuccess: invalidate,
  })
}

export function useReorderTrackingItems() {
  const invalidate = useTrackingInvalidator()
  return useMutation({
    mutationFn: (order: number[]) =>
      apiJson("/client-tracking/items/reorder", { order }).then((r) => r.items as TrackingItem[]),
    onSuccess: invalidate,
  })
}
