// Planning canvas — built on @xyflow/react (MIT).
//
// CONTROLLED MODE — `useNodesState` is DELIBERATELY NOT USED.
// The RF docs recommend it, but that hook keeps a THIRD copy of the array. Making
// it the source of truth would bring back the clobber bug fixed on 2026-07-25:
// when a server refetch arrives the array must be rebuilt, and the card under
// the user's finger jumps away.
// Single source of truth: `usePlanningStore`. `nodes` here is only DERIVED from it.
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react"
import {
  Background, ConnectionMode, Controls, MiniMap, Panel, ReactFlow,
  type Connection, type Edge, type EdgeChange, type FinalConnectionState,
  type Node, type NodeChange,
  MarkerType, useOnSelectionChange, useReactFlow,
} from "@xyflow/react"
import {
  AlignHorizontalJustifyCenter, AlignStartVertical, ArrowDownToLine, ArrowUpToLine,
  ArrowLeftRight, BoxSelect, BringToFront, ChevronLeft, ChevronRight, ClipboardPaste,
  Copy, Grid3x3,
  Group, Hand,
  ImagePlus,
  Keyboard, LayoutGrid, Lock, Maximize2, MousePointer2, Palette,
  Plus, Scan, SendToBack, SlidersHorizontal, SquareDashed, StickyNote, Tag, Trash2,
  Ungroup, Unlock, X,
} from "lucide-react"

import {
  ContextMenu, MenuItem, MenuSep, menuPosition, type MenuPos,
} from "@/components/planlama/BoardContextMenu"
import { PlanningEdge, type EdgeData } from "@/components/planlama/PlanningEdge"
import {
  CardNode, ImageNode, NoteNode, RegionNode, type NodeData,
} from "@/components/planlama/PlanningNodes"
import { ShortcutsHelp } from "@/components/planlama/ShortcutsHelp"
import { useI18n } from "@/lib/i18n"
import {
  COLORS, DEFAULT_SIZE, IMAGE_MAX_BYTES, IMAGE_TYPES, imageBoxSize, isLocked,
  MAX_ZOOM, MIN_ZOOM, newItemKey, parentKeyOf, SNAP, snap8, uploadPlanningImage,
  type PlanningItem, type PlanningItemType,
} from "@/lib/planlama"
import {
  absBoxOf, alignCenterX, alignLeft, alignTop, boundsOf, boxOf, connectedEdgeKeys,
  distributeX, distributeY, gridLayout, PASTE_OFFSET, resolveHandles, storedHandle,
  zMoves, type ZMod,
} from "@/lib/planlama-layout"
import { toast } from "sonner"

import type { ItemPatch } from "@/lib/usePlanningStore"
import { cn } from "@/lib/utils"

import "@xyflow/react/dist/style.css"

// MODULE-LEVEL and CONSTANT: if a new object is passed on every render, RF
// remounts all nodes and focus is lost while typing. The same rule applies to
// `edgeTypes` and `defaultEdgeOptions`.
const NODE_TYPES = { card: CardNode, note: NoteNode, region: RegionNode, image: ImageNode } as const
const EDGE_TYPES = { planning: PlanningEdge } as const
const DEFAULT_EDGE_OPTIONS = {
  type: "planning",
  // The visible line is 2px; the clickable strip must be THICKER than that or
  // selecting/right-clicking the arrow turns into pixel-hunting (RF default is 20).
  interactionWidth: 26,
  markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b", width: 18, height: 18 },
  style: { stroke: "#64748b", strokeWidth: 2 },
} as const
const SNAP_GRID: [number, number] = [SNAP, SNAP]

// Regions add their own `z` on top of this base value → they all stay behind
// cards but can still be ordered relative to each other.
const REGION_Z = -1000

const GROUP_PAD = 32          // padding the group region leaves around its content
const GROUP_HEADER = 28       // region title height

/** The toolbar (at the page level) commands the canvas through this interface.
 *  Adding items MUST go through here: where a new item lands depends on React
 *  Flow's viewport transform, and that information only exists INSIDE the provider. */
export interface PlanningFlowHandle {
  addItem: (type: PlanningItemType) => void
}

interface Props {
  boardKey: string
  items: PlanningItem[]
  byKey: Map<string, PlanningItem>
  matches: (it: PlanningItem) => boolean
  filterActive: boolean
  applyLocal: (patches: ItemPatch[]) => void
  commit: (patches: ItemPatch[]) => void
  remove: (keys: string[]) => void
  onPatch: (key: string, patch: Partial<PlanningItem>) => void
  onOpenDetail: (item: PlanningItem) => void
  readOnly?: boolean
}

function sizeOf(it: PlanningItem) {
  const d = DEFAULT_SIZE[it.type] || DEFAULT_SIZE.card
  return { width: it.width || d.width, height: it.height || d.height }
}

type Menu =
  | { kind: "node"; pos: MenuPos; key: string }
  | { kind: "selection"; pos: MenuPos }
  | { kind: "edge"; pos: MenuPos; key: string }
  | { kind: "pane"; pos: MenuPos; flow: { x: number; y: number } }

export const PlanningFlow = forwardRef<PlanningFlowHandle, Props>(function PlanningFlow({
  boardKey, items, byKey, matches, filterActive, applyLocal, commit, remove,
  onPatch, onOpenDetail, readOnly,
}: Props, ref) {
  const { t } = useI18n()
  const rf = useReactFlow()
  const [paletteFor, setPaletteFor] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  // Edge selection follows the SAME logic as nodes: RF reads `selected` from our
  // own edge object, and if we don't write it, selection is dropped whenever the
  // item changes (and the delete button on the arrow disappears).
  const [selectedEdges, setSelectedEdges] = useState<string[]>([])
  // Show the toolbar strip on hover over an arrow — same as cards. Requiring
  // selection first turned deleting into an unnecessary two-step process.
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null)
  // Double-click to edit the arrow's label (same pattern as `autoEdit` on cards).
  const [edgeAutoEdit, setEdgeAutoEdit] = useState<string | null>(null)
  const clearEdgeAutoEdit = useCallback(() => setEdgeAutoEdit(null), [])
  const [menu, setMenu] = useState<Menu | null>(null)
  const clipboard = useRef<PlanningItem[]>([])
  const wrapper = useRef<HTMLDivElement | null>(null)

  /** Select / Pan — like V/H in Figma. On touch, whether a one-finger drag with
   *  `selectionOnDrag` means one or the other CANNOT be determined by reading the
   *  source (d3-zoom's filter passes `touchstart` through, and the Pane's selection
   *  handler also sees `button===0`). Rather than guessing, we give the user an
   *  explicit toggle. */
  const [mode, setMode] = useState<"select" | "pan">("select")
  const [helpOpen, setHelpOpen] = useState(false)
  /** Newly added item: comes in selected, with its title open for editing. */
  const [autoEditKey, setAutoEditKey] = useState<string | null>(null)
  const clearAutoEdit = useCallback(() => setAutoEditKey(null), [])

  /** SELECTION IS KEPT ON OUR SIDE — fed back to React Flow via the `selected` field.
   *
   *  Why: internally, `adoptUserNodes` builds `internalNode = {...userNode}`, meaning
   *  `selected` is read from OUR OWN node object. Since we weren't writing it,
   *  selection silently dropped every time the `nodes` array was rebuilt (an item
   *  changing, every frame while dragging). It only survived when nothing changed,
   *  because RF sees the same array reference (`checkEquality`) and skips
   *  re-adopting. This same field is also the only viable way to implement
   *  "select all". */
  useOnSelectionChange({
    onChange: useCallback(({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) => {
      // Writing the same selection again would needlessly rebuild the array (and risk a loop).
      const ayni = (a: string[], b: string[]) =>
        a.length === b.length && a.every((k, i) => k === b[i])
      const nk = nodes.map((n) => n.id)
      const ek = edges.map((e) => e.id)
      setSelected((onceki) => (ayni(onceki, nk) ? onceki : nk))
      setSelectedEdges((onceki) => (ayni(onceki, ek) ? onceki : ek))
    }, []),
  })

  const selectedSet = useMemo(() => new Set(selected), [selected])
  const selectedEdgeSet = useMemo(() => new Set(selectedEdges), [selectedEdges])

  // --- model → RF -----------------------------------------------------------

  /** PARENT FIRST: RF says "parent nodes must appear before their children in the
   *  nodes array"; otherwise the child's position is computed wrong on the first frame. */
  const ordered = useMemo(() => {
    const nonEdge = items.filter((it) => it.type !== "edge")
    return [...nonEdge.filter((it) => !parentKeyOf(it)),
            ...nonEdge.filter((it) => parentKeyOf(it))]
  }, [items])

  // --- delete (without orphaning) ---------------------------------------------

  /** THE ONLY DELETE PATH — without orphaning anything.
   *
   *  Two kinds of leftovers are possible; both are closed off here:
   *
   *  1. **A region's children:** their links are resolved first (converted back to
   *     absolute coordinates), then the region is deleted. Otherwise the item points
   *     at a `parent_key` that no longer exists and snaps to 0,0.
   *  2. **Connected arrows (2026-08-09):** deleting a card left its arrows behind.
   *     RF's OWN delete path (Delete key) also removes connected edges, but the
   *     panel's own paths (trash icon, menu, bulk delete) never go through RF. An
   *     orphaned arrow stays in the DB as an invisible row and RF logs `error008`
   *     on every render. 3 such rows were found in production.
   *
   *  The trash icon on a card is also wired to THIS: it used to call raw `remove`,
   *  meaning deleting a region from the trash icon produced exactly the bug in
   *  (1) — the reason this cascade exists. */
  const removeCascade = useCallback((keys: string[]) => {
    if (!keys.length) return
    const set = new Set(keys)
    const freed: ItemPatch[] = []
    for (const it of items) {
      const pk = parentKeyOf(it)
      if (!pk || !set.has(pk) || set.has(it.item_key)) continue
      const parent = byKey.get(pk)
      if (!parent) continue
      freed.push({ item_key: it.item_key, x: snap8(parent.x + it.x),
                   y: snap8(parent.y + it.y), extra: { parent_key: null } })
    }
    if (freed.length) commit(freed)
    remove([...keys, ...connectedEdgeKeys(keys, items)])
  }, [items, byKey, commit, remove])

  /** A locked item cannot be deleted — the lock badge PROMISES this ("can't be
   *  moved, resized, or deleted") and the node's `deletable` field says the same;
   *  but the trash icon on the card never actually checked the lock. */
  const silinebilir = useCallback((keys: string[]) =>
    keys.filter((k) => { const it = byKey.get(k); return it && !isLocked(it) }),
    [byKey])

  const nodes = useMemo<Node[]>(() => ordered.map((it) => {
    const s = sizeOf(it)
    const locked = isLocked(it)
    // Don't pass `parentId` if the parent has been deleted — RF crashes on a missing parent.
    const pk = parentKeyOf(it)
    const parentId = pk && byKey.has(pk) ? pk : undefined
    const data: NodeData = {
      item: it,
      dim: filterActive && !matches(it),
      locked,
      readOnly: !!readOnly,
      onPatch, onRemove: (k) => removeCascade(silinebilir([k])),
      onOpenDetail, paletteFor, setPaletteFor, boardKey,
      autoEdit: autoEditKey === it.item_key,
      onAutoEditDone: clearAutoEdit,
    }
    return {
      id: it.item_key,
      type: it.type,
      // A node with a parent has its position stored RELATIVE TO THE PARENT (RF's
      // contract), and it's stored relative in the DB too — converted when grouping/ungrouping.
      position: { x: it.x, y: it.y },
      width: s.width, height: s.height,
      // A region always stays BEHIND its content, but its own `z` isn't ignored:
      // with a fixed -1, two overlapping regions couldn't be ordered relative to
      // each other and the "Bring forward" menu item silently did nothing on a region.
      zIndex: it.type === "region" ? REGION_Z + (it.z || 0) : (it.z || 0),
      draggable: !readOnly && !locked,
      deletable: !readOnly && !locked,
      connectable: !readOnly && !locked,
      selectable: true,
      selected: selectedSet.has(it.item_key),
      ...(parentId ? { parentId, extent: "parent" as const } : {}),
      data,
    }
  }), [ordered, byKey, filterActive, matches, readOnly, onPatch, removeCascade,
       silinebilir, onOpenDetail, paletteFor, boardKey, autoEditKey, clearAutoEdit,
       selectedSet])

  /** Reverse the arrow's direction — swaps the endpoints and the stored handles
   *  together. If only from/to were swapped, the stored handles would still belong
   *  to the arrow's old direction and the arrow would loop around the card. */
  const reverseEdge = useCallback((key: string) => {
    if (readOnly) return
    const it = byKey.get(key)
    if (!it || !it.from_key || !it.to_key) return
    commit([{
      item_key: key,
      from_key: it.to_key, to_key: it.from_key,
      extra: {
        from_handle: storedHandle(it.extra, "to_handle"),
        to_handle: storedHandle(it.extra, "from_handle"),
      },
    }])
  }, [readOnly, byKey, commit])

  const edges = useMemo<Edge[]>(() => items
    // ORPHANED ARROW FILTER (defensive): if either endpoint no longer exists, don't
    // pass it to RF — otherwise it logs `error008` on every render. `removeCascade`
    // no longer produces these, but legacy rows exist (3 found in production) and
    // there may be hand-edited boards too.
    .filter((it) => it.type === "edge" && it.from_key && it.to_key
      && byKey.has(it.from_key) && byKey.has(it.to_key))
    .map((it) => {
      const data: EdgeData = {
        item: it,
        readOnly: !!readOnly,
        hovered: hoveredEdge === it.item_key,
        autoEdit: edgeAutoEdit === it.item_key,
        onPatch,
        onRemove: (k) => remove([k]),
        onReverse: reverseEdge,
        onAutoEditDone: clearEdgeAutoEdit,
      }
      // HANDLES: the user's choice takes priority, geometry is the fallback.
      // If `sourceHandle`/`targetHandle` aren't given to an edge, RF falls back to
      // the node's FIRST handle (`l` = left, for us) → even an arrow drawn to a card
      // on the right would exit from the left and loop around. The choice is stored
      // in `extra`; `bestHandles` kicks in for OLD arrows that carry no handle info,
      // and whenever the stored choice contradicts the target.
      const s = byKey.get(it.from_key as string)
      const t = byKey.get(it.to_key as string)
      const [sh, th] = s && t
        ? resolveHandles(absBoxOf(s, byKey), absBoxOf(t, byKey), {
            from: storedHandle(it.extra, "from_handle"),
            to: storedHandle(it.extra, "to_handle"),
          })
        : (["r", "l"] as const)
      return {
        id: it.item_key,
        source: it.from_key as string,
        target: it.to_key as string,
        sourceHandle: sh,
        targetHandle: th,
        selected: selectedEdgeSet.has(it.item_key),
        // Visuals/marker come from `DEFAULT_EDGE_OPTIONS` — not repeated here,
        // or there'd be two separate sources of truth.
        deletable: !readOnly,
        data,
      }
    }), [items, byKey, readOnly, onPatch, remove, selectedEdgeSet, hoveredEdge,
         edgeAutoEdit, reverseEdge, clearEdgeAutoEdit])

  // --- RF → model -----------------------------------------------------------

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    if (readOnly) return
    const live: ItemPatch[] = []
    const done: ItemPatch[] = []
    const removed: string[] = []
    for (const c of changes) {
      if (c.type === "position" && c.position) {
        // `dragging` UNDEFINED = programmatic (fitView etc.) — don't write to the server.
        if (c.dragging === true) {
          live.push({ item_key: c.id, x: Math.round(c.position.x), y: Math.round(c.position.y) })
        } else if (c.dragging === false) {
          done.push({ item_key: c.id, x: snap8(c.position.x), y: snap8(c.position.y) })
        }
      } else if (c.type === "dimensions" && c.dimensions) {
        // `resizing` UNDEFINED = RF's INITIAL MEASUREMENT, not a user action.
        if (c.resizing === true) {
          live.push({ item_key: c.id, width: Math.round(c.dimensions.width),
                      height: Math.round(c.dimensions.height) })
        } else if (c.resizing === false) {
          done.push({ item_key: c.id, width: Math.round(c.dimensions.width),
                      height: Math.round(c.dimensions.height) })
        }
      } else if (c.type === "remove") {
        removed.push(c.id)
      }
    }
    if (live.length) applyLocal(live)
    if (done.length) commit(done)
    if (removed.length) removeCascade(removed)
  }, [readOnly, applyLocal, commit, removeCascade])

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (readOnly) return
    const removed = changes.filter((c) => c.type === "remove").map((c) => c.id)
    if (removed.length) remove(removed)
  }, [readOnly, remove])

  const onConnect = useCallback((c: Connection) => {
    if (readOnly || !c.source || !c.target || c.source === c.target) return
    // Don't allow drawing a second arrow between the same pair.
    if (items.some((i) => i.type === "edge" && i.from_key === c.source && i.to_key === c.target)) return
    commit([{
      item_key: newItemKey(), type: "edge",
      from_key: c.source, to_key: c.target,
      // The handles the user ACTUALLY dragged from. Used to be discarded; without a
      // handle written to the edge, RF fell back to the first handle (`l`) and the
      // arrow looped around from the left. The stored value takes priority in
      // `resolveHandles` (ignored if it contradicts the target).
      extra: { from_handle: c.sourceHandle || null, to_handle: c.targetHandle || null },
      x: 0, y: 0, status: "open",
    }])
  }, [readOnly, items, commit])

  /** Grab the END of an arrow and drag it onto a different item (2026-08-09).
   *
   *  React Flow's own `EdgeWrapper` draws the handles — passing `onReconnect` is
   *  enough, no need to touch our custom edge component (`PlanningEdge`). The
   *  circles are shifted OUTWARD from the endpoint (`shiftX`), so they don't
   *  overlap the node's own `Handle`s.
   *
   *  Dropped on empty space, RF itself snaps the arrow back via
   *  `setReconnecting(false)`; we don't silently delete it — that would be
   *  surprising, and there's already a trash icon on the arrow for that.
   *
   *  NOTE (accepted edge case): undo correctly restores `from_key`/`to_key`, but
   *  since the server shallow-merges `extra`, a handle newly written onto an old
   *  arrow that previously had NO handle info at all survives the undo too.
   *  Visual impact is limited: `resolveHandles` already ignores a handle that
   *  contradicts the target. */
  const onReconnect = useCallback((oldEdge: Edge, c: Connection) => {
    if (readOnly || !c.source || !c.target) return
    if (c.source === c.target) {
      toast.error(t("components.planlama.planningFlow.cannotConnectSelf"))
      return
    }
    if (items.some((i) => i.type === "edge" && i.item_key !== oldEdge.id
        && i.from_key === c.source && i.to_key === c.target)) {
      toast.error(t("components.planlama.planningFlow.arrowAlreadyExists"))
      return
    }
    commit([{
      item_key: oldEdge.id,
      from_key: c.source, to_key: c.target,
      extra: { from_handle: c.sourceHandle || null, to_handle: c.targetHandle || null },
    }])
  }, [readOnly, items, commit, t])

  /** Dragging from a handle and dropping on EMPTY canvas: creates a new card + arrow
   *  where it was dropped. The single most expected move in flow-based planning;
   *  previously dropping a connection on empty space did nothing. */
  const onConnectEnd = useCallback((_event: MouseEvent | TouchEvent,
                                    state: FinalConnectionState) => {
    if (readOnly || state.isValid) return       // if it dropped on a valid target, onConnect already handled it
    const from = state.fromNode?.id
    if (!from || !state.from || !state.to) return
    // JUST CLICKING A HANDLE SHOULDN'T CREATE A CARD. RF starts the connection on
    // pointerdown and ends it on pointerup, so a plain click also lands here, and
    // in that case `isValid` is `null`, not `false` — not distinguishable by itself.
    // Distance as the discriminator: did the user actually drag?
    if (Math.hypot(state.to.x - state.from.x, state.to.y - state.from.y) < 60) return
    // `from`/`to` are already FLOW coordinates — no screen transform needed.
    const pos = state.to
    const d = DEFAULT_SIZE.card
    const key = newItemKey()
    commit([
      { item_key: key, type: "card", title: t("components.planlama.planningFlow.newCardTitle"),
        color: COLORS[items.length % COLORS.length],
        x: snap8(pos.x - d.width / 2), y: snap8(pos.y - d.height / 2),
        width: d.width, height: d.height, z: 0, status: "open" },
      // The target card is BORN by this action; there's no such thing as choosing
      // a target handle, that side is computed from geometry.
      { item_key: newItemKey(), type: "edge", from_key: from, to_key: key,
        extra: { from_handle: state.fromHandle?.id || null, to_handle: null },
        x: 0, y: 0, status: "open" },
    ])
    setAutoEditKey(key)
  }, [readOnly, commit, items.length, t])

  // --- selection ----------------------------------------------------------------

  const selItems = useMemo(
    () => selected.map((k) => byKey.get(k)).filter(Boolean) as PlanningItem[],
    [selected, byKey])
  const selBoxes = useMemo(() => selItems.filter((it) => it.type !== "edge"), [selItems])
  const selRegions = useMemo(() => selBoxes.filter((it) => it.type === "region"), [selBoxes])

  const bulk = useCallback((patch: Partial<PlanningItem>) => {
    if (!selBoxes.length) return
    commit(selBoxes.map((it) => ({ item_key: it.item_key, ...patch })))
  }, [selBoxes, commit])

  /** Layer order. Four modes: one step forward/backward, and to front/to back.
   *
   *  In the absolute modes the selection's OWN internal order is preserved (sorted
   *  by current z, then given consecutive values) — if they all got the same z,
   *  the relative order of overlapping cards would get scrambled every time
   *  "bring to front" ran. */
  const zSirala = useCallback((liste: PlanningItem[], mod: ZMod) => {
    const moves = zMoves(liste, items.filter((i) => i.type !== "edge"), mod)
    if (moves.length) commit(moves)
  }, [items, commit])

  const layout = useCallback((fn: (b: ReturnType<typeof boxOf>[]) => { item_key: string; x: number; y: number }[]) => {
    const moves = fn(selBoxes.map(boxOf))
    if (moves.length) commit(moves)
  }, [selBoxes, commit])

  const duplicate = useCallback((source: PlanningItem[], dx = PASTE_OFFSET, dy = PASTE_OFFSET) => {
    const fresh = source.filter((it) => it.type !== "edge").map((it) => ({
      ...it, item_key: newItemKey(),
      x: snap8(it.x + dx), y: snap8(it.y + dy),
      // The copy shouldn't be born inside a group; server-derived fields shouldn't carry over either.
      extra: { ...(it.extra || {}), parent_key: null },
      rev: undefined, updated_at: undefined, updated_by: undefined,
    }))
    if (fresh.length) commit(fresh as ItemPatch[])
  }, [commit])

  // --- grouping -------------------------------------------------------------

  /** Group the selection inside a region. Children's positions are converted to be
   *  RELATIVE TO THE PARENT — that's how RF interprets x/y on a node given a `parentId`. */
  const group = useCallback(() => {
    const kids = selBoxes.filter((it) => it.type !== "region" && !parentKeyOf(it))
    if (kids.length < 2) return
    const b = boundsOf(kids.map(boxOf))
    const rx = snap8(b.x - GROUP_PAD)
    const ry = snap8(b.y - GROUP_PAD - GROUP_HEADER)
    const regionKey = newItemKey()
    commit([
      { item_key: regionKey, type: "region", title: t("components.planlama.planningFlow.groupDefaultTitle"), x: rx, y: ry,
        width: Math.round(b.width + GROUP_PAD * 2),
        height: Math.round(b.height + GROUP_PAD * 2 + GROUP_HEADER),
        z: -1, color: "#f1f5f9", status: "open" },
      ...kids.map((it) => ({
        item_key: it.item_key,
        x: Math.round(it.x - rx), y: Math.round(it.y - ry),
        extra: { parent_key: regionKey },
      })),
    ])
  }, [selBoxes, commit, t])

  /** Ungroup: convert children back to absolute coordinates, region stays (not deleted). */
  const ungroup = useCallback((regionKeys: string[]) => {
    const set = new Set(regionKeys)
    const freed: ItemPatch[] = []
    for (const it of items) {
      const pk = parentKeyOf(it)
      if (!pk || !set.has(pk)) continue
      const parent = byKey.get(pk)
      if (!parent) continue
      freed.push({ item_key: it.item_key, x: snap8(parent.x + it.x),
                   y: snap8(parent.y + it.y), extra: { parent_key: null } })
    }
    if (freed.length) commit(freed)
  }, [items, byKey, commit])

  const hasChildren = useCallback(
    (key: string) => items.some((it) => parentKeyOf(it) === key), [items])

  const setLock = useCallback((list: PlanningItem[], locked: boolean) => {
    if (!list.length) return
    // Sending `null` DELETES the key (the server shallow-merges `extra`).
    commit(list.map((it) => ({ item_key: it.item_key, extra: { locked: locked || null } })))
  }, [commit])

  // --- keyboard ---------------------------------------------------------------

  /** Select all box (non-edge) items. Since selection is kept on our side (see the
   *  `useOnSelectionChange` comment), all we need to do is write the list. */
  const selectAll = useCallback(() => {
    setSelected(items.filter((it) => it.type !== "edge").map((it) => it.item_key))
  }, [items])

  const zoomToSelection = useCallback(() => {
    if (!selected.length) return
    void rf.fitView({ nodes: selected.map((id) => ({ id })), padding: 0.3, duration: 300, maxZoom: 1.5 })
  }, [rf, selected])

  // --- navigating filter matches ---------------------------------------
  // The filter faded out non-matches, but if the match was outside the visible
  // area the user was staring at an empty canvas. Now you can step through them in order.
  const matchKeys = useMemo(
    () => (filterActive
      ? items.filter((it) => it.type !== "edge" && matches(it)).map((it) => it.item_key)
      : []),
    [items, filterActive, matches])
  const [matchIdx, setMatchIdx] = useState(0)
  useEffect(() => { setMatchIdx(0) }, [filterActive, matchKeys.length])

  const goMatch = useCallback((delta: number) => {
    if (!matchKeys.length) return
    const next = (matchIdx + delta + matchKeys.length) % matchKeys.length
    setMatchIdx(next)
    setSelected([matchKeys[next]])
    // `fitView` with a single node: since x/y are RELATIVE for items with a parent,
    // `setCenter` would go to the wrong place; fitView resolves the absolute
    // position itself.
    void rf.fitView({ nodes: [{ id: matchKeys[next] }], padding: 0.6, duration: 300, maxZoom: 1.2 })
  }, [matchKeys, matchIdx, rf])

  // Shortcuts. Help (?) and mode (V/H) should still work on a `readOnly` board —
  // only the MUTATING shortcuts are disabled.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return

      if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === "?") { setHelpOpen((v) => !v); e.preventDefault(); return }
        if (e.key === "Escape") { setHelpOpen(false); return }
        const tek = e.key.toLowerCase()
        if (tek === "v") { setMode("select"); return }
        if (tek === "h") { setMode("pan"); return }
        return
      }
      if (!(e.ctrlKey || e.metaKey)) return
      const k = e.key.toLowerCase()
      if (k === "a") { selectAll(); e.preventDefault(); return }
      if (readOnly) return
      if (k === "c" && selBoxes.length) { clipboard.current = selBoxes; e.preventDefault() }
      else if (k === "v" && clipboard.current.length) { duplicate(clipboard.current); e.preventDefault() }
      else if (k === "d" && selBoxes.length) { duplicate(selBoxes); e.preventDefault() }
      else if (k === "g" && selBoxes.length > 1) { group(); e.preventDefault() }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [selBoxes, duplicate, group, readOnly, selectAll])

  // --- right click --------------------------------------------------------------

  const openNodeMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault()
    const r = wrapper.current?.getBoundingClientRect(); if (!r) return
    // If there's a multi-selection and the clicked item is part of it, open the SELECTION menu.
    if (selected.length > 1 && selected.includes(node.id)) {
      setMenu({ kind: "selection", pos: menuPosition(e, r) })
    } else {
      setMenu({ kind: "node", pos: menuPosition(e, r), key: node.id })
    }
  }, [selected])

  const openPaneMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault()
    const r = wrapper.current?.getBoundingClientRect(); if (!r) return
    setMenu({
      kind: "pane", pos: menuPosition(e, r),
      flow: rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
    })
  }, [rf])

  const addAt = useCallback((type: PlanningItemType, x: number, y: number) => {
    const d = DEFAULT_SIZE[type]
    const key = newItemKey()
    commit([{
      item_key: key, type,
      title: type === "region" ? t("components.planlama.planningFlow.regionDefaultTitle")
           : type === "card" ? t("components.planlama.planningFlow.newCardTitle") : null,
      text: type === "note" ? t("components.planlama.planningFlow.newNoteText") : null,
      color: type === "region" ? "#f1f5f9" : COLORS[items.length % COLORS.length],
      x: snap8(x), y: snap8(y), width: d.width, height: d.height,
      z: type === "region" ? -1 : 0, status: "open",
    }])
    // The new item should come in open for editing: no need for a separate
    // double-click just to delete the "New card" placeholder text.
    setAutoEditKey(key)
    return key
  }, [commit, items.length, t])

  /** Add from the toolbar — into the center of the VISIBLE area.
   *
   *  OLD BEHAVIOR (bug): the page component placed the item at `maxX + 260`, i.e.
   *  to the right of the rightmost item; since the camera didn't move, on a board
   *  that had been `fitView`'d the "Card" button looked like it DID NOTHING. Since
   *  the viewport transform is only known inside the provider, the calculation
   *  was moved here. */
  const addItemAtCenter = useCallback((type: PlanningItemType) => {
    if (readOnly) return
    const r = wrapper.current?.getBoundingClientRect()
    const d = DEFAULT_SIZE[type]
    const merkez = r
      ? rf.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 })
      : { x: 0, y: 0 }
    addAt(type, merkez.x - d.width / 2, merkez.y - d.height / 2)
  }, [addAt, readOnly, rf])

  useImperativeHandle(ref, () => ({ addItem: addItemAtCenter }), [addItemAtCenter])

  // --- adding images (paste · drag-and-drop · menu) -----------------------
  // The item is added OPTIMISTICALLY first (so the user sees where it landed),
  // the file uploads in the background, and the returned `{name,w,h}` is written
  // to `extra.image`. Because the upload and the item write are separate, the
  // normal delta flow (undo, conflicts, offline queue) keeps working unbroken.
  const fileInput = useRef<HTMLInputElement | null>(null)
  // Separate ref so it's still valid after the menu closes: the file picker opens
  // asynchronously, by which time `menu` state has already become null.
  const menuFlowPos = useRef<{ x: number; y: number } | null>(null)
  // A paste event has NO cursor coordinate; we track the last known position so
  // the image lands where the mouse was resting (a ref → doesn't trigger a render).
  const pointer = useRef<{ x: number; y: number } | null>(null)

  const addImages = useCallback(async (files: File[], at?: { x: number; y: number }) => {
    if (readOnly) return
    const ok = files.filter((f) => IMAGE_TYPES.includes(f.type))
    if (!ok.length) {
      toast.error(t("components.planlama.planningFlow.imageTypeError"))
      return
    }
    const origin = at ?? (pointer.current
      ? rf.screenToFlowPosition(pointer.current)
      : rf.screenToFlowPosition({
          x: (wrapper.current?.getBoundingClientRect().left ?? 0) + 200,
          y: (wrapper.current?.getBoundingClientRect().top ?? 0) + 160,
        }))
    for (const [i, file] of ok.entries()) {
      if (file.size > IMAGE_MAX_BYTES) {
        toast.error(t("components.planlama.planningFlow.imageTooLarge",
          { name: file.name || t("components.planlama.planningFlow.imageFallbackName") }))
        continue
      }
      const key = newItemKey()
      const d = DEFAULT_SIZE.image
      commit([{
        item_key: key, type: "image",
        x: snap8(origin.x + i * 24), y: snap8(origin.y + i * 24),
        width: d.width, height: d.height, z: 0, status: "open",
      }])
      try {
        const img = await uploadPlanningImage(boardKey, file)
        commit([{ item_key: key, extra: { image: img, image_error: null },
                  ...imageBoxSize(img.width, img.height) }])
      } catch (err) {
        // The item is NOT deleted: let the user see the "failed to upload" box and
        // remove it themselves — a box that silently vanished would read as "paste is broken".
        commit([{ item_key: key, extra: { image_error: true } }])
        toast.error(err instanceof Error ? err.message : t("components.planlama.planningFlow.imageUploadFailed"))
      }
    }
  }, [boardKey, commit, readOnly, rf, t])

  // Clipboard paste. If there's NO image, the event is left untouched → the
  // existing Ctrl+V (item duplication) and text paste keep working as before.
  useEffect(() => {
    if (readOnly) return
    function onPaste(e: ClipboardEvent) {
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      const files = Array.from(e.clipboardData?.files || [])
        .filter((f) => f.type.startsWith("image/"))
      if (!files.length) return
      e.preventDefault()
      void addImages(files)
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [addImages, readOnly])

  const closeMenu = useCallback(() => setMenu(null), [])
  const act = (fn: () => void) => () => { fn(); closeMenu() }

  const multi = selBoxes.length > 1
  const mi = menu?.kind === "node" ? byKey.get(menu.key) : null

  return (
    <div ref={wrapper} className="relative h-full w-full"
      onMouseMove={(e) => { pointer.current = { x: e.clientX, y: e.clientY } }}
      // Dragging a file in from the desktop. preventDefault in `onDragOver` is
      // REQUIRED — otherwise the browser opens the file itself and navigates away
      // from the board.
      onDragOver={(e) => { if (!readOnly && e.dataTransfer.types.includes("Files")) e.preventDefault() }}
      onDrop={(e) => {
        const files = Array.from(e.dataTransfer.files || []).filter((f) => f.type.startsWith("image/"))
        if (!files.length) return
        e.preventDefault()
        void addImages(files, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }))
      }}>
      <input ref={fileInput} type="file" accept={IMAGE_TYPES.join(",")} multiple hidden
        onChange={(e) => {
          const files = Array.from(e.target.files || [])
          e.target.value = ""            // so the same file can be selected again consecutively
          if (files.length) void addImages(files, menuFlowPos.current ?? undefined)
        }} />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        // Grabbing and dragging an arrow's end. WITHOUT `onReconnect` the handles
        // aren't drawn at all (RF: isReconnectable = typeof onReconnect !== 'undefined' && …).
        onReconnect={onReconnect}
        edgesReconnectable={!readOnly}
        // Arrow INTERACTION PARITY — aligned with cards (2026-08-09):
        // hover → toolbar strip · double-click → label · right-click → menu.
        onEdgeMouseEnter={(_e, edge) => setHoveredEdge(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdge(null)}
        onEdgeDoubleClick={(e, edge) => { e.stopPropagation(); setEdgeAutoEdit(edge.id) }}
        onEdgeContextMenu={(e, edge) => {
          e.preventDefault()
          const r = wrapper.current?.getBoundingClientRect(); if (!r) return
          setMenu({ kind: "edge", pos: menuPosition(e, r), key: edge.id })
        }}
        // The handle circle is shifted this far OUTWARD from the endpoint — so it
        // doesn't overlap the node's own `Handle`. Default is 10; keep it easy to grab.
        reconnectRadius={14}
        onPaneClick={() => { setPaletteFor(null); closeMenu() }}
        onNodeContextMenu={openNodeMenu}
        onPaneContextMenu={openPaneMenu}
        onSelectionContextMenu={(e) => {
          e.preventDefault()
          const r = wrapper.current?.getBoundingClientRect(); if (!r) return
          setMenu({ kind: "selection", pos: menuPosition(e, r) })
        }}
        // CONNECTION — fix from 2026-07-27.
        // Loose: any handle can be both source and target. In the default Strict
        // mode only source→target was accepted; since the user had no way of
        // knowing which side was which, arrows looked "broken".
        connectionMode={ConnectionMode.Loose}
        // Snaps as soon as the cursor comes within 40px of a handle — no need to
        // hit the 12px circle exactly.
        connectionRadius={40}
        connectionLineStyle={{ stroke: "#0ea5e9", strokeWidth: 2 }}
        // 4PX THRESHOLD — DO NOT REMOVE. At 0, RF starts dragging on pointerdown
        // and `dblclick` never REACHES the node's contents; that was the "can't
        // type into cards" bug.
        nodeDragThreshold={4}
        // Snap to the grid while dragging too: it used to be free-form during the
        // drag with `snap8` applied only on DROP → the card visibly jumped the
        // instant it was released.
        snapToGrid
        snapGrid={SNAP_GRID}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        onlyRenderVisibleElements
        // `elevateNodesOnSelect` REMOVED (2026-08-09): items have an explicit
        // `zIndex` (the "bring forward"/"send backward" user feature) and RF says
        // the two conflict. Selection is already visible via `ring-2`.
        //
        // Select mode: left-drag = box selection, middle button/Space = pan.
        // Pan mode: left-drag = pan (also decisive for touch).
        // Right-click is DELIBERATELY NOT in pan — the menu uses it.
        selectionOnDrag={mode === "select" && !readOnly}
        panOnDrag={mode === "pan" ? true : [1]}
        panOnScroll
        panActivationKeyCode="Space"
        multiSelectionKeyCode={["Shift", "Meta", "Control"]}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        className="bg-muted/20"
      >
        <Background gap={16} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="!hidden md:!block"
          nodeColor={(n) => (n.data as NodeData)?.item?.color || "#e2e8f0"} />

        {/* Mode toggle + help. This is the only place that decides what a single
            finger does on a touch device. */}
        <Panel position="top-left"
          className="flex items-center gap-1 rounded-lg border bg-popover/95 p-1 shadow-sm backdrop-blur">
          <ToolBtn title={t("components.planlama.planningFlow.selectMode")} active={mode === "select"} onClick={() => setMode("select")}>
            <MousePointer2 className="h-4 w-4" />
          </ToolBtn>
          <ToolBtn title={t("components.planlama.planningFlow.panMode")} active={mode === "pan"} onClick={() => setMode("pan")}>
            <Hand className="h-4 w-4" />
          </ToolBtn>
          <Sep />
          <ToolBtn title={t("components.planlama.planningFlow.zoomToSelectionBtn")} onClick={zoomToSelection}>
            <Scan className="h-4 w-4" />
          </ToolBtn>
          <ToolBtn title={t("components.planlama.planningFlow.shortcutsHelpBtn")} onClick={() => setHelpOpen(true)}>
            <Keyboard className="h-4 w-4" />
          </ToolBtn>
        </Panel>

        {filterActive && matchKeys.length > 0 && (
          <Panel position="top-right"
            className="flex items-center gap-1 rounded-lg border bg-popover/95 p-1 shadow-sm backdrop-blur">
            <span className="px-1.5 text-xs text-muted-foreground">
              {t("components.planlama.planningFlow.matchCounter", { current: matchIdx + 1, total: matchKeys.length })}
            </span>
            <ToolBtn title={t("components.planlama.planningFlow.prevMatch")} onClick={() => goMatch(-1)}>
              <ChevronLeft className="h-4 w-4" />
            </ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.nextMatch")} onClick={() => goMatch(1)}>
              <ChevronRight className="h-4 w-4" />
            </ToolBtn>
          </Panel>
        )}

        {multi && !readOnly && (
          <Panel position="top-center"
            className="flex flex-wrap items-center gap-1 rounded-lg border bg-popover/95 p-1 shadow-lg backdrop-blur">
            <span className="px-2 text-xs font-medium text-muted-foreground">{t("components.planlama.planningFlow.selectedCount", { count: selBoxes.length })}</span>
            <Sep />
            <ToolBtn title={t("components.planlama.planningFlow.alignLeft")} onClick={() => layout(alignLeft)}><AlignStartVertical className="h-4 w-4" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.alignCenterX")} onClick={() => layout(alignCenterX)}><AlignHorizontalJustifyCenter className="h-4 w-4" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.alignTop")} onClick={() => layout(alignTop)}><AlignStartVertical className="h-4 w-4 rotate-90" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.distributeY")} onClick={() => layout((b) => distributeY(b))}><Grid3x3 className="h-4 w-4" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.distributeX")} onClick={() => layout((b) => distributeX(b))}><Grid3x3 className="h-4 w-4 rotate-90" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.gridLayout")} onClick={() => layout((b) => gridLayout(b))}><LayoutGrid className="h-4 w-4" /></ToolBtn>
            <Sep />
            <ToolBtn title={t("components.planlama.planningFlow.groupBtn")} onClick={group}><Group className="h-4 w-4" /></ToolBtn>
            {selRegions.length > 0 && (
              <ToolBtn title={t("components.planlama.planningFlow.ungroupBtn")} onClick={() => ungroup(selRegions.map((r) => r.item_key))}>
                <Ungroup className="h-4 w-4" />
              </ToolBtn>
            )}
            <ToolBtn title={t("components.planlama.planningFlow.duplicateBtn")} onClick={() => duplicate(selBoxes)}><Copy className="h-4 w-4" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.lockBtn")} onClick={() => setLock(selBoxes, true)}><Lock className="h-4 w-4" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.unlockBtn")} onClick={() => setLock(selBoxes, false)}><Unlock className="h-4 w-4" /></ToolBtn>
            <div className="relative">
              <ToolBtn title={t("components.planlama.planningFlow.colorBtn")} onClick={() => setPaletteFor(paletteFor === "__bulk__" ? null : "__bulk__")}>
                <Palette className="h-4 w-4" />
              </ToolBtn>
              {paletteFor === "__bulk__" && (
                <div className="absolute top-9 left-0 z-20 flex w-[118px] flex-wrap gap-1 rounded-md border bg-popover p-1 shadow-md">
                  {COLORS.map((c) => (
                    <button key={c} type="button" title={c} className="h-5 w-5 rounded border"
                      style={{ background: c }}
                      onClick={() => { bulk({ color: c }); setPaletteFor(null) }} />
                  ))}
                </div>
              )}
            </div>
            <ToolBtn title={t("components.planlama.planningFlow.markDoneBtn")} onClick={() => bulk({ status: "done" })}>✓</ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.markOpenBtn")} onClick={() => bulk({ status: "open" })}><X className="h-4 w-4" /></ToolBtn>
            <ToolBtn title={t("components.planlama.planningFlow.deleteBtn")} danger
              onClick={() => removeCascade(selBoxes.filter((i) => !isLocked(i)).map((i) => i.item_key))}>
              <Trash2 className="h-4 w-4" />
            </ToolBtn>
          </Panel>
        )}
      </ReactFlow>

      {menu && !readOnly && (
        <ContextMenu pos={menu.pos} onClose={closeMenu}>
          {menu.kind === "node" && mi && (
            <>
              <MenuItem icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
                onClick={act(() => onOpenDetail(mi))}>{t("components.planlama.planningFlow.menuDetails")}</MenuItem>
              <MenuItem icon={<Copy className="h-3.5 w-3.5" />} hint="Ctrl+D"
                onClick={act(() => duplicate([mi]))}>{t("components.planlama.planningFlow.menuDuplicate")}</MenuItem>
              <MenuItem icon={<ClipboardPaste className="h-3.5 w-3.5" />} hint="Ctrl+C"
                onClick={act(() => { clipboard.current = [mi] })}>{t("components.planlama.planningFlow.menuCopy")}</MenuItem>
              <MenuSep />
              <MenuItem icon={isLocked(mi) ? <Unlock className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
                onClick={act(() => setLock([mi], !isLocked(mi)))}>
                {isLocked(mi) ? t("components.planlama.planningFlow.menuUnlock") : t("components.planlama.planningFlow.menuLock")}
              </MenuItem>
              <MenuItem icon={<ArrowUpToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "one"))}>{t("components.planlama.planningFlow.menuBringForward")}</MenuItem>
              <MenuItem icon={<ArrowDownToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "arkaya"))}>{t("components.planlama.planningFlow.menuSendBackward")}</MenuItem>
              <MenuItem icon={<BringToFront className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "enOne"))}>{t("components.planlama.planningFlow.menuBringToFront")}</MenuItem>
              <MenuItem icon={<SendToBack className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "enArkaya"))}>{t("components.planlama.planningFlow.menuSendToBack")}</MenuItem>
              {mi.type === "region" && hasChildren(mi.item_key) && (
                <MenuItem icon={<Ungroup className="h-3.5 w-3.5" />}
                  onClick={act(() => ungroup([mi.item_key]))}>{t("components.planlama.planningFlow.menuUngroup")}</MenuItem>
              )}
              {parentKeyOf(mi) && (
                <MenuItem icon={<Ungroup className="h-3.5 w-3.5" />}
                  onClick={act(() => {
                    const p = byKey.get(parentKeyOf(mi) as string)
                    if (p) commit([{ item_key: mi.item_key, x: snap8(p.x + mi.x),
                                     y: snap8(p.y + mi.y), extra: { parent_key: null } }])
                  })}>{t("components.planlama.planningFlow.menuRemoveFromGroup")}</MenuItem>
              )}
              <MenuSep />
              <MenuItem danger disabled={isLocked(mi)} icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={act(() => removeCascade([mi.item_key]))}>{t("components.planlama.planningFlow.menuDelete")}</MenuItem>
            </>
          )}

          {menu.kind === "selection" && (
            <>
              <div className="px-2 py-1 text-[10px] text-muted-foreground">{t("components.planlama.planningFlow.selectionCount", { count: selBoxes.length })}</div>
              <MenuItem icon={<Group className="h-3.5 w-3.5" />} hint="Ctrl+G"
                disabled={selBoxes.filter((i) => i.type !== "region" && !parentKeyOf(i)).length < 2}
                onClick={act(group)}>{t("components.planlama.planningFlow.menuGroup")}</MenuItem>
              {selRegions.length > 0 && (
                <MenuItem icon={<Ungroup className="h-3.5 w-3.5" />}
                  onClick={act(() => ungroup(selRegions.map((r) => r.item_key)))}>{t("components.planlama.planningFlow.menuUngroup")}</MenuItem>
              )}
              <MenuSep />
              <MenuItem icon={<AlignStartVertical className="h-3.5 w-3.5" />}
                onClick={act(() => layout(alignLeft))}>{t("components.planlama.planningFlow.menuAlignLeft")}</MenuItem>
              <MenuItem icon={<LayoutGrid className="h-3.5 w-3.5" />}
                onClick={act(() => layout((b) => gridLayout(b)))}>{t("components.planlama.planningFlow.menuGridLayout")}</MenuItem>
              <MenuSep />
              <MenuItem icon={<ArrowUpToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "one"))}>{t("components.planlama.planningFlow.menuBringForward")}</MenuItem>
              <MenuItem icon={<ArrowDownToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "arkaya"))}>{t("components.planlama.planningFlow.menuSendBackward")}</MenuItem>
              <MenuItem icon={<BringToFront className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "enOne"))}>{t("components.planlama.planningFlow.menuBringToFront")}</MenuItem>
              <MenuItem icon={<SendToBack className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "enArkaya"))}>{t("components.planlama.planningFlow.menuSendToBack")}</MenuItem>
              <MenuSep />
              <MenuItem icon={<Copy className="h-3.5 w-3.5" />} hint="Ctrl+D"
                onClick={act(() => duplicate(selBoxes))}>{t("components.planlama.planningFlow.menuDuplicate")}</MenuItem>
              <MenuItem icon={<Lock className="h-3.5 w-3.5" />}
                onClick={act(() => setLock(selBoxes, true))}>{t("components.planlama.planningFlow.menuLock")}</MenuItem>
              <MenuItem icon={<Unlock className="h-3.5 w-3.5" />}
                onClick={act(() => setLock(selBoxes, false))}>{t("components.planlama.planningFlow.menuUnlock")}</MenuItem>
              <MenuSep />
              <MenuItem danger icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={act(() => removeCascade(selBoxes.filter((i) => !isLocked(i)).map((i) => i.item_key)))}>
                {t("components.planlama.planningFlow.menuDeleteSelection")}
              </MenuItem>
            </>
          )}

          {menu.kind === "edge" && (
            <>
              <MenuItem icon={<Tag className="h-3.5 w-3.5" />}
                onClick={act(() => setEdgeAutoEdit(menu.key))}>{t("components.planlama.planningFlow.menuEditLabel")}</MenuItem>
              <MenuItem icon={<ArrowLeftRight className="h-3.5 w-3.5" />}
                onClick={act(() => reverseEdge(menu.key))}>{t("components.planlama.planningFlow.menuReverseDirection")}</MenuItem>
              <MenuSep />
              <MenuItem danger icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={act(() => remove([menu.key]))}>{t("components.planlama.planningFlow.menuDeleteArrow")}</MenuItem>
            </>
          )}

          {menu.kind === "pane" && (
            <>
              <MenuItem icon={<Plus className="h-3.5 w-3.5" />}
                onClick={act(() => addAt("card", menu.flow.x, menu.flow.y))}>{t("components.planlama.planningFlow.menuAddCard")}</MenuItem>
              <MenuItem icon={<StickyNote className="h-3.5 w-3.5" />}
                onClick={act(() => addAt("note", menu.flow.x, menu.flow.y))}>{t("components.planlama.planningFlow.menuAddNote")}</MenuItem>
              <MenuItem icon={<SquareDashed className="h-3.5 w-3.5" />}
                onClick={act(() => addAt("region", menu.flow.x, menu.flow.y))}>{t("components.planlama.planningFlow.menuAddRegion")}</MenuItem>
              <MenuItem icon={<ImagePlus className="h-3.5 w-3.5" />}
                onClick={act(() => { menuFlowPos.current = menu.flow; fileInput.current?.click() })}>
                {t("components.planlama.planningFlow.menuAddImage")}
              </MenuItem>
              <MenuSep />
              <MenuItem icon={<ClipboardPaste className="h-3.5 w-3.5" />} hint="Ctrl+V"
                disabled={!clipboard.current.length}
                onClick={act(() => {
                  const b = boundsOf(clipboard.current.map(boxOf))
                  duplicate(clipboard.current, menu.flow.x - b.x, menu.flow.y - b.y)
                })}>{t("components.planlama.planningFlow.menuPaste")}</MenuItem>
              <MenuSep />
              <MenuItem icon={<BoxSelect className="h-3.5 w-3.5" />} hint="Ctrl+A"
                onClick={act(selectAll)}>{t("components.planlama.planningFlow.menuSelectAll")}</MenuItem>
              <MenuItem icon={<Maximize2 className="h-3.5 w-3.5" />}
                onClick={act(() => void rf.fitView({ padding: 0.2, duration: 300 }))}>{t("components.planlama.planningFlow.menuFitView")}</MenuItem>
            </>
          )}
        </ContextMenu>
      )}

      {helpOpen && <ShortcutsHelp onClose={() => setHelpOpen(false)} />}
    </div>
  )
})

function Sep() { return <div className="mx-1 h-5 w-px bg-border" /> }

function ToolBtn({ title, onClick, children, danger, active }: {
  title: string; onClick: () => void; children: React.ReactNode
  danger?: boolean; active?: boolean
}) {
  return (
    <button type="button" title={title} aria-label={title} aria-pressed={active}
      onClick={onClick}
      className={cn("flex h-7 min-w-7 items-center justify-center rounded px-1.5 text-sm hover:bg-muted",
                    active && "bg-primary text-primary-foreground hover:bg-primary/90",
                    danger && "text-destructive hover:bg-destructive/10")}>
      {children}
    </button>
  )
}
