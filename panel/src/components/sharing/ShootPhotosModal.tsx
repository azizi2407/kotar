// Designer's "Shoot Photos" modal — view photos uploaded by the videographer,
// multi-select → download as zip + mark "Used". Downloading auto-marks a photo as used
// (can be undone manually). Total / Used / Remaining summary at the top.
import { useMemo, useState } from "react"
import { Check, Download, RotateCcw } from "lucide-react"
import { toast } from "sonner"

import {
  downloadVgPhoto, downloadVgPhotosZip, thumbnailUrl, useMarkVgPhotoUsed, useVgPhotos,
} from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

export function ShootPhotosModal({ open, onClose, clientId, clientName }: {
  open: boolean
  onClose: () => void
  clientId: number
  clientName: string
}) {
  const { t } = useI18n()
  const { data: photos, isLoading, isError } = useVgPhotos(clientId)
  const markUsed = useMarkVgPhotoUsed()
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [lastIdx, setLastIdx] = useState<number | null>(null)

  const list = useMemo(
    () => (photos ?? []).slice().sort((a, b) => (b.shoot_date ?? "").localeCompare(a.shoot_date ?? "")),
    [photos])

  const total = list.length
  const used = list.filter((p) => p.used).length
  const remaining = total - used

  function toggle(idx: number, shift: boolean) {
    const id = list[idx].id
    setSelected((prev) => {
      const next = new Set(prev)
      if (shift && lastIdx !== null) {
        const [lo, hi] = lastIdx < idx ? [lastIdx, idx] : [idx, lastIdx]
        for (let i = lo; i <= hi; i++) next.add(list[i].id)
      } else if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setLastIdx(idx)
  }

  function mark(id: number, value: boolean) {
    markUsed.mutate({ id, used: value }, { onError: (e) => toast.error(e.message) })
  }

  async function bulkDownload() {
    const ids = [...selected]
    if (!ids.length) return
    try {
      await downloadVgPhotosZip(ids)
      // downloaded ones are auto-marked used (can be undone manually)
      ids.forEach((id) => mark(id, true))
      setSelected(new Set())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.sharing.shootPhotosModal.downloadFailed"))
    }
  }

  function bulkMark(value: boolean) {
    const ids = [...selected]
    if (!ids.length) return
    ids.forEach((id) => mark(id, value))
    setSelected(new Set())
  }

  function singleDownload(id: number) {
    downloadVgPhoto(id)
    mark(id, true) // downloading = auto-marked used
  }

  function handleClose() {
    setSelected(new Set())
    setLastIdx(null)
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="max-h-[85vh] overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("components.sharing.shootPhotosModal.title", { clientName })}</DialogTitle>
        </DialogHeader>

        {/* summary + bulk action strip */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
          <div className="flex items-center gap-2 text-sm">
            <span className="rounded-full bg-muted px-2 py-0.5">{t("components.sharing.shootPhotosModal.total", { count: total })}</span>
            <span className="rounded-full bg-emerald-600/15 px-2 py-0.5 text-emerald-700 dark:text-emerald-400">{t("components.sharing.shootPhotosModal.used", { count: used })}</span>
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-amber-700 dark:text-amber-400">{t("components.sharing.shootPhotosModal.remaining", { count: remaining })}</span>
          </div>
          {selected.size > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">{t("components.sharing.shootPhotosModal.selectedCount", { count: selected.size })}</span>
              <Button size="sm" variant="outline" onClick={bulkDownload}>
                <Download className="mr-1 h-4 w-4" /> {t("components.sharing.shootPhotosModal.download")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => bulkMark(true)}>
                <Check className="mr-1 h-4 w-4" /> {t("components.sharing.shootPhotosModal.markUsed")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => bulkMark(false)}>
                <RotateCcw className="mr-1 h-4 w-4" /> {t("components.sharing.shootPhotosModal.undo")}
              </Button>
            </div>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isError ? (
            <p className="py-8 text-center text-destructive">{t("components.sharing.shootPhotosModal.loadError")}</p>
          ) : isLoading ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {[...Array(8)].map((_, i) => <Skeleton key={i} className="aspect-square w-full" />)}
            </div>
          ) : list.length === 0 ? (
            <p className="py-8 text-center text-muted-foreground">{t("components.sharing.shootPhotosModal.noPhotos")}</p>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {list.map((p, idx) => {
                const isSel = selected.has(p.id)
                return (
                  <div key={p.id}
                    className={cn("group relative overflow-hidden rounded-lg border bg-muted/20", isSel && "ring-2 ring-primary")}>
                    <button onClick={(e) => toggle(idx, e.shiftKey)}
                      className={cn("absolute left-1.5 top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded border bg-background/80 transition",
                        isSel ? "border-primary bg-primary text-primary-foreground opacity-100" : "opacity-0 group-hover:opacity-100")}>
                      {isSel && <Check className="h-3.5 w-3.5" />}
                    </button>
                    <button onClick={() => toggle(idx, false)} className="block w-full">
                      {p.file_id ? (
                        <img src={thumbnailUrl(p.file_id, 300)} alt={p.file_name ?? ""} loading="lazy"
                          className="aspect-square w-full object-cover" />
                      ) : (
                        <div className="flex aspect-square w-full items-center justify-center text-xs text-muted-foreground">{t("components.sharing.shootPhotosModal.noImage")}</div>
                      )}
                    </button>
                    <span className={cn("absolute right-1.5 top-1.5 z-10 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                      p.used ? "bg-emerald-600 text-white" : "border bg-background/80 text-muted-foreground")}>
                      {p.used ? t("components.sharing.shootPhotosModal.usedLabel") : t("components.sharing.shootPhotosModal.notUsedLabel")}
                    </span>
                    <div className="flex items-center justify-between gap-1 px-2 py-1.5">
                      <span className="min-w-0 truncate text-xs font-medium">{p.file_name ?? "—"}</span>
                      <div className="flex shrink-0 items-center gap-0.5">
                        <button onClick={() => singleDownload(p.id)} title={t("components.sharing.shootPhotosModal.download")}
                          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
                          <Download className="h-4 w-4" />
                        </button>
                        <button onClick={() => mark(p.id, !p.used)}
                          title={p.used ? t("components.sharing.shootPhotosModal.undoUsed") : t("components.sharing.shootPhotosModal.markUsedTitle")}
                          className={cn("rounded p-1 hover:bg-muted",
                            p.used ? "text-emerald-600" : "text-muted-foreground hover:text-foreground")}>
                          <Check className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
