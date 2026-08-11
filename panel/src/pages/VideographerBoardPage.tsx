// Videographer shoot plan — dnd-kit Kanban. Drag a client from the pool column
// onto a day = create a plan; drag a card to another day = reschedule; within a
// column = reorder.
import { useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  DndContext, DragOverlay, PointerSensor, pointerWithin, rectIntersection,
  useDroppable, useSensor, useSensors,
  type CollisionDetection, type DragEndEvent, type DragStartEvent,
} from "@dnd-kit/core"
import {
  SortableContext, arrayMove, useSortable, verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { ChevronLeft, ChevronRight, Check, Trash2 } from "lucide-react"
import { toast } from "sonner"

import {
  useBusinesses, useMarkBusiness, useShootPlan, useShootMutations,
  type ShootPlan, type ShootTask,
} from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { currentWeekIso, localDateStr, shiftWeek, trFold, weekRangeLabel } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import { LogoButton } from "@/components/sharing/LogoButton"

const PRIO_DOT: Record<string, string> = { urgent: "bg-red-500", high: "bg-amber-500", normal: "bg-muted-foreground/40" }

type Cols = Record<string, string[]> // container -> item ids (p:<clientId> | t:<taskId>)

function buildCols(plan: ShootPlan): Cols {
  const cols: Cols = { pool: plan.pool.map((c) => `p:${c.id}`) }
  for (const d of plan.days) cols[d.date] = d.tasks.map((t) => `t:${t.id}`)
  return cols
}

function PoolChip({ id, name }: { id: string; name: string }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  return (
    <div ref={setNodeRef} {...attributes} {...listeners}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("cursor-grab rounded-md border bg-card px-2 py-1 text-xs", isDragging && "opacity-50")}>
      {name}
    </div>
  )
}

function TaskCard({ id, task, onDone, onDelete, disabled }: {
  id: string; task: ShootTask; onDone: () => void; onDelete: () => void; disabled?: boolean
}) {
  const { t } = useI18n()
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id, disabled })
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("rounded-md border bg-card p-2 text-xs", isDragging && "opacity-50",
        disabled && "animate-pulse opacity-70", task.status === "completed" && "opacity-60")}>
      <div className="flex items-center gap-1.5">
        <span className={cn("h-2 w-2 shrink-0 rounded-full", PRIO_DOT[task.priority] || PRIO_DOT.normal)} />
        <span className="flex-1 cursor-grab font-medium" {...attributes} {...listeners}>
          {task.client_name || task.title || t("pages.videographerBoard.adHocLabel")}
        </span>
      </div>
      {(task.start_time || task.location_note) && (
        <div className="mt-1 text-[11px] text-muted-foreground">
          {task.start_time}{task.location_note ? ` · ${task.location_note}` : ""}
        </div>
      )}
      <div className="mt-1.5 flex gap-1">
        <Button variant="ghost" size="icon-sm" className="h-6 w-6" onClick={onDone}
          title={task.status === "completed"
            ? t("pages.videographerBoard.undoTitle")
            : t("pages.videographerBoard.completeTitle")}>
          <Check className={cn("h-3.5 w-3.5", task.status === "completed" && "text-emerald-600")} />
        </Button>
        <Button variant="ghost" size="icon-sm" className="h-6 w-6 text-destructive" onClick={onDelete}
          title={t("pages.videographerBoard.deleteTitle")}>
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}

function Column({ id, title, subtitle, children, highlight }: {
  id: string; title: string; subtitle?: string; children: React.ReactNode; highlight?: boolean
}) {
  const { setNodeRef, isOver } = useDroppable({ id })
  return (
    <div ref={setNodeRef}
      className={cn("flex w-44 shrink-0 flex-col rounded-lg border bg-muted/20",
        isOver && "ring-2 ring-primary/50", highlight && "border-primary/40")}>
      <div className="border-b px-2 py-1.5">
        <div className="text-sm font-medium">{title}</div>
        {subtitle && <div className="text-[11px] text-muted-foreground">{subtitle}</div>}
      </div>
      <div className="flex min-h-24 flex-1 flex-col gap-1.5 p-2">{children}</div>
    </div>
  )
}

// Prefer whatever's directly under the cursor (reliable drops onto columns); fall
// back to rect intersection if the cursor lands on empty space. closestCorners kept
// picking the pool item and swallowing the drop when dropping a small chip onto a
// day — hence pointerWithin takes priority.
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args)
  return hits.length ? hits : rectIntersection(args)
}

function ShootPlanBoard() {
  const { t } = useI18n()
  const dayNames = [
    t("pages.videographerBoard.day.mon"), t("pages.videographerBoard.day.tue"),
    t("pages.videographerBoard.day.wed"), t("pages.videographerBoard.day.thu"),
    t("pages.videographerBoard.day.fri"), t("pages.videographerBoard.day.sat"),
    t("pages.videographerBoard.day.sun"),
  ]
  const [params, setParams] = useSearchParams()
  const weekIso = params.get("week") || currentWeekIso()
  const { data, isLoading, isError } = useShootPlan(weekIso)
  const m = useShootMutations(weekIso)
  const [cols, setCols] = useState<Cols>({})
  const [activeId, setActiveId] = useState<string | null>(null)
  // Optimistic temporary cards (so they show before create returns): colId -> task
  const [temp, setTemp] = useState<Record<string, ShootTask>>({})
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  useEffect(() => { if (data) { setCols(buildCols(data)); setTemp({}) } }, [data])

  const poolNames = useMemo(() => {
    const map: Record<number, string> = {}
    for (const c of data?.pool ?? []) map[c.id] = c.name
    return map
  }, [data])
  // Task keyed by card id (t:<id> | t:tmp-...): real + temporary
  const itemTasks = useMemo(() => {
    const map: Record<string, ShootTask> = {}
    for (const d of data?.days ?? []) for (const t of d.tasks) map[`t:${t.id}`] = t
    return { ...map, ...temp }
  }, [data, temp])

  function setWeek(w: string) {
    setParams((p) => { p.set("week", w); return p }, { replace: true })
  }

  function findContainer(itemId: string): string | undefined {
    if (itemId in cols) return itemId
    return Object.keys(cols).find((k) => cols[k].includes(itemId))
  }

  function onDragEnd(e: DragEndEvent) {
    setActiveId(null)
    const { active, over } = e
    if (!over) return
    const activeId = String(active.id)
    const overId = String(over.id)
    const from = findContainer(activeId)
    const to = findContainer(overId)
    if (!from || !to) return

    // pool → day: create (optimistic temporary card)
    if (activeId.startsWith("p:")) {
      if (to === "pool") return
      const clientId = Number(activeId.slice(2))
      const tmpId = `t:tmp-${clientId}-${Date.now()}`
      const tmpTask = { id: -1, client_id: clientId, client_name: poolNames[clientId],
        priority: "normal", status: "planned" } as unknown as ShootTask
      setTemp((t) => ({ ...t, [tmpId]: tmpTask }))
      setCols((c) => ({ ...c, [to]: [...(c[to] ?? []), tmpId] }))
      m.create.mutate({ client_id: clientId, scheduled_date: to },
        { onError: () => {
            toast.error(t("pages.videographerBoard.createFailed"))
            setCols((c) => ({ ...c, [to]: (c[to] ?? []).filter((i) => i !== tmpId) }))
            setTemp((prev) => { const n = { ...prev }; delete n[tmpId]; return n })
          } })
      return
    }
    // task
    const taskId = Number(activeId.slice(2))
    if (Number.isNaN(taskId)) return  // temporary card, not yet persisted
    if (from === to) {
      const oldI = cols[from].indexOf(activeId)
      let newI = cols[to].indexOf(overId)
      if (newI < 0) newI = cols[to].length - 1
      if (oldI === newI) return
      const next = arrayMove(cols[from], oldI, newI)
      setCols({ ...cols, [from]: next })
      m.reorder.mutate(next.filter((i) => !i.includes("tmp")).map((i) => Number(i.slice(2))))
    } else {
      if (to === "pool") return
      setCols({ ...cols, [from]: cols[from].filter((i) => i !== activeId), [to]: [...cols[to], activeId] })
      m.reschedule.mutate({ id: taskId, date: to },
        { onError: () => toast.error(t("pages.videographerBoard.rescheduleFailed")) })
    }
  }

  if (isError) return <p className="text-destructive">{t("pages.videographerBoard.loadError")}</p>

  const dayCols = data?.days.map((d) => d.date) ?? []
  const today = localDateStr()
  const activeTask = activeId ? itemTasks[activeId] : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{t("pages.videographerBoard.shootPlanHint")}</p>
        <WeekNav weekIso={weekIso} setWeek={setWeek} />
      </div>

      {isLoading ? (
        <div className="flex gap-3">{[...Array(6)].map((_, i) => <Skeleton key={i} className="h-64 w-44" />)}</div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={collision}
          onDragStart={(e: DragStartEvent) => setActiveId(String(e.active.id))}
          onDragCancel={() => setActiveId(null)} onDragEnd={onDragEnd}>
          <div className="flex gap-3 overflow-x-auto pb-4">
            <Column id="pool" title={t("pages.videographerBoard.poolTitle")} subtitle={t("pages.videographerBoard.poolSubtitle")}>
              <SortableContext items={cols.pool ?? []} strategy={verticalListSortingStrategy}>
                {(cols.pool ?? []).map((id) => (
                  <PoolChip key={id} id={id} name={poolNames[Number(id.slice(2))] || "?"} />
                ))}
              </SortableContext>
            </Column>

            {dayCols.map((date, i) => (
              <Column key={date} id={date} title={dayNames[i]}
                subtitle={date.slice(5)} highlight={date === today}>
                <SortableContext items={cols[date] ?? []} strategy={verticalListSortingStrategy}>
                  {(cols[date] ?? []).map((id) => {
                    const task = itemTasks[id]
                    if (!task) return null
                    const tid = Number(id.slice(2))
                    const isTmp = Number.isNaN(tid)
                    return (
                      <TaskCard key={id} id={id} task={task} disabled={isTmp}
                        onDone={() => !isTmp && m.done.mutate(tid)}
                        onDelete={() => !isTmp && m.remove.mutate(tid)} />
                    )
                  })}
                </SortableContext>
              </Column>
            ))}
          </div>
          <DragOverlay>
            {activeId?.startsWith("p:") ? (
              <div className="rounded-md border bg-card px-2 py-1 text-xs shadow-lg">
                {poolNames[Number(activeId.slice(2))] || "?"}
              </div>
            ) : activeTask ? (
              <div className="rounded-md border bg-card p-2 text-xs shadow-lg">
                {activeTask.client_name || activeTask.title || t("pages.videographerBoard.adHocLabel")}
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      )}
    </div>
  )
}

// Navigation between weeks — shared by the Shoot Plan + Businesses tabs.
function WeekNav({ weekIso, setWeek }: { weekIso: string; setWeek: (w: string) => void }) {
  const { t, lang } = useI18n()
  return (
    <div className="flex items-center gap-1 rounded-lg border p-1">
      <Button variant="ghost" size="icon" onClick={() => setWeek(shiftWeek(weekIso, -1))}>
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <div className="min-w-[9rem] text-center">
        <div className="text-sm font-medium">{weekIso}</div>
        <div className="text-xs text-muted-foreground">{weekRangeLabel(weekIso, lang)}</div>
      </div>
      <Button variant="ghost" size="icon" onClick={() => setWeek(shiftWeek(weekIso, 1))}>
        <ChevronRight className="h-4 w-4" />
      </Button>
      <Button variant="outline" size="sm" className="ml-1" onClick={() => setWeek(currentWeekIso())}>
        {t("pages.videographerBoard.todayBtn")}
      </Button>
    </div>
  )
}

// Businesses — which clients have a video shoot this week? Marked by the videographer.
function BusinessesTab() {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const weekIso = params.get("week") || currentWeekIso()
  const setWeek = (w: string) => setParams((p) => { p.set("week", w); return p }, { replace: true })
  const { data, isLoading, isError } = useBusinesses(weekIso)
  const mark = useMarkBusiness(weekIso)
  const [q, setQ] = useState("")

  const rows = useMemo(() => {
    const list = data ?? []
    const nq = trFold(q.trim())
    return nq ? list.filter((b) => trFold(b.client_name).includes(nq)) : list
  }, [data, q])

  if (isError) return <p className="text-destructive">{t("pages.videographerBoard.businessesLoadError")}</p>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Input placeholder={t("pages.videographerBoard.businessSearchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)}
          className="max-w-xs" />
        <WeekNav weekIso={weekIso} setWeek={setWeek} />
      </div>

      {isLoading ? (
        <div className="space-y-2">{[...Array(6)].map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground">{t("pages.videographerBoard.noBusinesses")}</p>
      ) : (
        <div className="divide-y rounded-lg border">
          {rows.map((b) => (
            <div key={b.client_id} className="flex items-center justify-between gap-3 px-3 py-2.5">
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-medium">{b.client_name}</span>
                <LogoButton clientId={b.client_id} />
              </div>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <span>{t("pages.videographerBoard.hasVideoLabel")}</span>
                <Switch checked={b.has_video}
                  disabled={mark.isPending}
                  onCheckedChange={(v) =>
                    mark.mutate({ client_id: b.client_id, has_video: v },
                      { onError: () => toast.error(t("pages.videographerBoard.markFailed")) })} />
              </label>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function VideographerBoardPage() {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") || "shoot"
  const setTab = (v: string) => setParams((p) => { p.set("tab", v); return p }, { replace: true })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("pages.videographerBoard.title")}</h1>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="shoot">{t("pages.videographerBoard.shootPlanTab")}</TabsTrigger>
          <TabsTrigger value="businesses">{t("pages.videographerBoard.businessesTab")}</TabsTrigger>
        </TabsList>
        <TabsContent value="shoot" className="mt-4"><ShootPlanBoard /></TabsContent>
        <TabsContent value="businesses" className="mt-4"><BusinessesTab /></TabsContent>
      </Tabs>
    </div>
  )
}
