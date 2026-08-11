// Auto layout — PURE functions (no React/DOM, input→output).
//
// Why a separate file: if the align/distribute/grid math lived inside the canvas
// component, it couldn't be tested and would be re-read on every render. Every
// function here returns `{item_key, x, y}` patches — passable directly to `store.commit()`.
import { DEFAULT_SIZE, parentKeyOf, SNAP, snap8, type PlanningItem } from "@/lib/planlama"

export interface Box { item_key: string; x: number; y: number; width: number; height: number }
export type Move = { item_key: string; x: number; y: number }

/** The edge an arrow connects to — SAME as the `Handle` ids in `PlanningNodes`. */
export type Yon = "l" | "r" | "t" | "b"

/** The item's actual size — falls back to the type default if `width/height` is NULL.
 *  NULL is common in old records; without normalizing, alignment would produce NaN. */
export function boxOf(it: PlanningItem): Box {
  const d = DEFAULT_SIZE[it.type] || DEFAULT_SIZE.card
  return {
    item_key: it.item_key, x: it.x, y: it.y,
    width: it.width || d.width, height: it.height || d.height,
  }
}

function sortByX(b: Box[]) { return [...b].sort((p, q) => p.x - q.x || p.y - q.y) }
function sortByY(b: Box[]) { return [...b].sort((p, q) => p.y - q.y || p.x - q.x) }

/** Align left edges to the leftmost one. */
export function alignLeft(boxes: Box[]): Move[] {
  if (boxes.length < 2) return []
  const x = snap8(Math.min(...boxes.map((b) => b.x)))
  return boxes.filter((b) => b.x !== x).map((b) => ({ item_key: b.item_key, x, y: b.y }))
}

/** Align top edges to the topmost one. */
export function alignTop(boxes: Box[]): Move[] {
  if (boxes.length < 2) return []
  const y = snap8(Math.min(...boxes.map((b) => b.y)))
  return boxes.filter((b) => b.y !== y).map((b) => ({ item_key: b.item_key, x: b.x, y }))
}

/** Center on the vertical axis (shared center X). */
export function alignCenterX(boxes: Box[]): Move[] {
  if (boxes.length < 2) return []
  const cx = boxes.reduce((s, b) => s + b.x + b.width / 2, 0) / boxes.length
  return boxes.map((b) => ({ item_key: b.item_key, x: snap8(cx - b.width / 2), y: b.y }))
                .filter((m, i) => m.x !== boxes[i].x)
}

/** Equalize vertical gaps — the two end items stay fixed, the ones in between are distributed. */
export function distributeY(boxes: Box[], gap = 24): Move[] {
  if (boxes.length < 3) return []
  const s = sortByY(boxes)
  const moves: Move[] = []
  let cursor = s[0].y + s[0].height + gap
  for (let i = 1; i < s.length - 1; i++) {
    const y = snap8(cursor)
    if (y !== s[i].y) moves.push({ item_key: s[i].item_key, x: s[i].x, y })
    cursor = y + s[i].height + gap
  }
  return moves
}

/** Equalize horizontal gaps. */
export function distributeX(boxes: Box[], gap = 24): Move[] {
  if (boxes.length < 3) return []
  const s = sortByX(boxes)
  const moves: Move[] = []
  let cursor = s[0].x + s[0].width + gap
  for (let i = 1; i < s.length - 1; i++) {
    const x = snap8(cursor)
    if (x !== s[i].x) moves.push({ item_key: s[i].item_key, x, y: s[i].y })
    cursor = x + s[i].width + gap
  }
  return moves
}

/** Arrange the selection into a grid — top-left corner is preserved, `cols` items per row.
 *  Cell width is based on the widest item, so differently-sized cards don't overlap. */
export function gridLayout(boxes: Box[], cols = 4, gap = 24): Move[] {
  if (!boxes.length) return []
  const ordered = sortByY(sortByX(boxes))
  const x0 = snap8(Math.min(...boxes.map((b) => b.x)))
  const y0 = snap8(Math.min(...boxes.map((b) => b.y)))
  const cw = Math.max(...boxes.map((b) => b.width)) + gap
  const ch = Math.max(...boxes.map((b) => b.height)) + gap
  const moves: Move[] = []
  ordered.forEach((b, i) => {
    const x = x0 + (i % cols) * cw
    const y = y0 + Math.floor(i / cols) * ch
    if (x !== b.x || y !== b.y) moves.push({ item_key: b.item_key, x, y })
  })
  return moves
}

/** Arrange into a single column (vertical list). */
export function columnLayout(boxes: Box[], gap = 16): Move[] {
  return gridLayout(boxes, 1, gap)
}

/** Duplicate/paste offset — a fixed shift so items don't stack on top of each other. */
export const PASTE_OFFSET = SNAP * 3

/** The item's ABSOLUTE box.
 *
 *  For an item with a parent (grouped into a region), `x/y` are RELATIVE TO THE PARENT
 *  — that's React Flow's contract, and it's stored that way in the DB too. Working with
 *  relative coordinates when computing arrow endpoints would make a grouped card's arrow
 *  come out from entirely the wrong edge. Since regions don't nest, a single level suffices. */
export function absBoxOf(it: PlanningItem, byKey: Map<string, PlanningItem>): Box {
  const b = boxOf(it)
  const pk = parentKeyOf(it)
  if (!pk) return b
  const p = byKey.get(pk)
  return p ? { ...b, x: b.x + p.x, y: b.y + p.y } : b
}

/** Which edges an arrow between two boxes should exit and enter from.
 *
 *  WHY THIS IS COMPUTED: `onConnect` wasn't saving the handle the user dragged from
 *  (`sourceHandle`/`targetHandle`), and the edge object didn't store a handle either;
 *  when no handle is given, React Flow falls back to the FIRST one in the list (`l` = left
 *  for us). The result: even an arrow dragged to a card on the right would exit from the
 *  LEFT and wrap around.
 *
 *  We chose to look at geometry instead of a stored handle: (1) OLD arrows carrying no
 *  handle info at all self-correct automatically, (2) when a card moves, the arrow
 *  straightens itself — a pinned edge would still wrap around after a move.
 *
 *  On a tie (|dx| == |dy|), horizontal is preferred; a horizontal arrow reads better
 *  visually for diagonal placements.
 */
export function bestHandles(a: Box, b: Box): [Yon, Yon] {
  const dx = (b.x + b.width / 2) - (a.x + a.width / 2)
  const dy = (b.y + b.height / 2) - (a.y + a.height / 2)
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ["r", "l"] : ["l", "r"]
  return dy >= 0 ? ["b", "t"] : ["t", "b"]
}

const YONLER: Yon[] = ["l", "r", "t", "b"]

/** READ and validate the handle stored in `extra`.
 *
 *  `extra` is free-form JSON: old records don't have the field at all, and a manually
 *  edited board could hold a nonsensical value. Anything we don't recognize counts as
 *  `null` → falls back to the computed value. */
export function storedHandle(extra: Record<string, unknown> | null | undefined,
                             alan: "from_handle" | "to_handle"): Yon | null {
  const v = extra?.[alan]
  return typeof v === "string" && (YONLER as string[]).includes(v) ? (v as Yon) : null
}

/** Is a direction consistent with the target vector?
 *  `r` only makes sense when the target is to the right, `t` only when it's above. */
function yonMakul(y: Yon, dx: number, dy: number): boolean {
  if (y === "r") return dx >= 0
  if (y === "l") return dx <= 0
  if (y === "b") return dy >= 0
  return dy <= 0
}

/** The arrow's edge pair: the handle the USER CHOSE takes priority, geometry is the fallback.
 *
 *  Why not simply "use whatever's stored": if the handle were pinned, moving the card to
 *  the opposite side of the arrow would still make the arrow wrap around the card — the
 *  exact bug that was reported. So the stored direction is only valid as long as the target
 *  is STILL in THAT direction; the moment it isn't, it falls back to the computed one. If the
 *  card is moved back, the stored choice kicks back in (the stored value is NOT DELETED, just
 *  ignored for the moment).
 *
 *  Source and target are evaluated separately: one may have a stored handle while the other
 *  doesn't (e.g. a card created by dragging into empty space has no target handle). */
export function resolveHandles(a: Box, b: Box,
                               stored: { from: Yon | null; to: Yon | null }): [Yon, Yon] {
  const [gs, gt] = bestHandles(a, b)
  const dx = (b.x + b.width / 2) - (a.x + a.width / 2)
  const dy = (b.y + b.height / 2) - (a.y + a.height / 2)
  return [
    stored.from && yonMakul(stored.from, dx, dy) ? stored.from : gs,
    // From the target box's perspective, the vector is REVERSED.
    stored.to && yonMakul(stored.to, -dx, -dy) ? stored.to : gt,
  ]
}

export type ZMod = "one" | "arkaya" | "enOne" | "enArkaya"

/** Layer order patches.
 *
 *  Four modes: one step forward/backward (`one`/`arkaya`) and bring to front/send to
 *  back (`enOne`/`enArkaya`).
 *
 *  In the absolute modes, the selection's INTERNAL order is preserved: items are sorted
 *  by their current `z` and given consecutive values. If they all got the same `z`, the
 *  relative order of overlapping cards would break every time "bring to front" is used.
 *
 *  `kutular` = all boxes on the board (edges excluded) — the extreme values come from there. */
export function zMoves(liste: PlanningItem[], kutular: PlanningItem[],
                       mod: ZMod): { item_key: string; z: number }[] {
  if (!liste.length) return []
  const enUst = Math.max(0, ...kutular.map((i) => i.z || 0))
  const enAlt = Math.min(0, ...kutular.map((i) => i.z || 0))
  const sirali = [...liste].sort((a, b) => (a.z || 0) - (b.z || 0))
  return sirali.map((it, i) => ({
    item_key: it.item_key,
    z: mod === "one" ? (it.z || 0) + 1
      : mod === "arkaya" ? (it.z || 0) - 1
      : mod === "enOne" ? enUst + 1 + i
      : enAlt - sirali.length + i,
  }))
}

/** Keys of arrows connected to deleted items.
 *
 *  WHY THIS EXISTS: deleting a card used to leave its arrows behind. React Flow's OWN
 *  delete path (Delete key → `deleteElements`) also removes connected edges, but the
 *  panel's own delete paths (the trash icon on a card, the right-click menu, bulk delete)
 *  bypass RF and go straight to the store, leaving arrows orphaned. An orphaned arrow
 *  stays in the DB as an invisible row, and RF prints `error008` on every render
 *  ("Couldn't create edge for source handle id"). 3 such rows were found in production
 *  (management board, 2026-08-09).
 *
 *  Doesn't re-return arrows that are already being deleted. */
export function connectedEdgeKeys(silinen: Iterable<string>,
                                  items: PlanningItem[]): string[] {
  const set = new Set(silinen)
  return items
    .filter((it) => it.type === "edge"
      && !set.has(it.item_key)
      && ((it.from_key && set.has(it.from_key)) || (it.to_key && set.has(it.to_key))))
    .map((it) => it.item_key)
}

/** Bounding rectangle of a set of boxes (used to align the cursor on paste). */
export function boundsOf(boxes: Box[]) {
  if (!boxes.length) return { x: 0, y: 0, width: 0, height: 0 }
  const x0 = Math.min(...boxes.map((b) => b.x))
  const y0 = Math.min(...boxes.map((b) => b.y))
  const x1 = Math.max(...boxes.map((b) => b.x + b.width))
  const y1 = Math.max(...boxes.map((b) => b.y + b.height))
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
}
