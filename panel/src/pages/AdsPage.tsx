// Reklam Takibi (/reklam) — yalnız management. Müşteri başına reklam çıkışları:
// tarih aralığı, harcanan tutar, platform, durum, sonuç metrikleri, notlar.
// Üstte filtre + özet (toplam & müşteri bazlı kırılım), altta kayıt tablosu.
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
  fmtDateRange, fmtTRY, PLATFORM_LABELS, STATUS_LABELS, useAds, useCreateAd, useDeleteAd,
  useUpdateAd, type AdCampaign, type AdFilters, type AdInput,
} from "@/lib/ads"
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
  // İlk girişte AKTİF reklamlar, zaman çizelgesi görünümünde (proje sahibi isteği 2026-07-24).
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
    if (!window.confirm(`"${c.title || c.client_name}" reklam kaydı silinsin mi?`)) return
    del.mutate(c.id, {
      onSuccess: () => toast.success("Kayıt silindi"),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Silinemedi"),
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Megaphone className="h-5 w-5" /> Reklam Takibi
          </h1>
          <p className="text-sm text-muted-foreground">
            Reklam çıktığımız müşteriler: tarih aralığı, harcanan tutar ve notlar.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border p-0.5">
            <Button size="sm" variant={view === "timeline" ? "secondary" : "ghost"} className="h-7 px-2"
              onClick={() => setView("timeline")} title="Zaman çizelgesi">
              <GanttChartSquare className="mr-1 h-4 w-4" /> Zaman Çizelgesi
            </Button>
            <Button size="sm" variant={view === "list" ? "secondary" : "ghost"} className="h-7 px-2"
              onClick={() => setView("list")} title="Liste">
              <List className="mr-1 h-4 w-4" /> Liste
            </Button>
          </div>
          <Button onClick={() => setAdding(true)}>
            <Plus className="mr-1 h-4 w-4" /> Reklam ekle
          </Button>
        </div>
      </div>

      {/* Filtreler */}
      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <div className="space-y-1">
          <Label className="text-xs">Müşteri</Label>
          <select
            className="h-9 w-48 rounded-md border bg-background px-2 text-sm"
            value={filters.client_id ?? ""}
            onChange={(e) => setFilters({ ...filters, client_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Tümü</option>
            {(clientsQ.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Durum</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={filters.status ?? ""}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          >
            <option value="">Tümü</option>
            {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Platform</Label>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={filters.platform ?? ""}
            onChange={(e) => setFilters({ ...filters, platform: e.target.value })}
          >
            <option value="">Tümü</option>
            {Object.entries(PLATFORM_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Başlangıç</Label>
          <Input type="date" className="h-9 w-40" value={filters.from ?? ""}
            onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Bitiş</Label>
          <Input type="date" className="h-9 w-40" value={filters.to ?? ""}
            onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
        </div>
        {(filters.client_id || filters.status || filters.platform || filters.from || filters.to) && (
          <Button variant="ghost" size="sm" onClick={() => setFilters({})}>Filtreleri temizle</Button>
        )}
      </div>

      {/* Özet */}
      {summary && (
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border p-4">
            <div className="text-xs text-muted-foreground">Toplam harcama (filtreli)</div>
            <div className="mt-1 text-2xl font-semibold">{fmtTRY(summary.total_amount)}</div>
            <div className="text-xs text-muted-foreground">{summary.count} kayıt</div>
          </div>
          <div className="rounded-lg border p-4 md:col-span-2">
            <div className="mb-2 text-xs text-muted-foreground">Müşteri bazlı toplam</div>
            {summary.by_client.length === 0 ? (
              <p className="text-sm text-muted-foreground">Kayıt yok</p>
            ) : (
              <div className="max-h-40 overflow-y-auto">
                <table className="w-full text-sm">
                  <tbody>
                    {summary.by_client.map((b) => (
                      <tr key={b.client_id} className="border-b last:border-0">
                        <td className="py-1">{b.client_name}</td>
                        <td className="py-1 text-right text-muted-foreground">{b.count} reklam</td>
                        <td className="py-1 text-right font-medium">{fmtTRY(b.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Liste */}
      {adsQ.isLoading && <Skeleton className="h-64 w-full" />}
      {adsQ.isError && (
        <p className="text-sm text-red-600">
          {adsQ.error instanceof Error ? adsQ.error.message : "Kayıtlar yüklenemedi"}
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
                <th className="px-3 py-2">Müşteri</th>
                <th className="px-3 py-2">Kampanya</th>
                <th className="px-3 py-2">Platform</th>
                <th className="px-3 py-2">Tarih aralığı</th>
                <th className="px-3 py-2 text-right">Harcanan</th>
                <th className="px-3 py-2">Durum</th>
                <th className="px-3 py-2 text-right">Erişim / Tıklama</th>
                <th className="px-3 py-2">Not</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {campaigns.length === 0 && (
                <tr><td colSpan={9} className="px-3 py-6 text-center text-muted-foreground">
                  Kayıt yok — "Reklam ekle" ile başla.
                </td></tr>
              )}
              {campaigns.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="px-3 py-2 font-medium">{c.client_name}</td>
                  <td className="px-3 py-2">{c.title || "—"}</td>
                  <td className="px-3 py-2 text-muted-foreground">{PLATFORM_LABELS[c.platform] ?? c.platform}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{fmtDateRange(c.start_date, c.end_date)}</td>
                  <td className="px-3 py-2 text-right font-medium">{fmtTRY(c.amount_spent)}</td>
                  <td className="px-3 py-2">
                    <span className={cn("rounded px-1.5 py-0.5 text-xs", STATUS_TONE[c.status])}>
                      {STATUS_LABELS[c.status] ?? c.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-muted-foreground">
                    {c.reach != null || c.clicks != null
                      ? `${c.reach?.toLocaleString("tr-TR") ?? "—"} / ${c.clicks?.toLocaleString("tr-TR") ?? "—"}`
                      : "—"}
                  </td>
                  <td className="max-w-[16rem] truncate px-3 py-2 text-muted-foreground" title={c.notes || ""}>
                    {c.notes || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1">
                      <Button size="icon" variant="ghost" title="Düzenle" onClick={() => setEditing(c)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" title="Sil" disabled={del.isPending}
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
  const title = useMemo(() => (campaign ? "Reklam kaydını düzenle" : "Reklam ekle"), [campaign])

  async function submit() {
    if (!form.client_id) { toast.error("Müşteri seçin"); return }
    if (!form.start_date) { toast.error("Başlangıç tarihi gerekli"); return }
    const body: AdInput = { ...form, end_date: form.end_date || null,
      reach: form.reach === "" ? null : form.reach, clicks: form.clicks === "" ? null : form.clicks }
    try {
      if (campaign) await update.mutateAsync({ id: campaign.id, body })
      else await create.mutateAsync(body)
      toast.success(campaign ? "Güncellendi" : "Reklam kaydı eklendi")
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Kaydedilemedi")
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 space-y-1">
              <Label>Müşteri</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.client_id}
                onChange={(e) => setForm({ ...form, client_id: Number(e.target.value) })}
              >
                {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div className="col-span-2 space-y-1">
              <Label>Kampanya adı</Label>
              <Input value={form.title ?? ""} placeholder="örn. Ramazan Kampanyası"
                onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>Başlangıç</Label>
              <Input type="date" value={form.start_date}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>Bitiş <span className="text-xs text-muted-foreground">(boş = devam ediyor)</span></Label>
              <Input type="date" value={form.end_date ?? ""}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>Harcanan tutar (₺)</Label>
              <Input inputMode="decimal" placeholder="0,00" value={String(form.amount_spent ?? "")}
                onChange={(e) => setForm({ ...form, amount_spent: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>Platform</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.platform}
                onChange={(e) => setForm({ ...form, platform: e.target.value as AdInput["platform"] })}
              >
                {Object.entries(PLATFORM_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <Label>Durum</Label>
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
                <Label className="text-xs">Erişim</Label>
                <Input inputMode="numeric" value={String(form.reach ?? "")}
                  onChange={(e) => setForm({ ...form, reach: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Tıklama</Label>
                <Input inputMode="numeric" value={String(form.clicks ?? "")}
                  onChange={(e) => setForm({ ...form, clicks: e.target.value })} />
              </div>
            </div>
            <div className="col-span-2 space-y-1">
              <Label>Notlar</Label>
              <Textarea rows={3} value={form.notes ?? ""}
                onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>İptal</Button>
            <Button onClick={submit} disabled={busy}>{busy ? "Kaydediliyor…" : "Kaydet"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
