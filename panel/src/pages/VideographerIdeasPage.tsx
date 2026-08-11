// Videographer Suggestions — AI trend-suggestion cards (link + reason + shoot idea).
// Selecting a client and clicking "Generate Suggestions" triggers a trend scan; each
// card is evaluated with "Like" (adds a ShootTask to the shoot list) or "Skip". (Moved
// out of the Suggestions tab on the Videographer Board into its own page — 2026-07-19.)
import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Sparkles, SkipForward, ThumbsUp } from "lucide-react"
import { toast } from "sonner"
import { useQueryClient } from "@tanstack/react-query"

import {
  pollJob, useGenerateIdeas, useLikeIdea, useSkipIdea, useVideographerIdeas,
} from "@/lib/sharing"
import { useClients } from "@/lib/clients"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function VideographerIdeasPage() {
  const { t } = useI18n()
  const { data: clients } = useClients({ status: "active", q: "" })
  const [params, setParams] = useSearchParams()
  const clientId = params.get("client") ?? ""
  const setClientId = (v: string) =>
    setParams((p) => { if (v) p.set("client", v); else p.delete("client"); return p }, { replace: true })
  const cid = clientId ? Number(clientId) : null

  const { data, isLoading, isError, refetch } = useVideographerIdeas(cid)
  const gen = useGenerateIdeas()
  const like = useLikeIdea(cid ?? 0)
  const skip = useSkipIdea(cid ?? 0)
  const qc = useQueryClient()
  const [generating, setGenerating] = useState(false)
  const [elapsed, setElapsed] = useState(0)

  // Elapsed time counter during generation (to remove the user's "is this stuck?" uncertainty).
  useEffect(() => {
    if (!generating) { setElapsed(0); return }
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [generating])

  function onGenerate() {
    if (!cid) { toast.error(t("pages.videographerIdeas.selectClientFirst")); return }
    setGenerating(true)
    gen.mutate(cid, {
      onSuccess: async (job) => {
        try {
          const done = await pollJob(job.id, { timeout: 240000 })
          if (done.status === "failed") toast.error(t("pages.videographerIdeas.generateFailed"))
          else toast.success(t("pages.videographerIdeas.ready"))
          qc.invalidateQueries({ queryKey: ["vg-ideas", cid] })
          refetch()
        } catch {
          toast.error(t("pages.videographerIdeas.timeout"))
        } finally {
          setGenerating(false)
        }
      },
      onError: (err) => {
        setGenerating(false)
        toast.error(err instanceof Error ? err.message : t("pages.videographerIdeas.triggerFailed"))
      },
    })
  }

  const ideas = data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("pages.videographerIdeas.title")}</h1>
        <p className="text-muted-foreground">{t("pages.videographerIdeas.subtitle")}</p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-muted/20 p-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">{t("pages.videographerIdeas.clientLabel")}</label>
          <select value={clientId} onChange={(e) => setClientId(e.target.value)}
            className="block h-9 w-48 rounded-md border bg-background px-2 text-sm">
            <option value="">{t("pages.videographerIdeas.selectPlaceholder")}</option>
            {(clients ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <Button onClick={onGenerate} disabled={!cid || generating}>
          <Sparkles className="mr-1 h-4 w-4" />
          {generating ? t("pages.videographerIdeas.generatingLabel") : t("pages.videographerIdeas.generateBtn")}
        </Button>
      </div>

      {generating && (
        <div className="flex items-center gap-3 rounded-lg border bg-muted/20 p-3 text-sm">
          <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-muted border-t-foreground" />
          <span>
            {t("pages.videographerIdeas.scanningText")}{" "}
            <span className="text-muted-foreground">{t("pages.videographerIdeas.etaText", { elapsed })}</span>
          </span>
        </div>
      )}

      {!cid ? (
        <p className="text-muted-foreground">{t("pages.videographerIdeas.selectClientHint")}</p>
      ) : isError ? (
        <p className="text-destructive">{t("pages.videographerIdeas.loadError")}</p>
      ) : isLoading ? (
        <div className="space-y-2">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}</div>
      ) : ideas.length === 0 ? (
        <p className="text-muted-foreground">{t("pages.videographerIdeas.noIdeas")}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {ideas.map((idea) => (
            <div key={idea.id} className="flex flex-col gap-2 rounded-lg border bg-card p-3">
              {idea.shoot_idea && <div className="text-sm font-medium">{idea.shoot_idea}</div>}
              {idea.reason && <div className="text-xs text-muted-foreground">{idea.reason}</div>}
              {idea.reference_link && (
                <a href={idea.reference_link} target="_blank" rel="noreferrer"
                  className="truncate text-xs text-primary hover:underline">
                  {idea.reference_link}
                </a>
              )}
              <div className="mt-1 flex gap-2">
                <Button size="sm" variant="outline" disabled={like.isPending}
                  onClick={() => like.mutate({ id: idea.id },
                    { onSuccess: () => toast.success(t("pages.videographerIdeas.addedToShootList")),
                      onError: () => toast.error(t("pages.videographerIdeas.addFailed")) })}>
                  <ThumbsUp className="mr-1 h-3.5 w-3.5" /> {t("pages.videographerIdeas.likeBtn")}
                </Button>
                <Button size="sm" variant="ghost" disabled={skip.isPending}
                  onClick={() => skip.mutate(idea.id, { onError: () => toast.error(t("pages.videographerIdeas.skipFailed")) })}>
                  <SkipForward className="mr-1 h-3.5 w-3.5" /> {t("pages.videographerIdeas.skipBtn")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
