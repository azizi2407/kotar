// Planlama Panosu — kişi eksenli, React Flow tuvali (2026-07-26 geçişi).
//
// Panolar: 'management' (yönetim ortak) + 'user:<sub>' (kişi başına ortak alan).
// Rol kapısı BACKEND'de: API tek pano döndürürse seçici hiç render edilmez.
//
// Üç görünüm aynı veriyi okur: Tuval (React Flow) · Liste · Takvim.
// Yazma her üçünde de tek yoldan gider: `usePlanningStore` → delta PATCH.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { ReactFlowProvider } from "@xyflow/react"
import { toast } from "sonner"
import {
  CalendarDays, LayoutDashboard, List, Plus, RotateCcw, RotateCw, Search, StickyNote, SquareDashed,
} from "lucide-react"

import { AssignedStrip, CalendarView, ListView } from "@/components/planlama/BoardViews"
import { CardDetailDialog } from "@/components/planlama/CardDetailDialog"
import { PlanningFlow, type PlanningFlowHandle } from "@/components/planlama/PlanningFlow"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/lib/auth"
import {
  COLORS, DEFAULT_SIZE, ITEM_STATUS_LABELS, MANAGEMENT_KEY, newItemKey,
  useAssigned, useBoard, useBoards, useBoardVersion,
  type PlanningItem, type PlanningItemType,
} from "@/lib/planlama"
import { chunkForPatch, TEMPLATES } from "@/lib/planlama-templates"
import { usePlanningStore, type ItemPatch } from "@/lib/usePlanningStore"
import { trFold } from "@/lib/week"
import { cn } from "@/lib/utils"

type ViewMode = "canvas" | "list" | "calendar"

export function PlanlamaPage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const boardsQ = useBoards()
  const boards = useMemo(() => boardsQ.data ?? [], [boardsQ.data])

  const wanted = params.get("pano")
  const boardKey = useMemo(() => {
    if (wanted && boards.some((b) => b.key === wanted)) return wanted
    return boards[0]?.key ?? MANAGEMENT_KEY
  }, [wanted, boards])

  const boardQ = useBoard(boardKey)
  const serverItems = useMemo(() => boardQ.data?.items ?? [], [boardQ.data])
  const serverVersion = boardQ.data?.board.version ?? 0
  const store = usePlanningStore(boardKey, serverItems, serverVersion)

  const [view, setView] = useState<ViewMode>("canvas")
  const [detail, setDetail] = useState<string | null>(null)
  const [q, setQ] = useState("")
  const [fAssignee, setFAssignee] = useState("")
  const [fStatus, setFStatus] = useState("")
  const viewportRef = useRef<HTMLDivElement | null>(null)
  // Tuvale emir kanalı: öğe ekleme viewport dönüşümünü bildiği yerde yapılmalı.
  const flowRef = useRef<PlanningFlowHandle | null>(null)

  const assignedQ = useAssigned(null, !!user)

  // --- uzak sürüm takibi ----------------------------------------------------
  const [seenVersion, setSeenVersion] = useState(0)
  const versionQ = useBoardVersion(boardKey, !!boardQ.data)
  useEffect(() => { if (serverVersion) setSeenVersion(serverVersion) }, [serverVersion])

  const remoteVersion = versionQ.data?.version ?? 0
  const remoteAhead = remoteVersion > seenVersion

  // `store.interacting` / `store.status` deps'TE: eskiden yoktu ve effect
  // etkileşim yüzünden erken dönünce bir daha hiç çalışmıyordu → pano bayat kalıyordu.
  useEffect(() => {
    if (!remoteAhead) return
    if (store.interacting || store.hasPending() || store.status === "saving") return
    void boardQ.refetch()
    setSeenVersion(remoteVersion)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remoteAhead, remoteVersion, store.interacting, store.status])

  useEffect(() => {
    if (store.conflicts.length) {
      toast.warning(`${store.conflicts.length} kartı başkası da düzenledi — son yazan geçerli.`)
      store.clearConflicts()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.conflicts])

  useEffect(() => {
    if (store.status === "error") toast.error("Kaydedilemedi — bağlantı koptu, yeniden denenecek.")
  }, [store.status])

  useEffect(() => {
    store.resetHistory()
    setDetail(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boardKey])

  useEffect(() => {
    const onHide = () => { if (document.hidden) void store.flush() }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.flush])

  // Ctrl+Z / Ctrl+Shift+Z. `contenteditable` ve `<select>` de dışlanır —
  // eski kontrol yalnız INPUT/TEXTAREA bakıyordu ve select odaktayken pano undo'su yapıyordu.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = document.activeElement as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return
      e.preventDefault()
      const ok = e.shiftKey ? store.redo() : store.undo()
      if (!ok) toast.info(e.shiftKey ? "İleri alınacak bir şey yok." : "Geri alınacak bir şey yok.")
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [store])

  // --- filtre ---------------------------------------------------------------

  const filterActive = !!(q.trim() || fAssignee || fStatus)
  const matches = useCallback((it: PlanningItem) => {
    if (fStatus && it.status !== fStatus) return false
    if (fAssignee && it.assignee_sub !== fAssignee) return false
    if (q.trim()) {
      const n = trFold(q)
      const hay = trFold([it.title, it.text, it.label, it.client_name,
                          it.shoot_title, it.campaign_title].filter(Boolean).join(" "))
      if (!hay.includes(n)) return false
    }
    return true
  }, [q, fAssignee, fStatus])

  const visible = useMemo(
    () => (filterActive ? store.items.filter(matches) : store.items),
    [store.items, filterActive, matches])

  const assignees = useMemo(() => {
    const m = new Map<string, string>()
    for (const it of store.items) {
      if (it.assignee_sub && it.assignee_name) m.set(it.assignee_sub, it.assignee_name)
    }
    return [...m.entries()]
  }, [store.items])

  // --- ekleme ---------------------------------------------------------------

  const onPatch = useCallback((key: string, patch: Partial<PlanningItem>) => {
    store.commit([{ item_key: key, ...patch }])
  }, [store])

  /** Öğe ekleme.
   *
   *  ESKİ DAVRANIŞ (hata, 2026-08-09'da düzeltildi): öğe `maxX + 260`'a, yani en
   *  sağdaki öğenin sağına konuyordu; kamera oynamadığı için `fitView` yapılmış bir
   *  panoda düğme HİÇBİR ŞEY YAPMAMIŞ gibi görünüyordu. Viewport dönüşümü yalnız
   *  `ReactFlowProvider`ın İÇİNDE bilindiğinden hesap tuvale taşındı; burası artık
   *  yalnız emri iletiyor. Liste/takvim görünümündeyken tuval mount değil, o yüzden
   *  orada eski "en sağa ekle" davranışı korunuyor (kamera kavramı yok). */
  function addItem(type: PlanningItemType) {
    if (view === "canvas" && flowRef.current) {
      flowRef.current.addItem(type)
      return
    }
    const r = viewportRef.current?.getBoundingClientRect()
    const d = DEFAULT_SIZE[type]
    const maxX = store.items.length ? Math.max(...store.items.map((i) => i.x)) : 0
    const minY = store.items.length ? Math.min(...store.items.map((i) => i.y)) : 0
    const x = store.items.length ? maxX + 260 : Math.round((r?.width ?? 800) / 2 - d.width / 2)
    const y = store.items.length ? minY : Math.round((r?.height ?? 600) / 2 - d.height / 2)
    store.commit([{
      item_key: newItemKey(), type,
      title: type === "region" ? "Bölge" : type === "card" ? "Yeni kart" : null,
      text: type === "note" ? "Not…" : null,
      color: type === "region" ? "#f1f5f9" : COLORS[store.items.length % COLORS.length],
      x, y, width: d.width, height: d.height,
      z: type === "region" ? -1 : 0, status: "open",
    }])
  }

  function applyTemplate(id: string) {
    const t = TEMPLATES.find((x) => x.id === id)
    if (!t) return
    const maxY = store.items.length ? Math.max(...store.items.map((i) => i.y + (i.height || 120))) : 0
    const items = t.build(0, store.items.length ? maxY + 60 : 0)
    // MAX_BATCH=200 — büyük şablon tek PATCH'e sığmazsa parçalanır.
    for (const chunk of chunkForPatch(items)) store.commit(chunk as ItemPatch[])
    toast.success(`${t.name} eklendi (${items.length} öğe).`)
  }

  const detailItem = detail ? store.byKey.get(detail) ?? null : null
  const boardTitle = boardQ.data?.board.title ?? "Pano"

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-3">
      {/* Başlık + pano seçici */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Planlama Panosu</h1>
            <p className="text-sm text-muted-foreground">
              {boardTitle} · {store.items.filter((i) => i.type !== "edge").length} öğe
            </p>
          </div>
          {boards.length > 1 && (
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={boardKey}
              onChange={(e) => setParams((prev) => {
                prev.set("pano", e.target.value); return prev
              }, { replace: true })}>
              {boards.map((b) => (
                <option key={b.key} value={b.key}>{b.title} ({b.item_count})</option>
              ))}
            </select>
          )}
        </div>

        <div className="flex items-center gap-1 rounded-lg border p-1">
          <ViewBtn active={view === "canvas"} onClick={() => setView("canvas")} icon={<LayoutDashboard className="h-4 w-4" />}>Tuval</ViewBtn>
          <ViewBtn active={view === "list"} onClick={() => setView("list")} icon={<List className="h-4 w-4" />}>Liste</ViewBtn>
          <ViewBtn active={view === "calendar"} onClick={() => setView("calendar")} icon={<CalendarDays className="h-4 w-4" />}>Takvim</ViewBtn>
        </div>
      </div>

      {/* Araç çubuğu */}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => addItem("card")}>
          <Plus className="mr-1 h-4 w-4" /> Kart
        </Button>
        <Button size="sm" variant="ghost" onClick={() => addItem("note")}>
          <StickyNote className="mr-1 h-4 w-4" /> Not
        </Button>
        <Button size="sm" variant="ghost" onClick={() => addItem("region")}>
          <SquareDashed className="mr-1 h-4 w-4" /> Bölge
        </Button>
        <select className="h-8 rounded-md border bg-background px-2 text-sm" value=""
          onChange={(e) => { if (e.target.value) { applyTemplate(e.target.value); e.target.value = "" } }}>
          <option value="">✨ Şablon…</option>
          {TEMPLATES.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>

        <div className="mx-1 h-6 w-px bg-border" />
        <Button size="sm" variant="ghost" title="Geri al (Ctrl+Z)" disabled={!store.canUndo}
          onClick={() => store.undo()}>
          <RotateCcw className="h-4 w-4" />
        </Button>
        <Button size="sm" variant="ghost" title="İleri al (Ctrl+Shift+Z)" disabled={!store.canRedo}
          onClick={() => store.redo()}>
          <RotateCw className="h-4 w-4" />
        </Button>

        <div className="mx-1 h-6 w-px bg-border" />
        <div className="relative min-w-[10rem] flex-1 sm:max-w-xs">
          <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="h-8 pl-8" placeholder="Kartlarda ara…" value={q}
            onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="h-8 rounded-md border bg-background px-2 text-sm"
          value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
          <option value="">Tüm durumlar</option>
          {Object.entries(ITEM_STATUS_LABELS).map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
        {assignees.length > 0 && (
          <select className="h-8 rounded-md border bg-background px-2 text-sm"
            value={fAssignee} onChange={(e) => setFAssignee(e.target.value)}>
            <option value="">Tüm sorumlular</option>
            {assignees.map(([sub, name]) => <option key={sub} value={sub}>{name}</option>)}
          </select>
        )}
        {filterActive && (
          <Button size="sm" variant="ghost"
            onClick={() => { setQ(""); setFAssignee(""); setFStatus("") }}>Temizle</Button>
        )}

        <span className="ml-auto text-xs text-muted-foreground">
          {store.status === "saving" ? "Kaydediliyor…"
            : store.status === "error" ? "Bağlantı yok — yeniden denenecek"
            : filterActive ? `${visible.filter((i) => i.type !== "edge").length} eşleşme` : ""}
        </span>
      </div>

      {remoteAhead && (store.interacting || store.hasPending()) && (
        <div className="flex items-center justify-between gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          Başkası bu panoyu güncelledi. Değişikliğin kaydedilince tazelenecek.
          <Button size="sm" variant="outline" className="h-6"
            onClick={() => { void boardQ.refetch(); setSeenVersion(remoteVersion) }}>Şimdi yenile</Button>
        </div>
      )}

      {assignedQ.data && (
        <AssignedStrip items={assignedQ.data.items} currentBoard={boardKey} />
      )}

      {boardQ.isLoading && <Skeleton className="flex-1" />}
      {boardQ.isError && <p className="text-destructive">Pano yüklenemedi.</p>}

      {boardQ.data && view === "canvas" && (
        <div ref={viewportRef} className="min-h-0 flex-1 overflow-hidden rounded-lg border">
          {/* Provider tuvalin DIŞINDA: useReactFlow yalnız içeride çalışır. */}
          <ReactFlowProvider>
            <PlanningFlow
              ref={flowRef}
              boardKey={boardKey}
              items={store.items}
              byKey={store.byKey}
              matches={matches}
              filterActive={filterActive}
              applyLocal={store.applyLocal}
              commit={store.commit}
              remove={store.remove}
              onPatch={onPatch}
              onOpenDetail={(it) => setDetail(it.item_key)}
            />
          </ReactFlowProvider>
        </div>
      )}

      {boardQ.data && view === "list" && (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ListView items={visible} onOpen={(it) => setDetail(it.item_key)} />
        </div>
      )}

      {boardQ.data && view === "calendar" && (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <CalendarView items={visible} onOpen={(it) => setDetail(it.item_key)} />
        </div>
      )}

      {detailItem && (
        <CardDetailDialog item={detailItem}
          onSave={(patch) => onPatch(detailItem.item_key, patch)}
          onClose={() => setDetail(null)} />
      )}
    </div>
  )
}

function ViewBtn({ active, onClick, icon, children }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode
}) {
  return (
    <button type="button" onClick={onClick}
      className={cn("flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors",
                    active ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>
      {icon}{children}
    </button>
  )
}
