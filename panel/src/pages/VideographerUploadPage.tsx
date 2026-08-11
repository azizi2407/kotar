// Videographer video-upload board (2026-07-21) — mirrors the designer board pattern
// exactly: assigned clients on top under "My Clients", the rest under a collapsible
// "Other Clients". 2026-07-25: every client row is expandable → shows that week's videos
// AWAITING SHARING (already-shared ones aren't listed) + a "Copy" button that puts the
// Drive link on the clipboard. Also has personal client hiding (⚙ Settings) and an
// entry point to the Videographer Depot.
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  ChevronDown, ChevronLeft, ChevronRight, Clapperboard, HardDrive, Search, Settings, Upload,
} from "lucide-react"

import { useVideographerBoard, type BoardRow } from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { currentWeekIso, shiftWeek, trFold, weekRangeLabel } from "@/lib/week"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { UploadModal } from "@/components/sharing/UploadModal"
import { PendingVideoList } from "@/components/videographer/PendingVideoList"
import { VgVisibilityDialog } from "@/components/videographer/VgVisibilityDialog"
import { cn } from "@/lib/utils"

function VideoTile({ row, weekIso, open, onToggle }: {
  row: BoardRow
  weekIso: string
  open: boolean
  onToggle: () => void
}) {
  const { t } = useI18n()
  const [uploadOpen, setUploadOpen] = useState(false)
  const pendingCount = row.video_pending_count ?? 0
  // 2026-07-30: the list shows ALL of the week's videos (already-shared ones are badged)
  // — so the videographer can still watch their uploaded video after it's been shared.
  // `video_pending` is no longer used (it was capped at the newest 5); `video_uploads` is
  // the full list, sorted newest first.
  const videos = useMemo(
    () => [...(row.video_uploads ?? [])].sort(
      (a, b) => (b.uploaded_at || "").localeCompare(a.uploaded_at || "") || b.id - a.id),
    [row.video_uploads])
  const total = row.video_total_count ?? videos.length
  const panelId = `videolar-${row.client.id}`

  const summary = total === 0
    ? t("pages.videographerUpload.noVideos")
    : pendingCount > 0
      ? t("pages.videographerUpload.pendingSummary", { total, pending: pendingCount })
      : t("pages.videographerUpload.allSharedSummary", { total })

  return (
    <div className={cn(
      "rounded-lg border",
      // A client with a video uploaded that week gets a light-green background — a
      // one-glance "did a shoot come in here?" distinction in the list. Whether it's been
      // shared doesn't matter, the criterion is whether an UPLOAD happened (video_total_count).
      total > 0
        ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-950/30"
        : "bg-card",
    )}>
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          {total > 0 && (
            <button type="button" aria-expanded={open} aria-controls={panelId}
              onClick={onToggle} title={t("pages.videographerUpload.showVideosTitle")}
              className="text-muted-foreground hover:text-foreground">
              <ChevronDown className={cn("h-4 w-4 transition-transform", !open && "-rotate-90")} />
            </button>
          )}
          <div className="min-w-0">
            <div className="truncate font-medium">{row.client.name}</div>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
              <Clapperboard className="h-3.5 w-3.5" />
              {summary}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {pendingCount > 0 && (
            <Badge variant="secondary" className="text-[10px]">
              {t("pages.videographerUpload.pendingBadge", { count: pendingCount })}
            </Badge>
          )}
          <Button size="sm" onClick={() => setUploadOpen(true)}>
            <Upload className="mr-1 h-4 w-4" /> {t("pages.videographerUpload.uploadVideoBtn")}
          </Button>
        </div>
      </div>

      {open && (
        <div id={panelId} className="border-t bg-muted/20 p-3">
          <PendingVideoList videos={videos} />
        </div>
      )}

      {uploadOpen && (
        <UploadModal open onClose={() => setUploadOpen(false)}
          clientId={row.client.id} clientName={row.client.name} weekIso={weekIso}
          fixedCategory="video" accept="video" />
      )}
    </div>
  )
}

export function VideographerUploadPage() {
  const { t, lang } = useI18n()
  const [params, setParams] = useSearchParams()
  const weekIso = params.get("week") || currentWeekIso()
  const [q, setQ] = useState("")
  const [othersOpen, setOthersOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Only one row open at a time: with 31 clients × 5 videos all open, 155 previews would
  // get mounted (a real memory/network cost) and the page would become unreadable.
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const { data, isLoading, isError } = useVideographerBoard(weekIso)

  function setWeek(w: string) {
    setParams((p) => { p.set("week", w); return p }, { replace: true })
  }

  const rows = useMemo(() => {
    const all = data?.rows ?? []
    if (!q.trim()) return all
    const needle = trFold(q)
    return all.filter((r) => trFold(r.client.name).includes(needle))
  }, [data, q])

  const mine = useMemo(() => rows.filter((r) => r.assigned), [rows])
  const others = useMemo(() => rows.filter((r) => !r.assigned), [rows])
  const hiddenCount = data?.hidden_count ?? 0

  const tile = (row: BoardRow) => (
    <VideoTile key={row.client.id} row={row} weekIso={weekIso}
      open={expandedId === row.client.id}
      onToggle={() => setExpandedId(expandedId === row.client.id ? null : row.client.id)} />
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("pages.videographerUpload.title")}</h1>
          <p className="text-muted-foreground">{t("pages.videographerUpload.subtitle")}</p>
        </div>
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
          <Button variant="outline" size="sm" className="ml-1"
            onClick={() => setWeek(currentWeekIso())}>{t("pages.videographerUpload.todayBtn")}</Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder={t("pages.videographerUpload.searchPlaceholder")} value={q}
            onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          {hiddenCount > 0 && (
            <button type="button" onClick={() => setSettingsOpen(true)}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline">
              {t("pages.videographerUpload.hiddenCountLabel", { count: hiddenCount })}
            </button>
          )}
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            <Settings className="mr-1 h-4 w-4" /> {t("pages.videographerUpload.settingsBtn")}
          </Button>
          {/* Button doesn't support `asChild` (the base-ui variant) → the link is styled directly */}
          <Link to="/videograf-deposu"
            className="inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted">
            <HardDrive className="mr-1 h-4 w-4" /> {t("nav.videographerDepot")}
          </Link>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">{t("pages.videographerUpload.loadError")}</p>}
      {data && rows.length === 0 && (
        <p className="py-8 text-center text-muted-foreground">
          {q ? t("pages.videographerUpload.noMatch")
             : hiddenCount > 0 ? t("pages.videographerUpload.allHidden")
             : t("pages.videographerUpload.noActiveClients")}
        </p>
      )}

      {mine.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-muted-foreground">
            {t("pages.videographerUpload.myClientsHeading", { count: mine.length })}
            <Badge variant="outline" className="ml-2 text-[10px]">{t("pages.videographerUpload.assignedBadge")}</Badge>
          </h2>
          <div className="space-y-2">{mine.map(tile)}</div>
        </section>
      )}

      {others.length > 0 && (
        <section className="space-y-2">
          <button onClick={() => setOthersOpen((o) => !o)}
            className="flex w-full items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("h-4 w-4 transition-transform", !othersOpen && "-rotate-90")} />
            {t("pages.videographerUpload.othersHeading", { count: others.length })}
          </button>
          {othersOpen && <div className="space-y-2">{others.map(tile)}</div>}
        </section>
      )}

      {settingsOpen && <VgVisibilityDialog onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
