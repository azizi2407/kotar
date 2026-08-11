// Client Tracking (/musteri-takip) — management only. Per-client sellable work
// items (trademark registration, catalog, website, custom project…) + a dated
// activity log. Last ad / last shoot / team are derived on the backend, not entered manually.
// Clicking a row expands it; detail data is only fetched at that point.
import { useMemo, useState } from "react"
import { ClipboardList, ListChecks, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { ClientTrackingRow, TRACKING_COLS } from "@/components/musteri-takip/ClientTrackingRow"
import { TrackingEntryDialog } from "@/components/musteri-takip/TrackingEntryDialog"
import { TrackingItemsDialog } from "@/components/musteri-takip/TrackingItemsDialog"
import { useI18n } from "@/lib/i18n"
import {
  daysSince, useClientTracking, useEntryStatusLabels,
  type TrackingItem, type TrackingRow,
} from "@/lib/musteri-takip"
import { trFold } from "@/lib/week"

type SortKey = "name" | "opportunity" | "ad" | "activity"

function sortLabels(t: (key: string) => string): Record<SortKey, string> {
  return {
    name: t("pages.clientTracking.sort.name"),
    opportunity: t("pages.clientTracking.sort.opportunity"),
    ad: t("pages.clientTracking.sort.ad"),
    activity: t("pages.clientTracking.sort.activity"),
  }
}

/** In sorting, "never" counts as the stalest — it should come first in the list. */
function staleRank(iso: string | null): number {
  const days = daysSince(iso)
  return days == null ? Number.MAX_SAFE_INTEGER : days
}

export function MusteriTakipPage() {
  const { t } = useI18n()
  const ENTRY_STATUS_LABELS = useEntryStatusLabels()
  const SORT_LABELS = sortLabels(t)
  const [q, setQ] = useState("")
  const [itemFilter, setItemFilter] = useState<number | null>(null)
  const [statusFilter, setStatusFilter] = useState("")
  const [onlyMissingAds, setOnlyMissingAds] = useState(false)
  const [sort, setSort] = useState<SortKey>("name")
  const [expanded, setExpanded] = useState<number | null>(null)
  const [editing, setEditing] = useState<{ row: TrackingRow; item: TrackingItem } | null>(null)
  const [itemsOpen, setItemsOpen] = useState(false)

  const { data, isLoading, isError } = useClientTracking()
  const items = data?.items ?? []

  const rows = useMemo(() => {
    const needle = trFold(q.trim())
    const filtered = (data?.clients ?? []).filter((row) => {
      if (needle && !trFold(row.client_name).includes(needle)) return false
      if (onlyMissingAds && row.signals.last_ad_date) return false
      if (itemFilter != null) {
        const status = row.entries[String(itemFilter)]?.status ?? "yok"
        if (statusFilter && status !== statusFilter) return false
        if (!statusFilter && status !== "yok") return false
      }
      return true
    })
    const sorted = [...filtered]
    if (sort === "opportunity") {
      sorted.sort((a, b) => b.summary.opportunity - a.summary.opportunity
        || a.client_name.localeCompare(b.client_name, "tr"))
    } else if (sort === "ad") {
      sorted.sort((a, b) => staleRank(b.signals.last_ad_date) - staleRank(a.signals.last_ad_date))
    } else if (sort === "activity") {
      sorted.sort((a, b) => staleRank(b.last_note?.happened_on ?? null)
        - staleRank(a.last_note?.happened_on ?? null))
    }
    return sorted
  }, [data, q, itemFilter, statusFilter, onlyMissingAds, sort])

  const totalOpportunity = rows.reduce((sum, r) => sum + r.summary.opportunity, 0)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <ClipboardList className="h-5 w-5" /> {t("pages.clientTracking.title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("pages.clientTracking.subtitle")}
          </p>
        </div>
        <Button variant="outline" onClick={() => setItemsOpen(true)}>
          <ListChecks className="mr-1 h-4 w-4" /> {t("pages.clientTracking.manageItems")}
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.clientTracking.search")}</Label>
          <div className="relative w-full sm:w-56">
            <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="h-9 pl-8" placeholder={t("pages.clientTracking.searchPlaceholder")} value={q}
              onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.clientTracking.item")}</Label>
          <select
            className="h-9 w-48 rounded-md border bg-background px-2 text-sm"
            value={itemFilter ?? ""}
            onChange={(e) => setItemFilter(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{t("pages.clientTracking.all")}</option>
            {items.filter((i) => i.active).map((i) => (
              <option key={i.id} value={i.id}>{i.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.clientTracking.itemStatus")}</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={statusFilter}
            disabled={itemFilter == null}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">{t("pages.clientTracking.missingOnes")}</option>
            {Object.entries(ENTRY_STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{t("pages.clientTracking.sortLabel")}</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            {Object.entries(SORT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <label className="flex h-9 items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 cursor-pointer accent-primary"
            checked={onlyMissingAds} onChange={(e) => setOnlyMissingAds(e.target.checked)} />
          {t("pages.clientTracking.noAdsEver")}
        </label>
        <div className="ml-auto text-sm text-muted-foreground">
          {t("pages.clientTracking.clientCount", { count: rows.length })} · <span className="font-semibold text-foreground">{totalOpportunity}</span> {t("pages.clientTracking.openOpportunity")}
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("pages.clientTracking.table.client")}</TableHead>
              <TableHead className="hidden sm:table-cell">{t("pages.clientTracking.table.sector")}</TableHead>
              <TableHead>{t("pages.clientTracking.table.items")}</TableHead>
              <TableHead>{t("pages.clientTracking.table.lastAd")}</TableHead>
              <TableHead className="hidden md:table-cell">{t("pages.clientTracking.table.lastShoot")}</TableHead>
              <TableHead className="hidden lg:table-cell">{t("pages.clientTracking.table.team")}</TableHead>
              <TableHead>{t("pages.clientTracking.table.opportunity")}</TableHead>
              <TableHead className="hidden xl:table-cell">{t("pages.clientTracking.table.lastActivity")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && [...Array(5)].map((_, i) => (
              <TableRow key={i}>
                <TableCell colSpan={TRACKING_COLS}><Skeleton className="h-5 w-full" /></TableCell>
              </TableRow>
            ))}
            {isError && (
              <TableRow>
                <TableCell colSpan={TRACKING_COLS} className="py-8 text-center text-destructive">
                  {t("pages.clientTracking.loadFailed")}
                </TableCell>
              </TableRow>
            )}
            {data && rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={TRACKING_COLS} className="py-8 text-center text-muted-foreground">
                  {q || itemFilter != null || onlyMissingAds
                    ? t("pages.clientTracking.noMatchingClients")
                    : t("pages.clientTracking.noClientsYet")}
                </TableCell>
              </TableRow>
            )}
            {rows.map((row) => (
              <ClientTrackingRow
                key={row.client_id}
                row={row}
                items={items}
                expanded={expanded === row.client_id}
                onToggle={() => setExpanded(expanded === row.client_id ? null : row.client_id)}
                onEditEntry={(item) => setEditing({ row, item })}
              />
            ))}
          </TableBody>
        </Table>
      </div>

      {editing && (
        <TrackingEntryDialog
          clientId={editing.row.client_id}
          clientName={editing.row.client_name}
          item={editing.item}
          entry={editing.row.entries[String(editing.item.id)]}
          onClose={() => setEditing(null)}
        />
      )}
      {itemsOpen && <TrackingItemsDialog onClose={() => setItemsOpen(false)} />}
    </div>
  )
}
