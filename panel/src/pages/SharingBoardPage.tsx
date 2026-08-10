// Sharing Board — yönetim "Paylaşım Takvimi" görünümü (Faz 2a).
// Model: müşteri satırı + yatay kart havuzu (matris değil). Hafta URL'de senkron.
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { ArrowRightLeft, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, FileText, FolderOpen, MailCheck, Plus, Search, Zap } from "lucide-react"
import { toast } from "sonner"

import {
  KIND_LABELS, thumbnailUrl, useBoard, useDriveCounts, usePriorityToggle,
  useSetManagerClient, type BoardRow, type Share,
} from "@/lib/sharing"
import { useAuth } from "@/lib/auth"
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
  const review = share.client_review?.status
  const strip = revising
    ? { text: "🔁 REVİZEDE", cls: "bg-red-500/15 text-red-700 dark:text-red-400" }
    : share.status === "published"
      ? { text: share.published_day_name || "YAYINDA", cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" }
      : { text: "TASLAK", cls: "bg-muted text-muted-foreground" }

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
          {share.caption_text || share.note || <span className="italic">içerik yok</span>}
        </div>
        <div className="flex items-center justify-between">
          <Badge variant="outline" className="text-[10px]">{KIND_LABELS[share.kind]}</Badge>
          {review === "approved" && <span title="Onaylandı">✅</span>}
          {review === "revision_requested" && <span title={share.client_review?.note || "Revize istendi"}>📝</span>}
        </div>
      </div>
    </button>
  )
}

function ClientRow({ row, weekIso, isOwner }: { row: BoardRow; weekIso: string; isOwner: boolean }) {
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
            title={row.assigned ? "Müşterilerimden çıkar" : "Müşterilerime ekle"}
            onChange={(e) =>
              setManager.mutate(
                { client_id: row.client.id, owned: e.target.checked },
                { onError: () => toast.error("İşaretleme kaydedilemedi") },
              )
            }
          />
        )}
        {/* Müşteri adı → medya sayfası (2026-08-04): tasarım board'undaki
            davranışın aynısı, hafta bağlamı korunur. */}
        <Link to={`/designer/musteri/${row.client.id}?week=${encodeURIComponent(weekIso)}`}
          className="font-medium hover:text-primary hover:underline"
          title="Müşteri medya sayfası">
          {row.client.name}
        </Link>
        <Badge variant={row.published_count === row.total_count && row.total_count > 0 ? "secondary" : "outline"}>
          {row.published_count}/{row.total_count} yayında
        </Badge>
        {row.upload_count > 0 && (
          <Badge variant="outline" className="text-muted-foreground">📂 {row.upload_count}</Badge>
        )}
        {driveCount != null && (
          <Badge variant="outline" className="text-muted-foreground" title="Drive'daki dosya sayısı">
            📁 {driveCount}
          </Badge>
        )}
        {row.priority && <Badge className="bg-amber-500 text-white">⚡ Öncelikli</Badge>}
        {row.open_revision_count > 0 && (
          <Badge className="bg-red-500 text-white">🔁 {row.open_revision_count} revize</Badge>
        )}
        <div className="ml-auto flex items-center gap-1">
          {row.drive_folder_url && (
            <Button variant="ghost" size="icon" title="Drive klasörünü aç"
              render={<a href={row.drive_folder_url} target="_blank" rel="noreferrer" />}>
              <FolderOpen className="h-4 w-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" title="Paylaşılmamış içerik taşı"
            onClick={() => setMoveOpen(true)}>
            <ArrowRightLeft className="h-4 w-4" />
          </Button>
          {row.client.instagram_url && (
            <Button variant="ghost" size="icon" render={<a href={row.client.instagram_url} target="_blank" rel="noreferrer" />} title="Instagram">
              <ExternalLink className="h-4 w-4" />
            </Button>
          )}
          <Button variant="ghost" size="icon" title="Öncelik"
            onClick={() => priority.mutate({ client_id: row.client.id, week_iso: weekIso })}>
            <Zap className={cn("h-4 w-4", row.priority && "fill-amber-500 text-amber-500")} />
          </Button>
          <Button variant="ghost" size="icon" title="Brief" onClick={() => setBriefOpen(true)}>
            <FileText className="h-4 w-4" />
          </Button>
          {/* Müşteri Onay Linki (2026-08-06) — eski otomatik kapsamlı "Onay linki
              kopyala" düğmesinin yerini aldı (proje sahibi 2026-08-06): üç haftalık
              pencereden ELLE seçim yaptırır ve seçimi linke dondurur. */}
          <Button variant="ghost" size="icon" title="Müşteri Onay Linki (seçerek gönder)"
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
          <Plus className="h-5 w-5" /> Paylaşım
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

  // Derin bağlantı `?onay=<client_id>` (2026-08-07) — "Yeni içerik yüklendi"
  // bildiriminden gelince o müşterinin Müşteri Onay Linki modalı kendiliğinden
  // açılır. Modal SAYFA seviyesinde render edilir, satırın içinde DEĞİL: hedef
  // müşteri arama filtresiyle elenmiş ya da kapalı "Diğer Müşteriler" bölümünde
  // olabilir — o durumda satıra bağlı bir modal hiç render edilmez ve bildirim
  // sessizce hiçbir şey yapmazdı. `rows` değil `data.rows` üzerinden aranmasının
  // sebebi de bu (arama kutusu doluyken de bulunsun).
  const onayId = Number(params.get("onay"))
  const onayRow = (data?.rows ?? []).find((r) => r.client.id === onayId)

  function onayKapat() {
    setParams((p) => { p.delete("onay"); return p }, { replace: true })
  }

  // Sahibin işaretledikleri (sahip görünümü) / işaretlemedikleri (diğer yöneticiler)
  // "Müşterilerim"de; geri kalan "Diğer Müşteriler"de. assigned bayrağını backend kurar.
  const mine = useMemo(() => rows.filter((r) => r.assigned), [rows])
  const others = useMemo(() => rows.filter((r) => !r.assigned), [rows])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sharing Board</h1>
          <p className="text-muted-foreground">Haftalık paylaşım takvimi.</p>
        </div>
        <div className="flex items-center gap-1 rounded-lg border p-1">
          <Button variant="ghost" size="icon" onClick={() => setWeek(shiftWeek(weekIso, -1))}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-[9rem] text-center">
            <div className="text-sm font-medium">{weekIso}</div>
            <div className="text-xs text-muted-foreground">{weekRangeLabel(weekIso)}</div>
          </div>
          <Button variant="ghost" size="icon" onClick={() => setWeek(shiftWeek(weekIso, 1))}>
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button variant="outline" size="sm" className="ml-1"
            onClick={() => setWeek(currentWeekIso())}>Bugün</Button>
        </div>
      </div>

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder="Müşteri ara…" value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      {isLoading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-40 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">Board yüklenemedi.</p>}
      {data && rows.length === 0 && (
        <p className="py-8 text-center text-muted-foreground">
          {q ? "Eşleşen müşteri yok." : "Aktif müşteri yok."}
        </p>
      )}

      {mine.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground">Müşterilerim · {mine.length}</h2>
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
            Diğer Müşteriler · {others.length}
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
