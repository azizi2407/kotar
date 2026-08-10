// Reklam zaman çizelgesi (Gantt) — her kampanya, tarih aralığı boyunca uzanan bir çubuk.
// Satırlar müşteriye göre gruplanır; x ekseni görünen kampanyaları kapsayan gün penceresi.
// Bitişi boş kampanya "devam ediyor" → çubuk pencerenin sonuna kadar uzar (kesikli uç).
import { useMemo } from "react"

import { fmtTRY, PLATFORM_LABELS, STATUS_LABELS, type AdCampaign } from "@/lib/ads"
import { cn } from "@/lib/utils"

const DAY = 86_400_000
const LABEL_W = 224 // sol etiket sütunu (px) — yatay kaydırmada sabit kalır

// 'YYYY-MM-DD' → UTC gün başlangıcı (ms). TZ kaymasını önlemek için UTC matematiği.
function parseDay(s: string) {
  const [y, m, d] = s.split("-").map(Number)
  return Date.UTC(y, (m || 1) - 1, d || 1)
}

function todayUTC() {
  const n = new Date()
  return Date.UTC(n.getFullYear(), n.getMonth(), n.getDate())
}

function fmtDay(ms: number) {
  return new Date(ms).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", timeZone: "UTC" })
}

const STATUS_BAR: Record<string, string> = {
  active: "bg-emerald-500/85 hover:bg-emerald-500",
  planned: "bg-amber-500/85 hover:bg-amber-500",
  finished: "bg-slate-400/80 hover:bg-slate-400 dark:bg-slate-500/70",
}

export function AdsGantt({
  campaigns,
  onSelect,
}: {
  campaigns: AdCampaign[]
  onSelect: (c: AdCampaign) => void
}) {
  const model = useMemo(() => {
    if (campaigns.length === 0) return null
    const today = todayUTC()

    // Pencere: en erken başlangıç → en geç bitiş (devam edenler için bugün + 14 gün pay)
    let min = Infinity
    let max = -Infinity
    for (const c of campaigns) {
      const s = parseDay(c.start_date)
      const e = c.end_date ? parseDay(c.end_date) : Math.max(today, s) + 14 * DAY
      if (s < min) min = s
      if (e > max) max = e
    }
    min -= 2 * DAY
    max += 2 * DAY
    const dayCount = Math.max(1, Math.round((max - min) / DAY) + 1)
    // Gün genişliği: dar aralıkta ferah, geniş aralıkta sıkışık ama okunur (yatay kaydırma var)
    const pxPerDay = dayCount <= 40 ? 26 : dayCount <= 120 ? 12 : dayCount <= 400 ? 5 : 2.2
    const width = Math.round(dayCount * pxPerDay)

    // Ay başlıkları (pencere içindeki her ayın payı)
    const months: { label: string; left: number; width: number }[] = []
    const first = new Date(min)
    const cur = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1))
    while (cur.getTime() <= max) {
      const mStart = Math.max(cur.getTime(), min)
      const next = Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 1)
      const mEnd = Math.min(next - DAY, max)
      const left = ((mStart - min) / DAY) * pxPerDay
      const w = ((mEnd - mStart) / DAY + 1) * pxPerDay
      if (w > 1) {
        months.push({
          label: new Date(mStart).toLocaleDateString("tr-TR", { month: "long", year: "numeric", timeZone: "UTC" }),
          left, width: w,
        })
      }
      cur.setUTCMonth(cur.getUTCMonth() + 1)
    }

    // Müşteriye göre grupla (müşteri adı alfabetik, içinde başlangıca göre)
    const groups = new Map<number, { name: string; rows: AdCampaign[] }>()
    for (const c of campaigns) {
      const g = groups.get(c.client_id) ?? { name: c.client_name || `#${c.client_id}`, rows: [] }
      g.rows.push(c)
      groups.set(c.client_id, g)
    }
    const grouped = [...groups.values()]
      .map((g) => ({ ...g, rows: [...g.rows].sort((a, b) => a.start_date.localeCompare(b.start_date)) }))
      .sort((a, b) => a.name.localeCompare(b.name, "tr"))

    const todayLeft = today >= min && today <= max ? ((today - min) / DAY) * pxPerDay : null

    // Gün numarası işaretleri: BUGÜN + kampanyaların BİTİŞ tarihleri. Her günü
    // numaralamıyoruz (kalabalık olurdu); bunlar "bir bakışta gün ayrımı" için çapa.
    // Çakışan etiketler elenir — bugün her zaman öncelikli, sonra soldan sağa yerleşir.
    const posOf = (ms: number) => ((ms - min) / DAY) * pxPerDay
    const dayNum = (ms: number) => String(new Date(ms).getUTCDate())
    const ticks: { ms: number; left: number; label: string; kind: "today" | "end" }[] = []
    const placed: number[] = []
    const MIN_GAP = 18 // px — etiketler bundan yakınsa ikincisi elenir

    if (todayLeft != null) {
      ticks.push({ ms: today, left: todayLeft, label: dayNum(today), kind: "today" })
      placed.push(todayLeft)
    }
    const endDays = [...new Set(campaigns.filter((c) => c.end_date).map((c) => c.end_date!))]
      .map(parseDay)
      .filter((ms) => ms >= min && ms <= max)
      .sort((a, b) => a - b)
    for (const ms of endDays) {
      // Bitiş günü DAHİL olduğu için çubuk o günün SONUNDA biter → etiket/çizgi de
      // oraya hizalanır (gün başına değil), yoksa bir gün solda görünürdü.
      const left = posOf(ms) + pxPerDay
      if (placed.some((p) => Math.abs(p - left) < MIN_GAP)) continue
      ticks.push({ ms, left, label: dayNum(ms), kind: "end" })
      placed.push(left)
    }
    ticks.sort((a, b) => a.left - b.left)

    // Gün ızgarası: gün başına çizgi (dar aralıkta), çok uzun pencerede haftalık
    const gridStep = pxPerDay >= 6 ? pxPerDay : pxPerDay * 7
    const gridBg = `repeating-linear-gradient(to right, rgba(148,163,184,0.28) 0 1px, transparent 1px ${gridStep}px)`

    return { min, max, pxPerDay, width, months, grouped, todayLeft, ticks, gridBg }
  }, [campaigns])

  if (!model) {
    return (
      <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
        Gösterilecek reklam yok — filtreyi değiştir ya da "Reklam ekle" ile başla.
      </div>
    )
  }

  const { min, pxPerDay, width, months, grouped, todayLeft, ticks, gridBg } = model

  function bar(c: AdCampaign) {
    const s = parseDay(c.start_date)
    const ongoing = !c.end_date
    const e = ongoing ? min + (width / pxPerDay) * DAY : parseDay(c.end_date!)
    const left = ((s - min) / DAY) * pxPerDay
    const w = Math.max(pxPerDay, ((e - s) / DAY + 1) * pxPerDay)
    return { left, width: Math.min(w, width - left), ongoing }
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <div style={{ width: LABEL_W + width }}>
        {/* Ay başlıkları */}
        <div className="flex border-b bg-muted/40">
          <div style={{ width: LABEL_W }}
            className="sticky left-0 z-20 shrink-0 border-r bg-muted/40 px-3 py-1.5 text-xs font-medium">
            Müşteri / Kampanya
          </div>
          <div className="relative" style={{ width, height: 28 }}>
            {months.map((m) => (
              <div key={m.label} style={{ left: m.left, width: m.width }}
                className="absolute top-0 h-full truncate border-l px-1.5 py-1.5 text-xs text-muted-foreground">
                {m.label}
              </div>
            ))}
            {todayLeft != null && (
              <div className="absolute top-0 z-10 h-full w-px bg-red-500" style={{ left: todayLeft }} />
            )}
          </div>
        </div>

        {/* Gün numarası şeridi — yalnız BUGÜN + kampanya BİTİŞ günleri (çakışanlar elenir) */}
        <div className="flex border-b bg-background">
          <div style={{ width: LABEL_W }}
            className="sticky left-0 z-20 shrink-0 border-r bg-background px-3 py-1 text-[10px] text-muted-foreground">
            gün
          </div>
          <div className="relative" style={{ width, height: 20, backgroundImage: gridBg }}>
            {ticks.map((t) => (
              <div
                key={`${t.kind}-${t.ms}`}
                style={{ left: t.left }}
                className={cn(
                  "absolute top-0 -translate-x-1/2 rounded px-1 text-[10px] font-medium leading-5",
                  t.kind === "today"
                    ? "bg-red-500 text-white"
                    : "text-muted-foreground",
                )}
                title={t.kind === "today" ? `Bugün · ${fmtDay(t.ms)}` : `Bitiş · ${fmtDay(t.ms)}`}
              >
                {t.label}
              </div>
            ))}
          </div>
        </div>

        {/* Gruplar + çubuklar */}
        {grouped.map((g) => (
          <div key={g.name}>
            <div className="flex border-b bg-muted/20">
              <div style={{ width: LABEL_W }}
                className="sticky left-0 z-20 shrink-0 border-r bg-muted/20 px-3 py-1 text-xs font-semibold">
                {g.name}
              </div>
              <div className="relative" style={{ width, backgroundImage: gridBg }}>
                {todayLeft != null && (
                  <div className="absolute top-0 h-full w-px bg-red-500/60" style={{ left: todayLeft }} />
                )}
              </div>
            </div>
            {g.rows.map((c) => {
              const b = bar(c)
              return (
                <div key={c.id} className="flex border-b last:border-0 hover:bg-muted/30">
                  <div style={{ width: LABEL_W }}
                    className="sticky left-0 z-20 shrink-0 truncate border-r bg-background px-3 py-2 text-xs">
                    <span className="text-muted-foreground">{c.title || "(adsız)"}</span>
                  </div>
                  <div className="relative" style={{ width, height: 36, backgroundImage: gridBg }}>
                    {/* ay sınırları (gün ızgarasından daha belirgin) */}
                    {months.map((m) => (
                      <div key={m.label} className="absolute top-0 h-full border-l border-border"
                        style={{ left: m.left }} />
                    ))}
                    {/* gün numarası verilen tarihlerde ince dikey çapa (etiketle hizalı) */}
                    {ticks.filter((t) => t.kind === "end").map((t) => (
                      <div key={t.ms} className="absolute top-0 h-full w-px bg-muted-foreground/25"
                        style={{ left: t.left }} />
                    ))}
                    {todayLeft != null && (
                      <div className="absolute top-0 z-10 h-full w-px bg-red-500/60" style={{ left: todayLeft }} />
                    )}
                    <button
                      type="button"
                      onClick={() => onSelect(c)}
                      title={`${c.client_name} · ${c.title || "(adsız)"}\n${PLATFORM_LABELS[c.platform] ?? c.platform} · ${STATUS_LABELS[c.status] ?? c.status}\n${fmtDay(parseDay(c.start_date))} → ${c.end_date ? fmtDay(parseDay(c.end_date)) : "devam ediyor"}\n${fmtTRY(c.amount_spent)}${c.notes ? `\n${c.notes}` : ""}`}
                      className={cn(
                        "absolute top-1.5 flex h-6 items-center gap-1 overflow-hidden rounded px-1.5 text-[11px] font-medium text-white transition-colors",
                        STATUS_BAR[c.status] ?? "bg-primary/80",
                        b.ongoing && "rounded-r-none",
                      )}
                      style={{ left: b.left, width: b.width }}
                    >
                      <span className="truncate">{fmtTRY(c.amount_spent)}</span>
                      {b.width > 110 && (
                        <span className="truncate opacity-80">· {PLATFORM_LABELS[c.platform] ?? c.platform}</span>
                      )}
                    </button>
                    {b.ongoing && (
                      // "devam ediyor" ucu: çubuk penceresinin sonunda kesikli sınır
                      <div className="pointer-events-none absolute top-1.5 h-6 border-y border-r border-dashed border-muted-foreground/60"
                        style={{ left: b.left + b.width, width: 10 }} />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* Açıklama */}
      <div className="flex flex-wrap items-center gap-3 border-t bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-emerald-500/85" /> Aktif</span>
        <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-amber-500/85" /> Planlandı</span>
        <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-slate-400/80" /> Bitti</span>
        <span className="flex items-center gap-1"><span className="h-3 w-px bg-red-500" /> Bugün</span>
        <span>· Üst şeritteki sayılar: <b className="rounded bg-red-500 px-1 text-white">gün</b> = bugün, gri sayılar = kampanya bitiş günü</span>
        <span>· Çubuğa tıkla → düzenle</span>
      </div>
    </div>
  )
}
