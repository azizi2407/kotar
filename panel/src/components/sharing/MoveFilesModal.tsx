// Paylaşılmamış içerik taşıma modalı — önceki/sonraki hafta Drive klasöründeki
// paylaşılmamış dosyaları seç, bulunduğumuz hafta klasörüne taşı.
import { useMemo, useState } from "react"
import { toast } from "sonner"

import {
  thumbnailUrl, useMovableFiles, useMoveFiles, type MovableWeek,
} from "@/lib/sharing"
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
  if (!week.files.length) {
    return (
      <div>
        <div className="mb-1 text-sm font-medium">{title} · {week.week_iso}</div>
        <p className="text-xs text-muted-foreground">Paylaşılmamış içerik yok.</p>
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
  const { data, isLoading, isError } = useMovableFiles(clientId, weekIso, open)
  const move = useMoveFiles()
  // seçim anahtarı: "<kaynak week>:<fileId>" (kaynak hafta taşımada gerekli)
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
      if (total) toast.success(`${total} dosya bu haftaya taşındı`)
      if (total) onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Taşıma başarısız")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>İçerik Taşı · {clientName}</DialogTitle>
          <DialogDescription>
            Önceki/sonraki haftanın paylaşılmamış içeriklerini {weekIso} klasörüne taşı.
          </DialogDescription>
        </DialogHeader>

        {isLoading && <Skeleton className="h-40 w-full" />}
        {isError && <p className="text-sm text-destructive">İçerik listesi alınamadı (Drive erişimi?).</p>}
        {data && (
          <div className="space-y-4">
            {weeks.every((w) => !w.files.length) && (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Taşınabilir paylaşılmamış içerik yok.
              </p>
            )}
            <WeekSection title="Önceki hafta" week={data.previous}
              selected={selForWeek(data.previous.week_iso)}
              toggle={(id) => toggle(data.previous.week_iso, id)} />
            <WeekSection title="Sonraki hafta" week={data.next}
              selected={selForWeek(data.next.week_iso)}
              toggle={(id) => toggle(data.next.week_iso, id)} />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Kapat</Button>
          <Button onClick={onMove} disabled={count === 0 || move.isPending}>
            {move.isPending ? "Taşınıyor…" : `Bu haftaya taşı${count ? ` (${count})` : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
