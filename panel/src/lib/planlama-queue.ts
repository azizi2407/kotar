// Planlama Panosu kaydetme kuyruğu — SAF fonksiyonlar (React/DOM yok).
//
// Neden ayrı dosya: bu mantık `usePlanningStore`'un içinde yaşarken test
// edilemiyordu ve buradaki bir hata SESSİZ: zombi kart (silinen öğe geri doğar),
// yutulan silme, yanlış geri alma. Kullanıcı hatayı ancak işini kaybettikten
// sonra fark eder. Saf hale getirildi ki `planlama-queue.test.ts` kilitleyebilsin.
//
// Sözleşme: bir anahtar ASLA aynı anda hem `upsert`te hem `delete`te olamaz.
import type { PlanningItem } from "@/lib/planlama"

export type ItemPatch = Partial<PlanningItem> & { item_key: string }

export interface Delta {
  upsert: ItemPatch[]
  delete: string[]
}

export function emptyDelta(): Delta {
  return { upsert: [], delete: [] }
}

/** Silme niyetini kuyruğa al: aynı anahtarın bekleyen upsert'ü DÜŞER.
 *  (Silinecek öğeye ait konum yaması göndermenin anlamı yok ve sunucu sırasına
 *  göre öğeyi diriltebilir.) */
export function mergeDelete(queue: Delta, keys: string[]): Delta {
  if (!keys.length) return queue
  const gelen = new Set(keys)
  const del = [...queue.delete]
  for (const key of keys) if (!del.includes(key)) del.push(key)
  return { upsert: queue.upsert.filter((u) => !gelen.has(u.item_key)), delete: del }
}

/** Upsert niyetini kuyruğa al: aynı anahtara ikinci yama BİRLEŞİR (üzerine yazmaz,
 *  alan alan merge) — ayrı satır eklemek son yazanın öncekini kaybetmesine yol açar. */
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

/** Başarısız batch'i kuyruğun BAŞINA geri koy.
 *
 *  Başarısız batch daha ESKİ niyettir: aynı anahtara bu arada yeni bir niyet
 *  geldiyse YENİ olan kazanır, eski satır düşer. Ham `concat` bu kuralı atlıyordu
 *  ve uçuşta silinen bir öğenin eski upsert'i geri gelip aynı PATCH'te ikisi
 *  birden gidiyordu → sunucu sırasına göre öğe DİRİLİYOR ya da silme yutuluyor. */
export function requeueFailed(failed: Delta, current: Delta): Delta {
  const yeni = new Set<string>([
    ...current.upsert.map((u) => u.item_key),
    ...current.delete,
  ])
  const upsert = [...failed.upsert.filter((u) => !yeni.has(u.item_key)), ...current.upsert]
  const del = [...failed.delete.filter((k) => !yeni.has(k)), ...current.delete]
  // Savunma amaçlı son süzgeç: buraya çakışma düşmemeli, düşerse SİLME kazanır
  // (veri diriltmek, fazladan silmekten daha kötü bir hata sınıfı).
  const delSet = new Set(del)
  return { upsert: upsert.filter((u) => !delSet.has(u.item_key)), delete: del }
}

/** Bir yamanın tersi — geri alma kaydı için.
 *
 *  YALNIZ yamada geçen alanlar ters çevrilir. Eskiden `{...before}` gönderiliyordu;
 *  bu `rev`/`updated_at`/`assignee_name` gibi SUNUCU TÜREVİ alanları da geri yazıyor
 *  ve sunucunun rev tabanlı çakışma tespitini yanlış tetikliyordu. */
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
