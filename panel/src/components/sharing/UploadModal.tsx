// Designer content upload modal — one category per batch (Post/Story/LinkedIn),
// multi-file selection, and PER-FILE progress. Each file uploads in parallel via
// its own apiUpload call; the status list is tracked from within the modal.
import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { AlertCircle, Check, Loader2, Upload, X } from "lucide-react"
import { toast } from "sonner"

import { acceptsFile, ApiError, apiUpload, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

// Video (2026-08-05) is also open to the designer: on the backend the file goes
// through the EXACT SAME path as one uploaded by the videographer (category='video' →
// "anyone with the link" permission on Drive + web-variant transcoding + revision
// detection) — there's no separate flow.
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
  // fixedCategory: hides the category picker, all files go to this category
  // (videographer flow: fixedCategory="video" + accept="video").
  fixedCategory?: string
  accept?: "image" | "video"
}) {
  const { t } = useI18n()
  const qc = useQueryClient()
  const [category, setCategory] = useState(fixedCategory ?? "post")
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState(false)
  // The expected file type is derived from the category: picking "Video" accepts
  // video instead of images. The `accept` prop is only decisive when the category
  // picker is hidden (videographer flow, fixedCategory).
  const kabul: "image" | "video" = category === "video" ? "video" : accept

  // Drop incompatible files remaining in the list when the category changes — if a
  // user picked 3 images and then switches the category to Video, those shouldn't upload silently.
  function changeCategory(next: string) {
    setCategory(next)
    const nextKabul = next === "video" ? "video" : accept
    setItems((s) => {
      const kalan = s.filter((it) => it.status === "done" || acceptsFile(it.file, nextKabul))
      const atilan = s.length - kalan.length
      if (atilan > 0) {
        s.filter((it) => !kalan.includes(it)).forEach((it) => URL.revokeObjectURL(it.url))
        toast.error(t("components.sharing.uploadModal.removedForCategory", { count: atilan }))
      }
      return kalan
    })
  }

  function addFiles(files: File[]) {
    let imgs = files.filter((f) => acceptsFile(f, kabul))
    if (imgs.length < files.length) {
      const n = files.length - imgs.length
      toast.error(kabul === "video"
        ? t("components.sharing.uploadModal.notAddedVideo", { count: n })
        : t("components.sharing.uploadModal.notAddedImage", { count: n }))
    }
    const tooBig = imgs.filter((f) => f.size > MAX_UPLOAD_BYTES)
    if (tooBig.length) toast.error(t("components.sharing.uploadModal.tooBig", { count: tooBig.length, maxMb: MAX_UPLOAD_MB }))
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
      setItem(i, { status: "error", error: err instanceof ApiError ? err.message : t("components.sharing.uploadModal.uploadError") })
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
    if (ok) toast.success(t("components.sharing.uploadModal.uploadedCount", { clientName, count: ok }))
    if (fail) toast.error(t("components.sharing.uploadModal.failedCount", { count: fail }))
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
          <DialogTitle>{t("components.sharing.uploadModal.title", { clientName })}</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          {!fixedCategory && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground">{t("components.sharing.uploadModal.category")}</span>
              <Select value={category} onValueChange={(v) => v && changeCategory(v)} disabled={busy}>
                <SelectTrigger size="sm" className="w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground">{t("components.sharing.uploadModal.categoryHint")}</span>
            </div>
          )}

          <label
            className="flex w-full cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/25 py-6 text-center text-sm transition-colors hover:border-primary/40">
            <Upload className="mb-1 h-5 w-5 text-muted-foreground" />
            <span className="text-muted-foreground">
              {kabul === "video" ? t("components.sharing.uploadModal.videosLabel") : t("components.sharing.uploadModal.imagesLabel")}{" "}
              <span className="text-primary">{t("components.sharing.uploadModal.pick")}</span> {t("components.sharing.uploadModal.multiple")}
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
              {doneCount > 0 && <span className="text-emerald-600">{t("components.sharing.uploadModal.doneCount", { count: doneCount })}</span>}
              {doneCount > 0 && errCount > 0 && <span className="text-muted-foreground"> · </span>}
              {errCount > 0 && <span className="text-destructive">{t("components.sharing.uploadModal.errorCount", { count: errCount })}</span>}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={handleClose} disabled={busy}>
            {doneCount > 0 && pendingCount === 0
              ? t("components.sharing.uploadModal.close")
              : t("components.sharing.uploadModal.cancel")}
          </Button>
          <Button onClick={startUpload} disabled={busy || pendingCount === 0}>
            {busy
              ? <><Loader2 className="mr-1 h-4 w-4 animate-spin" /> {t("components.sharing.uploadModal.uploading")}</>
              : <><Upload className="mr-1 h-4 w-4" />{" "}
                  {pendingCount > 0
                    ? t("components.sharing.uploadModal.uploadFilesCount", { count: pendingCount })
                    : t("components.sharing.uploadModal.upload")}</>}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatusBadge({ item }: { item: Item }) {
  const { t } = useI18n()
  if (item.status === "done") return <Check className="h-4 w-4 shrink-0 text-emerald-600" />
  if (item.status === "error") return <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
  if (item.status === "uploading") return <span className="shrink-0 text-[11px] text-muted-foreground">%{item.pct}</span>
  return <span className="shrink-0 text-[11px] text-muted-foreground">{t("components.sharing.uploadModal.pending")}</span>
}
