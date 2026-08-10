// Tasarımcı içerik yükleme modalı — batch başına tek kategori (Post/Story/LinkedIn),
// çoklu dosya seçimi ve DOSYA BAŞINA ilerleme. Her dosya ayrı apiUpload çağrısıyla
// paralel yüklenir; durum listesi modal içinden izlenir.
import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { AlertCircle, Check, Loader2, Upload, X } from "lucide-react"
import { toast } from "sonner"

import { acceptsFile, ApiError, apiUpload, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

// Video (2026-08-05) tasarımcıya da açık: dosya backend'de videografın yüklediğiyle
// AYNI yoldan geçer (kategori='video' → Drive'da "bağlantıya sahip herkes" izni +
// web türevi transkodu + revize tespiti), ayrı bir akış yok.
const CATEGORIES = [
  { value: "post", label: "Post" },
  { value: "story", label: "Story" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "video", label: "Video" },
]

type Status = "pending" | "uploading" | "done" | "error"
interface Item { file: File; url: string; pct: number; status: Status; error?: string }


export function UploadModal({ open, onClose, clientId, clientName, weekIso, fixedCategory, accept = "image" }: {
  open: boolean
  onClose: () => void
  clientId: number
  clientName: string
  weekIso: string
  // fixedCategory: kategori seçici gizlenir, tüm dosyalar bu kategoriye gider
  // (videografçı akışı: fixedCategory="video" + accept="video").
  fixedCategory?: string
  accept?: "image" | "video"
}) {
  const qc = useQueryClient()
  const [category, setCategory] = useState(fixedCategory ?? "post")
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState(false)
  // Beklenen dosya türü kategoriden türetilir: "Video" seçilince görsel değil video
  // kabul edilir. `accept` prop'u yalnız kategori seçicisi gizliyken (videografçı
  // akışı, fixedCategory) belirleyicidir.
  const kabul: "image" | "video" = category === "video" ? "video" : accept

  // Kategori değişince listede kalan uyumsuz dosyaları at — kullanıcı 3 görsel
  // seçip kategoriyi Video'ya çevirdiğinde onlar sessizce yüklenmemeli.
  function changeCategory(next: string) {
    setCategory(next)
    const nextKabul = next === "video" ? "video" : accept
    setItems((s) => {
      const kalan = s.filter((it) => it.status === "done" || acceptsFile(it.file, nextKabul))
      const atilan = s.length - kalan.length
      if (atilan > 0) {
        s.filter((it) => !kalan.includes(it)).forEach((it) => URL.revokeObjectURL(it.url))
        toast.error(`${atilan} dosya listeden çıkarıldı (kategoriye uygun değil)`)
      }
      return kalan
    })
  }

  function addFiles(files: File[]) {
    let imgs = files.filter((f) => acceptsFile(f, kabul))
    if (imgs.length < files.length) {
      const n = files.length - imgs.length
      toast.error(`${n} dosya eklenmedi (${kabul === "video" ? "video" : "görsel"} değil)`)
    }
    const tooBig = imgs.filter((f) => f.size > MAX_UPLOAD_BYTES)
    if (tooBig.length) toast.error(`${tooBig.length} dosya ${MAX_UPLOAD_MB} MB sınırını aşıyor, eklenmedi`)
    imgs = imgs.filter((f) => f.size <= MAX_UPLOAD_BYTES)
    if (imgs.length) {
      setItems((s) => [
        ...s,
        ...imgs.map((f) => ({ file: f, url: URL.createObjectURL(f), pct: 0, status: "pending" as Status })),
      ])
    }
  }

  function setItem(i: number, patch: Partial<Item>) {
    setItems((s) => s.map((it, j) => (j === i ? { ...it, ...patch } : it)))
  }

  function removeItem(i: number) {
    setItems((s) => {
      const it = s[i]
      if (it) URL.revokeObjectURL(it.url)
      return s.filter((_, j) => j !== i)
    })
  }

  async function uploadOne(i: number, file: File) {
    const form = new FormData()
    form.append("client_id", String(clientId))
    form.append("week_iso", weekIso)
    form.append("category", category)
    form.append("file", file)
    setItem(i, { status: "uploading", pct: 0 })
    try {
      await apiUpload("/sharing/upload", form, (p) => setItem(i, { pct: p }))
      setItem(i, { status: "done", pct: 100 })
      return true
    } catch (err) {
      setItem(i, { status: "error", error: err instanceof ApiError ? err.message : "Yükleme hatası" })
      return false
    }
  }

  async function startUpload() {
    const targets = items
      .map((it, i) => ({ it, i }))
      .filter((x) => x.it.status === "pending" || x.it.status === "error")
    if (!targets.length) return
    setBusy(true)
    const results = await Promise.all(targets.map(({ it, i }) => uploadOne(i, it.file)))
    setBusy(false)
    const ok = results.filter(Boolean).length
    const fail = results.length - ok
    qc.invalidateQueries({ queryKey: ["designer-board"] })
    qc.invalidateQueries({ queryKey: ["videographer-board"] })
    qc.invalidateQueries({ queryKey: ["board"] })
    qc.invalidateQueries({ queryKey: ["drive-counts", weekIso] })
    qc.invalidateQueries({ queryKey: ["uploads", clientId, weekIso] })
    if (ok) toast.success(`${clientName}: ${ok} dosya yüklendi`)
    if (fail) toast.error(`${fail} dosya yüklenemedi`)
  }

  const doneCount = items.filter((i) => i.status === "done").length
  const errCount = items.filter((i) => i.status === "error").length
  const pendingCount = items.filter((i) => i.status === "pending" || i.status === "error").length

  function handleClose() {
    if (busy) return
    items.forEach((it) => URL.revokeObjectURL(it.url))
    setItems([])
    setCategory(fixedCategory ?? "post")
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{clientName} · İçerik Yükle</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          {!fixedCategory && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground">Kategori</span>
              <Select value={category} onValueChange={(v) => v && changeCategory(v)} disabled={busy}>
                <SelectTrigger size="sm" className="w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground">Tüm dosyalar bu kategoriye yüklenir.</span>
            </div>
          )}

          <label
            className="flex w-full cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/25 py-6 text-center text-sm transition-colors hover:border-primary/40">
            <Upload className="mb-1 h-5 w-5 text-muted-foreground" />
            <span className="text-muted-foreground">
              {kabul === "video" ? "Videoları" : "Görselleri"} <span className="text-primary">seç</span> (çoklu)
            </span>
            <input type="file" accept={`${kabul}/*`} multiple className="hidden"
              onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = "" }} />
          </label>

          {items.length > 0 && (
            <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border p-2">
              {items.map((it, i) => (
                <div key={i} className="flex items-center gap-2">
                  {it.file.type.startsWith("video/") ? (
                    <video src={it.url} muted className="h-9 w-9 shrink-0 rounded border object-cover" />
                  ) : (
                    <img src={it.url} alt="" className="h-9 w-9 shrink-0 rounded border object-cover" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium">{it.file.name}</span>
                      <StatusBadge item={it} />
                    </div>
                    <div className={cn("mt-1", it.status === "error" && "opacity-40")}>
                      <Progress value={it.status === "done" ? 100 : it.pct} />
                    </div>
                    {it.status === "error" && (
                      <span className="text-[11px] text-destructive">{it.error}</span>
                    )}
                  </div>
                  {it.status === "pending" && !busy && (
                    <button onClick={() => removeItem(i)}
                      className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {(doneCount > 0 || errCount > 0) && (
            <div className="text-xs">
              {doneCount > 0 && <span className="text-emerald-600">{doneCount} tamam</span>}
              {doneCount > 0 && errCount > 0 && <span className="text-muted-foreground"> · </span>}
              {errCount > 0 && <span className="text-destructive">{errCount} hata</span>}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={handleClose} disabled={busy}>
            {doneCount > 0 && pendingCount === 0 ? "Kapat" : "İptal"}
          </Button>
          <Button onClick={startUpload} disabled={busy || pendingCount === 0}>
            {busy
              ? <><Loader2 className="mr-1 h-4 w-4 animate-spin" /> Yükleniyor…</>
              : <><Upload className="mr-1 h-4 w-4" /> {pendingCount > 0 ? `${pendingCount} dosyayı yükle` : "Yükle"}</>}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatusBadge({ item }: { item: Item }) {
  if (item.status === "done") return <Check className="h-4 w-4 shrink-0 text-emerald-600" />
  if (item.status === "error") return <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
  if (item.status === "uploading") return <span className="shrink-0 text-[11px] text-muted-foreground">%{item.pct}</span>
  return <span className="shrink-0 text-[11px] text-muted-foreground">bekliyor</span>
}
