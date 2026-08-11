// Client detail: tabbed view (General · Contract · Team · Contacts/Location · Drive).
// Edit/Delete/Restore management-only. A delete reason is required.
import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { ArrowLeft, Copy, ExternalLink, Loader2, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import {
  ROLE_SLOTS, useCatchUp, useClient, useDeleteClient, useOnboardingPrompt,
  usePutVaultAyar, useRestoreClient, useSetBriefEnabled, useUsers, useVaultAyar,
} from "@/lib/clients"
import type { VaultAyar } from "@/lib/clients"
import type { ClientDetail } from "@/lib/types"
import {
  useCaptionSettings, useSaveCaptionSettings, type CaptionSettings,
} from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Card, CardContent, CardHeader, CardTitle,
} from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { ClientFormDialog } from "@/components/clients/ClientFormDialog"
import { AyarView, Field } from "@/components/clients/AyarView"
import { BrandAssetsTab } from "@/components/clients/BrandAssetsTab"

// Caption settings UI: model options match ShareModal (ai_claude.py DEFAULT_MODEL
// + known alternative). Saving on this page becomes the persistent client default (Phase 1b).
const MODEL_DEFAULT = "__varsayilan__"

function modelOptions(t: (key: string) => string) {
  return [
    { value: MODEL_DEFAULT, label: t("pages.clientDetail.captionSettings.modelDefault") },
    { value: "claude-sonnet-5", label: t("pages.clientDetail.captionSettings.modelSonnet") },
    { value: "claude-opus-4-8", label: t("pages.clientDetail.captionSettings.modelOpus") },
  ]
}

function numOrNull(v: string): number | null {
  if (v.trim() === "") return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// Client's caption generation defaults: viewing (everyone) + editing (management).
function CaptionSettingsSection({ clientId, canEdit }: { clientId: number; canEdit: boolean }) {
  const { t } = useI18n()
  const { data, isLoading } = useCaptionSettings(clientId)
  const save = useSaveCaptionSettings(clientId)
  const [form, setForm] = useState<CaptionSettings>({})

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  async function onSave() {
    try {
      await save.mutateAsync(form)
      toast.success(t("pages.clientDetail.captionSettings.toast.saved"))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.clientDetail.captionSettings.toast.saveFailed"))
    }
  }

  if (isLoading) return <Skeleton className="h-40 w-full" />

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("pages.clientDetail.captionSettings.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {t("pages.clientDetail.captionSettings.description")}
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>{t("pages.clientDetail.captionSettings.model")}</Label>
            <Select value={form.model || MODEL_DEFAULT}
              disabled={!canEdit}
              onValueChange={(v) => v && setForm((f) => ({ ...f, model: v === MODEL_DEFAULT ? null : v }))}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {modelOptions(t).map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cs-tone">{t("pages.clientDetail.captionSettings.tone")}</Label>
            <Input id="cs-tone" value={form.tone ?? ""} disabled={!canEdit}
              placeholder={t("pages.clientDetail.captionSettings.tonePlaceholder")}
              onChange={(e) => setForm((f) => ({ ...f, tone: e.target.value || null }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cs-emoji">{t("pages.clientDetail.captionSettings.emojiLimit")}</Label>
            <Input id="cs-emoji" type="number" min={0} value={form.emoji_limit ?? ""} disabled={!canEdit}
              onChange={(e) => setForm((f) => ({ ...f, emoji_limit: numOrNull(e.target.value) }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cs-hashtag">{t("pages.clientDetail.captionSettings.hashtagCount")}</Label>
            <Input id="cs-hashtag" type="number" min={0} value={form.hashtag_count ?? ""} disabled={!canEdit}
              onChange={(e) => setForm((f) => ({ ...f, hashtag_count: numOrNull(e.target.value) }))} />
          </div>
          <div className="space-y-1.5">
            <Label>{t("pages.clientDetail.captionSettings.lang")}</Label>
            <Select value={form.lang || "TR"} disabled={!canEdit}
              onValueChange={(v) => v && setForm((f) => ({ ...f, lang: v as CaptionSettings["lang"] }))}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="TR">{t("pages.clientDetail.captionSettings.langTr")}</SelectItem>
                <SelectItem value="EN">{t("pages.clientDetail.captionSettings.langEn")}</SelectItem>
                <SelectItem value="TR+EN">{t("pages.clientDetail.captionSettings.langBoth")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cs-charlimit">{t("pages.clientDetail.captionSettings.charLimit")}</Label>
            <Input id="cs-charlimit" type="number" min={0} value={form.char_limit ?? ""} disabled={!canEdit}
              placeholder={t("pages.clientDetail.captionSettings.charLimitPlaceholder")}
              onChange={(e) => setForm((f) => ({ ...f, char_limit: numOrNull(e.target.value) }))} />
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={form.use_brief ?? false} disabled={!canEdit}
            onCheckedChange={(v) => setForm((f) => ({ ...f, use_brief: v }))} />
          {t("pages.clientDetail.captionSettings.useBrief")}
        </label>
        {canEdit && (
          <Button onClick={onSave} disabled={save.isPending}>
            {save.isPending ? t("pages.clientDetail.captionSettings.saving") : t("pages.clientDetail.captionSettings.save")}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

// Settings constants — posting days (canonical order, a data value sent to the
// backend in Turkish — not translated) + content mix format keys. `.key` is
// the API contract (immutable); `.label` is display text only (translated via t()).
const GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"] as const
const MIX_KEYS = [
  { key: "carousel", labelKey: "pages.clientDetail.ayarForm.mix.carousel" },
  { key: "editorial_single", labelKey: "pages.clientDetail.ayarForm.mix.editorialSingle" },
  { key: "reel", labelKey: "pages.clientDetail.ayarForm.mix.reel" },
] as const
const HASHTAG_GROUPS = [
  { key: "konu", labelKey: "pages.clientDetail.ayarForm.hashtag.konu" },
  { key: "marka", labelKey: "pages.clientDetail.ayarForm.hashtag.marka" },
  { key: "sektor", labelKey: "pages.clientDetail.ayarForm.hashtag.sektor" },
] as const

// Converts newline/comma-separated text into a clean string list (blanks dropped).
function toList(s: string): string[] {
  return s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean)
}

// Settings edit form — brand_profile fields (guides brief generation).
// caption_settings is edited in a SEPARATE "Caption Settings" tab; not touched here.
function AyarForm({ clientId, ayar, onDone }: { clientId: number; ayar: VaultAyar; onDone: () => void }) {
  const { t } = useI18n()
  const put = usePutVaultAyar(clientId)
  const [brandVoice, setBrandVoice] = useState(ayar.brand_voice)
  const [audience, setAudience] = useState(ayar.target_audience)
  const [cta, setCta] = useState(ayar.cta)
  const [pillars, setPillars] = useState(ayar.content_pillars)
  const [guide, setGuide] = useState(ayar.guide_md)
  const [forbiddenText, setForbiddenText] = useState((ayar.forbidden ?? []).join("\n"))
  const [palette, setPalette] = useState<string[]>(ayar.color_palette ?? [])
  const [mix, setMix] = useState<Record<string, number>>({ ...(ayar.content_mix ?? {}) })
  const [hashKonu, setHashKonu] = useState((ayar.hashtags?.konu ?? []).join("\n"))
  const [hashMarka, setHashMarka] = useState((ayar.hashtags?.marka ?? []).join("\n"))
  const [hashSektor, setHashSektor] = useState((ayar.hashtags?.sektor ?? []).join("\n"))
  const [days, setDays] = useState<string[]>(ayar.posting_days ?? [])
  const [ideas, setIdeas] = useState<string>(String(ayar.ideas_per_week ?? 5))

  const hashText: Record<string, string> = { konu: hashKonu, marka: hashMarka, sektor: hashSektor }
  const hashSetters: Record<string, (v: string) => void> = {
    konu: setHashKonu, marka: setHashMarka, sektor: setHashSektor,
  }

  function toggleDay(d: string) {
    setDays((cur) => cur.includes(d)
      ? cur.filter((x) => x !== d)
      : GUNLER.filter((g) => g === d || cur.includes(g)))
  }

  async function onSave() {
    const ipw = parseInt(ideas, 10)
    if (!Number.isFinite(ipw) || ipw < 0) {
      toast.error(t("pages.clientDetail.ayarForm.toast.invalidIdeas"))
      return
    }
    const body = {
      brand_voice: brandVoice,
      target_audience: audience,
      cta,
      content_pillars: pillars,
      guide_md: guide,
      forbidden: toList(forbiddenText),
      color_palette: palette.map((h) => h.trim()).filter(Boolean),
      content_mix: MIX_KEYS.reduce<Record<string, number>>((acc, m) => {
        acc[m.key] = Number(mix[m.key]) || 0
        return acc
      }, { ...mix }),
      hashtags: {
        konu: toList(hashKonu),
        marka: toList(hashMarka),
        sektor: toList(hashSektor),
      },
      posting_days: days,
      ideas_per_week: ipw,
    }
    try {
      await put.mutateAsync(body)
      toast.success(t("pages.clientDetail.ayarForm.toast.saved"))
      onDone()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.clientDetail.ayarForm.toast.saveFailed"))
    }
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="ay-voice">{t("pages.clientDetail.ayarForm.brandVoice")}</Label>
          <Textarea id="ay-voice" value={brandVoice} onChange={(e) => setBrandVoice(e.target.value)}
            placeholder={t("pages.clientDetail.ayarForm.brandVoicePlaceholder")} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ay-audience">{t("pages.clientDetail.ayarForm.targetAudience")}</Label>
          <Textarea id="ay-audience" value={audience} onChange={(e) => setAudience(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ay-cta">{t("pages.clientDetail.ayarForm.cta")}</Label>
          <Input id="ay-cta" value={cta} onChange={(e) => setCta(e.target.value)}
            placeholder={t("pages.clientDetail.ayarForm.ctaPlaceholder")} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ay-ideas">{t("pages.clientDetail.ayarForm.ideasPerWeek")}</Label>
          <Input id="ay-ideas" type="number" min={0} value={ideas}
            onChange={(e) => setIdeas(e.target.value)} />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>{t("pages.clientDetail.ayarForm.postingDays")}</Label>
        <div className="flex flex-wrap gap-1.5">
          {GUNLER.map((g) => {
            const on = days.includes(g)
            return (
              <button key={g} type="button" onClick={() => toggleDay(g)}
                className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                  on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted"}`}>
                {g}
              </button>
            )
          })}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ay-forbidden">{t("pages.clientDetail.ayarForm.forbidden")}</Label>
        <Textarea id="ay-forbidden" value={forbiddenText} onChange={(e) => setForbiddenText(e.target.value)}
          className="min-h-24" placeholder={t("pages.clientDetail.ayarForm.forbiddenPlaceholder")} />
        <p className="text-xs text-muted-foreground">{t("pages.clientDetail.ayarForm.forbiddenHint")}</p>
      </div>

      <div className="space-y-1.5">
        <Label>{t("pages.clientDetail.ayarForm.colorPalette")}</Label>
        <div className="space-y-2">
          {palette.map((hex, i) => (
            <div key={i} className="flex items-center gap-2">
              <input type="color" value={/^#[0-9a-fA-F]{6}$/.test(hex) ? hex : "#000000"}
                onChange={(e) => setPalette((p) => p.map((h, j) => (j === i ? e.target.value : h)))}
                className="h-9 w-10 cursor-pointer rounded border bg-transparent p-0.5" />
              <Input value={hex} placeholder={t("pages.clientDetail.ayarForm.colorHexPlaceholder")}
                onChange={(e) => setPalette((p) => p.map((h, j) => (j === i ? e.target.value : h)))}
                className="max-w-40" />
              <Button type="button" variant="ghost" size="icon"
                onClick={() => setPalette((p) => p.filter((_, j) => j !== i))}>
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={() => setPalette((p) => [...p, "#000000"])}>
            <Plus className="mr-1 h-3.5 w-3.5" /> {t("pages.clientDetail.ayarForm.addColor")}
          </Button>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>{t("pages.clientDetail.ayarForm.contentMix")}</Label>
        <div className="grid gap-3 sm:grid-cols-3">
          {MIX_KEYS.map((m) => (
            <div key={m.key} className="space-y-1">
              <Label htmlFor={`ay-mix-${m.key}`} className="text-xs text-muted-foreground">{t(m.labelKey)}</Label>
              <Input id={`ay-mix-${m.key}`} type="number" min={0} value={mix[m.key] ?? ""}
                onChange={(e) => setMix((cur) => ({ ...cur, [m.key]: Number(e.target.value) || 0 }))} />
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <Label>{t("pages.clientDetail.ayarForm.hashtagSets")}</Label>
        <div className="grid gap-3 sm:grid-cols-3">
          {HASHTAG_GROUPS.map((g) => (
            <div key={g.key} className="space-y-1">
              <Label htmlFor={`ay-hash-${g.key}`} className="text-xs text-muted-foreground">{t(g.labelKey)}</Label>
              <Textarea id={`ay-hash-${g.key}`} value={hashText[g.key]}
                onChange={(e) => hashSetters[g.key](e.target.value)}
                className="min-h-24" placeholder={t("pages.clientDetail.ayarForm.hashtagPlaceholder")} />
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ay-guide">{t("pages.clientDetail.ayarForm.guideMd")}</Label>
        <Textarea id="ay-guide" value={guide} onChange={(e) => setGuide(e.target.value)}
          className="min-h-32" />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="ay-pillars">{t("pages.clientDetail.ayarForm.contentPillars")}</Label>
        <Textarea id="ay-pillars" value={pillars} onChange={(e) => setPillars(e.target.value)}
          className="min-h-24" />
      </div>

      <div className="flex gap-2">
        <Button onClick={onSave} disabled={put.isPending}>
          {put.isPending ? t("pages.clientDetail.ayarForm.saving") : t("pages.clientDetail.ayarForm.save")}
        </Button>
        <Button variant="outline" onClick={onDone} disabled={put.isPending}>{t("pages.clientDetail.ayarForm.cancel")}</Button>
      </div>
    </div>
  )
}

// Settings tab body: view ↔ edit toggle (management-only).
function AyarPanel({ clientId, ayar }: { clientId: number; ayar: VaultAyar }) {
  const { t } = useI18n()
  const [editing, setEditing] = useState(false)
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">{t("pages.clientDetail.ayarPanel.title")}</CardTitle>
        {!editing && (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            <Pencil className="mr-1.5 h-3.5 w-3.5" /> {t("pages.clientDetail.ayarPanel.edit")}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {editing
          ? <AyarForm clientId={clientId} ayar={ayar} onDone={() => setEditing(false)} />
          : <AyarView ayar={ayar} />}
      </CardContent>
    </Card>
  )
}

// Client's vault Settings file: read-only view + brief toggle + catch-up + onboarding.
// All endpoints are management-only (write=True) → the tab is only shown to management.
function VaultAyarSection({ client }: { client: ClientDetail }) {
  const { t } = useI18n()
  const clientId = client.id
  const { data, isLoading, isError, error } = useVaultAyar(clientId)
  const onboarding = !!data?.onboarding
  const promptQ = useOnboardingPrompt(clientId, onboarding)
  const setBriefEnabled = useSetBriefEnabled(clientId)
  const catchUp = useCatchUp(clientId)

  async function onToggle(v: boolean) {
    try {
      await setBriefEnabled.mutateAsync(v)
      toast.success(v
        ? t("pages.clientDetail.vaultAyar.toast.briefOn")
        : t("pages.clientDetail.vaultAyar.toast.briefOff"))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.clientDetail.vaultAyar.toast.toggleFailed"))
    }
  }

  async function onCatchUp() {
    try {
      const r = await catchUp.mutateAsync()
      if (r.enqueued === 0) toast.info(t("pages.clientDetail.vaultAyar.toast.upToDate"))
      else toast.success(t("pages.clientDetail.vaultAyar.toast.queued", { count: r.enqueued, weeks: r.weeks.join(", ") }))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.clientDetail.vaultAyar.toast.queueFailed"))
    }
  }

  async function copyPrompt() {
    if (!promptQ.data) return
    try {
      await navigator.clipboard.writeText(promptQ.data)
      toast.success(t("pages.clientDetail.vaultAyar.toast.promptCopied"))
    } catch {
      toast.error(t("pages.clientDetail.vaultAyar.toast.copyFailed"))
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
          <label className="flex items-center gap-3 text-sm">
            <Switch checked={client.brief_enabled} disabled={setBriefEnabled.isPending}
              onCheckedChange={onToggle} />
            <span>
              <span className="font-medium">{t("pages.clientDetail.vaultAyar.briefLabel")}</span>
              <span className="block text-xs text-muted-foreground">
                {t("pages.clientDetail.vaultAyar.briefHint")}
              </span>
            </span>
          </label>
          {client.brief_enabled && !onboarding && (
            <Button variant="outline" size="sm" onClick={onCatchUp} disabled={catchUp.isPending}>
              {catchUp.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              {t("pages.clientDetail.vaultAyar.catchUpButton")}
            </Button>
          )}
        </CardContent>
      </Card>

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">
            {t("pages.clientDetail.vaultAyar.loadError", {
              message: error instanceof ApiError ? error.message : t("pages.clientDetail.vaultAyar.unknownError"),
            })}
          </CardContent>
        </Card>
      ) : data ? (
        <>
          {onboarding && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("pages.clientDetail.vaultAyar.onboardingTitle")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  {t("pages.clientDetail.vaultAyar.onboardingBody")}
                </p>
                {promptQ.isLoading ? (
                  <Skeleton className="h-40 w-full" />
                ) : promptQ.data ? (
                  <div className="space-y-2">
                    <div className="flex justify-end">
                      <Button variant="outline" size="sm" onClick={copyPrompt}>
                        <Copy className="mr-1.5 h-3.5 w-3.5" /> {t("pages.clientDetail.vaultAyar.copy")}
                      </Button>
                    </div>
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-xs">
                      {promptQ.data}
                    </pre>
                  </div>
                ) : (
                  <p className="text-sm text-destructive">{t("pages.clientDetail.vaultAyar.promptFetchFailed")}</p>
                )}
              </CardContent>
            </Card>
          )}
          <AyarPanel clientId={clientId} ayar={data.ayar} />
        </>
      ) : null}
    </div>
  )
}

function yesno(t: (key: string) => string, v: boolean | null | undefined) {
  return v == null ? "—" : v ? t("pages.clientDetail.yesno.yes") : t("pages.clientDetail.yesno.no")
}

export function ClientDetailPage() {
  const { t } = useI18n()
  const { id } = useParams()
  const clientId = Number(id)
  const navigate = useNavigate()
  const { isManagement } = useAuth()
  const { data: c, isLoading, isError } = useClient(clientId)
  const { data: users } = useUsers()
  const del = useDeleteClient()
  const restore = useRestoreClient()
  const [editing, setEditing] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [reason, setReason] = useState("")

  const nameBySub = useMemo(() => {
    const m: Record<string, string> = {}
    for (const u of users ?? []) m[u.sub] = u.name || u.email
    return m
  }, [users])

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (isError || !c) {
    return (
      <div className="space-y-4">
        <p className="text-destructive">{t("pages.clientDetail.notFound")}</p>
        <Button variant="outline" render={<Link to="/clients" />}>
          <ArrowLeft className="mr-1 h-4 w-4" /> {t("pages.clientDetail.backToList")}
        </Button>
      </div>
    )
  }

  async function doDelete() {
    try {
      await del.mutateAsync({ id: clientId, reason: reason.trim() || undefined })
      toast.success(t("pages.clientDetail.toast.archived"))
      setConfirmDel(false)
      navigate("/clients")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.clientDetail.toast.deleteFailed"))
    }
  }
  async function doRestore() {
    try {
      await restore.mutateAsync(clientId)
      toast.success(t("pages.clientDetail.toast.restored"))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pages.clientDetail.toast.restoreFailed"))
    }
  }

  const ct = c.contract

  return (
    <div className="space-y-6">
      <div>
        <Link to="/clients" className="mb-2 inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="mr-1 h-4 w-4" /> {t("pages.clientDetail.backToClients")}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{c.name}</h1>
            {c.status === "deleted"
              ? <Badge variant="outline" className="text-muted-foreground">{t("pages.clientDetail.status.deleted")}</Badge>
              : <Badge variant="secondary">{t("pages.clientDetail.status.active")}</Badge>}
          </div>
          {isManagement && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setEditing(true)}>
                <Pencil className="mr-1 h-4 w-4" /> {t("pages.clientDetail.edit")}
              </Button>
              {c.status === "active" ? (
                <Button variant="outline" className="text-destructive"
                  onClick={() => { setReason(""); setConfirmDel(true) }}>
                  <Trash2 className="mr-1 h-4 w-4" /> {t("pages.clientDetail.delete")}
                </Button>
              ) : (
                <Button variant="outline" onClick={doRestore} disabled={restore.isPending}>
                  <RotateCcw className="mr-1 h-4 w-4" /> {t("pages.clientDetail.restore")}
                </Button>
              )}
            </div>
          )}
        </div>
        {c.status === "deleted" && c.deleted_reason && (
          <p className="mt-1 text-sm text-muted-foreground">
            {t("pages.clientDetail.deleteReason", { reason: c.deleted_reason })}
          </p>
        )}
      </div>

      <Tabs defaultValue="genel">
        <TabsList>
          <TabsTrigger value="genel">{t("pages.clientDetail.tabs.general")}</TabsTrigger>
          <TabsTrigger value="anlasma">{t("pages.clientDetail.tabs.contract")}</TabsTrigger>
          <TabsTrigger value="ekip">{t("pages.clientDetail.tabs.team")}</TabsTrigger>
          <TabsTrigger value="kisiler">{t("pages.clientDetail.tabs.contacts")}</TabsTrigger>
          <TabsTrigger value="drive">{t("pages.clientDetail.tabs.drive")}</TabsTrigger>
          <TabsTrigger value="caption">{t("pages.clientDetail.tabs.captionSettings")}</TabsTrigger>
          {isManagement && <TabsTrigger value="marka">{t("pages.clientDetail.tabs.brandAssets")}</TabsTrigger>}
          {isManagement && <TabsTrigger value="vault">{t("pages.clientDetail.tabs.vaultAyar")}</TabsTrigger>}
        </TabsList>

        <TabsContent value="genel">
          <Card>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
              <Field label={t("pages.clientDetail.field.sector")} value={c.sector} />
              <Field label={t("pages.clientDetail.field.email")} value={c.client_email} />
              <Field label={t("pages.clientDetail.field.instagram")} value={
                c.instagram_url
                  ? <a className="text-primary hover:underline" href={c.instagram_url} target="_blank" rel="noreferrer">{c.instagram_url}</a>
                  : null} />
              <Field label={t("pages.clientDetail.field.notes")} value={c.notes} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="anlasma">
          <Card>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
              <Field label={t("pages.clientDetail.field.weeklyContent")} value={ct?.weekly_content_count} />
              <Field label={t("pages.clientDetail.field.post")} value={ct?.post_count} />
              <Field label={t("pages.clientDetail.field.story")} value={ct?.story_count} />
              <Field label={t("pages.clientDetail.field.vatRate")} value={ct?.vat_rate} />
              <Field label={t("pages.clientDetail.field.contentPlan")} value={ct?.content_plan} />
              <Field label={t("pages.clientDetail.field.feeEffectiveDate")} value={ct?.fee_effective_date} />
              <Field label={t("pages.clientDetail.field.videoShooting")} value={yesno(t, ct?.video_shooting_enabled)} />
              <Field label={t("pages.clientDetail.field.weeklyVideo")} value={ct?.weekly_video_count} />
              <Field label={t("pages.clientDetail.field.photoShooting")} value={yesno(t, ct?.photo_shooting_enabled)} />
              <Field label={t("pages.clientDetail.field.weeklyPhoto")} value={ct?.weekly_photo_count} />
              <Field label={t("pages.clientDetail.field.drone")} value={yesno(t, ct?.drone_usage)} />
              <Field label={t("pages.clientDetail.field.description")} value={ct?.description} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ekip">
          <Card>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
              {ROLE_SLOTS.map((slot) => (
                <Field key={slot.key} label={t(slot.labelKey)}
                  value={c.team_assignments[slot.key]
                    ? (nameBySub[c.team_assignments[slot.key]] ?? c.team_assignments[slot.key])
                    : null} />
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="kisiler">
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-base">{t("pages.clientDetail.contacts.title")}</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {(c.contacts ?? []).length === 0 && <p className="text-sm text-muted-foreground">{t("pages.clientDetail.contacts.empty")}</p>}
                {(c.contacts ?? []).map((p, i) => (
                  <div key={i} className="rounded-md border p-2 text-sm">
                    <div className="font-medium">{p.name || "—"}</div>
                    <div className="text-muted-foreground">{[p.email, p.phone].filter(Boolean).join(" · ") || "—"}</div>
                    {p.notes && <div className="text-muted-foreground">{p.notes}</div>}
                  </div>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">{t("pages.clientDetail.locations.title")}</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {(c.locations ?? []).length === 0 && <p className="text-sm text-muted-foreground">{t("pages.clientDetail.locations.empty")}</p>}
                {(c.locations ?? []).map((l, i) => (
                  <div key={i} className="rounded-md border p-2 text-sm">
                    <div className="font-medium">{l.name || "—"}</div>
                    <div className="text-muted-foreground">{l.address || "—"}</div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="drive">
          <Card>
            <CardContent className="space-y-4 pt-6">
              <Field label={t("pages.clientDetail.drive.mainFolder")} value={
                c.google_drive_url
                  ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={c.google_drive_url} target="_blank" rel="noreferrer">{t("pages.clientDetail.drive.openFolder")} <ExternalLink className="h-3 w-3" /></a>
                  : null} />
              <div>
                <div className="mb-2 text-xs text-muted-foreground">
                  {t("pages.clientDetail.drive.weekFolders", { count: c.week_folders.length })}
                </div>
                <div className="flex flex-wrap gap-2">
                  {c.week_folders.length === 0 && <span className="text-sm text-muted-foreground">—</span>}
                  {c.week_folders.map((w) => (
                    w.link
                      ? <a key={w.week_number} href={w.link} target="_blank" rel="noreferrer"
                          className="rounded border px-2 py-1 text-xs hover:bg-muted">{t("pages.clientDetail.drive.week", { number: w.week_number })}</a>
                      : <span key={w.week_number} className="rounded border px-2 py-1 text-xs text-muted-foreground">{t("pages.clientDetail.drive.week", { number: w.week_number })}</span>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="caption">
          <CaptionSettingsSection clientId={clientId} canEdit={isManagement} />
        </TabsContent>

        {isManagement && (
          <TabsContent value="marka">
            <BrandAssetsTab clientId={clientId} />
          </TabsContent>
        )}

        {isManagement && (
          <TabsContent value="vault">
            <VaultAyarSection client={c} />
          </TabsContent>
        )}
      </Tabs>

      {isManagement && (
        <ClientFormDialog open={editing} onOpenChange={setEditing} client={c} />
      )}

      <Dialog open={confirmDel} onOpenChange={setConfirmDel}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("pages.clientDetail.deleteDialog.title")}</DialogTitle>
            <DialogDescription>
              {t("pages.clientDetail.deleteDialog.description", { name: c.name })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="reason">{t("pages.clientDetail.deleteDialog.reasonLabel")}</Label>
            <Input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDel(false)}>{t("pages.clientDetail.deleteDialog.cancel")}</Button>
            <Button className="bg-destructive text-white hover:bg-destructive/90"
              onClick={doDelete} disabled={del.isPending}>
              {del.isPending ? t("pages.clientDetail.deleteDialog.deleting") : t("pages.clientDetail.deleteDialog.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
