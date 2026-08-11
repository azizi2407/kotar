// Sharing Board — management "Publishing Calendar" view (Phase 2a).
// Model: client row + horizontal card pool (not a matrix). Week is synced in the URL.
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { ArrowRightLeft, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, FileText, FolderOpen, MailCheck, Plus, Search, Zap } from "lucide-react"
import { toast } from "sonner"

import {
  KIND_LABELS, thumbnailUrl, useBoard, useDriveCounts, usePriorityToggle,
  useSetManagerClient, type BoardRow, type Share,
} from "@/lib/sharing"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { currentWeekIso, shiftWeek, trFold, weekRangeLabel } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { ShareModal } from "@/components/sharing/ShareModal"
import { BriefDialog } from "@/components/sharing/BriefDialog"
import { MoveFilesModal } from "@/components/sharing/MoveFilesModal"
import { ClientApprovalModal } from "@/components/sharing/ClientApprovalModal"
import { SpecialDayCard } from "@/components/sharing/SpecialDayCard"
import { VideoUploadCard } from "@/components/sharing/VideoUploadCard"

function ShareCard({ share, revising, onClick }: { share: Share; revising: boolean; onClick: () => void }) {
  const { t } = useI18n()
  const review = share.client_review?.status
  const strip = revising
    ? { text: `🔁 ${t("pages.sharing.card.inRevision")}`, cls: "bg-red-500/15 text-red-700 dark:text-red-400" }
    : share.status === "published"
      ? { text: share.published_day_name || t("pages.sharing.card.published"), cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" }
      : { text: t("pages.sharing.card.draft"), cls: "bg-muted text-muted-foreground" }

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative flex h-32 w-40 shrink-0 flex-col overflow-hidden rounded-lg border bg-card text-left transition-colors hover:border-primary/50"
    >
      <div className={cn("px-2 py-1 text-[10px] font-semibold tracking-wide", strip.cls)}>
        {strip.text}
      </div>
      {share.file_id ? (
        <img
          src={thumbnailUrl(share.file_id)}
          alt=""
          loading="lazy"
          className="h-14 w-full shrink-0 bg-muted object-contain"
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none" }}
        />
      ) : null}
      <div className="flex flex-1 flex-col justify-between p-2">
        <div className={cn("text-xs text-muted-foreground", share.file_id ? "line-clamp-2" : "line-clamp-3")}>
          {share.caption_text || share.note || <span className="italic">{t("pages.sharing.card.noContent")}</span>}
        </div>
        <div className="flex items-center justify-between">
          <Badge variant="outline" className="text-[10px]">{KIND_LABELS[share.kind]}</Badge>
          {review === "approved" && <span title={t("pages.sharing.card.approved")}>✅</span>}
          {review === "revision_requested" && <span title={share.client_review?.note || t("pages.sharing.card.revisionRequested")}>📝</span>}
        </div>
      </div>
    </button>
  )
}

function ClientRow({ row, weekIso, isOwner }: { row: BoardRow; weekIso: string; isOwner: boolean }) {
  const { t } = useI18n()
  const [modal, setModal] = useState<{ open: boolean; share: Share | null }>({ open: false, share: null })
  const [briefOpen, setBriefOpen] = useState(false)
  const [moveOpen, setMoveOpen] = useState(false)
  const [approvalOpen, setApprovalOpen] = useState(false)
  const priority = usePriorityToggle()
  const setManager = useSetManagerClient(weekIso)
  const { data: driveCounts } = useDriveCounts(weekIso)
  const driveCount = driveCounts?.[String(row.client.id)]

  return (
    <div className={cn("rounded-lg border", row.priority && "border-amber-500/50")}>
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        {isOwner && (
          <input
            type="checkbox"
            className="h-4 w-4 shrink-0 cursor-pointer accent-primary"
            checked={row.assigned}
            disabled={setManager.isPending}
            title={row.assigned ? t("pages.sharing.row.unassign") : t("pages.sharing.row.assign")}
            onChange={(e) =>
              setManager.mutate(
                { client_id: row.client.id, owned: e.target.checked },
                { onError: () => toast.error(t("pages.sharing.row.markError")) },
              )
            }
          />
        )}
        {/* Client name → media page (2026-08-04): same behavior as the design
            board, week context is preserved. */}
        <Link to={`/designer/musteri/${row.client.id}?week=${encodeURIComponent(weekIso)}`}
          className="font-medium hover:text-primary hover:underline"
          title={t("pages.sharing.row.clientMediaPage")}>
          {row.client.name}
        </Link>
        <Badge variant={row.published_count === row.total_count && row.total_count > 0 ? "secondary" : "outline"}>
          {t("pages.sharing.row.publishedCount", { published: row.published_count, total: row.total_count })}
        </Badge>
        {row.upload_count > 0 && (
          <Badge variant="outline" className="text-muted-foreground">📂 {row.upload_count}</Badge>
        )}
        {driveCount != null && (
          <Badge variant="outline" className="text-muted-foreground" title={t("pages.sharing.row.driveFileCount")}>
            📁 {driveCount}
          </Badge>
        )}
        {row.priority && <Badge className="bg-amber-500 text-white">⚡ {t("pages.sharing.row.priorityBadge")}</Badge>}
        {row.open_revision_count > 0 && (
          <Badge className="bg-red-500 text-white">🔁 {t("pages.sharing.row.revisionBadge", { count: row.open_revision_count })}</Badge>
        )}
        <div className="ml-auto flex items-center gap-1">
          {row.drive_folder_url && (
            <Button variant="ghost" size="icon" title={t("pages.sharing.row.openDriveFolder")}
              render={<a href={row.drive_folder_url} target="_blank" rel="noreferrer" />}>
              <FolderOpen className="h-4 w-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" title={t("pages.sharing.row.moveUnshared")}
            onClick={() => setMoveOpen(true)}>
            <ArrowRightLeft className="h-4 w-4" />
          </Button>
          {row.client.instagram_url && (
            <Button variant="ghost" size="icon" render={<a href={row.client.instagram_url} target="_blank" rel="noreferrer" />} title="Instagram">
              <ExternalLink className="h-4 w-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" title={t("pages.sharing.row.priority")}
            onClick={() => priority.mutate({ client_id: row.client.id, week_iso: weekIso })}>
            <Zap className={cn("h-4 w-4", row.priority && "fill-amber-500 text-amber-500")} />
          </Button>
          <Button variant="ghost" size="icon" title="Brief" onClick={() => setBriefOpen(true)}>
            <FileText className="h-4 w-4" />
          </Button>
          {/* Client Approval Link (2026-08-06) — replaced the old automatic broad-scope
              "Copy approval link" button (project owner's decision 2026-08-06): it has
              the user MANUALLY select from a three-week window and freezes that
              selection into the link. */}
          <Button variant="ghost" size="icon" title={t("pages.sharing.row.approvalLink")}
            onClick={() => setApprovalOpen(true)}>
            <MailCheck className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <BriefDialog open={briefOpen} onOpenChange={setBriefOpen}
        clientId={row.client.id} clientName={row.client.name} weekIso={weekIso} />
      {moveOpen && (
        <MoveFilesModal open={moveOpen} onOpenChange={setMoveOpen}
          clientId={row.client.id} clientName={row.client.name} weekIso={weekIso} />
      )}
      {approvalOpen && (
        <ClientApprovalModal open={approvalOpen} onOpenChange={setApprovalOpen}
          clientId={row.client.id} clientName={row.client.name} weekIso={weekIso} />
      )}
      <div className="flex gap-2 overflow-x-auto p-3">
        {row.special_days.map((sd) => (
          <SpecialDayCard key={sd.event_id} sd={sd} className="h-32 w-40" />
        ))}
        {(row.video_uploads ?? []).map((vu) => (
          <VideoUploadCard key={vu.id} vu={vu} className="h-32 w-40" />
        ))}
        {row.shares.map((s) => (
          <ShareCard key={s.id} share={s}
            revising={row.revision_share_ids.includes(s.id)}
            onClick={() => setModal({ open: true, share: s })} />
        ))}
        <button
          type="button"
          onClick={() => setModal({ open: true, share: null })}
          className="flex h-32 w-40 shrink-0 flex-col items-center justify-center gap-1 rounded-lg border border-dashed text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
        >
          <Plus className="h-5 w-5" /> {t("pages.sharing.row.addShare")}
        </button>
      </div>

      <ShareModal
        open={modal.open}
        onOpenChange={(v) => setModal((m) => ({ ...m, open: v }))}
        clientId={row.client.id}
        clientName={row.client.name}
        weekIso={weekIso}
        share={modal.share}
      />
    </div>
  )
}

export function SharingBoardPage() {
  const { t, lang } = useI18n()
  const [params, setParams] = useSearchParams()
  const weekIso = params.get("week") || currentWeekIso()
  const [q, setQ] = useState("")
  const [othersOpen, setOthersOpen] = useState(true)
  const { data, isLoading, isError } = useBoard(weekIso)
  const { isAgencyOwner: isOwner } = useAuth()

  function setWeek(w: string) {
    setParams((p) => { p.set("week", w); return p }, { replace: true })
  }

  const rows = useMemo(() => {
    const all = data?.rows ?? []
    if (!q.trim()) return all
    const needle = trFold(q)
    return all.filter((r) => trFold(r.client.name).includes(needle))
  }, [data, q])

  // Deep link `?onay=<client_id>` (2026-08-07) — arriving from a "New content
  // uploaded" notification automatically opens that client's Client Approval
  // Link modal. The modal is rendered at PAGE level, NOT inside the row: the
  // target client may be filtered out by the search box or sitting in the
  // collapsed "Other Clients" section — in that case a row-bound modal would
  // never render and the notification would silently do nothing. That's also
  // why it's looked up via `data.rows` instead of `rows` (so it's still found
  // while the search box has text in it).
  const onayId = Number(params.get("onay"))
  const onayRow = (data?.rows ?? []).find((r) => r.client.id === onayId)

  function onayKapat() {
    setParams((p) => { p.delete("onay"); return p }, { replace: true })
  }

  // Clients the owner marked (owner view) / did not mark (other managers) go
  // in "My Clients"; the rest go in "Other Clients". The `assigned` flag is set by the backend.
  const mine = useMemo(() => rows.filter((r) => r.assigned), [rows])
  const others = useMemo(() => rows.filter((r) => !r.assigned), [rows])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sharing Board</h1>
          <p className="text-muted-foreground">{t("pages.sharing.subtitle")}</p>
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
            onClick={() => setWeek(currentWeekIso())}>{t("pages.sharing.today")}</Button>
        </div>
      </div>

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder={t("pages.sharing.searchPlaceholder")} value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-40 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">{t("pages.sharing.loadError")}</p>}
      {data && rows.length === 0 && (
        <p className="py-8 text-center text-muted-foreground">
          {q ? t("pages.sharing.noMatch") : t("pages.sharing.noActiveClients")}
        </p>
      )}

      {mine.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground">
            {t("pages.sharing.myClients", { count: mine.length })}
          </h2>
          <div className="space-y-3">
            {mine.map((row) => <ClientRow key={row.client.id} row={row} weekIso={weekIso} isOwner={isOwner} />)}
          </div>
        </section>
      )}

      {onayRow && (
        <ClientApprovalModal open onOpenChange={(v) => { if (!v) onayKapat() }}
          clientId={onayRow.client.id} clientName={onayRow.client.name} weekIso={weekIso} />
      )}

      {others.length > 0 && (
        <section className="space-y-3">
          <button onClick={() => setOthersOpen((o) => !o)}
            className="flex w-full items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("h-4 w-4 transition-transform", !othersOpen && "-rotate-90")} />
            {t("pages.sharing.otherClients", { count: others.length })}
          </button>
          {othersOpen && (
            <div className="space-y-3">
              {others.map((row) => <ClientRow key={row.client.id} row={row} weekIso={weekIso} isOwner={isOwner} />)}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
