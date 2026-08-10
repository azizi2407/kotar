// Pano şablonları — SAF üreteçler. Her biri `Partial<PlanningItem>[]` döndürür,
// çağıran tek `store.commit()` ile hepsini bir delta PATCH'te gönderir.
//
// Sınır: sunucu `MAX_BATCH = 200`. Şablonlar bunun çok altında tutulur; yine de
// `chunkForPatch()` bölerek göndermeyi mümkün kılar.
import { COLORS, DEFAULT_SIZE, newItemKey, type PlanningItem } from "@/lib/planlama"

export type NewItem = Partial<PlanningItem> & { item_key: string }

/** Sunucunun tek PATCH'te kabul ettiği azami öğe (planning.MAX_BATCH ile eş). */
export const MAX_BATCH = 200

export function chunkForPatch<T>(items: T[], size = MAX_BATCH): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

function region(title: string, x: number, y: number, w: number, h: number, color = "#f1f5f9"): NewItem {
  return { item_key: newItemKey(), type: "region", title, x, y, width: w, height: h,
           z: -1, color, status: "open" }
}

function card(title: string, x: number, y: number, color?: string, label?: string): NewItem {
  return { item_key: newItemKey(), type: "card", title, x, y,
           width: DEFAULT_SIZE.card.width, height: DEFAULT_SIZE.card.height,
           z: 0, color: color ?? COLORS[0], label: label ?? null, status: "open" }
}

function note(text: string, x: number, y: number): NewItem {
  return { item_key: newItemKey(), type: "note", text, x, y,
           width: DEFAULT_SIZE.note.width, height: DEFAULT_SIZE.note.height,
           z: 0, color: "#fef9c3", status: "open" }
}

const GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

/** Haftalık plan: 7 gün bölgesi yan yana, her birinde bir başlangıç kartı. */
function haftalikPlan(ox: number, oy: number): NewItem[] {
  const W = 260, H = 520, GAP = 16
  const out: NewItem[] = []
  GUNLER.forEach((gun, i) => {
    const x = ox + i * (W + GAP)
    out.push(region(gun, x, oy, W, H, i >= 5 ? "#f8fafc" : "#f1f5f9"))
    out.push(card("Yeni iş", x + 20, oy + 44, COLORS[i % COLORS.length]))
  })
  return out
}

/** Kampanya akışı: brief → içerik → onay → yayın, aralarında bağlantı yok
 *  (bağlantıyı kullanıcı çizsin — otomatik ok koymak çoğu zaman yanlış oluyor). */
function kampanyaAkisi(ox: number, oy: number): NewItem[] {
  const asamalar: [string, string][] = [
    ["Brief", "#e0f2fe"], ["İçerik üretimi", "#e0e7ff"],
    ["İç onay", "#fef9c3"], ["Müşteri onayı", "#f3e8ff"], ["Yayın", "#dcfce7"],
  ]
  const out: NewItem[] = [region("Kampanya akışı", ox, oy, 5 * 236 + 24, 240, "#f8fafc")]
  asamalar.forEach(([ad, renk], i) => {
    out.push(card(ad, ox + 20 + i * 236, oy + 48, renk, `Aşama ${i + 1}`))
  })
  out.push(note("Aşamalar arasına ok çizmek için karttan diğerine sürükle.", ox + 20, oy + 190))
  return out
}

/** Çekim akışı: hazırlık → çekim → kurgu → teslim. */
function cekimAkisi(ox: number, oy: number): NewItem[] {
  const asamalar: [string, string][] = [
    ["Çekim planı", "#e0f2fe"], ["Ekipman / lokasyon", "#f1f5f9"],
    ["Çekim günü", "#fee2e2"], ["Kurgu", "#e0e7ff"], ["Teslim", "#dcfce7"],
  ]
  const out: NewItem[] = [region("Çekim akışı", ox, oy, 3 * 236 + 24, 460, "#f8fafc")]
  asamalar.forEach(([ad, renk], i) => {
    out.push(card(ad, ox + 20 + (i % 3) * 236, oy + 48 + Math.floor(i / 3) * 168, renk))
  })
  return out
}

export interface Template {
  id: string
  name: string
  description: string
  build: (originX: number, originY: number) => NewItem[]
}

export const TEMPLATES: Template[] = [
  { id: "hafta", name: "Haftalık plan",
    description: "7 gün bölgesi, her birinde bir başlangıç kartı",
    build: haftalikPlan },
  { id: "kampanya", name: "Kampanya akışı",
    description: "Brief → içerik → iç onay → müşteri onayı → yayın",
    build: kampanyaAkisi },
  { id: "cekim", name: "Çekim akışı",
    description: "Plan → ekipman → çekim → kurgu → teslim",
    build: cekimAkisi },
]
