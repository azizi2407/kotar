// Müşteri medya sayfası (2026-08-04) — /designer/musteri/:id.
//
// Tasarım board'unda müşteri adına tıklayınca açılır. İki işi var: müşterinin
// özet bilgilerini tek yerde göstermek ve — asıl sebep — müşteriye YÜKLENMİŞ tüm
// görsel/videoyu hafta hafta gösterip haftalar arasında ELLE taşımaya izin vermek.
//
// Taşıma iki yoldan yapılır: kartı başka hafta bloğuna sürükle-bırak (@dnd-kit),
// veya çoklu seçip üstteki şeritten hedef hafta seç. İkincisi dokunmatik/klavye
// için yedek yol — sürükleme her ortamda güvenilir değil.
//
// Rol kapısı ROUTE'tan gelir: `/designer/...` yolu AppLayout'un nav tablosundaki
// `/designer` kaydıyla prefix eşleşir → management + designer. Bu yüzden sayfa
// kendi rol kontrolünü yapmaz (backend uçları ayrıca zorlar).
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

const CATEGORY_LABELS: Record<string, string> = {
  post: "Post", story: "Story", video: "Video", linkedin: "LinkedIn",
}

function isVideo(up: Upload) {
  return up.category === "video" || Boolean(up.mime_type?.startsWith("video/"))
}

// İmleç neyin üstündeyse önce onu seç; boşa düşerse rect kesişimine düş.
// (VideographerBoardPage Kanban'ıyla aynı gerekçe: küçük kartı bloğa bırakırken
// closestCorners komşu kartı seçip drop'u yutuyor.)
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args)
  return hits.length ? hits : rectIntersection(args)
}

// --- medya kartı ---

function MediaCard({ up, selected, onToggle, onOpen }: {
  up: Upload
  selected: boolean
  onToggle: (e: React.MouseEvent) => void
  onOpen: () => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: up.id })
  const video = isVideo(up)

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
        <span>{CATEGORY_LABELS[up.category ?? ""] ?? up.category ?? "—"}</span>
        {up.used && <span title="Bu dosya için paylaşım kartı açılmış">✓</span>}
      </div>

      <div className="relative">
        {up.file_id ? (
          // `w=800`: kart artık ekran genişliğine göre esniyor (geniş ekranda
          // ~340 px) — 240 px'lik küçük resim bulanık kalıyordu.
          <img src={thumbnailUrl(up.file_id, 800)} alt={up.file_name ?? ""} loading="lazy"
            className="aspect-[4/5] w-full bg-muted object-contain"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }} />
        ) : (
          <div className="flex aspect-[4/5] w-full items-center justify-center text-xs text-muted-foreground">
            görsel yok
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
        {/* Büyütme AYRI düğme: karta tıklamak seçim yapıyor, çift-tık ile
            çakışmasın diye lightbox kendi tetikleyicisine alındı. */}
        <button type="button" title="Büyüt"
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
          title={`${up.moved_from_week_iso} haftasından taşındı`}>
          ↪ {up.moved_from_week_iso}
        </div>
      )}
    </div>
  )
}

// --- hafta bloğu (drop hedefi) ---

function WeekBlock({ week, selected, onToggle, onOpen, onUpload, isCurrent }: {
  week: ClientMediaWeek
  selected: Set<number>
  onToggle: (up: Upload, e: React.MouseEvent) => void
  onOpen: (up: Upload) => void
  onUpload: (weekIso: string) => void
  isCurrent: boolean
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `week:${week.week_iso}` })

  return (
    <section ref={setNodeRef}
      className={cn("rounded-lg border transition",
        isOver && "border-primary ring-2 ring-primary/40",
        isCurrent && !isOver && "border-primary/40")}>
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        <span className="font-medium">{week.week_iso}</span>
        <span className="text-xs text-muted-foreground">{weekRangeLabel(week.week_iso)}</span>
        {isCurrent && <Badge className="bg-primary text-primary-foreground">Bu hafta</Badge>}
        <Badge variant="outline" className="text-muted-foreground">{week.uploads.length} dosya</Badge>
        <Button variant="ghost" size="sm" className="ml-auto"
          onClick={() => onUpload(week.week_iso)}>
          <UploadIcon className="mr-1 h-3.5 w-3.5" /> Yükle
        </Button>
      </div>

      {/* Sabit genişlikli kart yerine esnek ızgara: geniş ekranda kart ~340 px'e
          çıkar, tasarımcı görseli ayrı ayrı açmadan değerlendirebilsin. */}
      <div className="p-3">
        {week.uploads.length === 0 ? (
          <p className={cn("py-3 text-sm", isOver ? "text-primary" : "text-muted-foreground")}>
            {isOver ? "Buraya bırak" : "Bu haftada dosya yok — buraya sürükleyebilirsin."}
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

// --- çekim fotoğrafları (salt okunur; hafta kavramı yok → taşınamaz) ---

function ShootPhotosBlock({ clientId }: { clientId: number }) {
  const { data, isLoading, isError } = useVgPhotos(clientId)
  const list = useMemo(
    () => (data ?? []).slice().sort((a, b) => (b.shoot_date ?? "").localeCompare(a.shoot_date ?? "")),
    [data])

  if (isLoading) return <Skeleton className="h-28 w-full" />
  if (isError) return <p className="text-sm text-destructive">Çekim fotoğrafları yüklenemedi.</p>
  if (!list.length) return <p className="text-sm text-muted-foreground">Çekim fotoğrafı yok.</p>

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Çekim fotoğrafları çekim tarihine bağlıdır, haftaya taşınmaz.
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
                görsel yok
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

// --- logo önizlemesi ---

// Başlıktaki logo (2026-08-04). `LogoButton` logoyu İNDİRİR, göstermez — tasarımcı
// doğru müşteride olduğunu görsel olarak da anlasın diye küçük önizleme. Görsel
// `thumbnailUrl` üzerinden gelir: asset'ler arasında PDF/SVG logolar da var, Drive
// bunlara da küçük resim üretir (asset download ucu `as_attachment`, <img>'e uygun değil).
// Logo slotu boş olan müşterilerde (ör. Çimsan–Tohum Gübre: iki marka, logo bilerek
// boş) ilk standart görsele düşülür; hiç asset yoksa hiçbir şey render edilmez.
function ClientLogo({ clientId }: { clientId: number }) {
  const { data: assets } = useClientAssets(clientId)
  const logo = assets?.find((a) => a.kind === "logo") ?? assets?.[0]
  if (!logo) return null
  return (
    <button type="button" onClick={() => downloadClientAsset(clientId, logo.id)}
      title={logo.file_name ?? "Logoyu indir"}
      className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-background p-1 transition-colors hover:border-primary/50">
      <img src={thumbnailUrl(logo.file_id, 200)} alt="Müşteri logosu"
        className="max-h-full max-w-full object-contain"
        onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none" }} />
    </button>
  )
}

// --- müşteri özeti ---

function ClientSummary({ clientId }: { clientId: number }) {
  const { data: c } = useClient(clientId)
  const { data: users } = useUsers()
  const { isManagement } = useAuth()
  if (!c) return <Skeleton className="h-20 w-full" />

  const nameOf = (sub: string) => users?.find((u) => u.sub === sub)?.name ?? sub
  const team = ROLE_SLOTS
    .map((s) => ({ label: s.label, sub: c.team_assignments?.[s.key] }))
    .filter((t) => t.sub)

  return (
    <div className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <div className="text-xs text-muted-foreground">Sektör</div>
        <div className="text-sm">{c.sector || "—"}</div>
      </div>
      <div>
        <div className="text-xs text-muted-foreground">Brief</div>
        <div className="text-sm">{c.brief_enabled ? "Açık" : "Kapalı"}</div>
      </div>
      <div>
        <div className="text-xs text-muted-foreground">Ekip</div>
        <div className="space-y-0.5 text-sm">
          {team.length === 0 ? "—" : team.map((t) => (
            <div key={t.label} className="truncate">
              <span className="text-muted-foreground">{t.label}:</span> {nameOf(t.sub!)}
            </div>
          ))}
        </div>
      </div>
      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">Bağlantılar</div>
        <div className="flex flex-wrap gap-2 text-sm">
          {/* Marka Rehberi HER üretim rolüne açık (nav: management/designer/
              content_creator/videographer) — tasarımcı ve videograf marka sesi,
              yasaklar ve içerik kurallarına buradan tek tıkla ulaşsın.
              `?client=` derin linki için MarkaRehberiPage seçimi URL'de tutuyor. */}
          <Link to={`/marka-rehberi?client=${clientId}`}
            className="inline-flex items-center gap-1 text-primary hover:underline">
            <BookOpen className="h-3.5 w-3.5" /> Marka Rehberi
          </Link>
          {c.instagram_url && (
            <a href={c.instagram_url} target="_blank" rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> Instagram
            </a>
          )}
          {c.google_drive_url && (
            <a href={c.google_drive_url} target="_blank" rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> Drive
            </a>
          )}
          {/* Tam detay yalnız management'ta: sözleşme/iletişim alanları zaten
              backend'de rol filtresinden geçiyor (api._client_json). */}
          {isManagement && (
            <Link to={`/clients/${clientId}`}
              className="inline-flex items-center gap-1 text-primary hover:underline">
              <ExternalLink className="h-3.5 w-3.5" /> Müşteri detayı
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}

// --- lightbox ---

function MediaLightbox({ up, onClose }: { up: Upload | null; onClose: () => void }) {
  if (!up?.file_id) return null
  const video = isVideo(up)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="truncate">{up.file_name ?? "Önizleme"}</DialogTitle>
        </DialogHeader>
        {video ? (
          up.local ? (
            <video controls src={mediaUrl(up.file_id)} className="max-h-[70vh] w-full rounded" />
          ) : (
            // Lokal kopya penceresi (21 gün) dolmuş → Drive'ın gömülü oynatıcısı.
            <iframe src={drivePreviewUrl(up.file_id)} allow="autoplay"
              className="h-[60vh] w-full rounded border" />
          )
        ) : (
          <img src={mediaUrl(up.file_id)} alt={up.file_name ?? ""}
            className="max-h-[70vh] w-full rounded object-contain"
            onError={(e) => {
              // Lokal orijinal yoksa büyük küçük-resme düş (Drive thumbnail).
              (e.currentTarget as HTMLImageElement).src = thumbnailUrl(up.file_id!, 1200)
            }} />
        )}
        <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
          <span>{up.week_iso}{up.moved_from_week_iso ? ` · ↪ ${up.moved_from_week_iso}` : ""}</span>
          <a href={downloadMediaUrl(up.file_id, up.file_name ?? undefined)}
            className="inline-flex items-center gap-1 text-primary hover:underline">
            <Download className="h-4 w-4" /> Tam boyutu indir
          </a>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// --- sayfa ---

export function ClientMediaPage() {
  const { id } = useParams()
  const clientId = Number(id)
  const [params] = useSearchParams()
  // Geldiğimiz hafta (board'un hafta ekseni) — geri linkinde korunur.
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
  const clientName = client?.name ?? "Müşteri"
  const totalFiles = useMemo(
    () => (weeks ?? []).reduce((s, w) => s + w.uploads.length, 0), [weeks])

  function toggle(up: Upload, e: React.MouseEvent) {
    // Sürükleme bittikten sonra tarayıcı aynı kartta click de üretebilir (kısa
    // mesafeli sürüklemede pointer karttan çıkmaz) — o click seçimi ters çevirip
    // "sürükledim, seçim değişti" sürprizi yaratırdı. Drag turunu yut.
    if (suppressClick.current) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (e.shiftKey) {
        // Shift: aynı hafta bloğundaki komşu aralığı topluca seç.
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
      if (r.moved) toast.success(`${r.moved} dosya ${target} haftasına taşındı`)
      // Kısmi başarı: taşınamayanlar ayrı bildirilir, taşınanlar yerinde kalır.
      if (r.errors?.length) toast.error(`${r.errors.length} dosya taşınamadı — ${r.errors[0]}`)
      setSelected(new Set())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Taşıma başarısız")
    }
  }

  function endDrag() {
    setActiveId(null)
    suppressClick.current = true
    // Sürükleme sonrası click aynı turda gelir; bir tur sonra kilidi aç.
    setTimeout(() => { suppressClick.current = false }, 0)
  }

  function onDragEnd(e: DragEndEvent) {
    // `activeId` state'i değil `e.active.id` kaynak alınır — state okuması
    // render turuna bağlı, event yükü değil.
    const dragged = Number(e.active.id)
    endDrag()
    if (!e.over || !Number.isFinite(dragged)) return
    const overId = String(e.over.id)
    if (!overId.startsWith("week:")) return
    // Sürüklenen kart seçimin parçasıysa TÜM seçim taşınır; değilse yalnız o kart.
    const ids = selected.has(dragged) ? [...selected] : [dragged]
    moveTo(overId.slice("week:".length), ids)
  }

  const activeUpload = activeId != null ? byId.get(activeId) : undefined
  const dragCount = activeId != null && selected.has(activeId) ? selected.size : 1

  if (!Number.isFinite(clientId)) {
    return <p className="py-8 text-center text-destructive">Geçersiz müşteri.</p>
  }

  return (
    <div className="space-y-5">
      {/* başlık + aksiyonlar */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <Link to={`/designer?week=${encodeURIComponent(fromWeek)}`}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" /> Tasarım
          </Link>
          <div className="flex items-center gap-3">
            <ClientLogo clientId={clientId} />
            <h1 className="text-2xl font-semibold tracking-tight">{clientName}</h1>
          </div>
          <p className="text-muted-foreground">
            {totalFiles} dosya · {(weeks ?? []).filter((w) => w.uploads.length).length} hafta
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button variant="outline" size="sm" onClick={() => setUploadWeek(fromWeek)}>
            <UploadIcon className="mr-1 h-3.5 w-3.5" /> Yükle
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setBriefOpen(true)}>
            <FileText className="mr-1 h-3.5 w-3.5" /> Brief
          </Button>
          <LogoButton clientId={clientId} />
          <Button variant="ghost" size="sm" onClick={() => setSummaryOpen((o) => !o)}>
            <ChevronDown className={cn("mr-1 h-3.5 w-3.5 transition-transform", !summaryOpen && "-rotate-90")} />
            Bilgiler
          </Button>
        </div>
      </div>

      {summaryOpen && <ClientSummary clientId={clientId} />}

      {/* İlgili fontlar (2026-08-05) — havuzun bu müşteriye süzülmüş hâli;
          önizleme metni müşterinin adı. Bilgiler bloğundan bağımsız, hep açık
          gelir: tasarımcı üretime başlarken ilk baktığı şeylerden biri. */}
      <ClientFonts clientId={clientId} clientName={clientName} />

      {/* Çalışma dosyaları (2026-08-07) — .psd/.ai gibi kaynak dosyalar, sürümlü.
          Fontların hemen altında: ikisi de "üretime başlarken elinin altında
          olması gereken malzeme"; haftalık teslim ızgarasının üstünde durur. */}
      <ClientDesignFiles clientId={clientId} />

      {/* seçim şeridi — sürükleme yerine hedef hafta menüsü (dokunmatik/klavye yolu) */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-lg border bg-background/95 p-2 shadow-sm backdrop-blur">
          <span className="text-sm font-medium">{selected.size} seçili</span>
          <DropdownMenu>
            <DropdownMenuTrigger
              disabled={move.isPending}
              className="inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted disabled:opacity-50">
              <MoveRight className="mr-1 h-3.5 w-3.5" />
              {move.isPending ? "Taşınıyor…" : "Haftaya taşı"}
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
            Seçimi temizle
          </Button>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            Kartları başka hafta bloğuna sürükleyerek de taşıyabilirsin.
          </span>
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-40 w-full" />)}
        </div>
      )}
      {isError && <p className="text-destructive">Medya listesi yüklenemedi.</p>}

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
              // Sürükleme önizlemesi bilerek karttan KÜÇÜK: imlecin altında
              // ekranı kapatmasın, altındaki drop hedefi görünür kalsın.
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

      {/* çekim fotoğrafları — kapalıyken sorgu hiç koşmasın diye koşullu mount */}
      <section className="space-y-2">
        <button onClick={() => setPhotosOpen((o) => !o)}
          className="flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("h-4 w-4 transition-transform", !photosOpen && "-rotate-90")} />
          <Camera className="h-4 w-4" /> Çekim Fotoğrafları
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
