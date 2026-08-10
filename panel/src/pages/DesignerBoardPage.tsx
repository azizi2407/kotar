// Tasarım board'u — designer'a atanmış müşteriler üstte ("Müşterilerim"), atanmamışlar
// altta açılır "Diğer Müşteriler" bölümünde. Her tile: Drive kopyala + Ön-Onay +
// Müşteri Onay Linki (seçerek) +
// Yükle (modal) + Çekim Fotoğrafları (modal) + Brief. Tüm gruplarda tam aksiyon.
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

// Müşteri logolarının ortak Drive klasörü — başlıktaki "Logolar" düğmesi buraya
// gider. Panelde karşılığı yok (Drive'da elle yönetiliyor), o yüzden sabit URL.
const LOGOLAR_DRIVE_URL =
  "https://drive.google.com/drive/folders/1guwgEzMpNASkTNoyT83knoQxX1meozUN"

function DesignerTile({ row, weekIso }: { row: BoardRow; weekIso: string }) {
  const [briefOpen, setBriefOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [photosOpen, setPhotosOpen] = useState(false)
  const [approvalOpen, setApprovalOpen] = useState(false)
  const preLink = usePreApprovalLink()

  async function copyDrive() {
    if (!row.drive_folder_url) return
    try {
      await navigator.clipboard.writeText(row.drive_folder_url)
      toast.success("Drive klasör linki kopyalandı")
    } catch {
      toast.error("Kopyalanamadı")
    }
  }

  // Ön-onay: yöneticiye gönderilecek iç link (kararı yönetim verir).
  async function copyPreApproval() {
    try {
      const { token, shareCount } = await preLink.mutateAsync({
        client_id: row.client.id, week_iso: weekIso,
      })
      await navigator.clipboard.writeText(`${window.location.origin}/review/${token}`)
      toast.success(`Ön-onay linki kopyalandı (${shareCount} içerik) — yöneticiye gönderin`)
    } catch {
      toast.error("Ön-onay linki üretilemedi")
    }
  }

  return (
    <div className={cn("flex flex-col rounded-lg border", row.priority && "border-amber-500/50")}>
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        {/* Müşteri adı → medya sayfası (2026-08-04): o müşterinin tüm yüklemeleri
            hafta hafta, haftalar arası elle taşımalı. Hafta bağlamı korunur. */}
        <Link to={`/designer/musteri/${row.client.id}?week=${encodeURIComponent(weekIso)}`}
          className="font-medium hover:text-primary hover:underline"
          title="Müşteri medya sayfası">
          {row.client.name}
        </Link>
        <Badge variant={row.published_count === row.total_count && row.total_count > 0 ? "secondary" : "outline"}>
          {row.published_count}/{row.total_count} yayında
        </Badge>
        {row.priority && <Badge className="bg-amber-500 text-white">⚡</Badge>}
        {row.pre_approved_count > 0 && (
          <Badge className="bg-teal-600 text-white" title="Ön onay verildi (yönetim)">
            ⚑ {row.pre_approved_count}
          </Badge>
        )}
        {row.pre_revision_count > 0 && (
          <Badge className="bg-rose-600 text-white" title="Ön onayda revize istendi (yönetim)">
            ⚑✎ {row.pre_revision_count}
          </Badge>
        )}
        {row.upload_approved_count > 0 && (
          <Badge className="bg-emerald-600 text-white" title="Müşteri onayladı (onay sayfasında)">
            ✓ {row.upload_approved_count}
          </Badge>
        )}
        {row.upload_revision_count > 0 && (
          <Badge className="bg-orange-500 text-white" title="Müşteri revize istedi (onay sayfasında)">
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
        {/* Video yüklemeleri (2026-08-05): tasarımcı artık video da yüklüyor, yüklediği
            kendi board'unda görünmeli. Kart yönetim board'undakiyle aynı bileşen —
            veri `_build_rows`'tan zaten geliyordu, yalnız burada render edilmiyordu. */}
        {(row.video_uploads ?? []).map((vu) => (
          <VideoUploadCard key={vu.id} vu={vu} className="w-24" />
        ))}
        {row.shares.length === 0 && row.special_days.length === 0
          && (row.video_uploads ?? []).length === 0 && (
          <span className="text-sm text-muted-foreground">Henüz paylaşım yok.</span>
        )}
        {row.shares.map((s) => {
          const revising = row.revision_share_ids.includes(s.id)
          return (
            <div key={s.id} className="w-24 overflow-hidden rounded border bg-card" title={s.caption_text || ""}>
              <div className={cn("px-1 py-0.5 text-[9px] font-semibold",
                revising ? "bg-red-500/15 text-red-600"
                  : s.status === "published" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                    : "bg-muted text-muted-foreground")}>
                {revising ? "🔁 REVİZE" : s.status === "published" ? "YAYIN" : "TASLAK"}
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
          <Upload className="mr-1 h-3.5 w-3.5" /> Yükle
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setPhotosOpen(true)}>
          <Images className="mr-1 h-3.5 w-3.5" /> Çekim Fotoğrafları
        </Button>
        <Button variant="ghost" size="sm" onClick={copyDrive} disabled={!row.drive_folder_url}
          title={row.drive_folder_url ? "Haftanın Drive klasörünü kopyala" : "Bu hafta için Drive klasörü yok"}>
          <Copy className="mr-1 h-3.5 w-3.5" /> Drive
        </Button>
        <Button variant="ghost" size="sm" onClick={copyPreApproval} disabled={preLink.isPending}>
          <ShieldCheck className="mr-1 h-3.5 w-3.5" /> Ön-Onay
        </Button>
        {/* Eski otomatik kapsamlı "Onay Linki"nin yerini aldı (proje sahibi 2026-08-06):
            o, haftanın uygun her yüklemesini gönderiyordu; bu üç haftalık
            pencereden ELLE seçtirir. Ön-Onay iç kapısı yerinde kaldı. */}
        <Button variant="ghost" size="sm" onClick={() => setApprovalOpen(true)}>
          <MailCheck className="mr-1 h-3.5 w-3.5" /> Onay Linki
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
            <h1 className="text-2xl font-semibold tracking-tight">Tasarım</h1>
            <p className="text-muted-foreground">Müşterilerin ve içerik yükleme.</p>
          </div>
          {/* Müşteri logolarının ortak Drive klasörü (2026-07-31, proje sahibi isteği).
              Panelde karşılığı yok — doğrudan Drive'a çıkar, yeni sekmede.
              `Button` `asChild` desteklemiyor (base-ui varyantı) → link elle stillendi. */}
          <a href={LOGOLAR_DRIVE_URL} target="_blank" rel="noreferrer"
            title="Müşteri logoları klasörünü Drive'da aç"
            className="inline-flex h-9 shrink-0 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted">
            <Shapes className="mr-1.5 h-4 w-4" /> Logolar
          </a>
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
          <Button variant="outline" size="sm" className="ml-1" onClick={() => setWeek(currentWeekIso())}>Bugün</Button>
        </div>
      </div>

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder="Müşteri ara…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      {isLoading && (
        <div className="grid gap-3 md:grid-cols-2">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-52 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">Board yüklenemedi.</p>}
      {data && rows.length === 0 && (
        <p className="py-8 text-center text-muted-foreground">
          {q ? "Eşleşen müşteri yok." : "Müşteri yok."}
        </p>
      )}

      {mine.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground">Müşterilerim · {mine.length}</h2>
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
            Diğer Müşteriler · {others.length}
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
