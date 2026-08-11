// Ad Tracking (/reklam) — management only. Per-client ad campaigns: date range,
// amount spent, platform, status, result metrics, notes.
// Filters + summary (total & per-client breakdown) on top, records table below.
import { useMemo, useState } from "react"
import { GanttChartSquare, List, Megaphone, Pencil, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { AdsGantt } from "@/components/ads/AdsGantt"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useClients } from "@/lib/clients"
import {
  fmtDateRange, fmtTRY, useAds, useCreateAd, useDeleteAd, usePlatformLabels, useStatusLabels,
  useUpdateAd, type AdCampaign, type AdFilters, type AdInput,
} from "@/lib/ads"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

const STATUS_TONE: Record<string, string> = {
  planned: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  finished: "bg-muted text-muted-foreground",
}

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

export function AdsPage() {
  const { t, lang } = useI18n()
  const PLATFORM_LABELS = usePlatformLabels()
  const STATUS_LABELS = useStatusLabels()
  // On first load, show ACTIVE ads in timeline view (project owner's request, 2026-07-24).
  const [filters, setFilters] = useState<AdFilters>({ status: "active" })
  const [view, setView] = useState<"timeline" | "list">("timeline")
  const [editing, setEditing] = useState<AdCampaign | null>(null)
  const [adding, setAdding] = useState(false)

  const adsQ = useAds(filters)
  const clientsQ = useClients({ status: "active", q: "" })
  const del = useDeleteAd()

  const campaigns = adsQ.data?.campaigns ?? []
  const summary = adsQ.data?.summary

  function onDelete(c: AdCampaign) {
    if (!window.confirm(t("pages.ads.confirmDelete", { name: c.title || c.client_name || "" }))) return
    del.mutate(c.id, {
      onSuccess: () => toast.success(t("pages.ads.deleted")),
      onError: (e) => toast.error(e instanceof Error ? e.message : t("pages.ads.deleteFailed")),
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Megaphone className="h-5 w-5" /> {t("pages.ads.title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("pages.ads.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border p-0.5">
            <Button size="sm" variant={view === "timeline" ? "secondary" : "ghost"} className="h-7 px-2"
              onClick={() => setView("timeline")} title={t("pages.ads.timeline")}>
              <GanttChartSquare className="mr-1 h-4 w-4" /> {t("pages.ads.timeline")}
            </Button>
            <Button size="sm" variant={view === "list" ? "secondary" : "ghost"} className="h-7 px-2"
              onClick={() => setView("list")} title={t("pages.ads.list")}>
              <List className="mr-1 h-4 w-4" /> {t("pages.ads.list")}
            </Button>
          </div>
          <Button onClick={() => setAdding(true)}>
            <Plus className="mr-1 h-4 w-4" /> {t("pages.ads.addAd")}
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.ads.client")}</Label>
          <select
            className="h-9 w-48 rounded-md border bg-background px-2 text-sm"
            value={filters.client_id ?? ""}
            onChange={(e) => setFilters({ ...filters, client_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">{t("pages.ads.all")}</option>
            {(clientsQ.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.ads.status")}</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={filters.status ?? ""}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          >
            <option value="">{t("pages.ads.all")}</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.ads.platform")}</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={filters.platform ?? ""}
            onChange={(e) => setFilters({ ...filters, platform: e.target.value })}
          >
            <option value="">{t("pages.ads.all")}</option>
            {Object.entries(PLATFORM_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.ads.startDate")}</Label>
          <Input type="date" className="h-9 w-40" value={filters.from ?? ""}
            onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.ads.endDate")}</Label>
          <Input type="date" className="h-9 w-40" value={filters.to ?? ""}
            onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
        </div>
        {(filters.client_id || filters.status || filters.platform || filters.from || filters.to) && (
          <Button variant="ghost" size="sm" onClick={() => setFilters({})}>{t("pages.ads.clearFilters")}</Button>
        )}
      </div>

      {/* Summary */}
      {summary && (
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border p-4">
            <div className="text-xs text-muted-foreground">{t("pages.ads.totalSpendFiltered")}</div>
            <div className="mt-1 text-2xl font-semibold">{fmtTRY(summary.total_amount, lang)}</div>
            <div className="text-xs text-muted-foreground">{t("pages.ads.recordCount", { count: summary.count })}</div>
          </div>
          <div className="rounded-lg border p-4 md:col-span-2">
            <div className="mb-2 text-xs text-muted-foreground">{t("pages.ads.byClientTotal")}</div>
            {summary.by_client.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("pages.ads.noRecords")}</p>
            ) : (
              <div className="max-h-40 overflow-y-auto">
                <table className="w-full text-sm">
                  <tbody>
                    {summary.by_client.map((b) => (
                      <tr key={b.client_id} className="border-b last:border-0">
                        <td className="py-1">{b.client_name}</td>
                        <td className="py-1 text-right text-muted-foreground">{t("pages.ads.adCount", { count: b.count })}</td>
                        <td className="py-1 text-right font-medium">{fmtTRY(b.total, lang)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* List */}
      {adsQ.isLoading && <Skeleton className="h-64 w-full" />}
      {adsQ.isError && (
        <p className="text-sm text-red-600">
          {adsQ.error instanceof Error ? adsQ.error.message : t("pages.ads.loadFailed")}
        </p>
      )}
      {adsQ.isSuccess && view === "timeline" && (
        <AdsGantt campaigns={campaigns} onSelect={(c) => setEditing(c)} />
      )}

      {adsQ.isSuccess && view === "list" && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2">{t("pages.ads.client")}</th>
                <th className="px-3 py-2">{t("pages.ads.campaign")}</th>
                <th className="px-3 py-2">{t("pages.ads.platform")}</th>
                <th className="px-3 py-2">{t("pages.ads.dateRange")}</th>
                <th className="px-3 py-2 text-right">{t("pages.ads.spent")}</th>
                <th className="px-3 py-2">{t("pages.ads.status")}</th>
                <th className="px-3 py-2 text-right">{t("pages.ads.reachClicks")}</th>
                <th className="px-3 py-2">{t("pages.ads.note")}</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {campaigns.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-muted-foreground">
                  {t("pages.ads.emptyList")}
                </td></tr>
              )}
              {campaigns.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="px-3 py-2 font-medium">{c.client_name}</td>
                  <td className="px-3 py-2">{c.title || "—"}</td>
                  <td className="px-3 py-2 text-muted-foreground">{PLATFORM_LABELS[c.platform] ?? c.platform}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{fmtDateRange(c.start_date, c.end_date, t("pages.ads.ongoing"), lang)}</td>
                  <td className="px-3 py-2 text-right font-medium">{fmtTRY(c.amount_spent, lang)}</td>
                  <td className="px-3 py-2">
                    <span className={cn("rounded px-1.5 py-0.5 text-xs", STATUS_TONE[c.status])}>
                      {STATUS_LABELS[c.status] ?? c.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-muted-foreground">
                    {c.reach != null || c.clicks != null
                      ? `${c.reach?.toLocaleString(lang === "tr" ? "tr-TR" : "en-US") ?? "—"} / ${c.clicks?.toLocaleString(lang === "tr" ? "tr-TR" : "en-US") ?? "—"}`
                      : "—"}
                  </td>
                  <td className="max-w-[16rem] truncate px-3 py-2 text-muted-foreground" title={c.notes || ""}>
                    {c.notes || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1">
                      <Button size="icon" variant="ghost" title={t("pages.ads.edit")} onClick={() => setEditing(c)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" title={t("pages.ads.delete")} disabled={del.isPending}
                        onClick={() => onDelete(c)}>
                        <Trash2 className="h-4 w-4 text-red-600" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(adding || editing) && (
        <AdDialog
          campaign={editing}
          clients={(clientsQ.data ?? []).map((c) => ({ id: c.id, name: c.name }))}
          onClose={() => { setAdding(false); setEditing(null) }}
        />
      )}
    </div>
  )
}

function AdDialog({
  campaign, clients, onClose,
}: {
  campaign: AdCampaign | null
  clients: { id: number; name: string }[]
  onClose: () => void
}) {
  const { t } = useI18n()
  const PLATFORM_LABELS = usePlatformLabels()
  const STATUS_LABELS = useStatusLabels()
  const create = useCreateAd()
  const update = useUpdateAd()
  const [form, setForm] = useState<AdInput>(() => ({
    client_id: campaign?.client_id ?? (clients[0]?.id ?? 0),
    title: campaign?.title ?? "",
    platform: campaign?.platform ?? "meta",
    start_date: campaign?.start_date ?? todayStr(),
    end_date: campaign?.end_date ?? "",
    amount_spent: campaign?.amount_spent ?? "",
    status: campaign?.status ?? "active",
    reach: campaign?.reach ?? "",
    clicks: campaign?.clicks ?? "",
    notes: campaign?.notes ?? "",
  }))
  const busy = create.isPending || update.isPending
  const title = useMemo(
    () => (campaign ? t("pages.ads.editTitle") : t("pages.ads.addAd")),
    [campaign, t],
  )

  async function submit() {
    if (!form.client_id) { toast.error(t("pages.ads.selectClient")); return }
    if (!form.start_date) { toast.error(t("pages.ads.startDateRequired")); return }
    const body: AdInput = { ...form, end_date: form.end_date || null,
      reach: form.reach === "" ? null : form.reach, clicks: form.clicks === "" ? null : form.clicks }
    try {
      if (campaign) await update.mutateAsync({ id: campaign.id, body })
      else await create.mutateAsync(body)
      toast.success(campaign ? t("pages.ads.updated") : t("pages.ads.added"))
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("pages.ads.saveFailed"))
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 space-y-1">
              <Label>{t("pages.ads.client")}</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.client_id}
                onChange={(e) => setForm({ ...form, client_id: Number(e.target.value) })}
              >
                {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div className="col-span-2 space-y-1">
              <Label>{t("pages.ads.campaignName")}</Label>
              <Input value={form.title ?? ""} placeholder={t("pages.ads.campaignNamePlaceholder")}
                onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>{t("pages.ads.start")}</Label>
              <Input type="date" value={form.start_date}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>{t("pages.ads.end")} <span className="text-xs text-muted-foreground">{t("pages.ads.endEmptyHint")}</span></Label>
              <Input type="date" value={form.end_date ?? ""}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>{t("pages.ads.amountSpent")}</Label>
              <Input inputMode="decimal" placeholder="0,00" value={String(form.amount_spent ?? "")}
                onChange={(e) => setForm({ ...form, amount_spent: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>{t("pages.ads.platform")}</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.platform}
                onChange={(e) => setForm({ ...form, platform: e.target.value as AdInput["platform"] })}
              >
                {Object.entries(PLATFORM_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <Label>{t("pages.ads.status")}</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value as AdInput["status"] })}
              >
                {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">{t("pages.ads.reach")}</Label>
                <Input inputMode="numeric" value={String(form.reach ?? "")}
                  onChange={(e) => setForm({ ...form, reach: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">{t("pages.ads.clicks")}</Label>
                <Input inputMode="numeric" value={String(form.clicks ?? "")}
                  onChange={(e) => setForm({ ...form, clicks: e.target.value })} />
              </div>
            </div>
            <div className="col-span-2 space-y-1">
              <Label>{t("pages.ads.notes")}</Label>
              <Textarea rows={3} value={form.notes ?? ""}
                onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>{t("pages.ads.cancel")}</Button>
            <Button onClick={submit} disabled={busy}>{busy ? t("pages.ads.saving") : t("pages.ads.save")}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
