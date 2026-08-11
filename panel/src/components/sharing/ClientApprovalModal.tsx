// Client approval link modal (2026-08-06) — NOT a twin of the "Approval Link"
// button on DesignerBoard: that one sends the (client, week) scope as-is, here you
// pick content items one by one from a three-week window and the selection gets
// frozen into the link. The generated link is `/onay/<token>` (visual language
// borrowed from the special days page).
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import {
  approvalShareMessage, thumbnailUrl, useApprovalCandidates, useApprovalLink,
  useApprovalLinks, type ApprovalCandidate, type ApprovalWeek,
} from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
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

function KararRozeti({ up }: { up: ApprovalCandidate }) {
  const { t } = useI18n()
  if (!up.review?.status) return null
  const onayli = up.review.status === "approved"
  return (
    <span className="absolute left-1 top-1 rounded bg-background/90 px-1 text-[10px]"
      title={onayli ? t("components.sharing.clientApprovalModal.clientApproved") : up.review.note || t("components.sharing.clientApprovalModal.revisionRequested")}>
      {onayli ? "✅" : "📝"}
    </span>
  )
}

function HaftaBolumu({ etiket, hafta, secili, toggle }: {
  etiket: string; hafta: ApprovalWeek; secili: Set<number>; toggle: (id: number) => void
}) {
  const { t } = useI18n()
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-sm font-medium">
        {etiket} <span className="text-xs font-normal text-muted-foreground">{hafta.week_iso}</span>
      </div>
      {!hafta.uploads.length ? (
        <p className="text-xs text-muted-foreground">{t("components.sharing.clientApprovalModal.noneToSend")}</p>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {hafta.uploads.map((up) => {
            const on = secili.has(up.id)
            return (
              <button key={up.id} type="button" onClick={() => toggle(up.id)}
                className={cn("relative overflow-hidden rounded-md border text-left transition",
                  on ? "ring-2 ring-primary" : "hover:border-primary/50")}
                title={up.file_name ?? ""}>
                {up.file_id ? (
                  <img src={thumbnailUrl(up.file_id, 200)} alt="" loading="lazy"
                    className="aspect-square w-full bg-muted object-cover"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }} />
                ) : (
                  <div className="aspect-square w-full bg-muted" />
                )}
                <KararRozeti up={up} />
                {on && (
                  <span className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
                    ✓
                  </span>
                )}
                <div className="flex items-center gap-1 px-1 py-0.5">
                  <span className="truncate text-[10px] text-muted-foreground">{up.file_name}</span>
                  {up.category === "video" && <span className="text-[10px]">🎬</span>}
                </div>
                {up.sent_before && (
                  <div className="px-1 pb-0.5 text-[9px] text-amber-600 dark:text-amber-500">
                    {t("components.sharing.clientApprovalModal.sentBefore")}
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// What the client wrote in the notebook on their approval page — the only place to read it in the panel.
function MusteriNotlari({ clientId, open }: { clientId: number; open: boolean }) {
  const { t, lang } = useI18n()
  const { data } = useApprovalLinks(clientId, open)
  const notlu = (data ?? []).filter((l) => l.note)
  if (!notlu.length) return null
  return (
    <div className="rounded-md border bg-muted/40 p-3">
      <div className="mb-2 text-sm font-medium">{t("components.sharing.clientApprovalModal.clientNotes")}</div>
      <div className="space-y-2">
        {notlu.slice(0, 3).map((l) => (
          <div key={l.id} className="text-xs">
            <div className="text-muted-foreground">
              {t("components.sharing.clientApprovalModal.contentCount", { count: l.count })} ·{" "}
              {l.note_at ? new Date(l.note_at).toLocaleString(lang === "tr" ? "tr-TR" : "en-US") : ""}
            </div>
            <p className="whitespace-pre-wrap">{l.note}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function ClientApprovalModal({ open, onOpenChange, clientId, clientName, weekIso }: Props) {
  const { t } = useI18n()
  const haftaEtiket = [
    t("components.sharing.clientApprovalModal.weekPrev"),
    t("components.sharing.clientApprovalModal.weekThis"),
    t("components.sharing.clientApprovalModal.weekNext"),
  ]
  const { data, isLoading, isError } = useApprovalCandidates(clientId, weekIso, open)
  const uret = useApprovalLink(clientId)
  const [secili, setSecili] = useState<Set<number>>(new Set())

  // The modal always starts clean on open: generating a link with a selection left
  // over from the previous round would break the "know what you're sending" contract.
  useEffect(() => { if (open) setSecili(new Set()) }, [open, clientId, weekIso])

  const tumu = useMemo(
    () => (data ?? []).flatMap((w) => w.uploads), [data])
  const bosMu = !tumu.length

  function toggle(id: number) {
    setSecili((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id); else n.add(id)
      return n
    })
  }

  function hepsi() {
    setSecili(secili.size === tumu.length ? new Set() : new Set(tumu.map((u) => u.id)))
  }

  async function linkiKopyala() {
    if (!secili.size) return
    // The list order is sent, NOT the selection order: the client should see the
    // content in weekly order (click order is arbitrary).
    const ids = tumu.filter((u) => secili.has(u.id)).map((u) => u.id)
    try {
      const r = await uret.mutateAsync({ client_id: clientId, upload_ids: ids })
      const url = `${window.location.origin}/onay/${r.token}`
      await navigator.clipboard.writeText(approvalShareMessage(url, r.count, t))
      toast.success(t("components.sharing.clientApprovalModal.linkCopied", { count: r.count }))
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.sharing.clientApprovalModal.linkFailed"))
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("components.sharing.clientApprovalModal.title", { clientName })}</DialogTitle>
          <DialogDescription>
            {t("components.sharing.clientApprovalModal.description")}
          </DialogDescription>
        </DialogHeader>

        {isLoading && <Skeleton className="h-40 w-full" />}
        {isError && <p className="text-sm text-destructive">{t("components.sharing.clientApprovalModal.listError")}</p>}

        {data && (
          <div className="space-y-4">
            <MusteriNotlari clientId={clientId} open={open} />
            {bosMu ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {t("components.sharing.clientApprovalModal.nothingToSend")}
              </p>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <Badge variant="outline">
                    {t("components.sharing.clientApprovalModal.selectedCount", { selected: secili.size, total: tumu.length })}
                  </Badge>
                  <Button variant="ghost" size="sm" onClick={hepsi}>
                    {secili.size === tumu.length
                      ? t("components.sharing.clientApprovalModal.clearSelection")
                      : t("components.sharing.clientApprovalModal.selectAll")}
                  </Button>
                </div>
                {data.map((w, i) => (
                  <HaftaBolumu key={w.week_iso} etiket={haftaEtiket[i] ?? w.week_iso}
                    hafta={w} secili={secili} toggle={toggle} />
                ))}
              </>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("components.sharing.clientApprovalModal.close")}
          </Button>
          <Button onClick={linkiKopyala} disabled={!secili.size || uret.isPending}>
            {uret.isPending
              ? t("components.sharing.clientApprovalModal.preparing")
              : secili.size
                ? t("components.sharing.clientApprovalModal.copyLinkCount", { count: secili.size })
                : t("components.sharing.clientApprovalModal.copyLink")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
