// Videografçı video yükleme board'u (2026-07-21) — designer board deseniyle birebir:
// atanmış müşteriler üstte "Müşterilerim", diğerleri açılır "Diğer Müşteriler".
// 2026-07-25: her müşteri satırı açılabilir → o haftanın PAYLAŞILMAYI BEKLEYEN videoları
// (paylaşılmışlar listede yok) + Drive linkini panoya alan "Kopyala" düğmesi. Ayrıca
// kişisel müşteri gizleme (⚙ Ayarlar) ve Videograf Deposu girişi.
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  ChevronDown, ChevronLeft, ChevronRight, Clapperboard, HardDrive, Search, Settings, Upload,
} from "lucide-react"

import { useVideographerBoard, type BoardRow } from "@/lib/sharing"
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
  const [uploadOpen, setUploadOpen] = useState(false)
  const pendingCount = row.video_pending_count ?? 0
  // 2026-07-30: liste haftanın TÜM videolarını gösteriyor (paylaşılmışlar rozetli)
  // — videograf yüklediği videoyu paylaşıldıktan sonra da izleyebilsin. `video_pending`
  // artık kullanılmıyor (en yeni 5 ile kırpılıyordu); `video_uploads` tam liste, en
  // yeni üstte sıralanır.
  const videos = useMemo(
    () => [...(row.video_uploads ?? [])].sort(
      (a, b) => (b.uploaded_at || "").localeCompare(a.uploaded_at || "") || b.id - a.id),
    [row.video_uploads])
  const total = row.video_total_count ?? videos.length
  const panelId = `videolar-${row.client.id}`

  const summary = total === 0
    ? "Bu hafta video yok"
    : pendingCount > 0
      ? `Bu hafta ${total} video · ${pendingCount} paylaşım bekliyor`
      : `Bu hafta ${total} video · hepsi paylaşıldı`

  return (
    <div className={cn(
      "rounded-lg border",
      // O hafta video yüklenmiş müşteri açık yeşil zeminde — listede tek bakışta
      // "buraya çekim girdi mi?" ayrımı. Paylaşılmış olması fark etmez, ölçüt
      // YÜKLEME yapılmış olması (video_total_count).
      total > 0
        ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900/50 dark:bg-emerald-950/30"
        : "bg-card",
    )}>
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          {total > 0 && (
            <button type="button" aria-expanded={open} aria-controls={panelId}
              onClick={onToggle} title="Videoları göster"
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
            <Badge variant="secondary" className="text-[10px]">{pendingCount} bekliyor</Badge>
          )}
          <Button size="sm" onClick={() => setUploadOpen(true)}>
            <Upload className="mr-1 h-4 w-4" /> Video Yükle
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
  const [params, setParams] = useSearchParams()
  const weekIso = params.get("week") || currentWeekIso()
  const [q, setQ] = useState("")
  const [othersOpen, setOthersOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Aynı anda tek satır açık: 31 müşteri × 5 video hepsi açık olsa 155 önizleme
  // mount edilir (gerçek bellek/ağ maliyeti) ve sayfa okunmaz hale gelir.
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
          <h1 className="text-2xl font-semibold tracking-tight">Video Yükleme</h1>
          <p className="text-muted-foreground">Müşterilere haftalık video yükle.</p>
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

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="Müşteri ara…" value={q}
            onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          {hiddenCount > 0 && (
            <button type="button" onClick={() => setSettingsOpen(true)}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline">
              {hiddenCount} müşteri gizli
            </button>
          )}
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            <Settings className="mr-1 h-4 w-4" /> Ayarlar
          </Button>
          {/* Button `asChild` desteklemiyor (base-ui varyantı) → link doğrudan stillendi */}
          <Link to="/videograf-deposu"
            className="inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted">
            <HardDrive className="mr-1 h-4 w-4" /> Videograf Deposu
          </Link>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">Liste yüklenemedi.</p>}
      {data && rows.length === 0 && (
        <p className="py-8 text-center text-muted-foreground">
          {q ? "Eşleşen müşteri yok."
             : hiddenCount > 0 ? "Tüm müşteriler gizlenmiş — Ayarlar'dan geri açabilirsin."
             : "Aktif müşteri yok."}
        </p>
      )}

      {mine.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-muted-foreground">
            Müşterilerim · {mine.length}
            <Badge variant="outline" className="ml-2 text-[10px]">atandığın müşteriler</Badge>
          </h2>
          <div className="space-y-2">{mine.map(tile)}</div>
        </section>
      )}

      {others.length > 0 && (
        <section className="space-y-2">
          <button onClick={() => setOthersOpen((o) => !o)}
            className="flex w-full items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("h-4 w-4 transition-transform", !othersOpen && "-rotate-90")} />
            Diğer Müşteriler · {others.length}
          </button>
          {othersOpen && <div className="space-y-2">{others.map(tile)}</div>}
        </section>
      )}

      {settingsOpen && <VgVisibilityDialog onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
