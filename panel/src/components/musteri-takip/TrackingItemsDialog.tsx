// Kalem kataloğu yönetimi (Müşteri Takip) — ayrı sayfa değil, aynı ekranda diyalog:
// kalem eklenip sonucu hemen müşteri satırlarında görülsün diye.
// Sıralama local state'te ↑/↓ ile değişir, "Sırayı kaydet" ile topluca yazılır.
import { useEffect, useState } from "react"
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  CATEGORY_LABELS, ITEM_ICONS, itemIcon, useCreateTrackingItem, useDeleteTrackingItem,
  useReorderTrackingItems, useTrackingItems, useUpdateTrackingItem, type TrackingItem,
} from "@/lib/musteri-takip"

function errText(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback
}

export function TrackingItemsDialog({ onClose }: { onClose: () => void }) {
  const itemsQ = useTrackingItems()
  const create = useCreateTrackingItem()
  const update = useUpdateTrackingItem()
  const remove = useDeleteTrackingItem()
  const reorder = useReorderTrackingItems()

  const [order, setOrder] = useState<TrackingItem[]>([])
  const [dirty, setDirty] = useState(false)
  const [newName, setNewName] = useState("")
  const [newCategory, setNewCategory] = useState("diger")
  const [newIcon, setNewIcon] = useState("Tag")

  // Sunucu listesi geldiğinde local sırayı tazele — kullanıcı sırayı bozduysa dokunma.
  useEffect(() => {
    if (itemsQ.data && !dirty) setOrder(itemsQ.data)
  }, [itemsQ.data, dirty])

  function move(index: number, delta: number) {
    const next = [...order]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setOrder(next)
    setDirty(true)
  }

  async function saveOrder() {
    try {
      await reorder.mutateAsync(order.map((i) => i.id))
      setDirty(false)
      toast.success("Sıra kaydedildi")
    } catch (e) {
      toast.error(errText(e, "Sıra kaydedilemedi"))
    }
  }

  async function addItem() {
    if (!newName.trim()) { toast.error("Kalem adı gerekli"); return }
    try {
      await create.mutateAsync({ name: newName.trim(), category: newCategory, icon: newIcon })
      setNewName("")
      toast.success("Kalem eklendi")
    } catch (e) {
      toast.error(errText(e, "Kalem eklenemedi"))
    }
  }

  async function toggleActive(item: TrackingItem) {
    try {
      await update.mutateAsync({ id: item.id, body: { active: !item.active } })
    } catch (e) {
      toast.error(errText(e, "Güncellenemedi"))
    }
  }

  async function deleteItem(item: TrackingItem) {
    if (!window.confirm(`"${item.name}" kalemi listeden kaldırılsın mı? Girilmiş müşteri kayıtları silinmez.`)) return
    try {
      await remove.mutateAsync(item.id)
      setDirty(false)
      toast.success("Kalem kaldırıldı")
    } catch (e) {
      toast.error(errText(e, "Kaldırılamadı"))
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Takip kalemleri</DialogTitle>
        </DialogHeader>
        <p className="-mt-2 text-sm text-muted-foreground">
          Müşterilere satılabilecek iş kalemleri. Pasife alınan kalem yeni satırlarda görünmez,
          girilmiş kayıtlar durur.
        </p>

        <div className="max-h-[50vh] space-y-1 overflow-y-auto pr-1">
          {order.map((item, index) => {
            const Icon = itemIcon(item.icon)
            return (
              <div key={item.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5">
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className={item.active ? "flex-1 text-sm" : "flex-1 text-sm text-muted-foreground line-through"}>
                  {item.name}
                </span>
                <span className="hidden text-xs text-muted-foreground sm:inline">
                  {CATEGORY_LABELS[item.category] ?? item.category}
                </span>
                <Switch
                  checked={item.active}
                  onCheckedChange={() => toggleActive(item)}
                  aria-label={`${item.name} aktif`}
                />
                <Button variant="ghost" size="icon-sm" onClick={() => move(index, -1)}
                  disabled={index === 0} aria-label="Yukarı taşı">
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={() => move(index, 1)}
                  disabled={index === order.length - 1} aria-label="Aşağı taşı">
                  <ArrowDown className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={() => deleteItem(item)}
                  aria-label={`${item.name} kaldır`}>
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            )
          })}
          {itemsQ.isLoading && <p className="py-4 text-center text-sm text-muted-foreground">Yükleniyor…</p>}
        </div>

        {dirty && (
          <div className="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2">
            <span className="text-sm">Sıra değişti.</span>
            <Button size="sm" onClick={saveOrder} disabled={reorder.isPending}>
              {reorder.isPending ? "Kaydediliyor…" : "Sırayı kaydet"}
            </Button>
          </div>
        )}

        <div className="grid grid-cols-1 items-end gap-2 border-t pt-3 sm:grid-cols-[1fr_auto_auto_auto]">
          <div className="space-y-1">
            <Label className="text-xs">Yeni kalem</Label>
            <Input placeholder="örn. Drone Çekimi" value={newName}
              onChange={(e) => setNewName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Kategori</Label>
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={newCategory} onChange={(e) => setNewCategory(e.target.value)}>
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">İkon</Label>
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={newIcon} onChange={(e) => setNewIcon(e.target.value)}>
              {Object.keys(ITEM_ICONS).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
          <Button onClick={addItem} disabled={create.isPending}>
            <Plus className="mr-1 h-4 w-4" /> Ekle
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
