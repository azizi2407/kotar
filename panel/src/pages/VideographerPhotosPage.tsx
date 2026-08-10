// Videografçı Fotoğraf Kütüphanesi — müşteri-merkezli master-detail. Sol: müşteri
// listesi (foto sayısı + son çekim). Sağ: seçili müşterinin fotoğrafları çekim
// tarihine göre gruplu galeri + sürükle-bırak yükleme (staging önizlemeli), çoklu
// seçim (toplu sil/indir), lightbox ve "designer kullandı mı" rozeti/filtresi.
import { useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  Check, ChevronLeft, ChevronRight, Download, Link2, MoreVertical, Pencil, Trash2, Upload, X,
} from "lucide-react"
import { toast } from "sonner"

import {
  downloadVgPhoto, downloadVgPhotosZip, publicMediaUrl, thumbnailUrl,
  useBulkDeleteVgPhotos, useDeleteVgPhoto, useMarkVgPhotoUsed, useRenameVgPhoto,
  useUploadVgPhotos, useVgPhotos, type VgPhoto,
} from "@/lib/sharing"
import { acceptsFile, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "@/lib/api"
import { useClients } from "@/lib/clients"
import { useAuth } from "@/lib/auth"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const TR_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]

function fmtDate(d: string | null): string {
  if (!d) return "Tarih yok"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d)
  return m ? `${Number(m[3])} ${TR_MONTHS[Number(m[2]) - 1]} ${m[1]}` : d
}

function fmtSize(b: number | null): string {
  if (!b) return ""
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

type Filter = "all" | "used" | "unused"

export function VideographerPhotosPage() {
  const [params, setParams] = useSearchParams()
  const { user } = useAuth()
  const canMarkUsed = user?.role === "management" || user?.role === "designer"

  const { data: photos, isLoading, isError } = useVgPhotos()
  const { data: clients } = useClients({ status: "active", q: "" })
  const upload = useUploadVgPhotos()
  const bulkDelete = useBulkDeleteVgPhotos()
  const markUsed = useMarkVgPhotoUsed()
  const delPhoto = useDeleteVgPhoto()
  const rename = useRenameVgPhoto()

  const [clientSearch, setClientSearch] = useState("")
  const [filter, setFilter] = useState<Filter>("all")
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [lastIdx, setLastIdx] = useState<number | null>(null)
  const [lightbox, setLightbox] = useState<number | null>(null)
  const [renaming, setRenaming] = useState<VgPhoto | null>(null)

  const selectedClientId = params.get("client") ? Number(params.get("client")) : null
  const setSelectedClient = (id: number) =>
    setParams((p) => { p.set("client", String(id)); return p }, { replace: true })

  // Müşteri başına foto sayısı + son çekim tarihi
  const perClient = useMemo(() => {
    const map = new Map<number, { count: number; last: string | null }>()
    for (const p of photos ?? []) {
      const cur = map.get(p.client_id) ?? { count: 0, last: null }
      cur.count += 1
      if (p.shoot_date && (!cur.last || p.shoot_date > cur.last)) cur.last = p.shoot_date
      map.set(p.client_id, cur)
    }
    return map
  }, [photos])

  // Sol liste: fotoğrafı olanlar üstte (foto sayısı azalan), sonra diğerleri; aramayla süz.
  const clientRows = useMemo(() => {
    const nq = trFold(clientSearch.trim())
    const list = (clients ?? [])
      .filter((c) => !nq || trFold(c.name).includes(nq))
      .map((c) => ({ id: c.id, name: c.name, ...(perClient.get(c.id) ?? { count: 0, last: null }) }))
    return list.sort((a, b) => {
      if ((b.count > 0 ? 1 : 0) !== (a.count > 0 ? 1 : 0)) return (b.count > 0 ? 1 : 0) - (a.count > 0 ? 1 : 0)
      if (b.count !== a.count) return b.count - a.count
      return a.name.localeCompare(b.name, "tr")
    })
  }, [clients, clientSearch, perClient])

  // Seçili müşterinin TÜM fotoğrafları (filtreden bağımsız) — kullanım özeti için.
  const clientPhotos = useMemo(
    () => (photos ?? []).filter((p) => p.client_id === selectedClientId), [photos, selectedClientId])
  const usedCount = clientPhotos.filter((p) => p.used).length

  // Seçili müşterinin görünür (filtreli) fotoğrafları, çekim tarihi azalan
  const visible = useMemo(() => {
    let list = (photos ?? []).filter((p) => p.client_id === selectedClientId)
    if (filter === "used") list = list.filter((p) => p.used)
    else if (filter === "unused") list = list.filter((p) => !p.used)
    return list.sort((a, b) => (b.shoot_date ?? "").localeCompare(a.shoot_date ?? ""))
  }, [photos, selectedClientId, filter])

  // Tarihe göre gruplar (boş tarih sona)
  const groups = useMemo(() => {
    const map = new Map<string, VgPhoto[]>()
    for (const p of visible) {
      const key = p.shoot_date ?? ""
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(p)
    }
    return [...map.entries()].sort((a, b) => {
      if (!a[0]) return 1
      if (!b[0]) return -1
      return b[0].localeCompare(a[0])
    })
  }, [visible])

  // Müşteri/filtre değişince seçim + lightbox sıfırlansın
  useEffect(() => { setSelected(new Set()); setLastIdx(null); setLightbox(null) }, [selectedClientId, filter])

  const clientName = clientRows.find((c) => c.id === selectedClientId)?.name

  function toggleSelect(idx: number, shift: boolean) {
    const id = visible[idx].id
    setSelected((prev) => {
      const next = new Set(prev)
      if (shift && lastIdx !== null) {
        const [lo, hi] = lastIdx < idx ? [lastIdx, idx] : [idx, lastIdx]
        for (let i = lo; i <= hi; i++) next.add(visible[i].id)
      } else if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setLastIdx(idx)
  }

  function doBulkDelete() {
    const ids = [...selected]
    if (!ids.length) return
    bulkDelete.mutate(ids, {
      onSuccess: (r) => {
        if (r.errors?.length) toast.error(r.errors.join(", "))
        if (r.deleted) toast.success(`${r.deleted} fotoğraf silindi`)
        setSelected(new Set())
      },
      onError: (e) => toast.error(e.message),
    })
  }

  async function doBulkDownload() {
    const ids = [...selected]
    if (!ids.length) return
    try {
      await downloadVgPhotosZip(ids)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "İndirilemedi")
    }
  }

  function doDelete(id: number) {
    delPhoto.mutate(id, {
      onSuccess: () => {
        toast.success("Fotoğraf silindi")
        setSelected((prev) => { const n = new Set(prev); n.delete(id); return n })
        setLightbox(null)
      },
      onError: (e) => toast.error(e.message),
    })
  }

  function doRename(id: number, name: string) {
    rename.mutate({ id, name }, {
      onSuccess: () => { toast.success("Yeniden adlandırıldı"); setRenaming(null) },
      onError: (e) => toast.error(e.message),
    })
  }

  if (isError) return <p className="text-destructive">Fotoğraflar yüklenemedi.</p>

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Fotoğraflar</h1>
        <p className="text-muted-foreground">Çekim fotoğrafları — müşteriye göre kütüphane.</p>
      </div>

      <div className="grid gap-4 md:grid-cols-[260px_1fr]">
        {/* Sol: müşteri listesi */}
        <aside className="space-y-2">
          <Input placeholder="Müşteri ara…" value={clientSearch}
            onChange={(e) => setClientSearch(e.target.value)} className="h-9" />
          <div className="max-h-[70vh] overflow-y-auto rounded-lg border">
            {isLoading ? (
              <div className="space-y-1 p-2">{[...Array(6)].map((_, i) => <Skeleton key={i} className="h-11 w-full" />)}</div>
            ) : clientRows.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">Müşteri yok.</p>
            ) : (
              <ul className="divide-y">
                {clientRows.map((c) => (
                  <li key={c.id}>
                    <button onClick={() => setSelectedClient(c.id)}
                      className={cn("flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/50",
                        c.id === selectedClientId && "bg-muted", c.count === 0 && "text-muted-foreground")}>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{c.name}</span>
                        {c.last && <span className="text-[11px] text-muted-foreground">son: {fmtDate(c.last)}</span>}
                      </span>
                      {c.count > 0 && (
                        <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                          {c.count}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Sağ: galeri */}
        <section className="min-w-0 space-y-4">
          {selectedClientId === null ? (
            <div className="flex h-64 items-center justify-center rounded-lg border border-dashed text-muted-foreground">
              Soldan bir müşteri seç.
            </div>
          ) : (
            <>
              <Uploader clientId={selectedClientId} upload={upload} />

              {/* Kullanım özeti — designer kaç fotoğraf kullandı / kaç kaldı */}
              {clientPhotos.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="rounded-full bg-muted px-2 py-0.5">Toplam {clientPhotos.length}</span>
                  <span className="rounded-full bg-emerald-600/15 px-2 py-0.5 text-emerald-700 dark:text-emerald-400">
                    Kullanıldı {usedCount}
                  </span>
                  <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-amber-700 dark:text-amber-400">
                    Kalan {clientPhotos.length - usedCount}
                  </span>
                </div>
              )}

              {/* Filtre + toplu aksiyon şeridi */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex gap-1 rounded-lg border p-1 text-sm">
                  {(["all", "unused", "used"] as Filter[]).map((f) => (
                    <button key={f} onClick={() => setFilter(f)}
                      className={cn("rounded-md px-2.5 py-1 transition-colors",
                        filter === f ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
                      {f === "all" ? "Hepsi" : f === "used" ? "Kullanılan" : "Kullanılmayan"}
                    </button>
                  ))}
                </div>
                {selected.size > 0 && (
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">{selected.size} seçili</span>
                    <Button size="sm" variant="outline" onClick={doBulkDownload}>
                      <Download className="mr-1 h-4 w-4" /> İndir
                    </Button>
                    <Button size="sm" variant="destructive" onClick={doBulkDelete} disabled={bulkDelete.isPending}>
                      <Trash2 className="mr-1 h-4 w-4" /> Sil
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                )}
              </div>

              {isLoading ? (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                  {[...Array(8)].map((_, i) => <Skeleton key={i} className="aspect-square w-full" />)}
                </div>
              ) : visible.length === 0 ? (
                <p className="text-muted-foreground">
                  {clientName ? `${clientName} için bu filtrede fotoğraf yok.` : "Fotoğraf yok."}
                </p>
              ) : (
                groups.map(([dateKey, items]) => (
                  <div key={dateKey || "nodate"} className="space-y-2">
                    <h3 className="text-sm font-medium text-muted-foreground">
                      {fmtDate(dateKey || null)} · {items.length} foto
                    </h3>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                      {items.map((p) => {
                        const idx = visible.indexOf(p)
                        const isSel = selected.has(p.id)
                        return (
                          <PhotoCard key={p.id} photo={p} selected={isSel}
                            onOpen={() => setLightbox(idx)}
                            onToggle={(shift) => toggleSelect(idx, shift)}
                            canMarkUsed={canMarkUsed}
                            onMarkUsed={(used) => markUsed.mutate({ id: p.id, used },
                              { onError: (e) => toast.error(e.message) })}
                            onDownload={() => downloadVgPhoto(p.id)}
                            onRename={() => setRenaming(p)}
                            onDelete={() => doDelete(p.id)} />
                        )
                      })}
                    </div>
                  </div>
                ))
              )}
            </>
          )}
        </section>
      </div>

      {lightbox !== null && visible[lightbox] && (
        <Lightbox photos={visible} index={lightbox} onClose={() => setLightbox(null)}
          onNav={(d) => setLightbox((i) => {
            if (i === null) return null
            const n = i + d
            return n >= 0 && n < visible.length ? n : i
          })}
          onDownload={(id) => downloadVgPhoto(id)}
          onRename={(p) => setRenaming(p)}
          onDelete={(id) => doDelete(id)} />
      )}

      <RenameDialog photo={renaming} pending={rename.isPending}
        onClose={() => setRenaming(null)} onSave={doRename} />
    </div>
  )
}

// Yeniden adlandırma dialog'u — uzantı backend'de korunur; kullanıcı sadece adı girer.
function RenameDialog({ photo, pending, onClose, onSave }: {
  photo: VgPhoto | null
  pending: boolean
  onClose: () => void
  onSave: (id: number, name: string) => void
}) {
  const [name, setName] = useState("")
  useEffect(() => { setName(photo?.file_name ?? "") }, [photo])
  return (
    <Dialog open={photo !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>Yeniden adlandır</DialogTitle></DialogHeader>
        <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter" && photo && name.trim()) onSave(photo.id, name.trim()) }} />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>İptal</Button>
          <Button disabled={pending || !name.trim()}
            onClick={() => photo && onSave(photo.id, name.trim())}>Kaydet</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Sürükle-bırak yükleme — staging önizlemeli. Dosyalar önce sahnelenir, "Yükle" ile
// gönderilir; yüklemeden önce küçük önizleme + dosya başına toplam ilerleme.
function Uploader({ clientId, upload }: {
  clientId: number
  upload: ReturnType<typeof useUploadVgPhotos>
}) {
  const [staged, setStaged] = useState<File[]>([])
  const [shootDate, setShootDate] = useState("")
  const [pct, setPct] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const previews = useMemo(
    () => staged.map((f) => ({ name: f.name, url: URL.createObjectURL(f) })), [staged])
  useEffect(() => () => previews.forEach((p) => URL.revokeObjectURL(p.url)), [previews])

  function addFiles(files: File[]) {
    let imgs = files.filter((f) => acceptsFile(f, "image"))
    if (imgs.length < files.length) toast.error(`${files.length - imgs.length} dosya eklenmedi (görsel değil)`)
    const tooBig = imgs.filter((f) => f.size > MAX_UPLOAD_BYTES)
    if (tooBig.length) toast.error(`${tooBig.length} dosya ${MAX_UPLOAD_MB} MB sınırını aşıyor, eklenmedi`)
    imgs = imgs.filter((f) => f.size <= MAX_UPLOAD_BYTES)
    if (imgs.length) setStaged((s) => [...s, ...imgs])
  }

  function doUpload() {
    if (!staged.length) return
    setPct(0)
    upload.mutate(
      { client_id: clientId, shoot_date: shootDate || undefined, files: staged, onProgress: setPct },
      {
        onSuccess: (r: { saved: unknown[]; errors: string[] }) => {
          if (r.errors?.length) toast.error(r.errors.join(", "))
          if (r.saved?.length) toast.success(`${r.saved.length} fotoğraf yüklendi`)
          setStaged([])
          setPct(0)
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : "Yükleme başarısız"),
      },
    )
  }

  return (
    <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(Array.from(e.dataTransfer.files)) }}
        onClick={() => fileInput.current?.click()}
        className={cn("flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed py-6 text-center text-sm transition-colors",
          dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/40")}>
        <Upload className="mb-1 h-5 w-5 text-muted-foreground" />
        <span className="text-muted-foreground">Fotoğrafları buraya sürükle ya da <span className="text-primary">seç</span></span>
        <input ref={fileInput} type="file" accept="image/*" multiple className="hidden"
          onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = "" }} />
      </div>

      {staged.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2">
            {previews.map((p, i) => (
              <div key={i} className="relative h-16 w-16 overflow-hidden rounded-md border">
                <img src={p.url} alt={p.name} className="h-full w-full object-cover" />
                <button onClick={() => setStaged((s) => s.filter((_, j) => j !== i))}
                  className="absolute right-0.5 top-0.5 rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80">
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Çekim tarihi (ops.)</label>
              <Input type="date" value={shootDate} onChange={(e) => setShootDate(e.target.value)} className="h-9 w-40" />
            </div>
            <Button onClick={doUpload} disabled={upload.isPending}>
              <Upload className="mr-1 h-4 w-4" />
              {upload.isPending ? `Yükleniyor… %${pct}` : `${staged.length} fotoğrafı yükle`}
            </Button>
            <Button variant="ghost" onClick={() => setStaged([])} disabled={upload.isPending}>Temizle</Button>
          </div>
        </>
      )}
    </div>
  )
}

// Fotoğrafın KENDİSİNE giden kalıcı bağlantıyı panoya alır (2026-07-30). Sunucudaki
// 21 günlük kopyadan servis edilir; süresi dolunca uç Drive'a 302 yönlendirir, yani
// kopyalanmış link ölmez. Panel oturumu gerektirmez (bkz. public_media.py).
async function copyPhotoLink(photo: VgPhoto) {
  if (!photo.file_id) {
    toast.error("Bu fotoğrafın dosya kimliği yok")
    return
  }
  try {
    await navigator.clipboard.writeText(publicMediaUrl(photo.file_id))
    toast.success("Doğrudan bağlantı kopyalandı")
  } catch {
    toast.error("Kopyalanamadı — tarayıcı izin vermedi")
  }
}

function PhotoCard({ photo, selected, onOpen, onToggle, canMarkUsed, onMarkUsed, onDownload, onRename, onDelete }: {
  photo: VgPhoto
  selected: boolean
  onOpen: () => void
  onToggle: (shift: boolean) => void
  canMarkUsed: boolean
  onMarkUsed: (used: boolean) => void
  onDownload: () => void
  onRename: () => void
  onDelete: () => void
}) {
  return (
    <div className={cn("group relative overflow-hidden rounded-lg border bg-muted/20 transition-shadow",
      selected && "ring-2 ring-primary")}>
      {/* seçim kutusu */}
      <button onClick={(e) => onToggle(e.shiftKey)}
        className={cn("absolute left-1.5 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded border bg-background/80 transition",
          selected ? "border-primary bg-primary text-primary-foreground opacity-100" : "opacity-0 group-hover:opacity-100")}>
        {selected && <Check className="h-3.5 w-3.5" />}
      </button>

      <button onClick={onOpen} className="block w-full">
        {photo.file_id ? (
          <img src={thumbnailUrl(photo.file_id, 300)} alt={photo.file_name ?? ""} loading="lazy"
            className="aspect-square w-full object-cover" />
        ) : (
          <div className="flex aspect-square w-full items-center justify-center text-xs text-muted-foreground">görsel yok</div>
        )}
      </button>

      {/* kullanım rozeti */}
      <span className={cn("absolute right-1.5 top-1.5 z-10 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
        photo.used ? "bg-emerald-600 text-white" : "bg-background/80 text-muted-foreground border")}>
        {photo.used ? "Kullanıldı" : "Kullanılmadı"}
      </span>

      <div className="flex items-center justify-between gap-1 px-2 py-1.5">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium">{photo.file_name ?? "—"}</div>
          {photo.file_size ? <div className="text-[11px] text-muted-foreground">{fmtSize(photo.file_size)}</div> : null}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<button className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="İşlemler" />}>
            <MoreVertical className="h-4 w-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onDownload}><Download className="mr-2 h-4 w-4" /> İndir</DropdownMenuItem>
            <DropdownMenuItem onClick={() => copyPhotoLink(photo)}>
              <Link2 className="mr-2 h-4 w-4" /> Bağlantıyı kopyala
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onRename}><Pencil className="mr-2 h-4 w-4" /> Yeniden adlandır</DropdownMenuItem>
            {canMarkUsed && (
              <DropdownMenuItem onClick={() => onMarkUsed(!photo.used)}>
                <Check className="mr-2 h-4 w-4" /> {photo.used ? "Kullanımı geri al" : "Kullanıldı işaretle"}
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onDelete} className="text-destructive focus:text-destructive">
              <Trash2 className="mr-2 h-4 w-4" /> Sil
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

function Lightbox({ photos, index, onClose, onNav, onDownload, onRename, onDelete }: {
  photos: VgPhoto[]
  index: number
  onClose: () => void
  onNav: (dir: number) => void
  onDownload: (id: number) => void
  onRename: (p: VgPhoto) => void
  onDelete: (id: number) => void
}) {
  const p = photos[index]
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
      else if (e.key === "ArrowLeft") onNav(-1)
      else if (e.key === "ArrowRight") onNav(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose, onNav])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4" onClick={onClose}>
      {/* aksiyon çubuğu */}
      <div className="absolute right-4 top-4 z-10 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
        <button className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="İndir" onClick={() => onDownload(p.id)}><Download className="h-5 w-5" /></button>
        <button className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="Doğrudan bağlantıyı kopyala (sunucudan; süresi dolarsa Drive'a yönlenir)"
          onClick={() => copyPhotoLink(p)}><Link2 className="h-5 w-5" /></button>
        <button className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="Yeniden adlandır" onClick={() => onRename(p)}><Pencil className="h-5 w-5" /></button>
        <button className="rounded-full bg-white/10 p-2 text-white hover:bg-red-500/70"
          title="Sil" onClick={() => onDelete(p.id)}><Trash2 className="h-5 w-5" /></button>
        <button className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="Kapat" onClick={onClose}><X className="h-5 w-5" /></button>
      </div>
      {index > 0 && (
        <button className="absolute left-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          onClick={(e) => { e.stopPropagation(); onNav(-1) }}>
          <ChevronLeft className="h-6 w-6" />
        </button>
      )}
      {index < photos.length - 1 && (
        <button className="absolute right-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          onClick={(e) => { e.stopPropagation(); onNav(1) }}>
          <ChevronRight className="h-6 w-6" />
        </button>
      )}
      <div className="flex max-h-full max-w-4xl flex-col items-center gap-3" onClick={(e) => e.stopPropagation()}>
        {p.file_id && (
          <img src={thumbnailUrl(p.file_id, 1200)} alt={p.file_name ?? ""}
            className="max-h-[80vh] w-auto rounded-lg object-contain" />
        )}
        <div className="flex items-center gap-3 text-sm text-white/80">
          <span>{p.file_name}</span>
          {p.shoot_date && <span>· {fmtDate(p.shoot_date)}</span>}
          {p.file_size ? <span>· {fmtSize(p.file_size)}</span> : null}
          <span className={cn("rounded-full px-2 py-0.5 text-[11px]",
            p.used ? "bg-emerald-600 text-white" : "bg-white/15")}>
            {p.used ? "Kullanıldı" : "Kullanılmadı"}
          </span>
        </div>
      </div>
    </div>
  )
}
