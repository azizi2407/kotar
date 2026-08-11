// Save queue — the panel's FIRST frontend tests.
//
// Every test here locks down a class of data loss that actually happened:
// zombie cards, swallowed deletes, undo writing back server-derived fields.
import { describe, expect, it } from "vitest"

import {
  emptyDelta, inverseOf, isEmpty, mergeDelete, mergeUpsert, requeueFailed,
  type Delta, type ItemPatch,
} from "@/lib/planlama-queue"
import type { PlanningItem } from "@/lib/planlama"

function delta(upsert: ItemPatch[] = [], del: string[] = []): Delta {
  return { upsert, delete: del }
}

describe("mergeUpsert", () => {
  it("aynı anahtara gelen ikinci yamayı ALAN ALAN birleştirir", () => {
    const q = mergeUpsert(delta(), [{ item_key: "a", x: 10, y: 20 }])
    const q2 = mergeUpsert(q, [{ item_key: "a", title: "Merhaba" }])
    expect(q2.upsert).toHaveLength(1)
    // Position shouldn't be lost: adding a separate row or overwriting outright
    // would let the most recent write swallow the previous one.
    expect(q2.upsert[0]).toEqual({ item_key: "a", x: 10, y: 20, title: "Merhaba" })
  })

  it("farklı anahtarları ayrı satır olarak tutar", () => {
    const q = mergeUpsert(delta(), [{ item_key: "a", x: 1 }, { item_key: "b", x: 2 }])
    expect(q.upsert.map((u) => u.item_key)).toEqual(["a", "b"])
  })

  it("boş yama listesinde kuyruğu OLDUĞU GİBİ döndürür", () => {
    const q = delta([{ item_key: "a", x: 1 }])
    expect(mergeUpsert(q, [])).toBe(q)
  })
})

describe("mergeDelete", () => {
  it("silinen anahtarın bekleyen upsert'ünü DÜŞÜRÜR (zombi kart koruması)", () => {
    const q = mergeUpsert(delta(), [{ item_key: "a", x: 10 }, { item_key: "b", x: 5 }])
    const q2 = mergeDelete(q, ["a"])
    expect(q2.upsert.map((u) => u.item_key)).toEqual(["b"])
    expect(q2.delete).toEqual(["a"])
  })

  it("aynı anahtarı iki kez silmeye çalışınca tekrar etmez", () => {
    const q = mergeDelete(mergeDelete(delta(), ["a"]), ["a"])
    expect(q.delete).toEqual(["a"])
  })
})

describe("requeueFailed", () => {
  it("başarısız batch'i geri koyar ama DAHA YENİ niyeti ezmez", () => {
    const failed = delta([{ item_key: "a", x: 10 }, { item_key: "b", y: 3 }])
    // Meanwhile the user moved 'a' again:
    const current = delta([{ item_key: "a", x: 99 }])
    const q = requeueFailed(failed, current)
    const a = q.upsert.find((u) => u.item_key === "a")
    expect(a).toEqual({ item_key: "a", x: 99 })   // the newer one wins
    expect(q.upsert.find((u) => u.item_key === "b")).toEqual({ item_key: "b", y: 3 })
  })

  it("bu arada SİLİNEN öğenin eski upsert'ini geri getirmez (zombi kart)", () => {
    const failed = delta([{ item_key: "a", x: 10 }])
    const current = delta([], ["a"])
    const q = requeueFailed(failed, current)
    expect(q.upsert).toHaveLength(0)
    expect(q.delete).toEqual(["a"])
  })

  it("bu arada yeniden yaratılan öğenin eski SİLME niyetini geri getirmez", () => {
    const failed = delta([], ["a"])
    const current = delta([{ item_key: "a", x: 1 }])
    const q = requeueFailed(failed, current)
    expect(q.delete).toHaveLength(0)
    expect(q.upsert).toEqual([{ item_key: "a", x: 1 }])
  })

  it("bir anahtar ASLA hem upsert hem delete'te kalmaz", () => {
    // Let's force an input that directly violates the contract:
    const q = requeueFailed(delta([{ item_key: "a", x: 1 }]), delta([], ["a"]))
    const upsertKeys = new Set(q.upsert.map((u) => u.item_key))
    expect(q.delete.some((k) => upsertKeys.has(k))).toBe(false)
  })
})

describe("inverseOf", () => {
  const before = {
    item_key: "a", x: 10, y: 20, title: "Eski", color: "#fff",
    rev: 7, updated_at: "2026-08-09T10:00:00Z", assignee_name: "Ali",
  } as unknown as PlanningItem

  it("YALNIZ yamada geçen alanları ters çevirir", () => {
    const ters = inverseOf(before, { item_key: "a", x: 99, title: "Yeni" })
    expect(ters).toEqual({ item_key: "a", x: 10, title: "Eski" })
  })

  it("sunucu türevi alanları (rev/updated_at) geri YAZMAZ", () => {
    const ters = inverseOf(before, { item_key: "a", y: 1 })
    expect(ters).not.toHaveProperty("rev")
    expect(ters).not.toHaveProperty("updated_at")
    expect(ters).not.toHaveProperty("assignee_name")
  })
})

describe("isEmpty / emptyDelta", () => {
  it("yeni delta boştur", () => {
    expect(isEmpty(emptyDelta())).toBe(true)
  })

  it("yalnız silme varsa boş DEĞİLDİR", () => {
    expect(isEmpty(delta([], ["a"]))).toBe(false)
  })
})
