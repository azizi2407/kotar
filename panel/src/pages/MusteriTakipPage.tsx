// Müşteri Takip (/musteri-takip) — yalnız management. Müşteri başına satılabilir iş
// kalemleri (marka tescili, katalog, web sitesi, özel proje…) + tarihli yapılanlar
// günlüğü. Son reklam / son çekim / ekip backend'de türetilir, elle girilmez.
// Satır tıklanınca expand olur; detay verisi ancak o zaman çekilir.
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
import {
  ENTRY_STATUS_LABELS, daysSince, useClientTracking,
  type TrackingItem, type TrackingRow,
} from "@/lib/musteri-takip"
import { trFold } from "@/lib/week"

type SortKey = "name" | "opportunity" | "ad" | "activity"

const SORT_LABELS: Record<SortKey, string> = {
  name: "İsme göre",
  opportunity: "Fırsat (çok → az)",
  ad: "Son reklam (eski → yeni)",
  activity: "Son hareket (eski → yeni)",
}

/** Sıralamada "hiç yok" en bayat sayılır — listenin başına gelsin. */
function staleRank(iso: string | null): number {
  const days = daysSince(iso)
  return days == null ? Number.MAX_SAFE_INTEGER : days
}

export function MusteriTakipPage() {
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
            <ClipboardList className="h-5 w-5" /> Müşteri Takip
          </h1>
          <p className="text-sm text-muted-foreground">
            Hangi müşteride ne var, ne eksik, en son ne yapıldı.
          </p>
        </div>
        <Button variant="outline" onClick={() => setItemsOpen(true)}>
          <ListChecks className="mr-1 h-4 w-4" /> Kalemleri yönet
        </Button>
      </div>

      {/* Filtreler */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <div className="space-y-1">
          <Label className="text-xs">Ara</Label>
          <div className="relative w-full sm:w-56">
            <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="h-9 pl-8" placeholder="Müşteri adı…" value={q}
              onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Kalem</Label>
          <select
            className="h-9 w-48 rounded-md border bg-background px-2 text-sm"
            value={itemFilter ?? ""}
            onChange={(e) => setItemFilter(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Tümü</option>
            {items.filter((i) => i.active).map((i) => (
              <option key={i.id} value={i.id}>{i.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Kalem durumu</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={statusFilter}
            disabled={itemFilter == null}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">Yok olanlar</option>
            {Object.entries(ENTRY_STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Sırala</Label>
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
          Hiç reklam çıkılmamışlar
        </label>
        <div className="ml-auto text-sm text-muted-foreground">
          {rows.length} müşteri · <span className="font-semibold text-foreground">{totalOpportunity}</span> açık fırsat
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Müşteri</TableHead>
              <TableHead className="hidden sm:table-cell">Sektör</TableHead>
              <TableHead>Kalemler</TableHead>
              <TableHead>Son reklam</TableHead>
              <TableHead className="hidden md:table-cell">Son çekim</TableHead>
              <TableHead className="hidden lg:table-cell">Ekip</TableHead>
              <TableHead>Fırsat</TableHead>
              <TableHead className="hidden xl:table-cell">Son hareket</TableHead>
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
                  Liste yüklenemedi.
                </TableCell>
              </TableRow>
            )}
            {data && rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={TRACKING_COLS} className="py-8 text-center text-muted-foreground">
                  {q || itemFilter != null || onlyMissingAds
                    ? "Filtreye uyan müşteri yok."
                    : "Henüz müşteri yok."}
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
