// Local state for the Planning Board: an in-flight draft layer + a save queue + undo.
//
// THE OLD CODE'S BUG: `useEffect(() => { if (data) setTasks(data.tasks) }, [data])` —
// every new piece of data from the server OVERWROTE local state. In the save →
// invalidate → refetch → setTasks loop, if the user was dragging a card at that
// moment, the change would fly away.
//
// THE MODEL — two layers, overwriting is physically impossible:
//   * queryItems (React Query cache) = server truth; a refresh only changes THIS
//   * draft (Map<item_key, Partial<Item>>) = the IN-FLIGHT layer; the card under the finger
//   * render = merge(queryItems, draft) — draft always wins
// An item only drops out of draft once its own write has been confirmed by the server.
//
// IDENTITY STABILITY (2026-08-09) — this file's second contract.
// If the returned object and its callbacks were NEW ON EVERY RENDER, the consumer
// chain collapsed like this: new `store` → new `onPatch` in `PlanlamaPage` → the
// `nodes` useMemo in `PlanningFlow` rebuilds → every node's `data` object is new →
// `memo` breaks → ALL visible nodes re-render on every render (every frame while
// dragging, every keystroke while searching). That's why the frequently-changing
// `byKey` is NOT in the deps — it's read through a ref, and the return value is
// memoized. Refs are assigned DURING RENDER — if assigned in an effect, a commit
// would read a stale value.
//
// BUT NOT EVERYTHING GOES INTO A REF: the write request depends on `boardKey`, and
// carrying that in a ref sent the write to the WRONG BOARD on a board change (see
// `gonder`). It's only safe to put a value in a ref when it affects the CONTENT,
// not the destination.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import {
  boardQueryKey, patchBoardItems, type PlanningItem,
} from "@/lib/planlama"
import {
  emptyDelta, inverseOf, isEmpty, mergeDelete, mergeUpsert, requeueFailed,
  type Delta, type ItemPatch,
} from "@/lib/planlama-queue"

// Consumers import this type from here — the single source of truth is
// `planlama-queue`, it's just re-exported here.
export type { ItemPatch }

interface UndoOp {
  undo: Delta
  redo: Delta
}

const UNDO_LIMIT = 50

export function usePlanningStore(boardKey: string, serverItems: PlanningItem[],
                                 serverVersion: number) {
  const qc = useQueryClient()

  const [draft, setDraft] = useState<Map<string, Partial<PlanningItem>>>(() => new Map())
  const [status, setStatus] = useState<"idle" | "saving" | "error">("idle")
  const [conflicts, setConflicts] = useState<string[]>([])

  const pending = useRef<Delta>(emptyDelta())
  const flushing = useRef(false)
  const undoStack = useRef<UndoOp[]>([])
  const redoStack = useRef<UndoOp[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** Stack depths are STATE so the Undo/Redo buttons can be disabled. There used to
   *  be only `canUndo()` (a function reading a ref); since it wasn't reactive, the
   *  buttons never disabled and pressing on an empty stack just triggered a toast. */
  const [undoDepth, setUndoDepth] = useState(0)
  const [redoDepth, setRedoDepth] = useState(0)

  /** Is the user currently dragging/typing — a refresh waits for this.
   *  STATE, NOT A REF: it used to be a ref read during render, meaning the
   *  "saving" indicator only appeared correct on some unrelated re-render.
   *  It was also only ever set to false by commit; if commit was never called
   *  (e.g. a drag left half-finished), it stayed true forever and PERMANENTLY
   *  killed auto-refresh. It's now also reset when a flush finishes. */
  const [interacting, setInteracting] = useState(false)

  /** Read `base_version` from a ref, NOT the closure: the debounced flush was
   *  carrying the version from the moment of commit; if a background refetch
   *  happened in between, a stale version would be sent and the server would
   *  needlessly return `stale: true` + the full list. */
  const versionRef = useRef(serverVersion)
  versionRef.current = serverVersion

  /** The write request is built as a PLAIN FUNCTION, NOT a mutation object.
   *
   *  A mutation gets a new identity on every render; if it were in the deps,
   *  `flush` and the `commit`/`remove` derived from it would also be renewed every
   *  render. But putting it in a REF was even worse: on a board change, the
   *  unmount cleanup calls the OLD `flush`, while the ref would ALREADY hold the
   *  new board's mutation by then → the pending write would go to the WRONG BOARD
   *  (refs are assigned in the render body, cleanup runs after render). `boardKey`
   *  now comes from the closure, so identity is stable and the target is correct. */
  const gonder = useCallback(
    (delta: { base_version: number; upsert: ItemPatch[]; delete: string[] }) =>
      patchBoardItems(qc, boardKey, delta),
    [qc, boardKey])

  const items = useMemo(() => {
    if (draft.size === 0) return serverItems
    return serverItems.map((it) => {
      const d = draft.get(it.item_key)
      return d ? { ...it, ...d } : it
    }).concat(
      // items in draft that don't exist on the server yet (newly created)
      [...draft.entries()]
        .filter(([key]) => !serverItems.some((s) => s.item_key === key))
        .map(([, v]) => v as PlanningItem),
    )
  }, [serverItems, draft])

  const byKey = useMemo(() => {
    const m = new Map<string, PlanningItem>()
    for (const it of items) m.set(it.item_key, it)
    return m
  }, [items])

  /** `byKey` is renewed on every item change (i.e. every frame while dragging);
   *  if it were in the deps, `commit`/`remove` identity would change every frame too. */
  const byKeyRef = useRef(byKey)
  byKeyRef.current = byKey

  // --- save queue ---------------------------------------------------

  const flush = useCallback(async () => {
    if (flushing.current) return
    const batch = pending.current
    if (isEmpty(batch)) return
    pending.current = emptyDelta()
    flushing.current = true
    setStatus("saving")
    try {
      const res = await gonder({
        base_version: versionRef.current,
        upsert: batch.upsert,
        delete: batch.delete,
      })
      qc.setQueryData(boardQueryKey(boardKey), (old: { board: unknown; items: PlanningItem[] } | undefined) => {
        if (!old) return old
        if (res.stale && res.items) return { board: res.board, items: res.items }
        const next = new Map(old.items.map((i) => [i.item_key, i]))
        for (const key of batch.delete) next.delete(key)
        for (const applied of res.applied) next.set(applied.item_key, applied)
        return { board: res.board, items: [...next.values()] }
      })
      // Drop the written items from draft — but ONLY the ones sent this round;
      // the user might be dragging another card in the meantime.
      setDraft((prev) => {
        const next = new Map(prev)
        for (const u of batch.upsert) next.delete(u.item_key)
        for (const key of batch.delete) next.delete(key)
        return next
      })
      if (res.conflicts.length) setConflicts(res.conflicts)
      setStatus("idle")
    } catch {
      // DON'T LOSE the queue — but not with a raw concat, run it through the conflict rule.
      pending.current = requeueFailed(batch, pending.current)
      setStatus("error")
    } finally {
      flushing.current = false
      // If a round-trip to the server completed, the interaction is over; when this
      // was a ref it wasn't reset here, so it could get stuck and lock out refreshes.
      setInteracting(false)
      if (!isEmpty(pending.current)) setTimeout(() => void flush(), 400)
    }
  }, [qc, boardKey, gonder])

  const scheduleFlush = useCallback((delay = 500) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void flush(), delay)
  }, [flush])

  const enqueue = useCallback((delta: Delta) => {
    let q = pending.current
    if (delta.delete.length) q = mergeDelete(q, delta.delete)
    if (delta.upsert.length) q = mergeUpsert(q, delta.upsert)
    pending.current = q
  }, [])

  // --- local changes -------------------------------------------------

  /** While dragging/resizing: write only to the draft, don't hit the server. */
  const applyLocal = useCallback((patches: ItemPatch[]) => {
    setInteracting(true)
    setDraft((prev) => {
      const next = new Map(prev)
      for (const p of patches) next.set(p.item_key, { ...next.get(p.item_key), ...p })
      return next
    })
  }, [])

  const pushUndo = useCallback((op: UndoOp) => {
    undoStack.current.push(op)
    if (undoStack.current.length > UNDO_LIMIT) undoStack.current.shift()
    redoStack.current = []
    setUndoDepth(undoStack.current.length)
    setRedoDepth(0)
  }, [])

  /** Persist the change (enqueue it + create an undo record). */
  const commit = useCallback((patches: ItemPatch[], opts: { undoable?: boolean } = {}) => {
    if (!patches.length) return
    if (opts.undoable !== false) {
      const undoUpsert: ItemPatch[] = []
      const undoDelete: string[] = []
      for (const p of patches) {
        const before = byKeyRef.current.get(p.item_key)
        if (!before) { undoDelete.push(p.item_key); continue }  // newly created → undo = delete
        undoUpsert.push(inverseOf(before, p))
      }
      pushUndo({ undo: { upsert: undoUpsert, delete: undoDelete },
                 redo: { upsert: patches, delete: [] } })
    }
    applyLocal(patches)
    enqueue({ upsert: patches, delete: [] })
    setInteracting(false)
    scheduleFlush(300)
  }, [applyLocal, enqueue, pushUndo, scheduleFlush])

  const remove = useCallback((keys: string[], opts: { undoable?: boolean } = {}) => {
    if (!keys.length) return
    if (opts.undoable !== false) {
      const before = keys.map((k) => byKeyRef.current.get(k)).filter(Boolean) as PlanningItem[]
      pushUndo({ undo: { upsert: before.map((b) => ({ ...b })), delete: [] },
                 redo: { upsert: [], delete: keys } })
    }
    setDraft((prev) => {
      const next = new Map(prev)
      for (const k of keys) next.delete(k)
      return next
    })
    qc.setQueryData(boardQueryKey(boardKey), (old: { board: unknown; items: PlanningItem[] } | undefined) =>
      old ? { ...old, items: old.items.filter((i) => !keys.includes(i.item_key)) } : old)
    enqueue({ upsert: [], delete: keys })
    scheduleFlush(150)
  }, [qc, boardKey, enqueue, pushUndo, scheduleFlush])

  // --- geri alma / ileri alma ---------------------------------------------

  const runDelta = useCallback((delta: Delta) => {
    if (delta.upsert.length) {
      applyLocal(delta.upsert)
      enqueue({ upsert: delta.upsert, delete: [] })
    }
    if (delta.delete.length) {
      setDraft((prev) => {
        const next = new Map(prev)
        for (const k of delta.delete) next.delete(k)
        return next
      })
      qc.setQueryData(boardQueryKey(boardKey), (old: { board: unknown; items: PlanningItem[] } | undefined) =>
        old ? { ...old, items: old.items.filter((i) => !delta.delete.includes(i.item_key)) } : old)
      enqueue({ upsert: [], delete: delta.delete })
    }
    setInteracting(false)
    scheduleFlush(150)
  }, [applyLocal, enqueue, qc, boardKey, scheduleFlush])

  const undo = useCallback(() => {
    const op = undoStack.current.pop()
    if (!op) return false
    redoStack.current.push(op)
    setUndoDepth(undoStack.current.length)
    setRedoDepth(redoStack.current.length)
    runDelta(op.undo)
    return true
  }, [runDelta])

  const redo = useCallback(() => {
    const op = redoStack.current.pop()
    if (!op) return false
    undoStack.current.push(op)
    setUndoDepth(undoStack.current.length)
    setRedoDepth(redoStack.current.length)
    runDelta(op.redo)
    return true
  }, [runDelta])

  // Board change / unmount → send the pending write FIRST.
  // `resetHistory()` used to clear `pending`; the last edit inside the debounce
  // window (300ms) would silently vanish. The cleanup function carries the closure
  // of the render that set up the effect → the `flush` called here is bound to the
  // OLD boardKey, so the write goes to the right board. Since `flush` clears
  // `pending` synchronously, it doesn't race with the following `resetHistory()`.
  useEffect(() => {
    return () => { void flush() }
    // `flush` is deliberately not in the deps: if it were rebuilt every render,
    // cleanup would run every render and defeat the debounce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boardKey])

  const resetHistory = useCallback(() => {
    undoStack.current = []
    redoStack.current = []
    pending.current = emptyDelta()
    setUndoDepth(0)
    setRedoDepth(0)
    setDraft(new Map())
    setConflicts([])
    setStatus("idle")
  }, [])

  const clearConflicts = useCallback(() => setConflicts([]), [])
  const hasPending = useCallback(() => !isEmpty(pending.current), [])

  // Return value is memoized: if its identity changed every render, the `nodes`
  // useMemo in the consumer would also rebuild every render (see the file header).
  return useMemo(() => ({
    items, byKey, status, conflicts, clearConflicts,
    applyLocal, commit, remove, flush, undo, redo, resetHistory,
    // `interacting` is a BOOLEAN (used to be a ref) — can be read directly in render.
    interacting,
    hasPending,
    canUndo: undoDepth > 0,
    canRedo: redoDepth > 0,
  }), [items, byKey, status, conflicts, clearConflicts, applyLocal, commit, remove,
       flush, undo, redo, resetHistory, interacting, hasPending, undoDepth, redoDepth])
}
