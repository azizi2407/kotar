// Tracking item catalog management (Client Tracking) — not a separate page, a dialog
// on the same screen: so an added item's effect is visible in client rows right away.
// Ordering changes in local state via ↑/↓, and gets written in bulk with "Save order".
import { useEffect, useState } from "react"
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useI18n } from "@/lib/i18n"
import {
  ITEM_ICONS, itemIcon, useCategoryLabels, useCreateTrackingItem, useDeleteTrackingItem,
  useReorderTrackingItems, useTrackingItems, useUpdateTrackingItem, type TrackingItem,
  itemDisplayName,
} from "@/lib/musteri-takip"

function errText(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback
}

export function TrackingItemsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const CATEGORY_LABELS = useCategoryLabels()
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

  // Refresh the local order when the server list arrives — don't touch it if the user has reordered.
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
      toast.success(t("components.clientTracking.trackingItemsDialog.orderSaved"))
    } catch (e) {
      toast.error(errText(e, t("components.clientTracking.trackingItemsDialog.orderSaveFailed")))
    }
  }

  async function addItem() {
    if (!newName.trim()) { toast.error(t("components.clientTracking.trackingItemsDialog.nameRequired")); return }
    try {
      await create.mutateAsync({ name: newName.trim(), category: newCategory, icon: newIcon })
      setNewName("")
      toast.success(t("components.clientTracking.trackingItemsDialog.itemAdded"))
    } catch (e) {
      toast.error(errText(e, t("components.clientTracking.trackingItemsDialog.itemAddFailed")))
    }
  }

  async function toggleActive(item: TrackingItem) {
    try {
      await update.mutateAsync({ id: item.id, body: { active: !item.active } })
    } catch (e) {
      toast.error(errText(e, t("components.clientTracking.trackingItemsDialog.updateFailed")))
    }
  }

  async function deleteItem(item: TrackingItem) {
    if (!window.confirm(t("components.clientTracking.trackingItemsDialog.confirmDelete", { name: itemDisplayName(item, t) }))) return
    try {
      await remove.mutateAsync(item.id)
      setDirty(false)
      toast.success(t("components.clientTracking.trackingItemsDialog.itemRemoved"))
    } catch (e) {
      toast.error(errText(e, t("components.clientTracking.trackingItemsDialog.itemRemoveFailed")))
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("components.clientTracking.trackingItemsDialog.title")}</DialogTitle>
        </DialogHeader>
        <p className="-mt-2 text-sm text-muted-foreground">
          {t("components.clientTracking.trackingItemsDialog.subtitle")}
        </p>

        <div className="max-h-[50vh] space-y-1 overflow-y-auto pr-1">
          {order.map((item, index) => {
            const Icon = itemIcon(item.icon)
            return (
              <div key={item.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5">
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className={item.active ? "flex-1 text-sm" : "flex-1 text-sm text-muted-foreground line-through"}>
                  {itemDisplayName(item, t)}
                </span>
                <span className="hidden text-xs text-muted-foreground sm:inline">
                  {CATEGORY_LABELS[item.category] ?? item.category}
                </span>
                <Switch
                  checked={item.active}
                  onCheckedChange={() => toggleActive(item)}
                  aria-label={t("components.clientTracking.trackingItemsDialog.activeAria", { name: item.name })}
                />
                <Button variant="ghost" size="icon-sm" onClick={() => move(index, -1)}
                  disabled={index === 0} aria-label={t("components.clientTracking.trackingItemsDialog.moveUp")}>
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={() => move(index, 1)}
                  disabled={index === order.length - 1} aria-label={t("components.clientTracking.trackingItemsDialog.moveDown")}>
                  <ArrowDown className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon-sm" onClick={() => deleteItem(item)}
                  aria-label={t("components.clientTracking.trackingItemsDialog.removeAria", { name: item.name })}>
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            )
          })}
          {itemsQ.isLoading && <p className="py-4 text-center text-sm text-muted-foreground">{t("components.clientTracking.trackingItemsDialog.loading")}</p>}
        </div>

        {dirty && (
          <div className="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2">
            <span className="text-sm">{t("components.clientTracking.trackingItemsDialog.orderChanged")}</span>
            <Button size="sm" onClick={saveOrder} disabled={reorder.isPending}>
              {reorder.isPending ? t("components.clientTracking.trackingItemsDialog.saving") : t("components.clientTracking.trackingItemsDialog.saveOrder")}
            </Button>
          </div>
        )}

        <div className="grid grid-cols-1 items-end gap-2 border-t pt-3 sm:grid-cols-[1fr_auto_auto_auto]">
          <div className="space-y-1">
            <Label className="text-xs">{t("components.clientTracking.trackingItemsDialog.newItem")}</Label>
            <Input placeholder={t("components.clientTracking.trackingItemsDialog.newItemPlaceholder")} value={newName}
              onChange={(e) => setNewName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("components.clientTracking.trackingItemsDialog.category")}</Label>
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={newCategory} onChange={(e) => setNewCategory(e.target.value)}>
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t("components.clientTracking.trackingItemsDialog.icon")}</Label>
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={newIcon} onChange={(e) => setNewIcon(e.target.value)}>
              {Object.keys(ITEM_ICONS).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
          <Button onClick={addItem} disabled={create.isPending}>
            <Plus className="mr-1 h-4 w-4" /> {t("components.clientTracking.trackingItemsDialog.add")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
