// Brand Guide — a READ-ONLY view of a client's "Ayar" exposed to production roles.
//
// Why a separate page: `/clients/:id` client detail carries commercial (VAT/fee)
// and contact (phone/email/address) info, so it stays management-only. This page
// only shows the brand guide — there's NO edit path, `usePutVaultAyar` is never
// imported. Authorization lives in the backend: `GET /clients/:id/vault-ayar` is
// READ_ROLES, PUT is still management.
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
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { trFold } from "@/lib/week"

export function MarkaRehberiPage() {
  const { t } = useI18n()
  const { isManagement } = useAuth()
  const { data: clients, isLoading: loadingClients } = useClients({ status: "active", q: "" })
  const [term, setTerm] = useState("")
  // Selected client lives in the URL (2026-08-04): the "Brand Guide" link on the
  // client media page deep-links here with `?client=<id>` — if the selection lived
  // in local state, the link couldn't open the client and the page would fall
  // back to the first in the list.
  const [params, setParams] = useSearchParams()
  const selected = Number(params.get("client")) || null
  const setSelected = (id: number) =>
    setParams((p) => { p.set("client", String(id)); return p }, { replace: true })

  // Search is client-side: the list is small (~30 clients) and `trFold` correctly
  // folds Turkish 'İ/ı' — the server's `ilike` doesn't guarantee that.
  const filtered = useMemo(() => {
    const list = clients ?? []
    const t = trFold(term.trim())
    if (!t) return list
    return list.filter((c) => trFold(c.name).includes(t) || trFold(c.sector ?? "").includes(t))
  }, [clients, term])

  // If there's no selection, fall back to the first client — the page shouldn't open empty.
  const activeId = selected ?? filtered[0]?.id ?? null
  const activeClient = (clients ?? []).find((c) => c.id === activeId) ?? null
  const { data, isLoading, isError, error } = useVaultAyar(activeId)

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <BookOpen className="h-5 w-5" /> {t("pages.brandGuide.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("pages.brandGuide.subtitle")}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <Card className="h-fit md:sticky md:top-4">
          <CardContent className="space-y-2 pt-6">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input className="pl-8" placeholder={t("pages.brandGuide.searchPlaceholder")} value={term}
                onChange={(e) => setTerm(e.target.value)} />
            </div>
            <div className="max-h-[60vh] space-y-0.5 overflow-y-auto">
              {loadingClients && <Skeleton className="h-40 w-full" />}
              {!loadingClients && filtered.length === 0 && (
                <p className="py-4 text-center text-sm text-muted-foreground">{t("pages.brandGuide.noMatches")}</p>
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
            <CardTitle className="text-base">{activeClient?.name ?? t("pages.brandGuide.selectClient")}</CardTitle>
            {isManagement && activeId != null && (
              <Link to={`/clients/${activeId}`}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                {t("pages.brandGuide.clientPageLink")} <ExternalLink className="h-3 w-3" />
              </Link>
            )}
          </CardHeader>
          <CardContent>
            {activeId == null ? (
              <p className="text-sm text-muted-foreground">{t("pages.brandGuide.selectClientPrompt")}</p>
            ) : isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : isError ? (
              <p className="text-sm text-destructive">
                {t("pages.brandGuide.loadError", {
                  message: error instanceof ApiError ? error.message : t("pages.brandGuide.unknownError"),
                })}
              </p>
            ) : data?.onboarding ? (
              <p className="text-sm text-muted-foreground">
                {t("pages.brandGuide.notFilled")}
              </p>
            ) : data ? (
              <AyarView ayar={data.ayar} />
            ) : null}

            {/* Reference accounts (2026-08-07) sit BELOW the brand guide: on the
                same page, "how this brand speaks" and "who to take as a reference"
                stand side by side. Shown even if the guide isn't filled in — the
                two are independent. */}
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
