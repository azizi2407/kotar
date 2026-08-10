// Planlama Panosu veri hook'ları — /api/planning.
// Panolar kişi eksenli: 'management' (yönetim ortak) + 'user:<sub>' (kişi başına).
// Yazma DELTA'dır: {base_version, upsert:[...], delete:[...]} — tam dizi gönderilmez.
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { apiGet, apiJson, apiUpload } from "@/lib/api"

export type PlanningItemType = "card" | "note" | "region" | "edge" | "image"
export type PlanningStatus = "open" | "done"

export interface PlanningItem {
  item_key: string
  type: PlanningItemType
  title: string | null
  text: string | null
  color: string | null
  label: string | null
  link: string | null
  x: number
  y: number
  width: number | null
  height: number | null
  z: number
  from_key: string | null
  to_key: string | null
  status: PlanningStatus
  due_date: string | null
  assignee_sub: string | null
  assignee_name: string | null
  // Domain bağları — id'yi panel yazar, `*_name`/`*_title` sunucuda toplu sorguyla
  // çözülür (öğe başına lazy erişim N+1 muhafızını kırardı).
  client_id: number | null
  client_name: string | null
  shoot_task_id: number | null
  shoot_title: string | null
  ad_campaign_id: number | null
  campaign_title: string | null
  extra: Record<string, unknown>
  rev: number
  updated_at: string | null
  updated_by: string | null
  updated_by_name: string | null
}

export interface PlanningBoard {
  key: string
  kind: "management" | "user"
  owner_sub: string | null
  title: string
  version: number
  updated_at: string | null
  last_modified_by: string | null
  last_modified_name: string | null
  item_count: number
}

export interface BoardVersion {
  version: number
  updated_at: string | null
  last_modified_by: string | null
  last_modified_name: string | null
  item_count: number
}

export interface PatchResult {
  board: PlanningBoard
  applied: PlanningItem[]
  conflicts: string[]
  stale: boolean
  items: PlanningItem[] | null
}

export const MANAGEMENT_KEY = "management"

export const ITEM_STATUS_LABELS: Record<string, string> = {
  open: "Açık",
  done: "Bitti",
}

export const COLORS = [
  "#e0e7ff", "#dcfce7", "#fef9c3", "#fee2e2",
  "#f3e8ff", "#e0f2fe", "#fce7f3", "#f1f5f9",
]

export const DEFAULT_SIZE: Record<PlanningItemType, { width: number; height: number }> = {
  card: { width: 220, height: 120 },
  note: { width: 180, height: 150 },
  region: { width: 420, height: 320 },
  edge: { width: 0, height: 0 },
  image: { width: 260, height: 200 },
}

// --- pano görselleri (2026-07-28) ----------------------------------------
// Dosya SUNUCUDA (`data/planning-images/<board_id>/`), öğe ona `extra.image`
// ile işaret eder. `link` kolonu kullanılmadı: orada http(s) doğrulaması var,
// bu ise bir dosya adı.

export interface PlanningImage {
  name: string
  width: number
  height: number
}

/** Öğenin görsel bilgisi (yoksa null). `extra` jsonb'de yaşar → ALTER gerekmedi. */
export function imageOf(it: PlanningItem): PlanningImage | null {
  const im = it.extra?.image as PlanningImage | undefined
  return im && typeof im.name === "string" && im.name ? im : null
}

/** Görselin servis URL'i — oturumlu uç, `<img src>` cookie ile çeker. */
export function planningImageUrl(boardKey: string, name: string): string {
  return `/api/planning/boards/${encodeURIComponent(boardKey)}/images/${encodeURIComponent(name)}`
}

export const IMAGE_MAX_BYTES = 10 * 1024 * 1024
// Sunucunun kabul ettiği türler (`planning_images.ALLOWED`). SVG bilerek yok.
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"]

/** Panoya yapıştırılan/sürüklenen dosyayı yükler → `{name,width,height,size}`.
 *  Öğe yazımı AYRI: normal delta akışıyla (undo/çakışma) gider. */
export async function uploadPlanningImage(
  boardKey: string, file: File,
): Promise<PlanningImage & { size: number }> {
  const form = new FormData()
  form.append("file", file)
  const d = await apiUpload(`/planning/boards/${encodeURIComponent(boardKey)}/images`, form)
  return d.image
}

/** Yeni görsel öğesinin tuval boyutu — en-boy oranını korur, ekrana sığdırır. */
export function imageBoxSize(w: number, h: number, max = 360): { width: number; height: number } {
  if (!w || !h) return DEFAULT_SIZE.image
  const scale = Math.min(1, max / Math.max(w, h))
  return { width: Math.max(SNAP, Math.round(w * scale)), height: Math.max(SNAP, Math.round(h * scale)) }
}

// Tuval sabitleri. Eskiden `planlama-geometry.ts`'te yaşıyorlardı; o dosya React
// Flow'a geçişte silindi (zoom/pan/culling matematiğinin tamamını RF sağlıyor).
// Korunan tek sözleşme bu üç değer + 8 px ızgara.
export const SNAP = 8
export const MIN_ZOOM = 0.2
export const MAX_ZOOM = 2.5

// Kilit ve gruplama `extra` jsonb'de taşınır — YENİ KOLON GEREKTİRMEZ.
// `extra` 2026-07-26'dan beri sunucuda shallow-MERGE edildiği için bir anahtarı
// yazmak diğerlerini silmez; bu iki bayrak bu yüzden güvenle orada yaşayabiliyor.
// (Kaldırmak için değeri `null` gönderilir.)

/** Kilitli öğe: taşınamaz, boyutlandırılamaz, silinemez, bağlanamaz. */
export function isLocked(it: PlanningItem): boolean {
  return it.extra?.locked === true
}

/** Bu öğe bir gruba (bölgeye) ait mi — aitse ebeveynin item_key'i.
 *  Grup içindeki öğenin `x`/`y`'si EBEVEYNE GÖRELİDİR (React Flow sözleşmesi). */
export function parentKeyOf(it: PlanningItem): string | null {
  const p = it.extra?.parent_key
  return typeof p === "string" && p ? p : null
}

/** 8 px ızgaraya hizala — sürükleme boyunca DEĞİL, yalnız bırakınca uygulanır
 *  (RF'in `snapToGrid`'i sürükleme boyunca hizalar, o his daha katı). */
export function snap8(v: number): number {
  return Math.round(v / SNAP) * SNAP
}

/** Son tarih rozeti tonu — geçmiş kırmızı, 2 gün içinde amber (musteri-takip sınıf dili). */
export function dueTone(due: string | null, status: PlanningStatus): string {
  if (!due || status === "done") return "bg-muted text-muted-foreground"
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const target = new Date(due)
  target.setHours(0, 0, 0, 0)
  const days = Math.round((target.getTime() - today.getTime()) / 86_400_000)
  if (days < 0) return "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200"
  if (days <= 2) return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
  return "bg-muted text-muted-foreground"
}

export function fmtDay(iso: string | null): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("tr-TR", { day: "2-digit", month: "short" })
}

/** Çakışmayan öğe anahtarı. crypto.randomUUID varsa onu kullanır (eski kod
 *  Date.now()^performance.now() ile teorik çakışma riski taşıyordu). */
export function newItemKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return "i" + uuid.replace(/-/g, "").slice(0, 20)
  return "i" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36)
}

// --- hook'lar ------------------------------------------------------------

export function useBoards() {
  return useQuery<PlanningBoard[]>({
    queryKey: ["planning-boards"],
    queryFn: () => apiGet("/planning/boards").then((d) => d.boards),
  })
}

export function boardQueryKey(boardKey: string) {
  return ["planning-board", boardKey] as const
}

export function useBoard(boardKey: string) {
  return useQuery<{ board: PlanningBoard; items: PlanningItem[] }>({
    queryKey: boardQueryKey(boardKey),
    queryFn: () => apiGet(`/planning/boards/${encodeURIComponent(boardKey)}`),
    // Tazeleme ucuz /version ucundan sürülür; bu sorgu kendiliğinden yenilenmez ki
    // sürükleme sırasında altımızdan veri değişmesin.
    staleTime: Infinity,
    refetchOnMount: true,
  })
}

/** Ucuz sürüm yoklaması — tam panoyu (600 öğe) çekmeden "biri yazdı mı?" sorar. */
export function useBoardVersion(boardKey: string, enabled: boolean) {
  return useQuery<BoardVersion>({
    queryKey: ["planning-version", boardKey],
    queryFn: () => apiGet(`/planning/boards/${encodeURIComponent(boardKey)}/version`),
    enabled,
    refetchInterval: 8000,
    refetchIntervalInBackground: false,
  })
}

export interface PlanningDelta {
  base_version?: number
  upsert?: Partial<PlanningItem>[]
  delete?: string[]
}

// --- domain bağları ------------------------------------------------------

export interface Linkables {
  clients: { id: number; name: string }[]
  users: { sub: string; name: string; role: string }[]
  shoot_tasks: { id: number; title: string; scheduled_date: string | null; client_name: string | null }[]
  ad_campaigns: { id: number; title: string; platform: string; client_name: string | null }[]
}

/** Karta bağlanabilecek kayıtlar — TEK uç, TEK rol kapısı.
 *  Yetkisi olmayan bölüm BOŞ DİZİ döner (403 değil) → panel yalnız dolu bölümü
 *  render eder, rol farkı kendiliğinden doğru olur. Tasarımcı kampanya bölümünü
 *  hiç görmez çünkü dizi boş gelir. */
export function useLinkables(q: string) {
  return useQuery<Linkables>({
    queryKey: ["planning-linkables", q],
    queryFn: () => apiGet(`/planning/linkables${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    staleTime: 60_000,
  })
}

export interface AssignedItem {
  board_key: string
  board_title: string
  item_key: string
  type: PlanningItemType
  title: string | null
  label: string | null
  status: PlanningStatus
  due_date: string | null
  client_id: number | null
  client_name: string | null
  shoot_title: string | null
  campaign_title: string | null
}

/** Bir kişiye atanmış kartlar — PANO SINIRINI AŞAR, salt-okunur.
 *  Yönetim panosundaki kart da döner: bilinçli yetki gediği (bkz. planning.py
 *  `assigned_items` docstring'i). Düzenleme yalnız kartın kendi panosundan. */
export function useAssigned(assigneeSub: string | null, enabled = true) {
  return useQuery<{ items: AssignedItem[] }>({
    queryKey: ["planning-assigned", assigneeSub],
    queryFn: () => apiGet(`/planning/assigned${assigneeSub ? `?assignee_sub=${encodeURIComponent(assigneeSub)}` : ""}`),
    enabled,
    staleTime: 30_000,
  })
}

/** Delta yazma — HOOK DEĞİL, düz fonksiyon.
 *
 *  `boardKey` argüman olarak gelir, bir closure'a SABİTLENMEZ. `usePlanningStore`
 *  bunu doğrudan çağırıyor: mutation nesnesini ref'te taşımak pano değişiminde
 *  yazmayı YANLIŞ PANOYA gönderiyordu (unmount temizliği eski `flush`'ı çağırır
 *  ama ref o an çoktan yeni panonun mutation'ını tutar). Tek gerçek kaynak burası;
 *  `usePlanningPatch` de bunu sarar. */
export async function patchBoardItems(qc: QueryClient, boardKey: string,
                                      delta: PlanningDelta): Promise<PatchResult> {
  const res = await apiJson(
    `/planning/boards/${encodeURIComponent(boardKey)}/items`, delta, "PATCH") as PatchResult
  qc.setQueryData(["planning-version", boardKey], {
    version: res.board.version,
    updated_at: res.board.updated_at,
    last_modified_by: res.board.last_modified_by,
    last_modified_name: res.board.last_modified_name,
    item_count: res.board.item_count,
  })
  return res
}

export function usePlanningPatch(boardKey: string) {
  const qc = useQueryClient()
  return useMutation<PatchResult, Error, PlanningDelta>({
    mutationFn: (delta) => patchBoardItems(qc, boardKey, delta),
  })
}
