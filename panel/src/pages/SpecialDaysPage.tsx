// Special Days management — monthly event list (add/delete) + client selection link +
// Calendar view (2026-07-21): shows at a glance which day belongs to which brand's special day.
import { useMemo, useState } from "react"
import { CalendarDays, ChevronLeft, ChevronRight, Copy, Link2, List, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import { useI18n } from "@/lib/i18n"
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

// Special day selection message sent to the client — kept as a translation key
// in the dictionary ("pages.specialDays.selectionMessage"), read via t() in the page component.

// Month/day names — translation keys, produced via t() at render time.
const MONTH_KEYS = ["", "pages.specialDays.month.1", "pages.specialDays.month.2",
  "pages.specialDays.month.3", "pages.specialDays.month.4", "pages.specialDays.month.5",
  "pages.specialDays.month.6", "pages.specialDays.month.7", "pages.specialDays.month.8",
  "pages.specialDays.month.9", "pages.specialDays.month.10", "pages.specialDays.month.11",
  "pages.specialDays.month.12"]
const WEEKDAY_KEYS = ["pages.specialDays.weekday.mon", "pages.specialDays.weekday.tue",
  "pages.specialDays.weekday.wed", "pages.specialDays.weekday.thu",
  "pages.specialDays.weekday.fri", "pages.specialDays.weekday.sat",
  "pages.specialDays.weekday.sun"]

// Day detail modal — clicking a cell shows that day's events in an expanded, detailed view.
function DayDetailDialog({ day, month, year, events, onClose }: {
  day: number | null; month: number; year: number
  events: SpecialDayOverviewItem[]; onClose: () => void
}) {
  const { t, lang } = useI18n()
  if (day == null) return null
  const date = new Date(year, month - 1, day)
  const title = date.toLocaleDateString(lang === "tr" ? "tr-TR" : "en-US", {
    day: "numeric", month: "long", year: "numeric", weekday: "long",
  })
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {events.length
              ? t("pages.specialDays.eventsOnThisDay", { count: events.length })
              : t("pages.specialDays.noEventsOnThisDay")}
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
          {/* Every item here is selected (filtered by backend) → single style, no condition.
              The "no brand has selected yet" branch was REMOVED: it contradicted itself,
              since a day with no selection never enters this list at all. */}
          {events.map((it) => (
            <div key={it.id}
              className="space-y-2 rounded-lg border border-amber-400/60 bg-amber-50/50 p-3 dark:border-amber-500/40 dark:bg-amber-950/20">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{it.day_name}</span>
                {it.date_start != null && it.date_end != null && (
                  <Badge variant="outline" className="text-[10px]">
                    {t("pages.specialDays.specialWeek")} · {it.date_start}–{it.date_end} {t(MONTH_KEYS[month])}
                  </Badge>
                )}
                {it.status === "draft" && (
                  <Badge variant="secondary" className="text-[10px]">
                    {t("pages.specialDays.draft")}{it.generated_by === "ai" ? " · AI" : ""}
                  </Badge>
                )}
              </div>
              {it.description && (
                <p className="text-sm text-muted-foreground">{it.description}</p>
              )}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">
                    {t("pages.specialDays.brandsWhoChose", { count: it.client_names.length })}
                  </span>
                  <Button
                    type="button" variant="ghost" size="sm" className="h-6 px-2 text-xs"
                    onClick={async () => {
                      await navigator.clipboard.writeText(it.client_names.join("\n"))
                      toast.success(t("pages.specialDays.brandListCopied"))
                    }}>
                    <Copy className="mr-1 h-3 w-3" /> {t("pages.specialDays.copy")}
                  </Button>
                </div>
                {/* selectable plain-text list — copyable line by line */}
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

// Calendar grid: each day cell shows the special days **SELECTED BY THE CLIENT**
// that fall on that day, with a brand chip. A day with no selection never appears
// (filtered by the backend's `sd_overview`) — the calendar is not a "suggested days"
// board but a "days clients want content on this month" board. Clicking a cell opens DayDetailDialog.
function CalendarView({ month, year, items }: {
  month: number; year: number; items: SpecialDayOverviewItem[]
}) {
  const { t } = useI18n()
  const WEEKDAYS = WEEKDAY_KEYS.map((k) => t(k))
  const [selectedDay, setSelectedDay] = useState<number | null>(null)
  const daysInMonth = new Date(year, month, 0).getDate()
  const firstOffset = (new Date(year, month - 1, 1).getDay() + 6) % 7  // Mon=0
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
      {/* Since the calendar is selection-driven, an empty month is normal; without
          stating the reason, an empty grid reads as "data isn't loading". */}
      {items.length === 0 && (
        <p className="rounded-lg border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          {t("pages.specialDays.calendarEmptyPre")} <strong>{t("pages.specialDays.marked")}</strong>{" "}
          {t("pages.specialDays.calendarEmptyMid")} <strong>{t("pages.specialDays.listView")}</strong>{t("pages.specialDays.calendarEmptyPost")}
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
                  {/* Chip only for brands that made a selection. The gray "no brand
                      selection" chip was REMOVED (2026-07-31): an unselected day never reaches here. */}
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
          <span className="font-medium">{t("pages.specialDays.dateless")}</span>
          {dateless.map((it) => (
            <Badge key={it.id} variant="outline" className="text-[10px]">
              {it.client_names.join(", ")} · {it.day_name}
            </Badge>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {t("pages.specialDays.calendarFooterPre")} <strong>{t("pages.specialDays.calendarFooterStrong")}</strong>{" "}
        {t("pages.specialDays.calendarFooterMid")} <strong>{t("pages.specialDays.listView")}</strong> {t("pages.specialDays.calendarFooterPost")}
      </p>
    </div>
  )
}

function dateLabel(e: SpecialDayEvent) {
  if (e.date_start && e.date_end) return `${e.date_start}–${e.date_end}`
  return e.date_num ? String(e.date_num) : ""
}

export function SpecialDaysPage() {
  const { t } = useI18n()
  const MONTHS = MONTH_KEYS.map((k) => (k ? t(k) : ""))
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
  const [clientQ, setClientQ] = useState("")          // client list search
  const [copying, setCopying] = useState<number | null>(null)  // client id being copied
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
    toast.success(t("pages.specialDays.eventAdded"))
  }

  // Ready-made message for the client + that month's selection link (feature owner 2026-07-25).
  async function copyForClient(clientId: number, clientName: string) {
    setCopying(clientId)
    try {
      const token = await m.link.mutateAsync({ client_id: clientId, month: ym.month, year: ym.year })
      const url = `${window.location.origin}/special-days/${token}`
      await navigator.clipboard.writeText(`${t("pages.specialDays.selectionMessage")}\n\n${url}`)
      toast.success(t("pages.specialDays.messageAndLinkCopied", { client: clientName }))
    } catch {
      toast.error(t("pages.specialDays.linkGenerationFailed"))
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
          <h1 className="text-2xl font-semibold tracking-tight">{t("pages.specialDays.title")}</h1>
          <p className="text-muted-foreground">
            {isManagement
              ? t("pages.specialDays.subtitleManagement")
              : t("pages.specialDays.subtitleReadOnly")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isManagement && (
            <div className="flex items-center gap-1 rounded-lg border p-1">
              <Button variant={view === "liste" ? "secondary" : "ghost"} size="sm"
                onClick={() => setView("liste")}>
                <List className="mr-1 h-4 w-4" /> {t("pages.specialDays.listView")}
              </Button>
              <Button variant={view === "takvim" ? "secondary" : "ghost"} size="sm"
                onClick={() => setView("takvim")}>
                <CalendarDays className="mr-1 h-4 w-4" /> {t("pages.specialDays.calendarViewLabel")}
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

      {/* Add — management only */}
      {isManagement && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
          <div className="flex-1 min-w-40">
            <Input placeholder={t("pages.specialDays.eventNamePlaceholder")} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Input className="w-20" type="number" placeholder={t("pages.specialDays.dayPlaceholder")} value={day} onChange={(e) => setDay(e.target.value)} />
          <div className="flex-1 min-w-40">
            <Input placeholder={t("pages.specialDays.descriptionPlaceholder")} value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <Button onClick={addEvent} disabled={m.create.isPending}>
            <Plus className="mr-1 h-4 w-4" /> {t("pages.specialDays.add")}
          </Button>
        </div>
      )}

      {/* Client selection links — one-click message+link per client (management only,
          list view). Replaces the old "select client → generate link" block. */}
      {isManagement && view === "liste" && (
        <div className="rounded-lg border">
          <div className="flex flex-wrap items-center gap-2 border-b bg-muted/20 px-3 py-2">
            <Link2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">{t("pages.specialDays.clientLinksTitle")}</span>
            <span className="text-xs text-muted-foreground">
              {MONTHS[ym.month]} {ym.year} · {t("pages.specialDays.clientLinksHint")}
            </span>
            <Input
              className="ml-auto h-8 w-48"
              placeholder={t("pages.specialDays.searchClientPlaceholder")}
              value={clientQ}
              onChange={(e) => setClientQ(e.target.value)}
            />
          </div>
          <div className="max-h-64 overflow-y-auto">
            {filteredClients.length === 0 ? (
              <p className="px-3 py-4 text-sm text-muted-foreground">{t("pages.specialDays.clientNotFound")}</p>
            ) : (
              <ul className="divide-y">
                {filteredClients.map((c) => (
                  <li key={c.id} className="flex items-center gap-2 px-3 py-1.5">
                    <span className="flex-1 truncate text-sm">{c.name}</span>
                    <Button
                      variant="ghost" size="sm"
                      onClick={() => copyForClient(c.id, c.name)}
                      disabled={copying === c.id}
                      title={t("pages.specialDays.copyMessageAndLinkTitle")}
                    >
                      <Copy className="mr-1 h-3.5 w-3.5" />
                      {copying === c.id ? t("pages.specialDays.copying") : t("pages.specialDays.copy")}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* Calendar view — day × brand at a glance (the only view for designers) */}
      {view === "takvim" || !isManagement ? (
        overviewLoading ? (
          <Skeleton className="h-72 w-full" />
        ) : (
          <CalendarView month={ym.month} year={ym.year} items={overview ?? []} />
        )
      ) : /* List */ isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : globalEvents.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">{t("pages.specialDays.noEventsThisMonth")}</p>
      ) : (
        <div className="space-y-2">
          {globalEvents.map((e) => (
            <div key={e.id} className="flex items-center gap-3 rounded-lg border p-3">
              {dateLabel(e) && <Badge variant="outline">{dateLabel(e)}</Badge>}
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{e.day_name}</span>
                  {e.status === "draft" && (
                    <Badge variant="secondary">{t("pages.specialDays.draft")}{e.generated_by === "ai" ? " · AI" : ""}</Badge>
                  )}
                </div>
                {e.description && <div className="text-sm text-muted-foreground">{e.description}</div>}
              </div>
              {isManagement && e.status === "draft" && (
                <Button variant="outline" size="sm" onClick={() => approve.mutate(e.id)}
                  disabled={approve.isPending}>
                  {t("pages.specialDays.approve")}
                </Button>
              )}
              <Button variant="ghost" size="icon" className="text-destructive"
                onClick={() => m.remove.mutate(e.id)} title={t("pages.specialDays.delete")}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
