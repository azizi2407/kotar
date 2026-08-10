// Brief sayfası — müşteri seç + hafta gezin, haftalık içerik brief'ini zengin görüntüle.
// management: elle "üret/yeniden üret" + taslak brief'i onaylama akışı (onay kapısı — 06/13).
import { useMemo, useState } from "react"
import { ChevronLeft, ChevronRight, Loader2, Sparkles } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import {
  pollJob, useApproveBrief, useBrief, useGenerateBrief, useSaveWeekNotes,
  type Brief, type WeekNotesInput,
} from "@/lib/sharing"
import { ApiError } from "@/lib/api"
import { currentWeekIso, localDateStr, shiftWeek, trFold, weekRangeLabel } from "@/lib/week"
import { BriefBody } from "@/components/sharing/BriefDialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card, CardContent, CardHeader, CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"

const DURUM_NONE = "__yok__"

// Hafta Notları writeback (yalnız management) — mevcut onay akışını bozmadan yanına eklenir.
// Kısmi merge: gönderilen anahtar üzerine yazılır; `brief.id` değişince key ile yeniden seed'lenir.
function WeekNotesForm({ brief }: { brief: Brief }) {
  const save = useSaveWeekNotes()
  const wn = (brief.week_notes || {}) as Record<string, unknown>
  const [durum, setDurum] = useState<string>(typeof wn.durum === "string" ? wn.durum : "")
  const [fikirler, setFikirler] = useState<string>(
    Array.isArray(wn.secilen_fikirler)
      ? (wn.secilen_fikirler as unknown[]).map(String).join("\n")
      : typeof wn.secilen_fikirler === "string" ? wn.secilen_fikirler : "")
  const [gun, setGun] = useState<string>(typeof wn.gun_atamasi === "string" ? wn.gun_atamasi : "")
  const [geri, setGeri] = useState<string>(typeof wn.geri_bildirim === "string" ? wn.geri_bildirim : "")

  async function onSave() {
    const notes: WeekNotesInput = {
      durum: durum || "",
      secilen_fikirler: fikirler.split("\n").map((s) => s.trim()).filter(Boolean),
      gun_atamasi: gun,
      geri_bildirim: geri,
    }
    if (durum === "onaylandı" && !wn.onay_tarihi) {
      notes.onay_tarihi = localDateStr()
    }
    try {
      await save.mutateAsync({ briefId: brief.id, notes })
      toast.success("Hafta notları kaydedildi")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Kaydedilemedi")
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Hafta Notları</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Durum</Label>
            <Select value={durum || DURUM_NONE}
              onValueChange={(v) => setDurum(v && v !== DURUM_NONE ? v : "")}>
              <SelectTrigger className="w-full"><SelectValue placeholder="Seç" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={DURUM_NONE}>—</SelectItem>
                <SelectItem value="taslak">Taslak</SelectItem>
                <SelectItem value="onaylandı">Onaylandı</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wn-gun">Gün Ataması</Label>
            <Input id="wn-gun" value={gun} placeholder="ör. Pzt: 1, Çar: 3…"
              onChange={(e) => setGun(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wn-fikirler">Seçilen Fikirler (her satıra bir madde)</Label>
          <Textarea id="wn-fikirler" rows={3} value={fikirler}
            onChange={(e) => setFikirler(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wn-geri">Geri Bildirim</Label>
          <Textarea id="wn-geri" rows={2} value={geri}
            onChange={(e) => setGeri(e.target.value)} />
        </div>
        <Button onClick={onSave} disabled={save.isPending}>
          {save.isPending ? "Kaydediliyor…" : "Kaydet"}
        </Button>
      </CardContent>
    </Card>
  )
}

export function BriefPage() {
  const { data: clients } = useClients({ status: "active", q: "" })
  const [clientId, setClientId] = useState<number | null>(null)
  const [weekIso, setWeekIso] = useState(currentWeekIso())
  const [q, setQ] = useState("")
  const { isManagement } = useAuth()
  // Taslakları (onaylanmamış AI üretimi) yalnız management görür — onay ucu için gerekli.
  const { data: brief, isLoading, refetch } = useBrief(clientId, weekIso, clientId != null, isManagement)
  const approve = useApproveBrief()
  const generate = useGenerateBrief()
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const list = clients ?? []
    const nq = trFold(q.trim())
    return nq ? list.filter((c) => trFold(c.name).includes(nq)) : list
  }, [clients, q])

  const clientName = clients?.find((c) => c.id === clientId)?.name

  // Elle üret / yeniden üret: enqueue → job'u poll et → sonucu (brief) yeniden yükle.
  // Brief zaten varsa `force` şart: handler onsuz idempotent, yani üretmeden atlar. Onay
  // kapısı kaldırıldığından (2026-07-30) yeniden üretim kötü brief'in TEK düzeltme yolu —
  // ama eski metni geri getirmediği için önce onay isteyip sonra gönderiyoruz.
  async function handleGenerate() {
    if (clientId == null) return
    const force = brief != null
    if (force && !window.confirm(
      `${clientName} · ${weekIso} brief'i yeniden üretilecek ve mevcut metnin üzerine `
      + `yazılacak. Eski metin geri getirilemez. Devam edilsin mi?`)) return
    setGenError(null)
    setGenerating(true)
    try {
      const job = await generate.mutateAsync({ client_id: clientId, week_iso: weekIso, force })
      const done = await pollJob(job.id)
      if (done.status === "failed") {
        setGenError(done.result?.error || "Brief üretimi başarısız oldu.")
      }
      await refetch()
    } catch (e) {
      setGenError(e instanceof Error ? e.message : "Brief üretimi başarısız oldu.")
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Brief</h1>
        <p className="text-muted-foreground">Müşteri seç, haftalık içerik brief'ini görüntüle.</p>
      </div>

      <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
        {/* Müşteri listesi */}
        <div className="space-y-2">
          <Input placeholder="Müşteri ara…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-[70vh] divide-y overflow-y-auto rounded-lg border">
            {(filtered ?? []).map((c) => (
              <button key={c.id} onClick={() => setClientId(c.id)}
                className={"block w-full px-3 py-2 text-left text-sm transition-colors "
                  + (c.id === clientId ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted/50")}>
                {c.name}
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="px-3 py-4 text-sm text-muted-foreground">Müşteri yok.</p>
            )}
          </div>
        </div>

        {/* Brief içeriği */}
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1 rounded-lg border p-1">
              <Button variant="ghost" size="icon" onClick={() => setWeekIso(shiftWeek(weekIso, -1))}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="min-w-[9rem] flex-1 text-center">
                <div className="text-sm font-medium">{weekIso}</div>
                <div className="text-xs text-muted-foreground">{weekRangeLabel(weekIso)}</div>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setWeekIso(shiftWeek(weekIso, 1))}>
                <ChevronRight className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="sm" className="ml-1" onClick={() => setWeekIso(currentWeekIso())}>
                Bugün
              </Button>
            </div>
            {isManagement && clientId != null && (
              <Button variant="outline" size="sm" onClick={handleGenerate} disabled={generating}>
                {generating
                  ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
                {brief ? "Yeniden üret" : "Brief üret"}
              </Button>
            )}
          </div>

          {genError && (
            <p className="text-sm text-destructive">{genError}</p>
          )}

          {clientId == null ? (
            <p className="py-12 text-center text-muted-foreground">Soldan bir müşteri seç.</p>
          ) : isLoading || generating ? (
            <Skeleton className="h-64 w-full" />
          ) : !brief ? (
            <p className="py-12 text-center text-muted-foreground">
              {clientName} · {weekIso} için brief yok.
            </p>
          ) : (
            <div className="space-y-4">
              {brief.status === "draft" && (
                <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
                  <Badge variant="secondary">Taslak{brief.generated_by === "ai" ? " · AI" : ""}</Badge>
                  <span className="flex-1 text-sm text-muted-foreground">
                    Bu brief henüz onaylanmadı.
                  </span>
                  {isManagement && (
                    <Button size="sm" onClick={() => approve.mutate(brief.id)} disabled={approve.isPending}>
                      Onayla
                    </Button>
                  )}
                </div>
              )}
              <BriefBody brief={brief} />
              {isManagement && <WeekNotesForm key={brief.id} brief={brief} />}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
