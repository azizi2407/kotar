// Özel Günler yönetimi — aylık etkinlik listesi (ekle/sil) + müşteri seçim linki +
// Takvim görünümü (2026-07-21): tek bakışta hangi gün hangi markanın özel günü.
import { useMemo, useState } from "react"
import { CalendarDays, ChevronLeft, ChevronRight, Copy, Link2, List, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import { trFold } from "@/lib/week"
import {
  useApproveSpecialDay, useSpecialDayEvents, useSpecialDayMutations, useSpecialDayOverview,
  type SpecialDayEvent, type SpecialDayOverviewItem,
} from "@/lib/sharing"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

// Müşteriye gönderilen özel gün seçim mesajı (metin proje sahibi tarafından belirlendi).
const SELECTION_MESSAGE =
  "Merhabalar, Özel gün listemiz hazır, yaptığınız seçimlere göre özel gün ve hafta " +
  "çalışmaları planlamaya eklenecektir. İyi günler."

const MONTHS = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
const WEEKDAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]

// Gün detay modalı — hücreye tıklayınca o günün etkinlikleri geniş/detaylı gösterilir.
function DayDetailDialog({ day, month, year, events, onClose }: {
  day: number | null; month: number; year: number
  events: SpecialDayOverviewItem[]; onClose: () => void
}) {
  if (day == null) return null
  const date = new Date(year, month - 1, day)
  const title = date.toLocaleDateString("tr-TR", {
    day: "numeric", month: "long", year: "numeric", weekday: "long",
  })
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {events.length
              ? `Bu güne düşen ${events.length} özel gün`
              : "Bu güne düşen özel gün yok."}
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
          {/* Her öğe seçilmiştir (backend süzüyor) → tek stil, koşul yok.
              "Henüz hiçbir marka seçmedi" dalı KALDIRILDI: artık kendi kendini
              yalanlıyordu, seçimi olmayan gün bu listeye hiç girmiyor. */}
          {events.map((it) => (
            <div key={it.id}
              className="space-y-2 rounded-lg border border-amber-400/60 bg-amber-50/50 p-3 dark:border-amber-500/40 dark:bg-amber-950/20">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{it.day_name}</span>
                {it.date_start != null && it.date_end != null && (
                  <Badge variant="outline" className="text-[10px]">
                    Özel Hafta · {it.date_start}–{it.date_end} {MONTHS[month]}
                  </Badge>
                )}
                {it.status === "draft" && (
                  <Badge variant="secondary" className="text-[10px]">
                    Taslak{it.generated_by === "ai" ? " · AI" : ""}
                  </Badge>
                )}
              </div>
              {it.description && (
                <p className="text-sm text-muted-foreground">{it.description}</p>
              )}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">
                    Seçen markalar ({it.client_names.length})
                  </span>
                  <Button
                    type="button" variant="ghost" size="sm" className="h-6 px-2 text-xs"
                    onClick={async () => {
                      await navigator.clipboard.writeText(it.client_names.join("\n"))
                      toast.success("Marka listesi kopyalandı")
                    }}>
                    <Copy className="mr-1 h-3 w-3" /> Kopyala
                  </Button>
                </div>
                {/* seçilebilir düz metin liste — satır satır kopyalanabilir */}
                <div className="rounded-md border bg-background px-2.5 py-1.5 text-sm leading-6 select-text">
                  {it.client_names.map((cn_) => <div key={cn_}>{cn_}</div>)}
                </div>
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// Takvim ızgarası: her gün hücresinde o güne düşen **müşteri tarafından SEÇİLMİŞ**
// özel günler, marka çipiyle. Seçilmeyen gün hiç gelmez (backend `sd_overview`
// süzüyor) — takvim "önerilen günler" panosu değil, "müşterilerin bu ay içerik
// istediği günler" panosu. Hücreye tıklanınca DayDetailDialog açılır.
function CalendarView({ month, year, items }: {
  month: number; year: number; items: SpecialDayOverviewItem[]
}) {
  const [selectedDay, setSelectedDay] = useState<number | null>(null)
  const daysInMonth = new Date(year, month, 0).getDate()
  const firstOffset = (new Date(year, month - 1, 1).getDay() + 6) % 7  // Pzt=0
  const byDay = useMemo(() => {
    const map = new Map<number, SpecialDayOverviewItem[]>()
    for (const it of items) {
      const start = it.date_num ?? it.date_start
      const end = it.date_num ?? it.date_end ?? it.date_start
      if (start == null) continue
      for (let d = start; d <= (end ?? start) && d <= daysInMonth; d++) {
        if (d >= 1) map.set(d, [...(map.get(d) ?? []), it])
      }
    }
    return map
  }, [items, daysInMonth])
  const dateless = items.filter((it) => it.date_num == null && it.date_start == null)
  const today = new Date()
  const isThisMonth = today.getMonth() + 1 === month && today.getFullYear() === year

  return (
    <div className="space-y-3">
      {/* Takvim seçim-güdümlü olduğu için boş bir ay normaldir; sebebini yazmadan
          boş ızgara "veri gelmiyor" gibi okunur. */}
      {items.length === 0 && (
        <p className="rounded-lg border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          Bu ay için müşteri tarafından seçilmiş özel gün yok. Takvimde yalnız
          müşterilerin seçim linkinden <strong>işaretlediği</strong> günler görünür —
          ayın tüm özel gün kataloğu için <strong>Liste</strong> görünümüne geçin.
        </p>
      )}
      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="grid grid-cols-7 gap-1">
            {WEEKDAYS.map((w) => (
              <div key={w} className="px-1 py-1 text-center text-xs font-semibold text-muted-foreground">{w}</div>
            ))}
            {Array.from({ length: firstOffset }).map((_, i) => <div key={`b${i}`} />)}
            {Array.from({ length: daysInMonth }, (_, i) => i + 1).map((d) => {
              const evs = byDay.get(d) ?? []
              return (
                <button key={d} type="button"
                  onClick={() => evs.length && setSelectedDay(d)}
                  className={cn("min-h-24 rounded-md border p-1 text-left",
                    evs.length
                      ? "cursor-pointer transition-colors hover:border-primary/60 hover:bg-muted/40"
                      : "cursor-default",
                    isThisMonth && today.getDate() === d && "border-primary bg-primary/5")}>
                  <div className="mb-1 text-right text-xs font-medium text-muted-foreground">{d}</div>
                  {/* Yalnız seçen markaların çipi. "Marka seçimi yok" gri çipi
                      KALDIRILDI (2026-07-31): seçilmeyen gün buraya hiç gelmiyor. */}
                  <div className="space-y-0.5">
                    {evs.flatMap((it) =>
                      it.client_names.map((cn_) => (
                        <div key={`${it.id}-${cn_}`}
                          title={`${cn_} · ${it.day_name ?? ""}`}
                          className="truncate rounded bg-amber-100 px-1 py-0.5 text-[10px] font-medium text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
                          {cn_} · {it.day_name}
                        </div>
                      )),
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </div>
      <DayDetailDialog day={selectedDay} month={month} year={year}
        events={selectedDay != null ? byDay.get(selectedDay) ?? [] : []}
        onClose={() => setSelectedDay(null)} />
      {dateless.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
          <span className="font-medium">Tarihsiz:</span>
          {dateless.map((it) => (
            <Badge key={it.id} variant="outline" className="text-[10px]">
              {it.client_names.join(", ")} · {it.day_name}
            </Badge>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Takvimde <strong>yalnızca müşterilerin seçim linkinden işaretlediği</strong> özel
        günler görünür; her çip bir markanın o günü seçtiğini gösterir. Ayın tüm özel gün
        kataloğu (seçilmemişler ve taslaklar dahil) <strong>Liste</strong> görünümünde.
      </p>
    </div>
  )
}

function dateLabel(e: SpecialDayEvent) {
  if (e.date_start && e.date_end) return `${e.date_start}–${e.date_end}`
  return e.date_num ? String(e.date_num) : ""
}

export function SpecialDaysPage() {
  const now = new Date()
  const [ym, setYm] = useState({ month: now.getMonth() + 1, year: now.getFullYear() })
  const { data: events, isLoading } = useSpecialDayEvents(ym.month, ym.year)
  const m = useSpecialDayMutations(ym.month, ym.year)
  const approve = useApproveSpecialDay(ym.month, ym.year)
  const { isManagement } = useAuth()
  const { data: clients } = useClients({ status: "active", q: "" })

  const [name, setName] = useState("")
  const [day, setDay] = useState("")
  const [desc, setDesc] = useState("")
  const [clientQ, setClientQ] = useState("")          // müşteri listesi araması
  const [copying, setCopying] = useState<number | null>(null)  // kopyalanan müşteri id
  const [view, setView] = useState<"liste" | "takvim">("liste")
  const { data: overview, isLoading: overviewLoading } =
    useSpecialDayOverview(ym.month, ym.year)

  function shift(delta: number) {
    setYm((s) => {
      let month = s.month + delta, year = s.year
      if (month < 1) { month = 12; year-- }
      if (month > 12) { month = 1; year++ }
      return { month, year }
    })
  }

  async function addEvent() {
    if (!name.trim()) return
    await m.create.mutateAsync({ day_name: name.trim(), date_num: Number(day) || null, description: desc.trim() || null })
    setName(""); setDay(""); setDesc("")
    toast.success("Özel gün eklendi")
  }

  // Müşteriye gönderilecek hazır mesaj + o ayın seçim linki (proje sahibi 2026-07-25).
  async function copyForClient(clientId: number, clientName: string) {
    setCopying(clientId)
    try {
      const token = await m.link.mutateAsync({ client_id: clientId, month: ym.month, year: ym.year })
      const url = `${window.location.origin}/special-days/${token}`
      await navigator.clipboard.writeText(`${SELECTION_MESSAGE}\n\n${url}`)
      toast.success(`${clientName} — mesaj ve link kopyalandı`)
    } catch {
      toast.error("Link üretilemedi")
    } finally {
      setCopying(null)
    }
  }

  const globalEvents = useMemo(() => (events ?? []).filter((e) => e.client_id == null), [events])

  const filteredClients = useMemo(() => {
    const q = trFold(clientQ.trim())
    const list = clients ?? []
    return q ? list.filter((c) => trFold(c.name).includes(q)) : list
  }, [clients, clientQ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Özel Günler</h1>
          <p className="text-muted-foreground">
            {isManagement
              ? "Aylık özel gün takvimi ve müşteri seçim linkleri."
              : "Aylık özel gün takvimi (salt-okunur)."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isManagement && (
            <div className="flex items-center gap-1 rounded-lg border p-1">
              <Button variant={view === "liste" ? "secondary" : "ghost"} size="sm"
                onClick={() => setView("liste")}>
                <List className="mr-1 h-4 w-4" /> Liste
              </Button>
              <Button variant={view === "takvim" ? "secondary" : "ghost"} size="sm"
                onClick={() => setView("takvim")}>
                <CalendarDays className="mr-1 h-4 w-4" /> Takvim
              </Button>
            </div>
          )}
          <div className="flex items-center gap-1 rounded-lg border p-1">
            <Button variant="ghost" size="icon" onClick={() => shift(-1)}><ChevronLeft className="h-4 w-4" /></Button>
            <div className="min-w-[8rem] text-center text-sm font-medium">{MONTHS[ym.month]} {ym.year}</div>
            <Button variant="ghost" size="icon" onClick={() => shift(1)}><ChevronRight className="h-4 w-4" /></Button>
          </div>
        </div>
      </div>

      {/* Ekle — yalnız yönetim */}
      {isManagement && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
          <div className="flex-1 min-w-40">
            <Input placeholder="Özel gün adı" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Input className="w-20" type="number" placeholder="Gün" value={day} onChange={(e) => setDay(e.target.value)} />
          <div className="flex-1 min-w-40">
            <Input placeholder="Açıklama (opsiyonel)" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <Button onClick={addEvent} disabled={m.create.isPending}>
            <Plus className="mr-1 h-4 w-4" /> Ekle
          </Button>
        </div>
      )}

      {/* Müşteri seçim linkleri — her müşteri için tek tıkla mesaj+link (yalnız yönetim,
          liste görünümü). Eski "müşteri seç → link üret" bloğunun yerini aldı. */}
      {isManagement && view === "liste" && (
        <div className="rounded-lg border">
          <div className="flex flex-wrap items-center gap-2 border-b bg-muted/20 px-3 py-2">
            <Link2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">Müşteri seçim linkleri</span>
            <span className="text-xs text-muted-foreground">
              {MONTHS[ym.month]} {ym.year} · kopyala düğmesi mesajı ve linki birlikte alır
            </span>
            <Input
              className="ml-auto h-8 w-48"
              placeholder="Müşteri ara…"
              value={clientQ}
              onChange={(e) => setClientQ(e.target.value)}
            />
          </div>
          <div className="max-h-64 overflow-y-auto">
            {filteredClients.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground">Müşteri bulunamadı.</p>
            ) : (
              <ul className="divide-y">
                {filteredClients.map((c) => (
                  <li key={c.id} className="flex items-center gap-2 px-3 py-1.5">
                    <span className="flex-1 truncate text-sm">{c.name}</span>
                    <Button
                      variant="ghost" size="sm"
                      onClick={() => copyForClient(c.id, c.name)}
                      disabled={copying === c.id}
                      title="Mesajı ve seçim linkini kopyala"
                    >
                      <Copy className="mr-1 h-3.5 w-3.5" />
                      {copying === c.id ? "Kopyalanıyor…" : "Kopyala"}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* Takvim görünümü — tek bakışta gün × marka (designer için tek görünüm) */}
      {view === "takvim" || !isManagement ? (
        overviewLoading ? (
          <Skeleton className="h-72 w-full" />
        ) : (
          <CalendarView month={ym.month} year={ym.year} items={overview ?? []} />
        )
      ) : /* Liste */ isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : globalEvents.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">Bu ay için özel gün yok.</p>
      ) : (
        <div className="space-y-2">
          {globalEvents.map((e) => (
            <div key={e.id} className="flex items-center gap-3 rounded-lg border p-3">
              {dateLabel(e) && <Badge variant="outline">{dateLabel(e)}</Badge>}
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{e.day_name}</span>
                  {e.status === "draft" && (
                    <Badge variant="secondary">Taslak{e.generated_by === "ai" ? " · AI" : ""}</Badge>
                  )}
                </div>
                {e.description && <div className="text-sm text-muted-foreground">{e.description}</div>}
              </div>
              {isManagement && e.status === "draft" && (
                <Button variant="outline" size="sm" onClick={() => approve.mutate(e.id)}
                  disabled={approve.isPending}>
                  Onayla
                </Button>
              )}
              <Button variant="ghost" size="icon" className="text-destructive"
                onClick={() => m.remove.mutate(e.id)} title="Sil">
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
