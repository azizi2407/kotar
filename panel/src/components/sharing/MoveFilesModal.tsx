// Unshared content move modal — pick unshared files from the previous/next week's
// Drive folder and move them into the current week's folder.
import { useMemo, useState } from "react"
import { toast } from "sonner"

import {
  thumbnailUrl, useMovableFiles, useMoveFiles, type MovableWeek,
} from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  clientId: number
  clientName: string
  weekIso: string
}

function WeekSection({ title, week, selected, toggle }: {
  title: string; week: MovableWeek; selected: Set<string>; toggle: (id: string) => void
}) {
  const { t } = useI18n()
  if (!week.files.length) {
    return (
      <div>
        <div className="mb-1 text-sm font-medium">{title} · {week.week_iso}</div>
        <p className="text-xs text-muted-foreground">{t("components.sharing.moveFilesModal.noUnshared")}</p>
      </div>
    )
  }
  return (
    <div>
      <div className="mb-1 text-sm font-medium">{title} · {week.week_iso}</div>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {week.files.map((f) => {
          const on = selected.has(f.id)
          return (
            <button key={f.id} type="button" onClick={() => toggle(f.id)}
              className={cn("relative overflow-hidden rounded-md border text-left transition",
                on ? "ring-2 ring-primary" : "hover:border-primary/50")}
              title={f.name ?? ""}>
              <img src={thumbnailUrl(f.id, 200)} alt={f.name ?? ""} loading="lazy"
                className="aspect-square w-full bg-muted object-cover"
                onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }} />
              {on && (
                <span className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
                  ✓
                </span>
              )}
              <div className="truncate px-1 py-0.5 text-[10px] text-muted-foreground">{f.name}</div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function MoveFilesModal({ open, onOpenChange, clientId, clientName, weekIso }: Props) {
  const { t } = useI18n()
  const { data, isLoading, isError } = useMovableFiles(clientId, weekIso, open)
  const move = useMoveFiles()
  // selection key: "<source week>:<fileId>" (source week is needed for the move)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  function toggle(week: string, id: string) {
    const key = `${week}:${id}`
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(key)) n.delete(key); else n.add(key)
      return n
    })
  }
  const selForWeek = (weekIso: string) =>
    new Set([...selected].filter((k) => k.startsWith(`${weekIso}:`)).map((k) => k.slice(weekIso.length + 1)))

  const count = selected.size
  const weeks = useMemo(() => (data ? [data.previous, data.next] : []), [data])

  async function onMove() {
    if (!data || count === 0) return
    const groups = [data.previous, data.next]
      .map((w) => ({ from: w.week_iso, ids: [...selForWeek(w.week_iso)] }))
      .filter((g) => g.ids.length)
    let total = 0
    try {
      for (const g of groups) {
        const r = await move.mutateAsync({
          client_id: clientId, from_week_iso: g.from, to_week_iso: weekIso, file_ids: g.ids,
        })
        total += r.moved ?? 0
        if (r.errors?.length) toast.error(r.errors.join(", "))
      }
      setSelected(new Set())
      if (total) toast.success(t("components.sharing.moveFilesModal.moved", { count: total }))
      if (total) onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.sharing.moveFilesModal.moveFailed"))
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("components.sharing.moveFilesModal.title", { clientName })}</DialogTitle>
          <DialogDescription>
            {t("components.sharing.moveFilesModal.description", { weekIso })}
          </DialogDescription>
        </DialogHeader>

        {isLoading && <Skeleton className="h-40 w-full" />}
        {isError && <p className="text-sm text-destructive">{t("components.sharing.moveFilesModal.listError")}</p>}
        {data && (
          <div className="space-y-4">
            {weeks.every((w) => !w.files.length) && (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {t("components.sharing.moveFilesModal.nothingMovable")}
              </p>
            )}
            <WeekSection title={t("components.sharing.moveFilesModal.prevWeek")} week={data.previous}
              selected={selForWeek(data.previous.week_iso)}
              toggle={(id) => toggle(data.previous.week_iso, id)} />
            <WeekSection title={t("components.sharing.moveFilesModal.nextWeek")} week={data.next}
              selected={selForWeek(data.next.week_iso)}
              toggle={(id) => toggle(data.next.week_iso, id)} />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("components.sharing.moveFilesModal.close")}
          </Button>
          <Button onClick={onMove} disabled={count === 0 || move.isPending}>
            {move.isPending
              ? t("components.sharing.moveFilesModal.moving")
              : count
                ? t("components.sharing.moveFilesModal.moveHereCount", { count })
                : t("components.sharing.moveFilesModal.moveHere")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
