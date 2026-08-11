// Planning Board save queue — PURE functions (no React/DOM).
//
// Why a separate file: this logic used to live inside `usePlanningStore` and
// couldn't be tested, and a bug here is SILENT: a zombie card (a deleted item
// comes back to life), a swallowed delete, a wrong undo. The user only notices
// the bug after already losing their work. Made pure so `planlama-queue.test.ts` can pin it down.
//
// Contract: a key can NEVER be in both `upsert` and `delete` at the same time.
import type { PlanningItem } from "@/lib/planlama"

export type ItemPatch = Partial<PlanningItem> & { item_key: string }

export interface Delta {
  upsert: ItemPatch[]
  delete: string[]
}

export function emptyDelta(): Delta {
  return { upsert: [], delete: [] }
}

/** Queue a delete intent: the pending upsert for the same key gets DROPPED.
 *  (There's no point sending a position patch for an item that's about to be
 *  deleted, and depending on server ordering it could revive the item.) */
export function mergeDelete(queue: Delta, keys: string[]): Delta {
  if (!keys.length) return queue
  const gelen = new Set(keys)
  const del = [...queue.delete]
  for (const key of keys) if (!del.includes(key)) del.push(key)
  return { upsert: queue.upsert.filter((u) => !gelen.has(u.item_key)), delete: del }
}

/** Queue an upsert intent: a second patch to the same key gets MERGED (not
 *  overwritten, merged field by field) — adding a separate row would let the
 *  last write lose the previous one's data. */
export function mergeUpsert(queue: Delta, patches: ItemPatch[]): Delta {
  if (!patches.length) return queue
  const upsert = [...queue.upsert]
  for (const p of patches) {
    const i = upsert.findIndex((u) => u.item_key === p.item_key)
    if (i >= 0) upsert[i] = { ...upsert[i], ...p }
    else upsert.push(p)
  }
  return { upsert, delete: queue.delete }
}

/** Put a failed batch back at the FRONT of the queue.
 *
 *  A failed batch is an OLDER intent: if a new intent for the same key arrived
 *  in the meantime, the NEW one wins and the old row is dropped. A raw
 *  `concat` skipped this rule, and an item's old upsert — deleted in flight —
 *  would come back and both would go out in the same PATCH → depending on
 *  server ordering the item gets REVIVED or the delete gets swallowed. */
export function requeueFailed(failed: Delta, current: Delta): Delta {
  const yeni = new Set<string>([
    ...current.upsert.map((u) => u.item_key),
    ...current.delete,
  ])
  const upsert = [...failed.upsert.filter((u) => !yeni.has(u.item_key)), ...current.upsert]
  const del = [...failed.delete.filter((k) => !yeni.has(k)), ...current.delete]
  // Final defensive filter: no conflict should reach here, but if one does,
  // DELETE wins (reviving data is a worse class of bug than over-deleting).
  const delSet = new Set(del)
  return { upsert: upsert.filter((u) => !delSet.has(u.item_key)), delete: del }
}

/** The inverse of a patch — for the undo record.
 *
 *  ONLY the fields present in the patch are reversed. It used to send
 *  `{...before}`, which also wrote back SERVER-DERIVED fields like
 *  `rev`/`updated_at`/`assignee_name` and incorrectly triggered the server's
 *  revision-based conflict detection. */
export function inverseOf(before: PlanningItem, patch: ItemPatch): ItemPatch {
  const ters: ItemPatch = { item_key: patch.item_key }
  for (const k of Object.keys(patch) as (keyof PlanningItem)[]) {
    if (k === "item_key") continue
    ;(ters as Record<string, unknown>)[k] = before[k]
  }
  return ters
}

export function isEmpty(delta: Delta): boolean {
  return delta.upsert.length === 0 && delta.delete.length === 0
}
