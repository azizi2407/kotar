// Tek kalem durumunu düzenleme diyaloğu (Müşteri Takip).
// Kaydetme UPSERT'tir: aynı (müşteri, kalem) için satır çoğalmaz.
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  ENTRY_STATUS_LABELS, useSaveTrackingEntry,
  type EntryStatus, type TrackingEntry, type TrackingItem,
} from "@/lib/musteri-takip"

interface Props {
  clientId: number
  clientName: string
  item: TrackingItem
  entry: TrackingEntry | undefined
  onClose: () => void
}

export function TrackingEntryDialog({ clientId, clientName, item, entry, onClose }: Props) {
  const save = useSaveTrackingEntry()
  const [status, setStatus] = useState<EntryStatus>(entry?.status ?? "yok")
  const [statusDate, setStatusDate] = useState(entry?.status_date ?? "")
  const [note, setNote] = useState(entry?.note ?? "")
  const [url, setUrl] = useState(entry?.url ?? "")

  async function submit() {
    try {
      await save.mutateAsync({
        clientId, itemId: item.id,
        body: { status, status_date: statusDate || null, note: note || null, url: url || null },
      })
      toast.success(`${item.name} güncellendi`)
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Kaydedilemedi")
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{item.name}</DialogTitle>
        </DialogHeader>
        <p className="-mt-2 text-sm text-muted-foreground">{clientName}</p>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>Durum</Label>
            <select
              className="h-9 w-full rounded-md border bg-background px-2 text-sm"
              value={status}
              onChange={(e) => setStatus(e.target.value as EntryStatus)}
            >
              {Object.entries(ENTRY_STATUS_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label>
              Tarih <span className="text-xs text-muted-foreground">(ör. tescil/teslim günü)</span>
            </Label>
            <Input type="date" value={statusDate} onChange={(e) => setStatusDate(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Link</Label>
            <Input
              placeholder="https://…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>Not</Label>
            <Textarea
              rows={3}
              placeholder="örn. WordPress, yıllık bakım bizde"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose}>Vazgeç</Button>
          <Button onClick={submit} disabled={save.isPending}>
            {save.isPending ? "Kaydediliyor…" : "Kaydet"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
