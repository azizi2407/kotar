// Marka Rehberi — müşteri "Ayar"ının üretim rollerine açık SALT-OKU görünümü.
//
// Neden ayrı sayfa: `/clients/:id` müşteri detayı ticari (KDV/ücret) ve iletişim
// (telefon/e-posta/adres) bilgisi taşıyor, o yüzden management'ta kalıyor. Burası
// yalnız marka rehberini gösterir — düzenleme yolu YOK, `usePutVaultAyar` hiç
// import edilmez. Yetki backend'de: `GET /clients/:id/vault-ayar` READ_ROLES,
// PUT hâlâ management.
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { BookOpen, ExternalLink, Search } from "lucide-react"

import { AyarView } from "@/components/clients/AyarView"
import { ReferenceAccounts } from "@/components/clients/ReferenceAccounts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useClients, useVaultAyar } from "@/lib/clients"
import { cn } from "@/lib/utils"
import { trFold } from "@/lib/week"

export function MarkaRehberiPage() {
  const { isManagement } = useAuth()
  const { data: clients, isLoading: loadingClients } = useClients({ status: "active", q: "" })
  const [term, setTerm] = useState("")
  // Seçili müşteri URL'de (2026-08-04): müşteri medya sayfasındaki "Marka Rehberi"
  // bağlantısı `?client=<id>` ile buraya derin link atıyor — seçim local state'te
  // kalsaydı link müşteriyi açamaz, sayfa listenin ilkine düşerdi.
  const [params, setParams] = useSearchParams()
  const selected = Number(params.get("client")) || null
  const setSelected = (id: number) =>
    setParams((p) => { p.set("client", String(id)); return p }, { replace: true })

  // Arama client-side: liste küçük (~30 müşteri) ve `trFold` Türkçe 'İ/ı'yı
  // doğru katlıyor — sunucunun `ilike`'ı bunu garanti etmiyor.
  const filtered = useMemo(() => {
    const list = clients ?? []
    const t = trFold(term.trim())
    if (!t) return list
    return list.filter((c) => trFold(c.name).includes(t) || trFold(c.sector ?? "").includes(t))
  }, [clients, term])

  // Seçim yoksa ilk müşteriye düş — sayfa boş açılmasın.
  const activeId = selected ?? filtered[0]?.id ?? null
  const activeClient = (clients ?? []).find((c) => c.id === activeId) ?? null
  const { data, isLoading, isError, error } = useVaultAyar(activeId)

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <BookOpen className="h-5 w-5" /> Marka Rehberi
        </h1>
        <p className="text-sm text-muted-foreground">
          Müşterinin marka sesi, hedef kitle, yasakları ve içerik kuralları. Salt-okunur —
          değişiklik gerekiyorsa yöneticine söyle.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <Card className="h-fit md:sticky md:top-4">
          <CardContent className="space-y-2 pt-6">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input className="pl-8" placeholder="Müşteri ara…" value={term}
                onChange={(e) => setTerm(e.target.value)} />
            </div>
            <div className="max-h-[60vh] space-y-0.5 overflow-y-auto">
              {loadingClients && <Skeleton className="h-40 w-full" />}
              {!loadingClients && filtered.length === 0 && (
                <p className="py-4 text-center text-sm text-muted-foreground">Eşleşen müşteri yok.</p>
              )}
              {filtered.map((c) => (
                <button key={c.id} type="button" onClick={() => setSelected(c.id)}
                  className={cn("w-full rounded-md px-2 py-1.5 text-left text-sm",
                                c.id === activeId ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted")}>
                  <div className="truncate">{c.name}</div>
                  {c.sector && <div className="truncate text-xs text-muted-foreground">{c.sector}</div>}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">{activeClient?.name ?? "Müşteri seç"}</CardTitle>
            {isManagement && activeId != null && (
              <Link to={`/clients/${activeId}`}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                Müşteri sayfası <ExternalLink className="h-3 w-3" />
              </Link>
            )}
          </CardHeader>
          <CardContent>
            {activeId == null ? (
              <p className="text-sm text-muted-foreground">Soldan bir müşteri seç.</p>
            ) : isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : isError ? (
              <p className="text-sm text-destructive">
                Rehber okunamadı: {error instanceof ApiError ? error.message : "bilinmeyen hata"}
              </p>
            ) : data?.onboarding ? (
              <p className="text-sm text-muted-foreground">
                Bu müşterinin marka rehberi henüz doldurulmamış.
              </p>
            ) : data ? (
              <AyarView ayar={data.ayar} />
            ) : null}

            {/* Örnek hesaplar (2026-08-07) marka rehberinin ALTINDA: aynı sayfada
                "bu marka nasıl konuşur" ve "kimler örnek alınabilir" yan yana dursun.
                Rehber doldurulmamış olsa bile gösterilir — ikisi bağımsız. */}
            {activeId != null && (
              <div className="mt-6 border-t pt-5">
                <ReferenceAccounts clientId={activeId} />
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
