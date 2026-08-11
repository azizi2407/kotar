// Videographer Depot upload dialog.
// Files are uploaded SEQUENTIALLY (NOT UploadModal's Promise.all): gunicorn runs 2
// workers × 4 threads = 8; 500MB requests hold a thread for up to the 1200s
// timeout. A parallel batch would also starve client content uploads
// (useUploadVgPhotos is sequential for the same reason).
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
import { useI18n } from "@/lib/i18n"

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
  const { t, lang } = useI18n()
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
      toast.error(t("components.depot.uploadDialog.blockedFiles", { count: blocked.length }))
    }
    let ok = files.filter((f) => !isBlockedFile(f.name))
    const tooBig = ok.filter((f) => f.size > fileLimit)
    if (tooBig.length) {
      toast.error(t("components.depot.uploadDialog.tooBigFiles", { count: tooBig.length, limit: fmtBytes(fileLimit, lang) }))
    }
    ok = ok.filter((f) => f.size <= fileLimit)
    if (!ok.length) return
    const nextTotal = ok.reduce((s, f) => s + f.size, 0)
    if (quota && nextTotal > quota.remaining) {
      toast.warning(
        t("components.depot.uploadDialog.overQuotaWarning", {
          selected: fmtBytes(nextTotal, lang), remaining: fmtBytes(quota.remaining, lang),
        }))
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
    for (const { it, i } of targets) {          // SEQUENTIAL — rationale at the top of the file
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
          error: e instanceof ApiError ? e.message : t("components.depot.uploadDialog.uploadError"),
        })
      }
    }
    setBusy(false)
    if (done) toast.success(t("components.depot.uploadDialog.uploadedCount", { count: done }))
  }

  const pending = items.filter((it) => it.status === "pending" || it.status === "error").length

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !busy) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{t("components.depot.uploadDialog.title")}</DialogTitle></DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t("components.depot.uploadDialog.filesLabel")}</Label>
            <Input ref={inputRef} type="file" multiple
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? [])   // COPY the live FileList
                e.target.value = ""
                addFiles(picked)
              }} />
            <p className="text-xs text-muted-foreground">
              {t("components.depot.uploadDialog.acceptHint")}
              {quota && ` ${t("components.depot.uploadDialog.remainingSpace", { remaining: fmtBytes(quota.remaining, lang) })}`}
            </p>
          </div>

          <div className="space-y-1">
            <Label>{t("components.depot.uploadDialog.noteLabel")} <span className="text-xs text-muted-foreground">({t("components.depot.uploadDialog.optional")})</span></Label>
            <Input value={note} placeholder={t("components.depot.uploadDialog.notePlaceholder")}
              onChange={(e) => setNote(e.target.value)} />
          </div>

          {items.length > 0 && (
            <div className="max-h-56 space-y-2 overflow-y-auto">
              {items.map((it, i) => (
                <div key={`${it.file.name}-${i}`} className="space-y-1 rounded-md border p-2">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm">{it.file.name}</span>
                    <span className="text-xs text-muted-foreground">{fmtBytes(it.file.size, lang)}</span>
                    {it.status === "done" && <span className="text-xs text-emerald-600">✓</span>}
                    {it.status === "error" && <span className="text-xs text-destructive">⚠</span>}
                    {it.status === "uploading" && (
                      <span className="text-xs text-muted-foreground">{t("components.depot.uploadDialog.pct", { pct: it.pct })}</span>
                    )}
                    {it.status === "pending" && !busy && (
                      <button type="button" aria-label={t("components.depot.uploadDialog.remove")}
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
          <Button variant="ghost" onClick={onClose} disabled={busy}>{t("components.depot.uploadDialog.close")}</Button>
          <Button onClick={start} disabled={busy || pending === 0}>
            <Upload className="mr-1 h-4 w-4" />
            {busy ? t("components.depot.uploadDialog.uploading") : t("components.depot.uploadDialog.uploadCount", { count: pending })}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
