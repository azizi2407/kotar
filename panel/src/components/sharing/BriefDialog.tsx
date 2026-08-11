// Weekly brief viewer (read-only). Opened from the board for a client × week.
// As full as the brief on the old server: sample title, content, slogan/CTA, visual
// style, what must appear in the visual, style reference, pinterest links + week notes.
import { useEffect, useState } from "react"
import { ChevronLeft, ChevronRight, ExternalLink } from "lucide-react"

import { useBrief, type Brief, type BriefIdea, type PinterestLink } from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { shiftWeek, weekRangeLabel } from "@/lib/week"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  clientId: number
  clientName: string
  weekIso: string
}

// Field label + free-text body (whitespace preserved).
function Field({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="whitespace-pre-wrap text-sm">{value}</div>
    </div>
  )
}

// Bullet list field (Plan, What must appear in the visual…).
function ListField({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
        {items.map((c, i) => <li key={i}>{c}</li>)}
      </ul>
    </div>
  )
}

// --- Idea key normalization ---
// Brief ideas can be found in the DB under THREE different key dictionaries:
//  1) Current/Option A (build_brief_prompt → parse_brief): başlık/içerik/cta/görsel_tarz/
//     görsel_gerekli/referans/pinterest + pillar/format/çekim_tipi/plan/ad
//  2) Old Mongo migration: ornek_baslik/icerik/slogan_cta/gorsel_tarzi/
//     gorselde_bulunmasi_gerekenler/izlenecek_tarz_referans/pinterest_links/title
//  3) Vault markdown: "Örnek başlık"/"İçerik"/"Slogan / CTA"/"Görsel tarzı"/…
// This normalizer collapses all three into a single view; new fields (pillar/format/
// çekim_tipi/plan) are rendered too.
interface NormIdea {
  heading: string
  konu?: string
  pillar?: string
  format?: string
  icerik?: string
  plan: string[]
  cekim_tipi?: string
  cta?: string
  gorsel_tarz?: string
  gorsel_gerekli: string[]
  referans?: string
  pinterest: PinterestLink[]
}

function pickStr(idea: BriefIdea, ...keys: string[]): string | undefined {
  for (const k of keys) {
    const v = idea[k]
    if (typeof v === "string" && v.trim()) return v.trim()
    if (Array.isArray(v) && v.length) return v.map((x) => String(x)).join("\n")
  }
  return undefined
}

function pickList(idea: BriefIdea, ...keys: string[]): string[] {
  for (const k of keys) {
    const v = idea[k]
    if (Array.isArray(v) && v.length) return v.map((x) => String(x)).filter((s) => s.trim())
    if (typeof v === "string" && v.trim()) return [v.trim()]
  }
  return []
}

function normPins(idea: BriefIdea): PinterestLink[] {
  const raw = idea.pinterest ?? idea.pinterest_links ?? idea["Pinterest ilham"]
  const out: PinterestLink[] = []
  const push = (item: unknown) => {
    if (!item) return
    if (typeof item === "string") {
      item.split(/\s+/).filter((s) => /^https?:\/\//.test(s)).forEach((u) => out.push({ url: u }))
    } else if (typeof item === "object") {
      const o = item as { url?: string; label?: string | null }
      if (o.url) out.push({ url: o.url, label: o.label })
    }
  }
  if (Array.isArray(raw)) raw.forEach(push)
  else push(raw)
  return out
}

function normalizeIdea(idea: BriefIdea): NormIdea {
  const heading = pickStr(idea, "ad", "başlık", "başlik", "ornek_baslik", "Örnek başlık", "title") ?? ""
  const konu = pickStr(idea, "başlık", "başlik", "title")
  return {
    heading,
    konu: konu && konu !== heading ? konu : undefined,
    pillar: pickStr(idea, "pillar"),
    format: pickStr(idea, "format"),
    icerik: pickStr(idea, "içerik", "icerik", "İçerik", "İçerik (post angle)", "İçerit"),
    plan: pickList(idea, "plan", "Plan çekim listesi", "Plan çekim listesi / fotoğraf",
      "Plan çekim listesi / görsel"),
    cekim_tipi: pickStr(idea, "çekim_tipi", "Çekim tipi"),
    cta: pickStr(idea, "cta", "slogan_cta", "Slogan / CTA", "Slogan (foto üstüne)"),
    gorsel_tarz: pickStr(idea, "görsel_tarz", "gorsel_tarzi", "Görsel tarzı"),
    gorsel_gerekli: pickList(idea, "görsel_gerekli", "gorselde_bulunmasi_gerekenler",
      "Görselde bulunması gerekenler"),
    referans: pickStr(idea, "referans", "izlenecek_tarz_referans", "İzlenecek tarz / referans"),
    pinterest: normPins(idea),
  }
}

function IdeaCard({ idea, index }: { idea: BriefIdea; index: number }) {
  const { t } = useI18n()
  const n = normalizeIdea(idea)
  const number = typeof idea.number === "number" ? idea.number : index + 1
  return (
    <li className="space-y-3 rounded-lg border p-3">
      <div className="flex items-baseline gap-2">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
          {number}
        </span>
        <div className="text-sm font-semibold">
          {n.heading || <span className="italic text-muted-foreground">{t("components.sharing.briefDialog.untitled")}</span>}
        </div>
      </div>
      {(n.pillar || n.format) && (
        <div className="flex flex-wrap gap-1.5">
          {n.pillar && (
            <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">{n.pillar}</span>
          )}
          {n.format && (
            <span className="rounded-md border px-2 py-0.5 text-[11px] font-medium text-muted-foreground">{n.format}</span>
          )}
        </div>
      )}
      {n.konu && <Field label={t("components.sharing.briefDialog.fieldTopic")} value={n.konu} />}
      <Field label={t("components.sharing.briefDialog.fieldContent")} value={n.icerik} />
      <ListField label={t("components.sharing.briefDialog.fieldPlan")} items={n.plan} />
      <Field label={t("components.sharing.briefDialog.fieldShootType")} value={n.cekim_tipi} />
      <Field label={t("components.sharing.briefDialog.fieldCta")} value={n.cta} />
      <Field label={t("components.sharing.briefDialog.fieldVisualStyle")} value={n.gorsel_tarz} />
      <ListField label={t("components.sharing.briefDialog.fieldVisualRequirements")} items={n.gorsel_gerekli} />
      <Field label={t("components.sharing.briefDialog.fieldReference")} value={n.referans} />
      {n.pinterest.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("components.sharing.briefDialog.pinterestReferences")}
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {n.pinterest.map((p, i) => (
              <a key={i} href={p.url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-xs text-primary hover:bg-primary/10">
                <ExternalLink className="h-3 w-3" />
                {p.label || t("components.sharing.briefDialog.pinterestN", { n: i + 1 })}
              </a>
            ))}
          </div>
        </div>
      )}
    </li>
  )
}

// Week notes (editor_notlari, secilen_4_fikir, musteri_onayi_tarihi …) — since keys
// can be dynamic, this renders generically: array → list, text → paragraph.
function humanizeKey(k: string, t: ReturnType<typeof useI18n>["t"]) {
  const noteLabels: Record<string, string> = {
    editor_notlari: t("components.sharing.briefDialog.noteEditorNotes"),
    secilen_4_fikir: t("components.sharing.briefDialog.noteSelectedIdeas"),
    musteri_onayi_tarihi: t("components.sharing.briefDialog.noteClientApprovalDate"),
  }
  return noteLabels[k] || k.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase())
}

function WeekNotes({ notes }: { notes: Record<string, unknown> }) {
  const { t } = useI18n()
  const entries = Object.entries(notes).filter(([, v]) =>
    Array.isArray(v) ? v.length > 0 : v != null && String(v).trim() !== "")
  if (entries.length === 0) return null
  return (
    <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
      <div className="text-sm font-medium">{t("components.sharing.briefDialog.weekNotes")}</div>
      {entries.map(([k, v]) => (
        <div key={k}>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{humanizeKey(k, t)}</div>
          {Array.isArray(v) ? (
            <ul className="mt-0.5 list-disc space-y-0.5 pl-5 text-sm">
              {v.map((item, i) => <li key={i}>{String(item)}</li>)}
            </ul>
          ) : (
            <div className="whitespace-pre-wrap text-sm">{String(v)}</div>
          )}
        </div>
      ))}
    </div>
  )
}

// Brief body — shared by the dialog + the Brief page.
export function BriefBody({ brief }: { brief: Brief }) {
  const { t } = useI18n()
  return (
    <div className="space-y-4">
      {brief.title && <div className="text-base font-semibold">{brief.title}</div>}
      {brief.intro && (
        <div className="whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-sm">
          {brief.intro}
        </div>
      )}
      <WeekNotes notes={brief.week_notes} />
      {brief.ideas.length > 0 && (
        <div className="space-y-2">
          <div className="text-sm font-medium">
            {t("components.sharing.briefDialog.contentIdeas", { count: brief.ideas.length })}
          </div>
          <ol className="space-y-3">
            {brief.ideas.map((idea, i) => <IdeaCard key={i} idea={idea} index={i} />)}
          </ol>
        </div>
      )}
      {!brief.intro && brief.ideas.length === 0 && brief.raw_md && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-xs">
          {brief.raw_md}
        </pre>
      )}
    </div>
  )
}

export function BriefDialog({ open, onOpenChange, clientId, clientName, weekIso }: Props) {
  const { t, lang } = useI18n()
  // In-modal week navigation (2026-08-04): so viewing a brief doesn't require closing
  // the modal and changing the week on the board. Always resets to the caller's week
  // on open (the `open` dependency) — if navigation stayed in state, a user clicking
  // into another row's brief on the board would see the wrong week.
  const [week, setWeek] = useState(weekIso)
  useEffect(() => { if (open) setWeek(weekIso) }, [open, weekIso])
  const { data: brief, isLoading } = useBrief(clientId, week, open)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("components.sharing.briefDialog.title", { clientName })}</DialogTitle>
          <DialogDescription>{t("components.sharing.briefDialog.description", { week })}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-1 border-b pb-2">
          <Button variant="ghost" size="icon" title={t("components.sharing.briefDialog.prevWeek")}
            onClick={() => setWeek(shiftWeek(week, -1))}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="min-w-44 text-center text-sm font-medium">
            {week} · {weekRangeLabel(week, lang)}
          </span>
          <Button variant="ghost" size="icon" title={t("components.sharing.briefDialog.nextWeek")}
            onClick={() => setWeek(shiftWeek(week, 1))}>
            <ChevronRight className="h-4 w-4" />
          </Button>
          {week !== weekIso && (
            <Button variant="outline" size="sm" className="ml-1" onClick={() => setWeek(weekIso)}>
              {t("components.sharing.briefDialog.openingWeek")}
            </Button>
          )}
        </div>

        {isLoading && <Skeleton className="h-40 w-full" />}
        {!isLoading && !brief && (
          <p className="py-8 text-center text-muted-foreground">{t("components.sharing.briefDialog.noBrief")}</p>
        )}
        {brief && <BriefBody brief={brief} />}
      </DialogContent>
    </Dialog>
  )
}
