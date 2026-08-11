// Video Upload page client visibility (2026-07-25) — a PERSONAL preference:
// a client you hide here only drops off YOUR page, your team's view is unaffected.
import { useMemo, useState } from "react"
import { Search } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { useClients } from "@/lib/clients"
import { useI18n } from "@/lib/i18n"
import { useHiddenClients, useSetClientHidden } from "@/lib/prefs"
import { trFold } from "@/lib/week"

export function VgVisibilityDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const clientsQ = useClients({ status: "active", q: "" })
  const hiddenQ = useHiddenClients("videographer_upload")
  const setHidden = useSetClientHidden("videographer_upload")
  const [q, setQ] = useState("")

  const hidden = useMemo(() => new Set(hiddenQ.data ?? []), [hiddenQ.data])
  // ALL active clients are listed (including hidden ones) — otherwise they couldn't be unhidden.
  const rows = useMemo(() => {
    const needle = trFold(q.trim())
    return (clientsQ.data ?? []).filter((c) => !needle || trFold(c.name).includes(needle))
  }, [clientsQ.data, q])

  function toggle(clientId: number, visible: boolean) {
    setHidden.mutate({ client_id: clientId, hidden: !visible }, {
      onError: (e) => toast.error(e instanceof Error ? e.message : t("components.videographer.vgVisibilityDialog.saveFailed")),
    })
  }

  async function showAll() {
    for (const id of hidden) {
      await setHidden.mutateAsync({ client_id: id, hidden: false }).catch(() => null)
    }
    toast.success(t("components.videographer.vgVisibilityDialog.allVisible"))
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>{t("components.videographer.vgVisibilityDialog.title")}</DialogTitle></DialogHeader>
        <p className="-mt-2 text-sm text-muted-foreground">
          {t("components.videographer.vgVisibilityDialog.description")}
        </p>

        <div className="relative">
          <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder={t("components.videographer.vgVisibilityDialog.searchPlaceholder")} value={q}
            onChange={(e) => setQ(e.target.value)} />
        </div>

        <div className="max-h-[50vh] space-y-1 overflow-y-auto pr-1">
          {clientsQ.isLoading && (
            <p className="py-4 text-center text-sm text-muted-foreground">
              {t("components.videographer.vgVisibilityDialog.loading")}
            </p>
          )}
          {rows.map((c) => {
            const visible = !hidden.has(c.id)
            return (
              <div key={c.id}
                className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
                <span className={visible ? "text-sm" : "text-sm text-muted-foreground"}>
                  {c.name}
                </span>
                <Switch checked={visible} onCheckedChange={(v) => toggle(c.id, !!v)}
                  aria-label={t("components.videographer.vgVisibilityDialog.visibleAria", { name: c.name })} />
              </div>
            )
          })}
          {clientsQ.data && rows.length === 0 && (
            <p className="py-4 text-center text-sm text-muted-foreground">
              {t("components.videographer.vgVisibilityDialog.noMatch")}
            </p>
          )}
        </div>

        <div className="flex items-center justify-between border-t pt-3">
          <span className="text-sm text-muted-foreground">
            {hidden.size > 0
              ? t("components.videographer.vgVisibilityDialog.hiddenCountLabel", { count: hidden.size })
              : t("components.videographer.vgVisibilityDialog.allVisibleLabel")}
          </span>
          <div className="flex gap-2">
            {hidden.size > 0 && (
              <Button variant="outline" size="sm" onClick={showAll} disabled={setHidden.isPending}>
                {t("components.videographer.vgVisibilityDialog.showAllBtn")}
              </Button>
            )}
            <Button size="sm" onClick={onClose}>{t("components.videographer.vgVisibilityDialog.closeBtn")}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
