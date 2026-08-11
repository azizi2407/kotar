// Planning Board data hooks — /api/planning.
// Boards are person-centric: 'management' (shared management space) + 'user:<sub>' (per-person).
// Writes are DELTAS: {base_version, upsert:[...], delete:[...]} — the full array is never sent.
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
  // Domain links — the panel writes the id, `*_name`/`*_title` are resolved server-side
  // with a batch query (a per-item lazy lookup would break the N+1 guard).
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

/** Status labels — translated per language, so it's produced via `t()` (not a fixed
 *  Record). Callers pass in the `t` they got from `useI18n()`. */
export function itemStatusLabels(t: (key: string) => string): Record<PlanningStatus, string> {
  return {
    open: t("pages.planning.status.open"),
    done: t("pages.planning.status.done"),
  }
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

// --- board images (2026-07-28) ----------------------------------------
// The file lives ON THE SERVER (`data/planning-images/<board_id>/`), and the item
// points to it via `extra.image`. The `link` column wasn't used: it has http(s)
// validation, while this is just a file name.

export interface PlanningImage {
  name: string
  width: number
  height: number
}

/** The item's image info (null if none). Lives in `extra` jsonb → no ALTER needed. */
export function imageOf(it: PlanningItem): PlanningImage | null {
  const im = it.extra?.image as PlanningImage | undefined
  return im && typeof im.name === "string" && im.name ? im : null
}

/** The image's serving URL — a session-gated endpoint, fetched by `<img src>` with the cookie. */
export function planningImageUrl(boardKey: string, name: string): string {
  return `/api/planning/boards/${encodeURIComponent(boardKey)}/images/${encodeURIComponent(name)}`
}

export const IMAGE_MAX_BYTES = 10 * 1024 * 1024
// Types accepted by the server (`planning_images.ALLOWED`). SVG is deliberately excluded.
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"]

/** Uploads a file pasted/dragged onto the board → `{name,width,height,size}`.
 *  Writing the item is SEPARATE: it goes through the normal delta flow (undo/conflict). */
export async function uploadPlanningImage(
  boardKey: string, file: File,
): Promise<PlanningImage & { size: number }> {
  const form = new FormData()
  form.append("file", file)
  const d = await apiUpload(`/planning/boards/${encodeURIComponent(boardKey)}/images`, form)
  return d.image
}

/** Canvas size for a new image item — preserves aspect ratio, fits it on screen. */
export function imageBoxSize(w: number, h: number, max = 360): { width: number; height: number } {
  if (!w || !h) return DEFAULT_SIZE.image
  const scale = Math.min(1, max / Math.max(w, h))
  return { width: Math.max(SNAP, Math.round(w * scale)), height: Math.max(SNAP, Math.round(h * scale)) }
}

// Canvas constants. Used to live in `planlama-geometry.ts`; that file was deleted during
// the React Flow migration (RF handles all zoom/pan/culling math now).
// The only preserved contract is these three values + the 8 px grid.
export const SNAP = 8
export const MIN_ZOOM = 0.2
export const MAX_ZOOM = 2.5

// Lock and grouping are carried in the `extra` jsonb — NO NEW COLUMN NEEDED.
// Since `extra` has been shallow-MERGED on the server since 2026-07-26, writing one
// key doesn't delete the others; that's why these two flags can safely live there.
// (To remove one, send its value as `null`.)

/** Locked item: cannot be moved, resized, deleted, or connected. */
export function isLocked(it: PlanningItem): boolean {
  return it.extra?.locked === true
}

/** Whether this item belongs to a group (region) — if so, the parent's item_key.
 *  An item's `x`/`y` inside a group is RELATIVE TO THE PARENT (React Flow contract). */
export function parentKeyOf(it: PlanningItem): string | null {
  const p = it.extra?.parent_key
  return typeof p === "string" && p ? p : null
}

/** Snap to the 8 px grid — applied only on drop, NOT during dragging
 *  (RF's `snapToGrid` snaps during dragging, which feels stricter). */
export function snap8(v: number): number {
  return Math.round(v / SNAP) * SNAP
}

/** Due-date badge tone — overdue is red, within 2 days is amber (same class language as musteri-takip). */
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

/** A non-colliding item key. Uses crypto.randomUUID when available (the old code
 *  used Date.now()^performance.now(), which had a theoretical collision risk). */
export function newItemKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return "i" + uuid.replace(/-/g, "").slice(0, 20)
  return "i" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36)
}

// --- hooks ------------------------------------------------------------

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
    // Refreshing is driven by the cheap /version endpoint; this query doesn't refetch on
    // its own so that data doesn't change out from under us mid-drag.
    staleTime: Infinity,
    refetchOnMount: true,
  })
}

/** Cheap version poll — asks "did someone write?" without fetching the whole board (600 items). */
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

// --- domain links ------------------------------------------------------

export interface Linkables {
  clients: { id: number; name: string }[]
  users: { sub: string; name: string; role: string }[]
  shoot_tasks: { id: number; title: string; scheduled_date: string | null; client_name: string | null }[]
  ad_campaigns: { id: number; title: string; platform: string; client_name: string | null }[]
}

/** Records a card can link to — ONE endpoint, ONE role gate.
 *  A section the user isn't authorized for returns an EMPTY ARRAY (not 403) → the panel
 *  just renders the non-empty sections, so the role difference is automatically correct.
 *  A designer never sees the campaign section at all because the array comes back empty. */
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

/** Cards assigned to a person — CROSSES BOARD BOUNDARIES, read-only.
 *  Also returns cards from the management board: a deliberate authorization gap (see
 *  planning.py's `assigned_items` docstring). Editing is only possible from the card's own board. */
export function useAssigned(assigneeSub: string | null, enabled = true) {
  return useQuery<{ items: AssignedItem[] }>({
    queryKey: ["planning-assigned", assigneeSub],
    queryFn: () => apiGet(`/planning/assigned${assigneeSub ? `?assignee_sub=${encodeURIComponent(assigneeSub)}` : ""}`),
    enabled,
    staleTime: 30_000,
  })
}

/** Delta write — NOT A HOOK, a plain function.
 *
 *  `boardKey` comes in as an argument, it is NOT CAPTURED in a closure. `usePlanningStore`
 *  calls this directly: carrying the mutation object in a ref sent writes to the WRONG
 *  BOARD on a board switch (unmount cleanup calls the old `flush`, but by then the ref
 *  already holds the new board's mutation). This is the single source of truth;
 *  `usePlanningPatch` just wraps it. */
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
