// Müşteri örnek (referans) hesapları — Marka Rehberi'nin alt bölümü (2026-08-07).
//
// Tasarımcı/videografın "bu müşteri için nasıl içerik üretilir" sorusuna bakacağı
// örnekler. ONAY KAPISI var: hesaplar aday doğar, yönetim karara bağlar, üretim
// rollerine yalnız onaylananlar görünür (süzgeç BACKEND'de — panel kuralı yeniden
// kurmaz, aksi halde yetki iki yerde yaşardı).
import { useState } from "react"
import { AtSign, Check, ExternalLink, Loader2, Plus, Trash2, X } from "lucide-react"
import { toast } from "sonner"

import {
  useAddReferenceAccount, useDeleteReferenceAccount, useReferenceAccounts,
  useUpdateReferenceAccount, type ReferenceAccount,
} from "@/lib/reference"
import { useAuth } from "@/lib/auth"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

function sayi(n: number | null) {
  if (n == null) return null
  return n >= 1000 ? `${Math.round(n / 1000)}B` : String(n)
}

function HesapSatiri({ acc, clientId, yonetim }: {
  acc: ReferenceAccount; clientId: number; yonetim: boolean
}) {
  const guncelle = useUpdateReferenceAccount(clientId)
  const sil = useDeleteReferenceAccount(clientId)
  const [not, setNot] = useState(acc.note ?? "")
  const [notAcik, setNotAcik] = useState(false)

  const aday = acc.status === "candidate"
  const red = acc.status === "rejected"

  return (
    <div className={cn("rounded-lg border p-3", red && "opacity-50")}>
      <div className="flex flex-wrap items-center gap-2">
        <a href={acc.url} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1.5 font-medium hover:text-primary hover:underline">
          <AtSign className="h-4 w-4" />{acc.handle}
          <ExternalLink className="h-3 w-3 opacity-60" />
        </a>
        {acc.followers != null && (
          <Badge variant="outline" className="text-[10px]">{sayi(acc.followers)} takipçi</Badge>
        )}
        {aday && <Badge className="bg-amber-500 text-[10px] text-white">aday</Badge>}
        {red && <Badge variant="outline" className="text-[10px]">reddedildi</Badge>}

        {yonetim && (
          <div className="ml-auto flex items-center gap-1">
            {/* Karar geri alınabilir: yanlışlıkla reddedilen hesap tekrar
                onaylanabilsin diye iki düğme de her durumda duruyor. */}
            <Button variant="ghost" size="sm" title="Onayla"
              disabled={guncelle.isPending}
              onClick={() => guncelle.mutate({ id: acc.id, status: "approved" })}>
              <Check className={cn("h-3.5 w-3.5",
                acc.status === "approved" ? "text-emerald-600" : "text-muted-foreground")} />
            </Button>
            <Button variant="ghost" size="sm" title="Reddet"
              disabled={guncelle.isPending}
              onClick={() => guncelle.mutate({ id: acc.id, status: "rejected" })}>
              <X className={cn("h-3.5 w-3.5", red ? "text-destructive" : "text-muted-foreground")} />
            </Button>
            <Button variant="ghost" size="sm" title="Not"
              onClick={() => setNotAcik((a) => !a)}>
              <span className="text-xs">✎</span>
            </Button>
            <Button variant="ghost" size="sm" title="Sil"
              onClick={() => {
                if (!confirm(`@${acc.handle} listeden silinsin mi?`)) return
                sil.mutate(acc.id)
              }}>
              <Trash2 className="h-3.5 w-3.5 text-destructive" />
            </Button>
          </div>
        )}
      </div>

      {acc.title && <div className="mt-0.5 text-sm text-muted-foreground">{acc.title}</div>}
      {acc.note && !notAcik && <p className="mt-1 text-sm">{acc.note}</p>}

      {yonetim && notAcik && (
        <div className="mt-2 flex gap-2">
          <Input value={not} onChange={(e) => setNot(e.target.value)}
            placeholder="Neden örnek? (ör. Reels kurgusu, ürün çekimi)" className="h-8" />
          <Button size="sm" onClick={() => {
            guncelle.mutate({ id: acc.id, note: not }, {
              onSuccess: () => { setNotAcik(false); toast.success("Not kaydedildi") },
            })
          }}>Kaydet</Button>
        </div>
      )}
    </div>
  )
}

export function ReferenceAccounts({ clientId }: { clientId: number }) {
  const { isManagement } = useAuth()
  const { data, isLoading } = useReferenceAccounts(clientId)
  const ekle = useAddReferenceAccount(clientId)
  const [yeni, setYeni] = useState("")

  const hesaplar = data ?? []
  // Yönetim görünümünde aday/onaylı ayrımı; üretim rolleri zaten yalnız
  // onaylanmışları alıyor (backend süzüyor), onlarda bu ayrım hiç görünmez.
  const adaylar = hesaplar.filter((a) => a.status === "candidate")
  const onayli = hesaplar.filter((a) => a.status === "approved")
  const reddedilen = hesaplar.filter((a) => a.status === "rejected")

  function hesapEkle() {
    const h = yeni.trim()
    if (!h) return
    ekle.mutate({ handle: h }, {
      onSuccess: () => { setYeni(""); toast.success("Hesap eklendi") },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Eklenemedi"),
    })
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="font-medium">Örnek Hesaplar</h3>
        <p className="text-sm text-muted-foreground">
          Aynı sektörden, içerik üretiminde örnek alınabilecek Instagram hesapları.
        </p>
      </div>

      {isLoading && <Skeleton className="h-24 w-full" />}

      {!isLoading && !hesaplar.length && (
        <p className="py-4 text-sm text-muted-foreground">
          Bu müşteri için henüz örnek hesap yok.
        </p>
      )}

      {adaylar.length > 0 && isManagement && (
        <div className="space-y-2">
          <div className="text-xs font-semibold tracking-wide text-amber-600 uppercase">
            İncelenecek · {adaylar.length}
          </div>
          {adaylar.map((a) => (
            <HesapSatiri key={a.id} acc={a} clientId={clientId} yonetim={isManagement} />
          ))}
        </div>
      )}

      {onayli.length > 0 && (
        <div className="space-y-2">
          {isManagement && adaylar.length > 0 && (
            <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              Onaylı · {onayli.length}
            </div>
          )}
          {onayli.map((a) => (
            <HesapSatiri key={a.id} acc={a} clientId={clientId} yonetim={isManagement} />
          ))}
        </div>
      )}

      {isManagement && reddedilen.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Reddedilenler ({reddedilen.length})
          </summary>
          <div className="mt-2 space-y-2">
            {reddedilen.map((a) => (
              <HesapSatiri key={a.id} acc={a} clientId={clientId} yonetim={isManagement} />
            ))}
          </div>
        </details>
      )}

      {isManagement && (
        <div className="flex gap-2 pt-1">
          <Input value={yeni} onChange={(e) => setYeni(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") hesapEkle() }}
            placeholder="@kullaniciadi veya instagram.com/… yapıştır" className="h-9 max-w-sm" />
          <Button size="sm" onClick={hesapEkle} disabled={ekle.isPending || !yeni.trim()}>
            {ekle.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Ekle
          </Button>
        </div>
      )}
    </div>
  )
}
