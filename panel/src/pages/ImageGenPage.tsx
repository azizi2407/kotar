// AI Görsel Üretim sayfası (Faz 6, step 19) — müşteri seç + opsiyonel brief + boyut/model/
// motor/çözünürlük + (Task 7) referans + üret + sonuç göster + onayla/yeniden. GATE 18:
// üretim backend'de Magnific/Freepik Mystic REST + API-key (MCP YOK). KVKK onay kapısı:
// onaysız müşteride üretim başlatılamaz (backend 409). Üretim onay bekler (pending).
import { useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Check, Languages, Lightbulb, Loader2, RefreshCw, Sparkles, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
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

// Mystic REST modelleri + MCP modelleri (Magnific MCP; backend MCP_IMAGE_MODELS ile eş).
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
const ENGINES: [string, string][] = [
  ["automatic", "Otomatik"], ["magnific_illusio", "Illusio"],
  ["magnific_sharpy", "Sharpy"], ["magnific_sparkle", "Sparkle"],
]
const RESOLUTIONS = ["1k", "2k", "4k"]
const ASPECTS: [string, string][] = [
  ["social_post_4_5", "POST (4:5)"], ["social_story_9_16", "STORY (9:16)"],
]

const STATUS_LABEL: Record<string, string> = {
  pending: "Onay bekliyor",
  approved: "Onaylandı",
  rejected: "Yeniden üretildi",
}

const selectCls =
  "h-9 rounded-md border bg-background px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"

export function ImageGenPage() {
  const { data: clients } = useClients({ status: "active", q: "" })
  const { isManagement } = useAuth()
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
          // motor yalnız Mystic'te anlamlı; referanslar her iki yolda da çalışır (v2).
          engine: mystic ? engine : undefined,
          structure_ref: structureRef ?? undefined,
          style_ref: styleRef ?? undefined,
        },
      })
      const done = await pollJob(job.id)
      if (done.status === "failed") {
        setError(done.result?.error || "Görsel üretimi başarısız oldu.")
      }
      await refetch()
      // Üretim kredi harcadı; worker tazeleme job'u kuyrukladı → rozet sorgusunu tazele
      // (refreshing=true dönerse hook 20 sn'de bir kendini günceller).
      qc.invalidateQueries({ queryKey: ["magnific-credits"] })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Görsel üretimi başlatılamadı.")
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
        setError(done.result?.error || "Yeniden üretim başarısız oldu.")
      }
      await refetch()
      qc.invalidateQueries({ queryKey: ["magnific-credits"] })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Yeniden üretim başlatılamadı.")
    } finally {
      setGenerating(false)
    }
  }

  if (!isManagement) {
    return <div className="p-6 text-sm text-muted-foreground">Bu sayfa yalnız yönetim içindir.</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">AI Görsel Üretimi</h1>
        <p className="text-sm text-muted-foreground">
          Müşteri seç, istem + (opsiyonel) brief + boyut/model ayarlarını ver, görsel üret.
          Üretilen görsel onay bekler. Onaysız müşteride üretim yapılamaz (KVKK).
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-[260px_1fr]">
        {/* müşteri seçimi */}
        <div className="space-y-2">
          <Input placeholder="Müşteri ara…" value={q} onChange={(e) => setQ(e.target.value)} />
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

        {/* üretim formu + sonuçlar */}
        <div className="space-y-4">
          {clientId == null ? (
            <div className="text-sm text-muted-foreground">Başlamak için bir müşteri seçin.</div>
          ) : (
            <>
              <div className="space-y-3 rounded-lg border p-4">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ig-prompt">İstem (prompt)</Label>
                    <Button
                      type="button" variant="outline" size="sm"
                      disabled={!prompt.trim() || convertPrompt.isPending}
                      title="İstemi İngilizce'ye çevirip yapılandırılmış JSON'a dönüştürür (kredi harcamaz)"
                      onClick={() =>
                        convertPrompt.mutate({ prompt }, {
                          onSuccess: (p) => setPrompt(p),
                          onError: (e) => toast.error(e instanceof Error ? e.message : "Dönüşüm başarısız"),
                        })}
                    >
                      {convertPrompt.isPending
                        ? <Loader2 className="mr-1 size-4 animate-spin" />
                        : <Languages className="mr-1 size-4" />}
                      İngilizce JSON'a çevir
                    </Button>
                  </div>
                  <Textarea
                    id="ig-prompt"
                    placeholder="Ne üretilsin? Örn: taze kahve, sıcak tonlar, üstten çekim…"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                  />
                </div>

                {/* opsiyonel brief + brief'ten örnek istemler */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ig-brief">Haftalık brief (opsiyonel)</Label>
                    {briefId != null && (
                      <Button
                        type="button" variant="outline" size="sm"
                        disabled={promptExamples.isPending}
                        title="Seçili brief'in fikirlerinden 3 örnek istem üretir (kredi harcamaz)"
                        onClick={() =>
                          promptExamples.mutate({ client_id: clientId, brief_id: briefId }, {
                            onSuccess: setExamples,
                            onError: (e) => toast.error(e instanceof Error ? e.message : "Örnekler üretilemedi"),
                          })}
                      >
                        {promptExamples.isPending
                          ? <Loader2 className="mr-1 size-4 animate-spin" />
                          : <Lightbulb className="mr-1 size-4" />}
                        Örnek promptlar
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
                    <option value="">Brief kullanma</option>
                    {(briefs ?? []).map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.week_iso} · {b.title || "Başlıksız"} ({b.status})
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
                          title="İsteme kopyala"
                        >
                          {ex}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* boyut */}
                <div className="space-y-1.5">
                  <Label>Boyut</Label>
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

                {/* model / motor / çözünürlük — motor yalnız Mystic'te anlamlı */}
                <div className="flex flex-wrap gap-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="ig-model">Model</Label>
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
                      <Label htmlFor="ig-engine">Motor</Label>
                      <select id="ig-engine" className={selectCls}
                        value={engine} onChange={(e) => setEngine(e.target.value)}>
                        {ENGINES.map(([val, lbl]) => <option key={val} value={val}>{lbl}</option>)}
                      </select>
                    </div>
                  )}
                  <div className="space-y-1.5">
                    <Label htmlFor="ig-res">Çözünürlük</Label>
                    <select id="ig-res" className={selectCls}
                      value={resolution} onChange={(e) => setResolution(e.target.value)}>
                      {RESOLUTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </div>
                </div>

                {/* referanslar (yapı + stil) — tüm modellerde (MCP yolunda upload+references) */}
                <div className="grid gap-2 sm:grid-cols-2">
                  <ReferencePicker label="Yapı referansı" clientId={clientId}
                    value={structureRef} onChange={setStructureRef} />
                  <ReferencePicker label="Stil referansı" clientId={clientId}
                    value={styleRef} onChange={setStyleRef} />
                </div>

                <div className="flex flex-wrap items-center gap-4">
                  <label className="flex items-center gap-2 text-sm"
                    title="Açıkken istem üretim anında otomatik İngilizce+JSON'a dönüştürülür (zaten JSON ise atlanır)">
                    <Switch checked={refine} onCheckedChange={setRefine} />
                    Üretimde otomatik İngilizce+JSON dönüşümü
                  </label>
                  <Button className="ml-auto" onClick={handleGenerate} disabled={generating}>
                    {generating ? (
                      <Loader2 className="mr-1 size-4 animate-spin" />
                    ) : (
                      <Sparkles className="mr-1 size-4" />
                    )}
                    Üret
                  </Button>
                </div>
                {error && (error.includes("claude mcp login") ? (
                  // Magnific MCP OAuth token'ı düşmüş — çözüm adımlarını göster.
                  <div className="space-y-2 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                    <div className="flex items-center gap-2 font-medium">
                      <TriangleAlert className="h-4 w-4 shrink-0" />
                      Magnific yetkilendirmesi süresi doldu — üretim yapılamadı
                    </div>
                    <ol className="list-decimal space-y-1 pl-5">
                      <li>Sunucuda interaktif bir terminal açın (SSH).</li>
                      <li><code className="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs dark:bg-amber-900/60">claude mcp login magnific --no-browser</code> komutunu çalıştırın.</li>
                      <li>Basılan URL'i tarayıcıda açıp Magnific'i onaylayın.</li>
                      <li>Tarayıcının yönlendiği adresi (localhost/callback…) kopyalayıp terminaldeki isteme yapıştırın.</li>
                      <li>Bu sayfaya dönüp üretimi yeniden deneyin.</li>
                    </ol>
                  </div>
                ) : (
                  <div className="text-sm text-destructive">{error}</div>
                ))}
              </div>

              {/* sonuçlar */}
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
                    <div className="text-sm text-muted-foreground">Henüz üretim yok.</div>
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
          alt="AI üretilen görsel"
          className="aspect-square w-full rounded object-cover"
        />
      ) : (
        <div className="flex aspect-square w-full items-center justify-center rounded bg-muted text-xs text-muted-foreground">
          Görsel yok
        </div>
      )}
      {row.prompt && <p className="line-clamp-3 text-xs text-muted-foreground">{row.prompt}</p>}
      {row.status === "pending" && (
        <div className="flex gap-2">
          <Button size="sm" onClick={onApprove} disabled={busy}>
            <Check className="mr-1 size-4" /> Onayla
          </Button>
          <Button size="sm" variant="outline" onClick={onRegenerate} disabled={busy}>
            <RefreshCw className="mr-1 size-4" /> Yeniden
          </Button>
        </div>
      )}
    </div>
  )
}
