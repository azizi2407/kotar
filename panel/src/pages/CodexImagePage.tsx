// Codex Image Generation (2026-08-10) — via ChatGPT subscription using `codex exec` +
// `$imagegen`. SEPARATE from the existing "AI Image Generation" page (the Magnific pipeline)
// and doesn't replace it; the user decides which to use by picking a page.
//
// v1 scope is deliberately narrow (user decision): generate → view → download. No variation,
// archive, approve/reject, or retry buttons. The approval-gate invariant is preserved by these
// images never being wired into any client-facing surface — downloading and re-inserting into
// the existing flow keeps a human in the loop.
import { useMemo, useState } from "react"
import { Download, ImageOff, Loader2, Sparkles, TriangleAlert } from "lucide-react"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import { useI18n } from "@/lib/i18n"
import {
  ASPECTS,
  IMAGEGEN_TIMEOUT_MARKER,
  MAX_REFERENCES,
  STATUS_LABEL_KEY,
  VARIANT_LABEL_KEY,
  generateImage,
  getClientSettings,
  imageUrl,
  listBatch,
  listImageJobs,
  listWeeks,
  pollImageJob,
  setAutoImage,
  startBatch,
  type BatchGroup,
  type BriefWeek,
  type ImageJob,
  type SkippedIdea,
} from "@/lib/imagegen"
import { useClientAssets, useImageGenBriefs } from "@/lib/sharing"
import { trFold } from "@/lib/week"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

const selectCls =
  "h-9 rounded-md border bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"

export function CodexImagePage() {
  const { t } = useI18n()
  const { isManagement } = useAuth()
  const { data: clients } = useClients({ status: "active", q: "" })
  const [clientId, setClientId] = useState<number | null>(null)
  const [q, setQ] = useState("")
  const [prompt, setPrompt] = useState("")
  const [aspect, setAspect] = useState("social_post_4_5")
  const [briefId, setBriefId] = useState<number | null>(null)
  const [refIds, setRefIds] = useState<number[]>([])
  const [uretiliyor, setUretiliyor] = useState(false)
  const [sonuc, setSonuc] = useState<ImageJob | null>(null)
  const [hata, setHata] = useState<string | null>(null)
  const [hataKodu, setHataKodu] = useState<string | null>(null)
  const [gecmis, setGecmis] = useState<ImageJob[]>([])
  // --- batch generation state ---
  const [haftalar, setHaftalar] = useState<BriefWeek[]>([])
  const [hafta, setHafta] = useState("")
  const [otomatik, setOtomatik] = useState(false)
  const [kvkk, setKvkk] = useState(true)
  const [gruplar, setGruplar] = useState<BatchGroup[]>([])
  const [atlanan, setAtlanan] = useState<SkippedIdea[]>([])
  const [partiCalisiyor, setPartiCalisiyor] = useState(false)
  const [logoYok, setLogoYok] = useState(false)

  const { data: briefs } = useImageGenBriefs(clientId)
  const { data: assets } = useClientAssets(clientId)

  const filtered = useMemo(() => {
    const list = clients ?? []
    const nq = trFold(q.trim())
    return nq ? list.filter((c) => trFold(c.name).includes(nq)) : list
  }, [clients, q])

  async function musteriSec(id: number) {
    setClientId(id)
    setBriefId(null)
    setRefIds([])
    setSonuc(null)
    setHata(null)
    setHataKodu(null)
    setGruplar([])
    setAtlanan([])
    try {
      setGecmis(await listImageJobs(id))
    } catch {
      setGecmis([])
    }
    try {
      // The toggle's state is READ FROM the DB, not left at a default — otherwise the page
      // looks off on every reload while the DB actually has it on, and the button stays
      // disabled for no apparent reason.
      const ayar = await getClientSettings(id)
      setOtomatik(ayar.auto_image_enabled)
      setKvkk(ayar.ai_image_consent)
      await haftalariYukle(id)
    } catch {
      setOtomatik(false)
      setHaftalar([])
      setHafta("")
    }
  }

  // "This week" is DELIBERATELY not the default: briefs are generated for the current
  // week + 2 (enqueue_briefs._target_week_iso), so the current week's brief is usually missing.
  async function haftalariYukle(id: number) {
    const w = await listWeeks(id)
    setHaftalar(w)
    const ilk = w[0]?.week_iso ?? ""
    setHafta(ilk)
    if (ilk) await partiYukle(id, ilk)
  }

  async function partiYukle(id: number, weekIso: string) {
    const b = await listBatch(id, weekIso)
    setGruplar(b.groups)
    setAtlanan(b.skipped)
  }

  async function haftaSec(weekIso: string) {
    setHafta(weekIso)
    if (clientId != null && weekIso) await partiYukle(clientId, weekIso)
  }

  async function partiBaslat() {
    if (clientId == null || !hafta) return
    setPartiCalisiyor(true)
    setHata(null)
    setHataKodu(null)
    try {
      const r = await startBatch(clientId, hafta)
      setLogoYok(r.logo_missing)
      // Each job finishes individually; wait for all of them then refresh the list.
      await Promise.allSettled(r.created.map((j) => pollImageJob(j.id)))
      await partiYukle(clientId, hafta)
    } catch (e) {
      setHata(e instanceof Error ? e.message : t("pages.codexImage.batchStartFailed"))
    } finally {
      setPartiCalisiyor(false)
    }
  }

  function refToggle(id: number) {
    setRefIds((mevcut) =>
      mevcut.includes(id)
        ? mevcut.filter((x) => x !== id)
        : mevcut.length >= MAX_REFERENCES
          ? mevcut
          : [...mevcut, id],
    )
  }

  async function uret() {
    if (clientId == null || !prompt.trim()) return
    setUretiliyor(true)
    setHata(null)
    setHataKodu(null)
    setSonuc(null)
    try {
      const { image_job } = await generateImage({
        client_id: clientId,
        prompt,
        aspect_ratio: aspect,
        brief_id: briefId ?? undefined,
        reference_asset_ids: refIds,
      })
      // Codex takes 1-4 min; pollImageJob's timeout is 15 min to accommodate this.
      const bitmis = await pollImageJob(image_job.id)
      if (bitmis.status === "completed") {
        setSonuc(bitmis)
      } else {
        setHata(bitmis.error_public ?? t("pages.codexImage.generateFailed"))
        setHataKodu(bitmis.error_code)
      }
      setGecmis(await listImageJobs(clientId))
    } catch (e) {
      setHata(e instanceof Error && e.message === IMAGEGEN_TIMEOUT_MARKER
        ? t("pages.codexImage.timeout")
        : e instanceof Error ? e.message : t("pages.codexImage.generateStartFailed"))
    } finally {
      setUretiliyor(false)
    }
  }

  if (!isManagement) {
    return <div className="p-6 text-sm text-muted-foreground">{t("pages.codexImage.managementOnly")}</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("pages.codexImage.title")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("pages.codexImage.subtitle")}
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-[260px_1fr]">
        {/* client selection */}
        <div className="space-y-2">
          <Input placeholder={t("pages.codexImage.searchClientPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-96 space-y-1 overflow-auto rounded-md border p-1">
            {filtered.map((c) => (
              <button
                key={c.id}
                onClick={() => musteriSec(c.id)}
                className={`w-full rounded px-2 py-1.5 text-left text-sm ${
                  clientId === c.id ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                }`}
              >
                {c.name}
              </button>
            ))}
          </div>
        </div>

        {/* generation form + result */}
        <div className="space-y-4">
          {clientId == null ? (
            <div className="text-sm text-muted-foreground">{t("pages.codexImage.selectClientPrompt")}</div>
          ) : (
            <>
              {/* --- Weekly batch generation: bulk images from brief ideas --- */}
              <div className="space-y-3 rounded-md border p-3">
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={otomatik}
                      onChange={async (e) => {
                        const acik = e.target.checked
                        setOtomatik(acik)
                        await setAutoImage(clientId, acik)
                      }}
                    />
                    {t("pages.codexImage.weeklyAutoLabel")}
                  </label>
                  <select
                    className={selectCls}
                    value={hafta}
                    onChange={(e) => haftaSec(e.target.value)}
                    aria-label={t("pages.codexImage.weekAriaLabel")}
                  >
                    {haftalar.length === 0 && <option value="">{t("pages.codexImage.noApprovedBrief")}</option>}
                    {haftalar.map((w) => (
                      <option key={w.week_iso} value={w.week_iso}>
                        {w.week_iso}
                      </option>
                    ))}
                  </select>
                  <Button
                    onClick={partiBaslat}
                    disabled={!otomatik || !kvkk || !hafta || partiCalisiyor}
                  >
                    {partiCalisiyor ? (
                      <Loader2 className="mr-2 size-4 animate-spin" />
                    ) : (
                      <Sparkles className="mr-2 size-4" />
                    )}
                    {t("pages.codexImage.generateWeekButton")}
                  </Button>
                </div>

                {!kvkk && (
                  <p className="text-xs text-muted-foreground">
                    {t("pages.codexImage.noConsent")}
                  </p>
                )}
                {logoYok && (
                  <p className="text-xs text-muted-foreground">
                    {t("pages.codexImage.noLogo")}
                  </p>
                )}
                {partiCalisiyor && (
                  <p className="text-sm text-muted-foreground">
                    {t("pages.codexImage.batchRunning")}
                  </p>
                )}

                {gruplar.map((g) => (
                  <div key={g.index} className="space-y-1">
                    <h3 className="text-sm font-medium">{g.baslik}</h3>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {(["with_text", "clean"] as const).map((v) => {
                        const job = g.jobs[v]
                        if (!job) return null
                        return (
                          <div key={v} className="rounded border p-2 text-xs">
                            <div className="mb-1 flex items-center justify-between">
                              <Badge variant="secondary">{t(VARIANT_LABEL_KEY[v])}</Badge>
                              <span>{job.status in STATUS_LABEL_KEY ? t(STATUS_LABEL_KEY[job.status]) : job.status}</span>
                            </div>
                            {job.has_image ? (
                              <>
                                <img
                                  src={imageUrl(job.id)}
                                  alt={g.baslik}
                                  className="w-full rounded"
                                />
                                <a
                                  href={imageUrl(job.id)}
                                  download={`codex-gorsel-${job.id}.png`}
                                  className="underline"
                                >
                                  {t("pages.codexImage.download")}
                                </a>
                              </>
                            ) : (
                              <p className="text-muted-foreground">
                                {job.error_public ?? t("pages.codexImage.waiting")}
                              </p>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}

                {atlanan.map((sk) => (
                  <p key={sk.index} className="text-xs text-muted-foreground">
                    {sk.baslik} — {sk.sebep}
                  </p>
                ))}
              </div>

              <div className="space-y-2">
                <Label htmlFor="codex-istem">{t("pages.codexImage.promptLabel")}</Label>
                <Textarea
                  id="codex-istem"
                  rows={4}
                  placeholder={t("pages.codexImage.promptPlaceholder")}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </div>

              <div className="flex flex-wrap items-end gap-4">
                <div className="space-y-1">
                  <Label htmlFor="codex-oran">{t("pages.codexImage.aspectLabel")}</Label>
                  <select
                    id="codex-oran"
                    className={selectCls}
                    value={aspect}
                    onChange={(e) => setAspect(e.target.value)}
                  >
                    {ASPECTS.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1">
                  <Label htmlFor="codex-brief">{t("pages.codexImage.briefLabel")}</Label>
                  <select
                    id="codex-brief"
                    className={selectCls}
                    value={briefId ?? ""}
                    onChange={(e) => setBriefId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">{t("pages.codexImage.briefNone")}</option>
                    {(briefs ?? []).map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.week_iso}
                        {b.title ? ` · ${b.title}` : ""}
                      </option>
                    ))}
                  </select>
                </div>

                <Button onClick={uret} disabled={uretiliyor || !prompt.trim()}>
                  {uretiliyor ? (
                    <Loader2 className="mr-2 size-4 animate-spin" />
                  ) : (
                    <Sparkles className="mr-2 size-4" />
                  )}
                  {t("pages.codexImage.generateButton")}
                </Button>
              </div>

              {(assets ?? []).length > 0 && (
                <div className="space-y-2">
                  <Label>
                    {t("pages.codexImage.referenceImagesLabel")}{" "}
                    <span className="text-muted-foreground">
                      {t("pages.codexImage.referenceImagesMax", { count: MAX_REFERENCES })}
                    </span>
                  </Label>
                  <div className="flex flex-wrap gap-2">
                    {(assets ?? []).map((a) => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => refToggle(a.id)}
                        className={`rounded-md border px-2 py-1 text-xs ${
                          refIds.includes(a.id)
                            ? "border-primary bg-primary/10"
                            : "hover:bg-muted"
                        }`}
                      >
                        {a.label || a.file_name || (a.kind === "logo" ? t("pages.codexImage.logoFallback") : t("pages.codexImage.imageFallback"))}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {uretiliyor && (
                <p className="text-sm text-muted-foreground">
                  {t("pages.codexImage.generating")}
                </p>
              )}

              {hata && (
                <div className="flex gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                  <div>
                    {hata}
                    {hataKodu === "auth" && (
                      <p className="mt-1 font-medium">
                        {t("pages.codexImage.authInterventionNeeded")}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {sonuc && (
                <div className="space-y-2 rounded-md border p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <Badge variant="secondary">{sonuc.status in STATUS_LABEL_KEY ? t(STATUS_LABEL_KEY[sonuc.status]) : sonuc.status}</Badge>
                    {sonuc.output_meta && (
                      <span className="text-muted-foreground">
                        {sonuc.output_meta.width}×{sonuc.output_meta.height}
                      </span>
                    )}
                  </div>
                  <img
                    src={imageUrl(sonuc.id)}
                    alt={t("pages.codexImage.generatedImageAlt")}
                    className="max-w-full rounded-lg border"
                  />
                  {/* Button doesn't support `asChild` (the shadcn version in this project) →
                      the download link is styled with buttonVariants instead. */}
                  <a
                    href={imageUrl(sonuc.id)}
                    download={`codex-gorsel-${sonuc.id}.png`}
                    className={buttonVariants({ variant: "outline", size: "sm" })}
                  >
                    <Download className="mr-2 size-4" />
                    {t("pages.codexImage.download")}
                  </a>
                </div>
              )}

              {gecmis.length > 0 && (
                <div className="space-y-2">
                  <h2 className="text-sm font-medium">{t("pages.codexImage.recentGenerations")}</h2>
                  <div className="grid gap-3 sm:grid-cols-3">
                    {gecmis.map((g) => (
                      <div key={g.id} className="space-y-1 rounded-md border p-2 text-xs">
                        {g.has_image ? (
                          <img
                            src={imageUrl(g.id)}
                            alt={g.original_user_prompt}
                            className="aspect-square w-full rounded object-cover"
                          />
                        ) : (
                          <div className="flex aspect-square w-full items-center justify-center rounded bg-muted text-muted-foreground">
                            <ImageOff className="size-6" />
                          </div>
                        )}
                        <div className="flex items-center justify-between gap-1">
                          <Badge variant="secondary" className="text-[10px]">
                            {g.status in STATUS_LABEL_KEY ? t(STATUS_LABEL_KEY[g.status]) : g.status}
                          </Badge>
                          {g.has_image && (
                            <a
                              href={imageUrl(g.id)}
                              download={`codex-gorsel-${g.id}.png`}
                              className="underline"
                            >
                              {t("pages.codexImage.download")}
                            </a>
                          )}
                        </div>
                        <p className="line-clamp-2 text-muted-foreground">
                          {g.error_public || g.original_user_prompt}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
