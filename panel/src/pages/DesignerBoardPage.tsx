// Design board — clients assigned to the designer are on top ("My Clients"), unassigned
// ones are in the collapsible "Other Clients" section below. Each tile: copy Drive + Pre-Approval +
// Client Approval Link (by selection) +
// Upload (modal) + Shoot Photos (modal) + Brief. Full actions available in all groups.
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  ChevronDown, ChevronLeft, ChevronRight, Copy, FileText, Images, MailCheck, Search,
  ShieldCheck, Shapes, Upload,
} from "lucide-react"
import { toast } from "sonner"

import {
  KIND_LABELS, thumbnailUrl, useDesignerBoard, usePreApprovalLink, type BoardRow,
} from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { currentWeekIso, shiftWeek, trFold, weekRangeLabel } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { BriefDialog } from "@/components/sharing/BriefDialog"
import { ClientApprovalModal } from "@/components/sharing/ClientApprovalModal"
import { LogoButton } from "@/components/sharing/LogoButton"
import { SpecialDayCard } from "@/components/sharing/SpecialDayCard"
import { UploadModal } from "@/components/sharing/UploadModal"
import { ShootPhotosModal } from "@/components/sharing/ShootPhotosModal"
import { VideoUploadCard } from "@/components/sharing/VideoUploadCard"

// Shared Drive folder for client logos — the "Logos" button in the header
// links here. There's no panel equivalent (managed manually in Drive), hence the fixed URL.
const LOGOLAR_DRIVE_URL =
  "https://drive.google.com/drive/folders/1guwgEzMpNASkTNoyT83knoQxX1meozUN"

function DesignerTile({ row, weekIso }: { row: BoardRow; weekIso: string }) {
  const { t } = useI18n()
  const [briefOpen, setBriefOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [photosOpen, setPhotosOpen] = useState(false)
  const [approvalOpen, setApprovalOpen] = useState(false)
  const preLink = usePreApprovalLink()

  async function copyDrive() {
    if (!row.drive_folder_url) return
    try {
      await navigator.clipboard.writeText(row.drive_folder_url)
      toast.success(t("pages.designerBoard.driveCopied"))
    } catch {
      toast.error(t("pages.designerBoard.copyFailed"))
    }
  }

  // Pre-approval: internal link to be sent to the manager (management makes the decision).
  async function copyPreApproval() {
    try {
      const { token, shareCount } = await preLink.mutateAsync({
        client_id: row.client.id, week_iso: weekIso,
      })
      await navigator.clipboard.writeText(`${window.location.origin}/review/${token}`)
      toast.success(t("pages.designerBoard.preApprovalCopied", { count: shareCount }))
    } catch {
      toast.error(t("pages.designerBoard.preApprovalFailed"))
    }
  }

  return (
    <div className={cn("flex flex-col rounded-lg border", row.priority && "border-amber-500/50")}>
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        {/* Client name → media page (2026-08-04): all of that client's uploads,
            week by week, must be moved manually between weeks. Week context is preserved. */}
        <Link to={`/designer/musteri/${row.client.id}?week=${encodeURIComponent(weekIso)}`}
          className="font-medium hover:text-primary hover:underline"
          title={t("pages.designerBoard.clientMediaPageTitle")}>
          {row.client.name}
        </Link>
        <Badge variant={row.published_count === row.total_count && row.total_count > 0 ? "secondary" : "outline"}>
          {t("pages.designerBoard.publishedCount", { published: row.published_count, total: row.total_count })}
        </Badge>
        {row.priority && <Badge className="bg-amber-500 text-white">⚡</Badge>}
        {row.pre_approved_count > 0 && (
          <Badge className="bg-teal-600 text-white" title={t("pages.designerBoard.preApprovedTitle")}>
            ⚑ {row.pre_approved_count}
          </Badge>
        )}
        {row.pre_revision_count > 0 && (
          <Badge className="bg-rose-600 text-white" title={t("pages.designerBoard.preRevisionTitle")}>
            ⚑✎ {row.pre_revision_count}
          </Badge>
        )}
        {row.upload_approved_count > 0 && (
          <Badge className="bg-emerald-600 text-white" title={t("pages.designerBoard.approvedTitle")}>
            ✓ {row.upload_approved_count}
          </Badge>
        )}
        {row.upload_revision_count > 0 && (
          <Badge className="bg-orange-500 text-white" title={t("pages.designerBoard.revisionTitle")}>
            ✎ {row.upload_revision_count}
          </Badge>
        )}
        {row.open_revision_count > 0 && (
          <Badge className="bg-red-500 text-white">🔁 {row.open_revision_count}</Badge>
        )}
        {row.upload_count > 0 && (
          <Badge variant="outline" className="text-muted-foreground">📂 {row.upload_count}</Badge>
        )}
      </div>

      <div className="flex flex-1 flex-wrap gap-2 p-3">
        {row.special_days.map((sd) => (
          <SpecialDayCard key={sd.event_id} sd={sd} className="w-24" />
        ))}
        {/* Video uploads (2026-08-05): the designer now uploads video too, and what they
            upload should show up on their own board. The card is the same component as on the
            management board — the data was already coming from `_build_rows`, just wasn't rendered here. */}
        {(row.video_uploads ?? []).map((vu) => (
          <VideoUploadCard key={vu.id} vu={vu} className="w-24" />
        ))}
        {row.shares.length === 0 && row.special_days.length === 0
          && (row.video_uploads ?? []).length === 0 && (
          <span className="text-sm text-muted-foreground">{t("pages.designerBoard.noSharesYet")}</span>
        )}
        {row.shares.map((s) => {
          const revising = row.revision_share_ids.includes(s.id)
          return (
            <div key={s.id} className="w-24 overflow-hidden rounded border bg-card" title={s.caption_text || ""}>
              <div className={cn("px-1 py-0.5 text-[9px] font-semibold",
                revising ? "bg-red-500/15 text-red-600"
                  : s.status === "published" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                    : "bg-muted text-muted-foreground")}>
                {revising ? t("pages.designerBoard.revisingBadge")
                  : s.status === "published" ? t("pages.designerBoard.publishedBadge")
                    : t("pages.designerBoard.draftBadge")}
              </div>
              {s.file_id ? (
                <img src={thumbnailUrl(s.file_id, 200)} alt="" loading="lazy"
                  className="h-16 w-full bg-muted object-contain"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none" }} />
              ) : (
                <div className="flex h-16 items-center justify-center text-[10px] text-muted-foreground">
                  {KIND_LABELS[s.kind]}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t p-2">
        <Button variant="outline" size="sm" onClick={() => setUploadOpen(true)}>
          <Upload className="mr-1 h-3.5 w-3.5" /> {t("pages.designerBoard.uploadBtn")}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setPhotosOpen(true)}>
          <Images className="mr-1 h-3.5 w-3.5" /> {t("pages.designerBoard.shootPhotosBtn")}
        </Button>
        <Button variant="ghost" size="sm" onClick={copyDrive} disabled={!row.drive_folder_url}
          title={row.drive_folder_url
            ? t("pages.designerBoard.copyDriveTitle")
            : t("pages.designerBoard.noDriveTitle")}>
          <Copy className="mr-1 h-3.5 w-3.5" /> Drive
        </Button>
        <Button variant="ghost" size="sm" onClick={copyPreApproval} disabled={preLink.isPending}>
          <ShieldCheck className="mr-1 h-3.5 w-3.5" /> {t("pages.designerBoard.preApprovalBtn")}
        </Button>
        {/* Replaces the old automatic blanket "Approval Link" (feature owner 2026-08-06):
            it used to send every eligible upload of the week; this one lets you MANUALLY
            select from a three-week window. The Pre-Approval internal gate stays in place. */}
        <Button variant="ghost" size="sm" onClick={() => setApprovalOpen(true)}>
          <MailCheck className="mr-1 h-3.5 w-3.5" /> {t("pages.designerBoard.approvalLinkBtn")}
        </Button>
        <LogoButton clientId={row.client.id} />
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setBriefOpen(true)}>
          <FileText className="mr-1 h-3.5 w-3.5" /> Brief
        </Button>
      </div>

      <BriefDialog open={briefOpen} onOpenChange={setBriefOpen}
        clientId={row.client.id} clientName={row.client.name} weekIso={weekIso} />
      {uploadOpen && (
        <UploadModal open onClose={() => setUploadOpen(false)}
          clientId={row.client.id} clientName={row.client.name} weekIso={weekIso} />
      )}
      {photosOpen && (
        <ShootPhotosModal open onClose={() => setPhotosOpen(false)}
          clientId={row.client.id} clientName={row.client.name} />
      )}
      {approvalOpen && (
        <ClientApprovalModal open={approvalOpen} onOpenChange={setApprovalOpen}
          clientId={row.client.id} clientName={row.client.name} weekIso={weekIso} />
      )}
    </div>
  )
}

export function DesignerBoardPage() {
  const { t, lang } = useI18n()
  const [params, setParams] = useSearchParams()
  const weekIso = params.get("week") || currentWeekIso()
  const [q, setQ] = useState("")
  const [othersOpen, setOthersOpen] = useState(false)
  const { data, isLoading, isError } = useDesignerBoard(weekIso)

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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{t("pages.designerBoard.title")}</h1>
            <p className="text-muted-foreground">{t("pages.designerBoard.subtitle")}</p>
          </div>
          {/* Shared Drive folder for client logos (2026-07-31, feature owner request).
              No panel equivalent — links directly to Drive in a new tab.
              `Button` doesn't support `asChild` (base-ui variant) → the link is manually styled. */}
          <a href={LOGOLAR_DRIVE_URL} target="_blank" rel="noreferrer"
            title={t("pages.designerBoard.logosFolderTitle")}
            className="inline-flex h-9 shrink-0 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted">
            <Shapes className="mr-1.5 h-4 w-4" /> {t("pages.designerBoard.logosLabel")}
          </a>
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
          <Button variant="outline" size="sm" className="ml-1" onClick={() => setWeek(currentWeekIso())}>
            {t("pages.designerBoard.todayBtn")}
          </Button>
        </div>
      </div>

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder={t("pages.designerBoard.searchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      {isLoading && (
        <div className="grid gap-3 md:grid-cols-2">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-52 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">{t("pages.designerBoard.loadError")}</p>}
      {data && rows.length === 0 && (
        <p className="py-8 text-center text-muted-foreground">
          {q ? t("pages.designerBoard.noMatch") : t("pages.designerBoard.noClients")}
        </p>
      )}

      {mine.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground">
            {t("pages.designerBoard.myClientsHeading", { count: mine.length })}
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            {mine.map((row) => <DesignerTile key={row.client.id} row={row} weekIso={weekIso} />)}
          </div>
        </section>
      )}

      {others.length > 0 && (
        <section className="space-y-3">
          <button onClick={() => setOthersOpen((o) => !o)}
            className="flex w-full items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("h-4 w-4 transition-transform", !othersOpen && "-rotate-90")} />
            {t("pages.designerBoard.othersHeading", { count: others.length })}
          </button>
          {othersOpen && (
            <div className="grid gap-3 md:grid-cols-2">
              {others.map((row) => <DesignerTile key={row.client.id} row={row} weekIso={weekIso} />)}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
