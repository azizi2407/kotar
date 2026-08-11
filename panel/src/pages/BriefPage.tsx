// Brief page — select client + navigate weeks, rich display of the weekly content brief.
// management: manual "generate/regenerate" + draft brief approval flow (approval gate — 06/13).
import { useMemo, useState } from "react"
import { ChevronLeft, ChevronRight, Loader2, Sparkles } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
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

// Week Notes writeback (management only) — added alongside the existing approval
// flow without breaking it. Partial merge: only the submitted key gets overwritten;
// re-seeded via the `key` prop when `brief.id` changes.
function WeekNotesForm({ brief }: { brief: Brief }) {
  const { t } = useI18n()
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
      toast.success(t("pages.brief.weekNotes.saved"))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.brief.weekNotes.saveFailed"))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("pages.brief.weekNotes.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>{t("pages.brief.weekNotes.status")}</Label>
            <Select value={durum || DURUM_NONE}
              onValueChange={(v) => setDurum(v && v !== DURUM_NONE ? v : "")}>
              <SelectTrigger className="w-full"><SelectValue placeholder={t("pages.brief.weekNotes.selectPlaceholder")} /></SelectTrigger>
              <SelectContent>
                <SelectItem value={DURUM_NONE}>—</SelectItem>
                <SelectItem value="taslak">{t("pages.brief.weekNotes.draft")}</SelectItem>
                <SelectItem value="onaylandı">{t("pages.brief.weekNotes.approved")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wn-gun">{t("pages.brief.weekNotes.dayAssignment")}</Label>
            <Input id="wn-gun" value={gun} placeholder={t("pages.brief.weekNotes.dayAssignmentPlaceholder")}
              onChange={(e) => setGun(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wn-fikirler">{t("pages.brief.weekNotes.selectedIdeas")}</Label>
          <Textarea id="wn-fikirler" rows={3} value={fikirler}
            onChange={(e) => setFikirler(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wn-geri">{t("pages.brief.weekNotes.feedback")}</Label>
          <Textarea id="wn-geri" rows={2} value={geri}
            onChange={(e) => setGeri(e.target.value)} />
        </div>
        <Button onClick={onSave} disabled={save.isPending}>
          {save.isPending ? t("pages.brief.weekNotes.saving") : t("pages.brief.weekNotes.save")}
        </Button>
      </CardContent>
    </Card>
  )
}

export function BriefPage() {
  const { t, lang } = useI18n()
  const { data: clients } = useClients({ status: "active", q: "" })
  const [clientId, setClientId] = useState<number | null>(null)
  const [weekIso, setWeekIso] = useState(currentWeekIso())
  const [q, setQ] = useState("")
  const { isManagement } = useAuth()
  // Only management sees drafts (unapproved AI generation) — needed for the approval endpoint.
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

  // Manual generate / regenerate: enqueue → poll the job → reload the result (brief).
  // If a brief already exists, `force` is required: the handler is idempotent without
  // it, so it skips generation. Since the approval gate was removed (2026-07-30),
  // regeneration is the ONLY way to fix a bad brief — but since it doesn't restore the
  // old text, we ask for confirmation first before submitting.
  async function handleGenerate() {
    if (clientId == null) return
    const force = brief != null
    if (force && !window.confirm(
      t("pages.brief.confirmRegenerate", { client: clientName ?? "", week: weekIso }))) return
    setGenError(null)
    setGenerating(true)
    try {
      const job = await generate.mutateAsync({ client_id: clientId, week_iso: weekIso, force })
      const done = await pollJob(job.id)
      if (done.status === "failed") {
        setGenError(done.result?.error || t("pages.brief.generateFailed"))
      }
      await refetch()
    } catch (e) {
      setGenError(e instanceof Error ? e.message : t("pages.brief.generateFailed"))
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("pages.brief.title")}</h1>
        <p className="text-muted-foreground">{t("pages.brief.subtitle")}</p>
      </div>

      <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
        {/* Client list */}
        <div className="space-y-2">
          <Input placeholder={t("pages.brief.searchClientsPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-[70vh] divide-y overflow-y-auto rounded-lg border">
            {(filtered ?? []).map((c) => (
              <button key={c.id} onClick={() => setClientId(c.id)}
                className={"block w-full px-3 py-2 text-left text-sm transition-colors "
                  + (c.id === clientId ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted/50")}>
                {c.name}
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="px-3 py-4 text-sm text-muted-foreground">{t("pages.brief.noClients")}</p>
            )}
          </div>
        </div>

        {/* Brief content */}
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1 rounded-lg border p-1">
              <Button variant="ghost" size="icon" onClick={() => setWeekIso(shiftWeek(weekIso, -1))}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="min-w-[9rem] flex-1 text-center">
                <div className="text-sm font-medium">{weekIso}</div>
                <div className="text-xs text-muted-foreground">{weekRangeLabel(weekIso, lang)}</div>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setWeekIso(shiftWeek(weekIso, 1))}>
                <ChevronRight className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="sm" className="ml-1" onClick={() => setWeekIso(currentWeekIso())}>
                {t("pages.brief.today")}
              </Button>
            </div>
            {isManagement && clientId != null && (
              <Button variant="outline" size="sm" onClick={handleGenerate} disabled={generating}>
                {generating
                  ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
                {brief ? t("pages.brief.regenerate") : t("pages.brief.generate")}
              </Button>
            )}
          </div>

          {genError && (
            <p className="text-sm text-destructive">{genError}</p>
          )}

          {clientId == null ? (
            <p className="py-12 text-center text-muted-foreground">{t("pages.brief.selectClientPrompt")}</p>
          ) : isLoading || generating ? (
            <Skeleton className="h-64 w-full" />
          ) : !brief ? (
            <p className="py-12 text-center text-muted-foreground">
              {t("pages.brief.noBrief", { client: clientName ?? "", week: weekIso })}
            </p>
          ) : (
            <div className="space-y-4">
              {brief.status === "draft" && (
                <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
                  <Badge variant="secondary">{t("pages.brief.draftBadge")}{brief.generated_by === "ai" ? t("pages.brief.aiSuffix") : ""}</Badge>
                  <span className="flex-1 text-sm text-muted-foreground">
                    {t("pages.brief.notApprovedYet")}
                  </span>
                  {isManagement && (
                    <Button size="sm" onClick={() => approve.mutate(brief.id)} disabled={approve.isPending}>
                      {t("pages.brief.approve")}
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
