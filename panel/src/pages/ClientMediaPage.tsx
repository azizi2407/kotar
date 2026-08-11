// Client media page (2026-08-04) — /designer/musteri/:id.
//
// Opens when clicking a client's name on the design board. It does two things: show
// the client's summary info in one place, and — the main reason — display ALL media
// UPLOADED for the client week by week and let it be MANUALLY moved between weeks.
//
// Moving is done two ways: drag-and-drop a card onto a different week block
// (@dnd-kit), or multi-select and pick a target week from the strip at the top. The
// latter is the fallback for touch/keyboard — dragging isn't reliable in every environment.
//
// The role gate comes from the ROUTE: the `/designer/...` path prefix-matches the
// `/designer` entry in AppLayout's nav table → management + designer. So the page
// doesn't do its own role check (the backend endpoints enforce it separately).
import { useMemo, useRef, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import {
  DndContext, DragOverlay, PointerSensor, pointerWithin, rectIntersection,
  useDraggable, useDroppable, useSensor, useSensors,
  type CollisionDetection, type DragEndEvent,
} from "@dnd-kit/core"
import {
  ArrowLeft, BookOpen, Camera, Check, ChevronDown, Download, ExternalLink, FileText,
  Maximize2, MoveRight, Play, Upload as UploadIcon,
} from "lucide-react"
import { toast } from "sonner"

import { ROLE_SLOTS, useClient, useUsers } from "@/lib/clients"
import {
  downloadClientAsset, downloadMediaUrl, drivePreviewUrl, mediaUrl, thumbnailUrl,
  useClientAssets, useClientMedia, useMoveUploadsToWeek, useVgPhotos,
  type ClientMediaWeek, type Upload,
} from "@/lib/sharing"
import { currentWeekIso, weekRangeLabel } from "@/lib/week"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import { BriefDialog } from "@/components/sharing/BriefDialog"
import { ClientFonts } from "@/components/fonts/ClientFonts"
import { ClientDesignFiles } from "@/components/design-files/ClientDesignFiles"
import { LogoButton } from "@/components/sharing/LogoButton"
import { UploadModal } from "@/components/sharing/UploadModal"

function categoryLabels(t: (key: string) => string): Record<string, string> {
  return {
    post: t("pages.clientMedia.category.post"),
    story: t("pages.clientMedia.category.story"),
    video: t("pages.clientMedia.category.video"),
    linkedin: t("pages.clientMedia.category.linkedin"),
  }
}

function isVideo(up: Upload) {
  return up.category === "video" || Boolean(up.mime_type?.startsWith("video/"))
}

// Pick whatever's under the pointer first; fall back to rect intersection if nothing
// hits. (Same rationale as the VideographerBoardPage Kanban: closestCorners would pick
// the neighboring card when dropping a small card onto a block, swallowing the drop.)
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args)
  return hits.length ? hits : rectIntersection(args)
}

// --- media card ---

function MediaCard({ up, selected, onToggle, onOpen }: {
  up: Upload
  selected: boolean
  onToggle: (e: React.MouseEvent) => void
  onOpen: () => void
}) {
  const { t } = useI18n()
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: up.id })
  const video = isVideo(up)
  const labels = categoryLabels(t)

  return (
    <div ref={setNodeRef} {...attributes} {...listeners}
      onClick={onToggle}
      title={up.file_name ?? ""}
      className={cn(
        "group relative cursor-grab overflow-hidden rounded-lg border bg-card transition",
        selected ? "ring-2 ring-primary" : "hover:border-primary/50",
        isDragging && "opacity-40")}>

      <div className={cn("flex items-center justify-between px-2 py-1 text-[11px] font-semibold",
        up.used ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
          : "bg-muted text-muted-foreground")}>
        <span>{labels[up.category ?? ""] ?? up.category ?? "—"}</span>
        {up.used && <span title={t("pages.clientMedia.mediaCard.usedTitle")}>✓</span>}
      </div>

      <div className="relative">
        {up.file_id ? (
          // `w=800`: the card now flexes with screen width (~340 px on wide
          // screens) — a 240 px thumbnail looked blurry.
          <img src={thumbnailUrl(up.file_id, 800)} alt={up.file_name ?? ""} loading="lazy"
            className="aspect-[4/5] w-full bg-muted object-contain"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }} />
        ) : (
          <div className="flex aspect-[4/5] w-full items-center justify-center text-xs text-muted-foreground">
            {t("pages.clientMedia.mediaCard.noImage")}
          </div>
        )}
        {video && (
          <span className="pointer-events-none absolute left-2 top-2 rounded-full bg-black/60 p-1.5">
            <Play className="h-4 w-4 fill-white text-white" />
          </span>
        )}
        {selected && (
          <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <Check className="h-4 w-4" />
          </span>
        )}
        {/* Enlarge is a SEPARATE button: clicking the card selects it, so the lightbox
            got its own trigger to avoid clashing with double-tap. */}
        <button type="button" title={t("pages.clientMedia.mediaCard.enlarge")}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onOpen() }}
          className="absolute bottom-2 right-2 rounded bg-background/80 p-1.5 text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:text-foreground">
          <Maximize2 className="h-4 w-4" />
        </button>
      </div>

      <div className="truncate px-2 py-1 text-xs text-muted-foreground">
        {up.file_name ?? "—"}
      </div>
      {up.moved_from_week_iso && (
        <div className="truncate bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-400"
          title={t("pages.clientMedia.mediaCard.movedFromTitle", { week: up.moved_from_week_iso })}>
          ↪ {up.moved_from_week_iso}
        </div>
      )}
    </div>
  )
}

// --- week block (drop target) ---

function WeekBlock({ week, selected, onToggle, onOpen, onUpload, isCurrent }: {
  week: ClientMediaWeek
  selected: Set<number>
  onToggle: (up: Upload, e: React.MouseEvent) => void
  onOpen: (up: Upload) => void
  onUpload: (weekIso: string) => void
  isCurrent: boolean
}) {
  const { t } = useI18n()
  const { setNodeRef, isOver } = useDroppable({ id: `week:${week.week_iso}` })

  return (
    <section ref={setNodeRef}
      className={cn("rounded-lg border transition",
        isOver && "border-primary ring-2 ring-primary/40",
        isCurrent && !isOver && "border-primary/40")}>
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        <span className="font-medium">{week.week_iso}</span>
        <span className="text-xs text-muted-foreground">{weekRangeLabel(week.week_iso)}</span>
        {isCurrent && <Badge className="bg-primary text-primary-foreground">{t("pages.clientMedia.weekBlock.currentWeek")}</Badge>}
        <Badge variant="outline" className="text-muted-foreground">
          {t("pages.clientMedia.weekBlock.fileCount", { count: week.uploads.length })}
        </Badge>
        <Button variant="ghost" size="sm" className="ml-auto"
          onClick={() => onUpload(week.week_iso)}>
          <UploadIcon className="mr-1 h-3.5 w-3.5" /> {t("pages.clientMedia.weekBlock.upload")}
        </Button>
      </div>

      {/* Flexible grid instead of fixed-width cards: on wide screens the card grows to
          ~340 px so the designer can evaluate the image without opening it separately. */}
      <div className="p-3">
        {week.uploads.length === 0 ? (
          <p className={cn("py-3 text-sm", isOver ? "text-primary" : "text-muted-foreground")}>
            {isOver ? t("pages.clientMedia.weekBlock.dropHere") : t("pages.clientMedia.weekBlock.empty")}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {week.uploads.map((up) => (
              <MediaCard key={up.id} up={up} selected={selected.has(up.id)}
                onToggle={(e) => onToggle(up, e)} onOpen={() => onOpen(up)} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

// --- shoot photos (read-only; no week concept → can't be moved) ---

function ShootPhotosBlock({ clientId }: { clientId: number }) {
  const { t } = useI18n()
  const { data, isLoading, isError } = useVgPhotos(clientId)
  const list = useMemo(
    () => (data ?? []).slice().sort((a, b) => (b.shoot_date ?? "").localeCompare(a.shoot_date ?? "")),
    [data])

  if (isLoading) return <Skeleton className="h-28 w-full" />
  if (isError) return <p className="text-sm text-destructive">{t("pages.clientMedia.shootPhotos.loadError")}</p>
  if (!list.length) return <p className="text-sm text-muted-foreground">{t("pages.clientMedia.shootPhotos.empty")}</p>

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {t("pages.clientMedia.shootPhotos.hint")}
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {list.map((p) => (
          <div key={p.id} className="overflow-hidden rounded-lg border bg-card"
            title={`${p.file_name ?? ""}${p.shoot_date ? ` · ${p.shoot_date}` : ""}`}>
            {p.file_id ? (
              <img src={thumbnailUrl(p.file_id, 800)} alt={p.file_name ?? ""} loading="lazy"
                className="aspect-[4/5] w-full bg-muted object-cover" />
            ) : (
              <div className="flex aspect-[4/5] w-full items-center justify-center text-xs text-muted-foreground">
                {t("pages.clientMedia.mediaCard.noImage")}
              </div>
            )}
            <div className="truncate px-2 py-1 text-xs text-muted-foreground">
              {p.shoot_date ?? p.file_name ?? "—"}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// --- logo preview ---

// Logo in the header (2026-08-04). `LogoButton` DOWNLOADS the logo, doesn't display
// it — this is a small preview so the designer can also visually confirm they're on
// the right client. The image comes via `thumbnailUrl`: some assets are PDF/SVG logos,
// and Drive generates thumbnails for those too (the asset download endpoint is
// `as_attachment`, not suitable for an <img>). For clients with an empty logo slot
// (e.g. Çimsan–Tohum Gübre: two brands, logo left blank on purpose), it falls back to
// the first standard image; if there are no assets at all, nothing is rendered.
function ClientLogo({ clientId }: { clientId: number }) {
  const { t } = useI18n()
  const { data: assets } = useClientAssets(clientId)
  const logo = assets?.find((a) => a.kind === "logo") ?? assets?.[0]
  if (!logo) return null
  return (
    <button type="button" onClick={() => downloadClientAsset(clientId, logo.id)}
      title={logo.file_name ?? t("pages.clientMedia.clientLogo.downloadTitle")}
      className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-background p-1 transition-colors hover:border-primary/50">
      <img src={thumbnailUrl(logo.file_id, 200)} alt={t("pages.clientMedia.clientLogo.alt")}
        className="max-h-full max-w-full object-contain"
        onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none" }} />
    </button>
  )
}

// --- client summary ---

function ClientSummary({ clientId }: { clientId: number }) {
  const { t } = useI18n()
  const { data: c } = useClient(clientId)
  const { data: users } = useUsers()
  const { isManagement } = useAuth()
  if (!c) return <Skeleton className="h-20 w-full" />

  const nameOf = (sub: string) => users?.find((u) => u.sub === sub)?.name ?? sub
  const team = ROLE_SLOTS
    .map((s) => ({ label: t(s.labelKey), sub: c.team_assignments?.[s.key] }))
    .filter((entry) => entry.sub)

  return (
    <div className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <div className="text-xs text-muted-foreground">{t("pages.clientMedia.summary.sector")}</div>
        <div className="text-sm">{c.sector || "—"}</div>
      </div>
      <div>
        <div className="text-xs text-muted-foreground">{t("pages.clientMedia.summary.brief")}</div>
        <div className="text-sm">{c.brief_enabled ? t("pages.clientMedia.summary.briefOn") : t("pages.clientMedia.summary.briefOff")}</div>
      </div>
      <div>
        <div className="text-xs text-muted-foreground">{t("pages.clientMedia.summary.team")}</div>
        <div className="space-y-0.5 text-sm">
          {team.length === 0 ? "—" : team.map((entry) => (
            <div key={entry.label} className="truncate">
              <span className="text-muted-foreground">{entry.label}:</span> {nameOf(entry.sub!)}
            </div>
          ))}
        </div>
      </div>
      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">{t("pages.clientMedia.summary.links")}</div>
        <div className="flex flex-wrap gap-2 text-sm">
          {/* Brand Guide is open to EVERY production role (nav: management/designer/
              content_creator/videographer) — so the designer and videographer can reach
              brand voice, prohibitions, and content rules with one click from here.
              MarkaRehberiPage keeps the selection in the URL for the `?client=` deep link. */}
          <Link to={`/marka-rehberi?client=${clientId}`}
            className="inline-flex items-center gap-1 text-primary hover:underline">
            <BookOpen className="h-3.5 w-3.5" /> {t("pages.clientMedia.summary.brandGuide")}
          </Link>
          {c.instagram_url && (
            <a href={c.instagram_url} target="_blank" rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> {t("pages.clientMedia.summary.instagram")}
            </a>
          )}
          {c.google_drive_url && (
            <a href={c.google_drive_url} target="_blank" rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> {t("pages.clientMedia.summary.drive")}
            </a>
          )}
          {/* Full detail is management-only: contract/contact fields already go
              through the role filter on the backend (api._client_json). */}
          {isManagement && (
            <Link to={`/clients/${clientId}`}
              className="inline-flex items-center gap-1 text-primary hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> {t("pages.clientMedia.summary.clientDetail")}
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}

// --- lightbox ---

function MediaLightbox({ up, onClose }: { up: Upload | null; onClose: () => void }) {
  const { t } = useI18n()
  if (!up?.file_id) return null
  const video = isVideo(up)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="truncate">{up.file_name ?? t("pages.clientMedia.lightbox.previewFallback")}</DialogTitle>
        </DialogHeader>
        {video ? (
          up.local ? (
            <video controls src={mediaUrl(up.file_id)} className="max-h-[70vh] w-full rounded" />
          ) : (
            // The local copy window (21 days) has expired → fall back to Drive's embedded player.
            <iframe src={drivePreviewUrl(up.file_id)} allow="autoplay"
              className="h-[60vh] w-full rounded border" />
          )
        ) : (
          <img src={mediaUrl(up.file_id)} alt={up.file_name ?? ""}
            className="max-h-[70vh] w-full rounded object-contain"
            onError={(e) => {
              // No local original → fall back to a large thumbnail (Drive thumbnail).
              (e.currentTarget as HTMLImageElement).src = thumbnailUrl(up.file_id!, 1200)
            }} />
        )}
        <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
          <span>{up.week_iso}{up.moved_from_week_iso ? ` · ↪ ${up.moved_from_week_iso}` : ""}</span>
          <a href={downloadMediaUrl(up.file_id, up.file_name ?? undefined)}
            className="inline-flex items-center gap-1 text-primary hover:underline">
            <Download className="h-4 w-4" /> {t("pages.clientMedia.lightbox.downloadFull")}
          </a>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// --- page ---

export function ClientMediaPage() {
  const { t } = useI18n()
  const { id } = useParams()
  const clientId = Number(id)
  const [params] = useSearchParams()
  // The week we came from (the board's week axis) — preserved in the back link.
  const fromWeek = params.get("week") || currentWeekIso()

  const { data: client } = useClient(Number.isFinite(clientId) ? clientId : null)
  const { data: weeks, isLoading, isError } = useClientMedia(
    Number.isFinite(clientId) ? clientId : null)
  const move = useMoveUploadsToWeek(clientId)

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [activeId, setActiveId] = useState<number | null>(null)
  const [lightbox, setLightbox] = useState<Upload | null>(null)
  const [uploadWeek, setUploadWeek] = useState<string | null>(null)
  const [briefOpen, setBriefOpen] = useState(false)
  const [photosOpen, setPhotosOpen] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(true)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))
  const suppressClick = useRef(false)

  const byId = useMemo(() => {
    const map = new Map<number, Upload>()
    for (const w of weeks ?? []) for (const u of w.uploads) map.set(u.id, u)
    return map
  }, [weeks])

  const currentWeek = currentWeekIso()
  const clientName = client?.name ?? t("pages.clientMedia.clientFallback")
  const totalFiles = useMemo(
    () => (weeks ?? []).reduce((s, w) => s + w.uploads.length, 0), [weeks])

  function toggle(up: Upload, e: React.MouseEvent) {
    // After a drag ends, the browser can also fire a click on the same card (with a
    // short-distance drag the pointer never leaves the card) — that click would flip the
    // selection, creating the surprise "I dragged, and the selection changed". Swallow that click.
    if (suppressClick.current) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (e.shiftKey) {
        // Shift: bulk-select the contiguous range within the same week block.
        const week = (weeks ?? []).find((w) => w.week_iso === up.week_iso)
        const list = week?.uploads ?? []
        const anchor = list.findIndex((u) => next.has(u.id))
        const idx = list.findIndex((u) => u.id === up.id)
        if (anchor >= 0 && idx >= 0) {
          const [lo, hi] = anchor < idx ? [anchor, idx] : [idx, anchor]
          for (let i = lo; i <= hi; i++) next.add(list[i].id)
          return next
        }
      }
      if (next.has(up.id)) next.delete(up.id)
      else next.add(up.id)
      return next
    })
  }

  async function moveTo(target: string, ids: number[]) {
    const moving = ids.filter((i) => byId.get(i)?.week_iso !== target)
    if (!moving.length) return
    try {
      const r = await move.mutateAsync({ upload_ids: moving, to_week_iso: target })
      if (r.moved) toast.success(t("pages.clientMedia.toast.moved", { count: r.moved, week: target }))
      // Partial success: items that couldn't be moved are reported separately; moved ones stay in place.
      if (r.errors?.length) toast.error(t("pages.clientMedia.toast.moveErrors", { count: r.errors.length, error: r.errors[0] }))
      setSelected(new Set())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("pages.clientMedia.toast.moveFailed"))
    }
  }

  function endDrag() {
    setActiveId(null)
    suppressClick.current = true
    // The post-drag click arrives in the same tick; release the lock one tick later.
    setTimeout(() => { suppressClick.current = false }, 0)
  }

  function onDragEnd(e: DragEndEvent) {
    // Sourced from `e.active.id`, not the `activeId` state — reading state is tied
    // to the render cycle, not the event payload.
    const dragged = Number(e.active.id)
    endDrag()
    if (!e.over || !Number.isFinite(dragged)) return
    const overId = String(e.over.id)
    if (!overId.startsWith("week:")) return
    // If the dragged card is part of the selection, the WHOLE selection moves; otherwise just that card.
    const ids = selected.has(dragged) ? [...selected] : [dragged]
    moveTo(overId.slice("week:".length), ids)
  }

  const activeUpload = activeId != null ? byId.get(activeId) : undefined
  const dragCount = activeId != null && selected.has(activeId) ? selected.size : 1

  if (!Number.isFinite(clientId)) {
    return <p className="py-8 text-center text-destructive">{t("pages.clientMedia.invalidClient")}</p>
  }

  return (
    <div className="space-y-5">
      {/* title + actions */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <Link to={`/designer?week=${encodeURIComponent(fromWeek)}`}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" /> {t("pages.clientMedia.backToDesign")}
          </Link>
          <div className="flex items-center gap-3">
            <ClientLogo clientId={clientId} />
            <h1 className="text-2xl font-semibold tracking-tight">{clientName}</h1>
          </div>
          <p className="text-muted-foreground">
            {t("pages.clientMedia.fileWeekCount", {
              files: totalFiles, weeks: (weeks ?? []).filter((w) => w.uploads.length).length,
            })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="outline" size="sm" onClick={() => setUploadWeek(fromWeek)}>
            <UploadIcon className="mr-1 h-3.5 w-3.5" /> {t("pages.clientMedia.upload")}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setBriefOpen(true)}>
            <FileText className="mr-1 h-3.5 w-3.5" /> {t("pages.clientMedia.brief")}
          </Button>
          <LogoButton clientId={clientId} />
          <Button variant="ghost" size="sm" onClick={() => setSummaryOpen((o) => !o)}>
            <ChevronDown className={cn("mr-1 h-3.5 w-3.5 transition-transform", !summaryOpen && "-rotate-90")} />
            {t("pages.clientMedia.info")}
          </Button>
        </div>
      </div>

      {summaryOpen && <ClientSummary clientId={clientId} />}

      {/* Related fonts (2026-08-05) — the pool filtered down to this client;
          preview text is the client's name. Independent of the info block, always
          shown expanded: one of the first things a designer looks at before starting work. */}
      <ClientFonts clientId={clientId} clientName={clientName} />

      {/* Working files (2026-08-07) — versioned source files like .psd/.ai. Placed
          right under fonts: both are "material that should be at hand before
          starting production"; sits above the weekly delivery grid. */}
      <ClientDesignFiles clientId={clientId} />

      {/* selection strip — target-week menu instead of dragging (touch/keyboard path) */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-lg border bg-background/95 p-2 shadow-sm backdrop-blur">
          <span className="text-sm font-medium">{t("pages.clientMedia.selectedCount", { count: selected.size })}</span>
          <DropdownMenu>
            <DropdownMenuTrigger
              disabled={move.isPending}
              className="inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted disabled:opacity-50">
              <MoveRight className="mr-1 h-3.5 w-3.5" />
              {move.isPending ? t("pages.clientMedia.moving") : t("pages.clientMedia.moveToWeek")}
              <ChevronDown className="ml-1 h-3.5 w-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="max-h-72 overflow-y-auto">
              {(weeks ?? []).map((w) => (
                <DropdownMenuItem key={w.week_iso}
                  onClick={() => moveTo(w.week_iso, [...selected])}>
                  {w.week_iso} · {weekRangeLabel(w.week_iso)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
            {t("pages.clientMedia.clearSelection")}
          </Button>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {t("pages.clientMedia.dragHint")}
          </span>
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-40 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">{t("pages.clientMedia.loadError")}</p>}

      {weeks && (
        <DndContext sensors={sensors} collisionDetection={collision}
          onDragStart={(e) => setActiveId(Number(e.active.id))}
          onDragCancel={endDrag}
          onDragEnd={onDragEnd}>
          <div className="space-y-3">
            {weeks.map((w) => (
              <WeekBlock key={w.week_iso} week={w} selected={selected}
                onToggle={toggle} onOpen={setLightbox} onUpload={setUploadWeek}
                isCurrent={w.week_iso === currentWeek} />
            ))}
          </div>

          <DragOverlay>
            {activeUpload && (
              // The drag preview is intentionally SMALLER than the card: shouldn't
              // cover the screen under the pointer, so the drop target underneath stays visible.
              <div className="relative w-40 overflow-hidden rounded-lg border bg-card shadow-lg">
                {activeUpload.file_id && (
                  <img src={thumbnailUrl(activeUpload.file_id, 400)} alt=""
                    className="aspect-[4/5] w-full bg-muted object-contain" />
                )}
                {dragCount > 1 && (
                  <span className="absolute right-1 top-1 rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground">
                    {dragCount}
                  </span>
                )}
              </div>
            )}
          </DragOverlay>
        </DndContext>
      )}

      {/* shoot photos — conditionally mounted so the query never runs while collapsed */}
      <section className="space-y-2">
        <button onClick={() => setPhotosOpen((o) => !o)}
          className="flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("h-4 w-4 transition-transform", !photosOpen && "-rotate-90")} />
          <Camera className="h-4 w-4" /> {t("pages.clientMedia.shootPhotos.sectionTitle")}
        </button>
        {photosOpen && <ShootPhotosBlock clientId={clientId} />}
      </section>

      <BriefDialog open={briefOpen} onOpenChange={setBriefOpen}
        clientId={clientId} clientName={clientName} weekIso={fromWeek} />
      {uploadWeek && (
        <UploadModal open onClose={() => setUploadWeek(null)}
          clientId={clientId} clientName={clientName} weekIso={uploadWeek} />
      )}
      <MediaLightbox up={lightbox} onClose={() => setLightbox(null)} />
    </div>
  )
}
