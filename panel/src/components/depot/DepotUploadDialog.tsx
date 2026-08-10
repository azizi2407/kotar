// Videograf Deposu yükleme diyaloğu.
// Dosyalar SIRALI yüklenir (UploadModal'ın Promise.all'u DEĞİL): gunicorn 2 worker ×
// 4 thread = 8; 500 MB'lık istekler 1200 sn timeout ile thread tutuyor. Paralel parti
// müşteri içerik yüklemelerini de aç bırakırdı (useUploadVgPhotos aynı nedenle sıralı).
import { useRef, useState } from "react"
import { Upload, X } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { ApiError } from "@/lib/api"
import { fmtBytes, isBlockedFile, useUploadDepotFile, type DepotQuota } from "@/lib/depot"

interface Item {
  file: File
  pct: number
  status: "pending" | "uploading" | "done" | "error"
  error?: string
}

export function DepotUploadDialog({ quota, onClose }: {
  quota: DepotQuota | undefined
  onClose: () => void
}) {
  const [items, setItems] = useState<Item[]>([])
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const upload = useUploadDepotFile()
  const inputRef = useRef<HTMLInputElement | null>(null)

  function setItem(i: number, patch: Partial<Item>) {
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)))
  }

  function addFiles(files: File[]) {
    const fileLimit = quota?.file_limit ?? 500 * 1024 * 1024
    const blocked = files.filter((f) => isBlockedFile(f.name))
    if (blocked.length) {
      toast.error(`${blocked.length} dosya eklenmedi (çalıştırılabilir dosya türü)`)
    }
    let ok = files.filter((f) => !isBlockedFile(f.name))
    const tooBig = ok.filter((f) => f.size > fileLimit)
    if (tooBig.length) {
      toast.error(`${tooBig.length} dosya ${fmtBytes(fileLimit)} sınırını aşıyor, eklenmedi`)
    }
    ok = ok.filter((f) => f.size <= fileLimit)
    if (!ok.length) return
    const nextTotal = ok.reduce((s, f) => s + f.size, 0)
    if (quota && nextTotal > quota.remaining) {
      toast.warning(
        `Seçilen dosyalar (${fmtBytes(nextTotal)}) kalan alandan (${fmtBytes(quota.remaining)}) büyük — bir kısmı reddedilebilir.`)
    }
    setItems((prev) => [...prev, ...ok.map((f) => ({ file: f, pct: 0, status: "pending" as const }))])
  }

  async function start() {
    const targets = items
      .map((it, i) => ({ it, i }))
      .filter((x) => x.it.status === "pending" || x.it.status === "error")
    if (!targets.length) return
    setBusy(true)
    let done = 0
    for (const { it, i } of targets) {          // SIRALI — gerekçe dosya başında
      setItem(i, { status: "uploading", pct: 0, error: undefined })
      try {
        await upload.mutateAsync({
          file: it.file,
          note: note.trim() || undefined,
          onProgress: (p) => setItem(i, { pct: p }),
        })
        setItem(i, { status: "done", pct: 100 })
        done += 1
      } catch (e) {
        setItem(i, {
          status: "error",
          error: e instanceof ApiError ? e.message : "Yükleme hatası",
        })
      }
    }
    setBusy(false)
    if (done) toast.success(`${done} dosya yüklendi`)
  }

  const pending = items.filter((it) => it.status === "pending" || it.status === "error").length

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>Depoya dosya yükle</DialogTitle></DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label>Dosyalar</Label>
            <Input ref={inputRef} type="file" multiple
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? [])   // canlı FileList'i KOPYALA
                e.target.value = ""
                addFiles(picked)
              }} />
            <p className="text-xs text-muted-foreground">
              Her tür dosya kabul edilir (çalıştırılabilir dosyalar hariç).
              {quota && ` Kalan alan: ${fmtBytes(quota.remaining)}.`}
            </p>
          </div>

          <div className="space-y-1">
            <Label>Not <span className="text-xs text-muted-foreground">(opsiyonel)</span></Label>
            <Input value={note} placeholder="örn. Gusto Mare ham çekim"
              onChange={(e) => setNote(e.target.value)} />
          </div>

          {items.length > 0 && (
            <div className="max-h-56 space-y-2 overflow-y-auto">
              {items.map((it, i) => (
                <div key={`${it.file.name}-${i}`} className="space-y-1 rounded-md border p-2">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm">{it.file.name}</span>
                    <span className="text-xs text-muted-foreground">{fmtBytes(it.file.size)}</span>
                    {it.status === "done" && <span className="text-xs text-emerald-600">✓</span>}
                    {it.status === "error" && <span className="text-xs text-destructive">⚠</span>}
                    {it.status === "uploading" && (
                      <span className="text-xs text-muted-foreground">%{it.pct}</span>
                    )}
                    {it.status === "pending" && !busy && (
                      <button type="button" aria-label="Kaldır"
                        onClick={() => setItems((prev) => prev.filter((_, idx) => idx !== i))}
                        className="text-muted-foreground hover:text-destructive">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                  {(it.status === "uploading" || it.status === "done") && (
                    <Progress value={it.status === "done" ? 100 : it.pct} />
                  )}
                  {it.error && <p className="text-xs text-destructive">{it.error}</p>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Kapat</Button>
          <Button onClick={start} disabled={busy || pending === 0}>
            <Upload className="mr-1 h-4 w-4" />
            {busy ? "Yükleniyor…" : `${pending} dosyayı yükle`}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
