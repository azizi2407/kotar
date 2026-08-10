// Videografçı Öneriler — AI trend-öneri kartları (link + neden + çekim fikri). Müşteri
// seçip "Öneri Üret" ile trend tarama tetiklenir; her kart "Beğen" (çekim listesine
// ShootTask ekler) ya da "Atla" ile değerlendirilir. (Videografçı Panosu'ndaki Öneriler
// sekmesinden ayrı sayfaya taşındı — 2026-07-19.)
import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Sparkles, SkipForward, ThumbsUp } from "lucide-react"
import { toast } from "sonner"
import { useQueryClient } from "@tanstack/react-query"

import {
  pollJob, useGenerateIdeas, useLikeIdea, useSkipIdea, useVideographerIdeas,
} from "@/lib/sharing"
import { useClients } from "@/lib/clients"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function VideographerIdeasPage() {
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

  // Üretim sırasında geçen süre sayacı (kullanıcı "takıldı mı?" belirsizliğini gidermek için).
  useEffect(() => {
    if (!generating) { setElapsed(0); return }
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [generating])

  function onGenerate() {
    if (!cid) { toast.error("Önce müşteri seç"); return }
    setGenerating(true)
    gen.mutate(cid, {
      onSuccess: async (job) => {
        try {
          const done = await pollJob(job.id, { timeout: 240000 })
          if (done.status === "failed") toast.error("Öneri üretilemedi")
          else toast.success("Öneriler hazır")
          qc.invalidateQueries({ queryKey: ["vg-ideas", cid] })
          refetch()
        } catch {
          toast.error("Üretim zaman aşımına uğradı")
        } finally {
          setGenerating(false)
        }
      },
      onError: (err) => {
        setGenerating(false)
        toast.error(err instanceof Error ? err.message : "Öneri tetiklenemedi")
      },
    })
  }

  const ideas = data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Öneriler</h1>
        <p className="text-muted-foreground">AI trend-öneri kartları — müşteriye özel çekim fikirleri.</p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-muted/20 p-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Müşteri</label>
          <select value={clientId} onChange={(e) => setClientId(e.target.value)}
            className="block h-9 w-48 rounded-md border bg-background px-2 text-sm">
            <option value="">Seç…</option>
            {(clients ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <Button onClick={onGenerate} disabled={!cid || generating}>
          <Sparkles className="mr-1 h-4 w-4" />
          {generating ? "Üretiliyor…" : "Öneri Üret"}
        </Button>
      </div>

      {generating && (
        <div className="flex items-center gap-3 rounded-lg border bg-muted/20 p-3 text-sm">
          <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-muted border-t-foreground" />
          <span>
            Trend videolar taranıyor ve öneriler üretiliyor…{" "}
            <span className="text-muted-foreground">~1 dk sürebilir ({elapsed}s)</span>
          </span>
        </div>
      )}

      {!cid ? (
        <p className="text-muted-foreground">Trend önerilerini görmek için müşteri seç.</p>
      ) : isError ? (
        <p className="text-destructive">Öneriler yüklenemedi.</p>
      ) : isLoading ? (
        <div className="space-y-2">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}</div>
      ) : ideas.length === 0 ? (
        <p className="text-muted-foreground">Henüz öneri yok. "Öneri Üret" ile trend tarat.</p>
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
                    { onSuccess: () => toast.success("Çekim listesine eklendi"),
                      onError: () => toast.error("Eklenemedi") })}>
                  <ThumbsUp className="mr-1 h-3.5 w-3.5" /> Beğen
                </Button>
                <Button size="sm" variant="ghost" disabled={skip.isPending}
                  onClick={() => skip.mutate(idea.id, { onError: () => toast.error("Atlanamadı") })}>
                  <SkipForward className="mr-1 h-3.5 w-3.5" /> Atla
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
