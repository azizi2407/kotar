// AI Image Generation page (Phase 6, step 19) — pick client + optional brief + size/model/
// engine/resolution + (Task 7) reference + generate + show result + approve/regenerate. GATE 18:
// generation runs on the backend via Magnific/Freepik Mystic REST + API key (NO MCP). KVKK
// (Turkish data-protection) consent gate: generation can't start for an unconsented client
// (backend 409). Generations start out pending approval.
import { useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Check, Languages, Lightbulb, Loader2, RefreshCw, Sparkles, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import { useI18n } from "@/lib/i18n"
import {
  pollJob,
  useApproveImage,
  useConvertPrompt,
  useGenerateImage,
  useImageGenBriefs,
  useImageGenerations,
  usePromptExamples,
  useRegenerateImage,
  type ImageGeneration,
  type ImageRef,
} from "@/lib/sharing"
import { trFold } from "@/lib/week"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ReferencePicker } from "@/components/sharing/ReferencePicker"
import { cn } from "@/lib/utils"

// Mystic REST models + MCP models (Magnific MCP; kept in sync with backend MCP_IMAGE_MODELS).
const MODEL_GROUPS: [string, [string, string][]][] = [
  ["Mystic", [
    ["realism", "Realism"], ["fluid", "Fluid"], ["zen", "Zen"], ["flexible", "Flexible"],
    ["super_real", "Super Real"], ["editorial_portraits", "Editorial Portraits"],
  ]],
  ["Google", [
    ["imagen-nano-banana-2", "Nano Banana Pro"],
    ["imagen-nano-banana-2-flash", "Nano Banana 2"],
  ]],
  ["OpenAI", [["gpt-2", "GPT 2"]]],
  ["Recraft", [["recraft-v4-1", "Recraft V4.1"]]],
  ["Flux", [["flux-2", "Flux 2 Pro"]]],
  ["Seedream", [["seedream-4-5", "Seedream 4.5"]]],
]
const MYSTIC_MODELS = new Set(MODEL_GROUPS[0][1].map(([v]) => v))
// engine value → translation key (label is produced with t() at render time).
const ENGINE_KEYS: [string, string][] = [
  ["automatic", "pages.imageGen.engineAuto"], ["magnific_illusio", "Illusio"],
  ["magnific_sharpy", "Sharpy"], ["magnific_sparkle", "Sparkle"],
]
const RESOLUTIONS = ["1k", "2k", "4k"]
const ASPECTS: [string, string][] = [
  ["social_post_4_5", "POST (4:5)"], ["social_story_9_16", "STORY (9:16)"],
]

const selectCls =
  "h-9 rounded-md border bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"

export function ImageGenPage() {
  const { t } = useI18n()
  const { data: clients } = useClients({ status: "active", q: "" })
  const { isManagement } = useAuth()
  const ENGINES: [string, string][] = ENGINE_KEYS.map(
    ([val, key]) => [val, key.startsWith("pages.") ? t(key) : key])
  const [clientId, setClientId] = useState<number | null>(null)
  const [q, setQ] = useState("")
  const [prompt, setPrompt] = useState("")
  const [briefId, setBriefId] = useState<number | null>(null)
  const [aspect, setAspect] = useState("social_post_4_5")
  const [model, setModel] = useState("realism")
  const [engine, setEngine] = useState("automatic")
  const [resolution, setResolution] = useState("2k")
  const [refine, setRefine] = useState(true)
  const [structureRef, setStructureRef] = useState<ImageRef | null>(null)
  const [styleRef, setStyleRef] = useState<ImageRef | null>(null)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [examples, setExamples] = useState<string[]>([])
  const qc = useQueryClient()
  const { data: rows, isLoading, refetch } = useImageGenerations(clientId)
  const { data: briefs } = useImageGenBriefs(clientId)
  const promptExamples = usePromptExamples()
  const convertPrompt = useConvertPrompt()
  const generate = useGenerateImage()
  const approve = useApproveImage(clientId ?? 0)
  const regenerate = useRegenerateImage(clientId ?? 0)

  const filtered = useMemo(() => {
    const list = clients ?? []
    const nq = trFold(q.trim())
    return nq ? list.filter((c) => trFold(c.name).includes(nq)) : list
  }, [clients, q])

  async function handleGenerate() {
    if (clientId == null) return
    setError(null)
    setGenerating(true)
    try {
      const mystic = MYSTIC_MODELS.has(model)
      const job = await generate.mutateAsync({
        client_id: clientId,
        brief_id: briefId ?? undefined,
        settings: {
          aspect_ratio: aspect, model, resolution, refine, prompt,
          // engine only matters for Mystic; references work on both paths (v2).
          engine: mystic ? engine : undefined,
          structure_ref: structureRef ?? undefined,
          style_ref: styleRef ?? undefined,
        },
      })
      const done = await pollJob(job.id)
      if (done.status === "failed") {
        setError(done.result?.error || t("pages.imageGen.generateFailedResult"))
      }
      await refetch()
      // The generation spent credit; the worker queued a refresh job → invalidate the badge query
      // (if it returns refreshing=true, the hook refreshes itself every 20s).
      qc.invalidateQueries({ queryKey: ["magnific-credits"] })
    } catch (e) {
      setError(e instanceof Error ? e.message : t("pages.imageGen.generateFailedStart"))
    } finally {
      setGenerating(false)
    }
  }

  async function handleRegenerate(id: number) {
    setError(null)
    setGenerating(true)
    try {
      const job = await regenerate.mutateAsync(id)
      const done = await pollJob(job.id)
      if (done.status === "failed") {
        setError(done.result?.error || t("pages.imageGen.regenerateFailedResult"))
      }
      await refetch()
      qc.invalidateQueries({ queryKey: ["magnific-credits"] })
    } catch (e) {
      setError(e instanceof Error ? e.message : t("pages.imageGen.regenerateFailedStart"))
    } finally {
      setGenerating(false)
    }
  }

  if (!isManagement) {
    return <div className="p-6 text-sm text-muted-foreground">{t("pages.imageGen.managementOnly")}</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{t("pages.imageGen.title")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("pages.imageGen.subtitle")}
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-[260px_1fr]">
        {/* client selection */}
        <div className="space-y-2">
          <Input placeholder={t("pages.imageGen.searchClientPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="max-h-96 space-y-1 overflow-auto rounded-md border p-1">
            {filtered.map((c) => (
              <button
                key={c.id}
                onClick={() => { setClientId(c.id); setBriefId(null); setExamples([]) }}
                className={`w-full rounded px-2 py-1.5 text-left text-sm ${
                  clientId === c.id ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                }`}
              >
                {c.name}
              </button>
            ))}
          </div>
        </div>

        {/* generation form + results */}
        <div className="space-y-4">
          {clientId == null ? (
            <div className="text-sm text-muted-foreground">{t("pages.imageGen.selectClientPrompt")}</div>
          ) : (
            <>
              <div className="space-y-3 rounded-lg border p-4">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ig-prompt">{t("pages.imageGen.promptLabel")}</Label>
                    <Button
                      type="button" variant="outline" size="sm"
                      disabled={!prompt.trim() || convertPrompt.isPending}
                      title={t("pages.imageGen.convertPromptTitle")}
                      onClick={() =>
                        convertPrompt.mutate({ prompt }, {
                          onSuccess: (p) => setPrompt(p),
                          onError: (e) => toast.error(e instanceof Error ? e.message : t("pages.imageGen.convertFailed")),
                        })}
                    >
                      {convertPrompt.isPending
                        ? <Loader2 className="mr-1 size-4 animate-spin" />
                        : <Languages className="mr-1 size-4" />}
                      {t("pages.imageGen.convertPromptButton")}
                    </Button>
                  </div>
                  <Textarea
                    id="ig-prompt"
                    placeholder={t("pages.imageGen.promptPlaceholder")}
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                  />
                </div>

                {/* optional brief + example prompts from the brief */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ig-brief">{t("pages.imageGen.briefLabel")}</Label>
                    {briefId != null && (
                      <Button
                        type="button" variant="outline" size="sm"
                        disabled={promptExamples.isPending}
                        title={t("pages.imageGen.examplesButtonTitle")}
                        onClick={() =>
                          promptExamples.mutate({ client_id: clientId, brief_id: briefId }, {
                            onSuccess: setExamples,
                            onError: (e) => toast.error(e instanceof Error ? e.message : t("pages.imageGen.examplesFailed")),
                          })}
                      >
                        {promptExamples.isPending
                          ? <Loader2 className="mr-1 size-4 animate-spin" />
                          : <Lightbulb className="mr-1 size-4" />}
                        {t("pages.imageGen.examplesButton")}
                      </Button>
                    )}
                  </div>
                  <select
                    id="ig-brief"
                    className={cn(selectCls, "w-full")}
                    value={briefId ?? ""}
                    onChange={(e) => {
                      setBriefId(e.target.value ? Number(e.target.value) : null)
                      setExamples([])
                    }}
                  >
                    <option value="">{t("pages.imageGen.noBriefOption")}</option>
                    {(briefs ?? []).map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.week_iso} · {b.title || t("pages.imageGen.untitledBrief")} ({b.status})
                      </option>
                    ))}
                  </select>
                  {examples.length > 0 && (
                    <div className="space-y-1">
                      {examples.map((ex, i) => (
                        <button
                          key={i} type="button"
                          onClick={() => setPrompt(ex)}
                          className="block w-full rounded-md border px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
                          title={t("pages.imageGen.copyToPromptTitle")}
                        >
                          {ex}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* size */}
                <div className="space-y-1.5">
                  <Label>{t("pages.imageGen.sizeLabel")}</Label>
                  <div className="flex gap-1 rounded-md border p-1">
                    {ASPECTS.map(([val, lbl]) => (
                      <button
                        key={val}
                        type="button"
                        onClick={() => setAspect(val)}
                        className={cn(
                          "flex-1 rounded px-3 py-1.5 text-sm transition-colors",
                          aspect === val ? "bg-primary text-primary-foreground" : "hover:bg-muted",
                        )}
                      >
                        {lbl}
                      </button>
                    ))}
                  </div>
                </div>

                {/* model / engine / resolution — engine only matters for Mystic */}
                <div className="flex flex-wrap gap-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="ig-model">{t("pages.imageGen.modelLabel")}</Label>
                    <select id="ig-model" className={selectCls}
                      value={model} onChange={(e) => setModel(e.target.value)}>
                      {MODEL_GROUPS.map(([group, models]) => (
                        <optgroup key={group} label={group}>
                          {models.map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
                        </optgroup>
                      ))}
                    </select>
                  </div>
                  {MYSTIC_MODELS.has(model) && (
                    <div className="space-y-1.5">
                      <Label htmlFor="ig-engine">{t("pages.imageGen.engineLabel")}</Label>
                      <select id="ig-engine" className={selectCls}
                        value={engine} onChange={(e) => setEngine(e.target.value)}>
                        {ENGINES.map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
                      </select>
                    </div>
                  )}
                  <div className="space-y-1.5">
                    <Label htmlFor="ig-res">{t("pages.imageGen.resolutionLabel")}</Label>
                    <select id="ig-res" className={selectCls}
                      value={resolution} onChange={(e) => setResolution(e.target.value)}>
                      {RESOLUTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </div>
                </div>

                {/* references (structure + style) — for all models (upload+references on the MCP path) */}
                <div className="grid gap-2 sm:grid-cols-2">
                  <ReferencePicker label={t("pages.imageGen.structureRefLabel")} clientId={clientId}
                    value={structureRef} onChange={setStructureRef} />
                  <ReferencePicker label={t("pages.imageGen.styleRefLabel")} clientId={clientId}
                    value={styleRef} onChange={setStyleRef} />
                </div>

                <div className="flex flex-wrap items-center gap-4">
                  <label className="flex items-center gap-2 text-sm"
                    title={t("pages.imageGen.autoRefineTitle")}>
                    <Switch checked={refine} onCheckedChange={setRefine} />
                    {t("pages.imageGen.autoRefineLabel")}
                  </label>
                  <Button className="ml-auto" onClick={handleGenerate} disabled={generating}>
                    {generating ? (
                      <Loader2 className="mr-1 size-4 animate-spin" />
                    ) : (
                      <Sparkles className="mr-1 size-4" />
                    )}
                    {t("pages.imageGen.generateButton")}
                  </Button>
                </div>
                {error && (error.includes("claude mcp login") ? (
                  // The Magnific MCP OAuth token has expired — show the resolution steps.
                  <div className="space-y-2 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                    <div className="flex items-center gap-2 font-medium">
                      <TriangleAlert className="h-4 w-4 shrink-0" />
                      {t("pages.imageGen.mcpAuthExpiredTitle")}
                    </div>
                    <ol className="list-decimal space-y-1 pl-5">
                      <li>{t("pages.imageGen.mcpAuthStep1")}</li>
                      <li><code className="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs dark:bg-amber-900/60">claude mcp login magnific --no-browser</code> {t("pages.imageGen.mcpAuthStep2")}</li>
                      <li>{t("pages.imageGen.mcpAuthStep3")}</li>
                      <li>{t("pages.imageGen.mcpAuthStep4")}</li>
                      <li>{t("pages.imageGen.mcpAuthStep5")}</li>
                    </ol>
                  </div>
                ) : (
                  <div className="text-sm text-destructive">{error}</div>
                ))}
              </div>

              {/* results */}
              {isLoading ? (
                <Skeleton className="h-40 w-full" />
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  {(rows ?? []).map((r) => (
                    <ResultCard
                      key={r.id}
                      row={r}
                      busy={generating}
                      onApprove={() => approve.mutate(r.id)}
                      onRegenerate={() => handleRegenerate(r.id)}
                    />
                  ))}
                  {(rows ?? []).length === 0 && (
                    <div className="text-sm text-muted-foreground">{t("pages.imageGen.noResultsYet")}</div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ResultCard({
  row,
  busy,
  onApprove,
  onRegenerate,
}: {
  row: ImageGeneration
  busy: boolean
  onApprove: () => void
  onRegenerate: () => void
}) {
  const { t } = useI18n()
  const STATUS_LABEL: Record<string, string> = {
    pending: t("pages.imageGen.status.pending"),
    approved: t("pages.imageGen.status.approved"),
    rejected: t("pages.imageGen.status.rejected"),
  }
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex items-center justify-between">
        <Badge variant={row.status === "approved" ? "default" : "secondary"}>
          {STATUS_LABEL[row.status] ?? row.status}
        </Badge>
        <span className="text-xs text-muted-foreground">{row.created_at?.slice(0, 16)}</span>
      </div>
      {row.result_url ? (
        <img
          src={row.result_url}
          alt={t("pages.imageGen.resultAlt")}
          className="aspect-square w-full rounded object-cover"
        />
      ) : (
        <div className="flex aspect-square w-full items-center justify-center rounded bg-muted text-xs text-muted-foreground">
          {t("pages.imageGen.noImage")}
        </div>
      )}
      {row.prompt && <p className="line-clamp-3 text-xs text-muted-foreground">{row.prompt}</p>}
      {row.status === "pending" && (
        <div className="flex gap-2">
          <Button size="sm" onClick={onApprove} disabled={busy}>
            <Check className="mr-1 size-4" /> {t("pages.imageGen.approveButton")}
          </Button>
          <Button size="sm" variant="outline" onClick={onRegenerate} disabled={busy}>
            <RefreshCw className="mr-1 size-4" /> {t("pages.imageGen.regenerateButton")}
          </Button>
        </div>
      )}
    </div>
  )
}
