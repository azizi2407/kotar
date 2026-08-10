// Planlama Panosu yerel durumu: uçuşan taslak katmanı + kaydetme kuyruğu + geri alma.
//
// ESKİ KODUN HATASI: `useEffect(() => { if (data) setTasks(data.tasks) }, [data])` —
// sunucudan gelen her yeni veri yerel state'i EZİYORDU. Kaydet → invalidate → refetch
// → setTasks döngüsünde kullanıcı o sırada bir kartı sürüklüyorsa değişikliği uçuyordu.
//
// MODEL — iki katman, ezilme fiziken imkânsız:
//   * queryItems (React Query cache) = sunucu gerçeği; tazeleme yalnız BUNU değiştirir
//   * draft (Map<item_key, Partial<Item>>) = UÇUŞAN katman; parmağın altındaki kart
//   * render = merge(queryItems, draft) — draft her zaman üstte
// Bir öğe ancak kendi yazması sunucuda onaylandıktan sonra draft'tan düşer.
//
// KİMLİK SABİTLİĞİ (2026-08-09) — bu dosyanın ikinci sözleşmesi.
// Dönen nesne ve içindeki callback'ler HER RENDER'DA YENİ olursa tüketici zincir
// şöyle çöküyordu: yeni `store` → `PlanlamaPage`teki `onPatch` yeni → `PlanningFlow`
// içindeki `nodes` useMemo'su yeniden kuruluyor → tüm node `data` nesneleri yeni →
// `memo` çöküyor → görünür TÜM node'lar her render'da yeniden çiziliyor (sürüklemede
// her karede, aramada her tuş vuruşunda). Bu yüzden sık değişen `byKey` deps'te DEĞİL,
// ref üzerinden okunuyor ve dönüş `useMemo`'da. Ref'ler RENDER SIRASINDA atanır —
// effect'te atansa commit anında bayat okurdu.
//
// AMA HER ŞEY REF'E ALINMAZ: yazma isteği `boardKey`e bağlıdır ve onu ref'te taşımak
// pano değişiminde yazmayı YANLIŞ PANOYA gönderiyordu (bkz. `gonder`). Ref'e almanın
// güvenli olduğu değer, hedefi değil yalnız İÇERİĞİ etkileyen değerdir.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import {
  boardQueryKey, patchBoardItems, type PlanningItem,
} from "@/lib/planlama"
import {
  emptyDelta, inverseOf, isEmpty, mergeDelete, mergeUpsert, requeueFailed,
  type Delta, type ItemPatch,
} from "@/lib/planlama-queue"

// Tüketiciler bu tipi buradan import ediyor — tek kaynak `planlama-queue`, burada
// yalnız yeniden yayınlanıyor.
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

  /** Geri/İleri alma düğmelerinin pasifleşebilmesi için yığın derinlikleri STATE.
   *  Eskiden yalnız `canUndo()` (ref okuyan fonksiyon) vardı; reaktif olmadığı için
   *  düğmeler hiç pasifleşmiyor, boş yığında basınca ancak toast çıkıyordu. */
  const [undoDepth, setUndoDepth] = useState(0)
  const [redoDepth, setRedoDepth] = useState(0)

  /** Kullanıcı şu an sürüklüyor/yazıyor mu — tazeleme bunu bekler.
   *  REF DEĞİL STATE: eskiden ref'ti ve render sırasında okunuyordu, yani
   *  "kaydediliyor" şeridi ancak alakasız bir re-render'da doğru görünüyordu.
   *  Ayrıca yalnız commit false'a çekiyordu; commit hiç çağrılmazsa (ör. sürükleme
   *  yarıda kalırsa) sonsuza kadar true kalıp otomatik tazelemeyi KALICI olarak
   *  öldürüyordu. Artık flush bittiğinde de sıfırlanır. */
  const [interacting, setInteracting] = useState(false)

  /** base_version'ı closure'dan DEĞİL ref'ten oku: debounce'lu flush, commit
   *  anındaki sürümü taşıyordu; arada uzak refetch olursa bayat sürüm gidiyor ve
   *  sunucu gereksiz yere `stale: true` + tam liste döndürüyordu. */
  const versionRef = useRef(serverVersion)
  versionRef.current = serverVersion

  /** Yazma isteği DÜZ FONKSİYONLA kurulur, mutation nesnesiyle DEĞİL.
   *
   *  Mutation her render'da yeni kimlik alır; deps'te olsaydı `flush` ve ondan
   *  türeyen `commit`/`remove` de her render'da yenilenirdi. Ama onu bir REF'e
   *  almak çok daha kötüydü: pano değişiminde unmount temizliği ESKİ `flush`'ı
   *  çağırır, ref ise o an ÇOKTAN yeni panonun mutation'ını tutar → bekleyen
   *  yazma YANLIŞ PANOYA giderdi (ref'ler render gövdesinde atanır, temizlik
   *  render'dan sonra koşar). `boardKey` artık closure'dan geliyor, hem kimlik
   *  sabit hem hedef doğru. */
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
      // draft'ta olup sunucuda henüz olmayan (yeni yaratılmış) öğeler
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

  /** `byKey` her öğe değişiminde (yani sürüklemede her karede) yenilenir; deps'te
   *  olsaydı `commit`/`remove` kimliği de her karede değişirdi. */
  const byKeyRef = useRef(byKey)
  byKeyRef.current = byKey

  // --- kaydetme kuyruğu ---------------------------------------------------

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
      // Yazılan öğeleri taslaktan düşür — ama SADECE bu turda gönderilenleri;
      // kullanıcı bu arada başka bir kartı sürüklüyor olabilir.
      setDraft((prev) => {
        const next = new Map(prev)
        for (const u of batch.upsert) next.delete(u.item_key)
        for (const key of batch.delete) next.delete(key)
        return next
      })
      if (res.conflicts.length) setConflicts(res.conflicts)
      setStatus("idle")
    } catch {
      // Kuyruğu KAYBETME — ama ham concat ile DEĞİL, çakışma kuralından geçirerek.
      pending.current = requeueFailed(batch, pending.current)
      setStatus("error")
    } finally {
      flushing.current = false
      // Bir tur sunucuya gidip geldiyse etkileşim bitmiştir; ref'ken burada
      // sıfırlanmadığı için takılı kalabiliyor ve tazelemeyi kilitliyordu.
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

  // --- yerel değişiklikler -------------------------------------------------

  /** Sürükleme/boyutlandırma sırasında: yalnız taslağa yaz, sunucuya gitme. */
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

  /** Değişikliği kalıcılaştır (kuyruğa al + geri alma kaydı oluştur). */
  const commit = useCallback((patches: ItemPatch[], opts: { undoable?: boolean } = {}) => {
    if (!patches.length) return
    if (opts.undoable !== false) {
      const undoUpsert: ItemPatch[] = []
      const undoDelete: string[] = []
      for (const p of patches) {
        const before = byKeyRef.current.get(p.item_key)
        if (!before) { undoDelete.push(p.item_key); continue }  // yeni yaratıldı → geri al = sil
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

  // Pano değişimi / unmount → bekleyen yazmayı ÖNCE gönder.
  // Eskiden `resetHistory()` `pending`i boşaltıyordu; debounce penceresindeki
  // (300 ms) son düzenleme sessizce çöpe gidiyordu. Temizlik fonksiyonu, effect'in
  // kurulduğu render'ın closure'ını taşır → burada çağrılan `flush` ESKİ boardKey'e
  // bağlıdır, yani yazma doğru panoya gider. `flush` senkron olarak `pending`i
  // boşalttığı için sonraki `resetHistory()` ile yarışmaz.
  useEffect(() => {
    return () => { void flush() }
    // `flush` bilerek deps'te değil: her render'da yeniden kurulsa temizlik
    // her render'da koşar ve debounce'u anlamsızlaştırır.
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

  // Dönüş `useMemo`'da: kimliği her render'da değişirse tüketicideki `nodes`
  // useMemo'su da her render'da yeniden kurulur (bkz. dosya başlığı).
  return useMemo(() => ({
    items, byKey, status, conflicts, clearConflicts,
    applyLocal, commit, remove, flush, undo, redo, resetHistory,
    // `interacting` BOOLEAN (eskiden ref'ti) — render'da doğrudan okunabilir.
    interacting,
    hasPending,
    canUndo: undoDepth > 0,
    canRedo: redoDepth > 0,
  }), [items, byKey, status, conflicts, clearConflicts, applyLocal, commit, remove,
       flush, undo, redo, resetHistory, interacting, hasPending, undoDepth, redoDepth])
}
