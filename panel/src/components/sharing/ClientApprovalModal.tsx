// Müşteri onay linki modalı (2026-08-06) — DesignerBoard'daki "Onay Linki"
// düğmesinin ikizi DEĞİL: orası (müşteri, hafta) kapsamını olduğu gibi gönderir,
// burada üç haftalık pencereden içerikleri tek tek seçersin ve seçim linke
// dondurulur. Üretilen link `/onay/<token>` (özel günler sayfasının görsel dili).
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import {
  approvalShareMessage, thumbnailUrl, useApprovalCandidates, useApprovalLink,
  useApprovalLinks, type ApprovalCandidate, type ApprovalWeek,
} from "@/lib/sharing"
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

const HAFTA_ETIKET = ["Önceki hafta", "Bu hafta", "Sonraki hafta"]

function KararRozeti({ up }: { up: ApprovalCandidate }) {
  if (!up.review?.status) return null
  const onayli = up.review.status === "approved"
  return (
    <span className="absolute left-1 top-1 rounded bg-background/90 px-1 text-[10px]"
      title={onayli ? "Müşteri onayladı" : up.review.note || "Revize istendi"}>
      {onayli ? "✅" : "📝"}
    </span>
  )
}

function HaftaBolumu({ etiket, hafta, secili, toggle }: {
  etiket: string; hafta: ApprovalWeek; secili: Set<number>; toggle: (id: number) => void
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-sm font-medium">
        {etiket} <span className="text-xs font-normal text-muted-foreground">{hafta.week_iso}</span>
      </div>
      {!hafta.uploads.length ? (
        <p className="text-xs text-muted-foreground">Gönderilecek içerik yok.</p>
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
                    daha önce gönderildi
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

// Müşterinin onay sayfasındaki not defterine yazdıkları — panelde okumanın tek yeri.
function MusteriNotlari({ clientId, open }: { clientId: number; open: boolean }) {
  const { data } = useApprovalLinks(clientId, open)
  const notlu = (data ?? []).filter((l) => l.note)
  if (!notlu.length) return null
  return (
    <div className="rounded-md border bg-muted/40 p-3">
      <div className="mb-2 text-sm font-medium">Müşteri notları</div>
      <div className="space-y-2">
        {notlu.slice(0, 3).map((l) => (
          <div key={l.id} className="text-xs">
            <div className="text-muted-foreground">
              {l.count} içerik · {l.note_at ? new Date(l.note_at).toLocaleString("tr-TR") : ""}
            </div>
            <p className="whitespace-pre-wrap">{l.note}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function ClientApprovalModal({ open, onOpenChange, clientId, clientName, weekIso }: Props) {
  const { data, isLoading, isError } = useApprovalCandidates(clientId, weekIso, open)
  const uret = useApprovalLink(clientId)
  const [secili, setSecili] = useState<Set<number>>(new Set())

  // Modal her açılışta temiz başlar: önceki turdan kalan seçimle link üretmek
  // "ne gönderdiğini bil" sözleşmesini bozardı.
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
    // Seçim sırası DEĞİL, listedeki sıra gönderilir: müşteri içerikleri hafta
    // sırasıyla görsün (tıklama sırası tesadüfi).
    const ids = tumu.filter((u) => secili.has(u.id)).map((u) => u.id)
    try {
      const r = await uret.mutateAsync({ client_id: clientId, upload_ids: ids })
      const url = `${window.location.origin}/onay/${r.token}`
      await navigator.clipboard.writeText(approvalShareMessage(url, r.count))
      toast.success(`Onay linki kopyalandı (${r.count} içerik)`)
      onOpenChange(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Link üretilemedi")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Müşteri Onay Linki · {clientName}</DialogTitle>
          <DialogDescription>
            Henüz yayınlanmamış içeriklerden müşteriye sunulacakları seçin. Seçim linke
            sabitlenir — sonradan yüklenenler bu linke girmez.
          </DialogDescription>
        </DialogHeader>

        {isLoading && <Skeleton className="h-40 w-full" />}
        {isError && <p className="text-sm text-destructive">İçerik listesi alınamadı.</p>}

        {data && (
          <div className="space-y-4">
            <MusteriNotlari clientId={clientId} open={open} />
            {bosMu ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                Bu üç haftada gönderilebilecek içerik yok (yayınlananlar listelenmez).
              </p>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <Badge variant="outline">{secili.size} / {tumu.length} seçili</Badge>
                  <Button variant="ghost" size="sm" onClick={hepsi}>
                    {secili.size === tumu.length ? "Seçimi temizle" : "Tümünü seç"}
                  </Button>
                </div>
                {data.map((w, i) => (
                  <HaftaBolumu key={w.week_iso} etiket={HAFTA_ETIKET[i] ?? w.week_iso}
                    hafta={w} secili={secili} toggle={toggle} />
                ))}
              </>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Kapat</Button>
          <Button onClick={linkiKopyala} disabled={!secili.size || uret.isPending}>
            {uret.isPending ? "Hazırlanıyor…" : `Linki kopyala${secili.size ? ` (${secili.size})` : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
