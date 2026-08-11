// Create/edit share modal. Manages a single share.
// The file is picked from the upload pool (Drive thumbnails in Phase 2b). AI caption
// generation is active (via the ai_worker job queue); the "Generation settings" block
// is pre-filled with the client's default, overridable per generation (Phase 1b).
import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Check, Copy, Download, Image as ImageIcon, RotateCw, Settings2, Sparkles, Upload, X } from "lucide-react"
import { toast } from "sonner"

import { ApiError, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import {
  KIND_LABELS, pollJob, useCaptionSettings, useCreateShare, useDeleteShare, useDeleteUpload,
  useGenerateCaption, useProcessMedia, useRequestRevision, useShareAction, useUpdateShareById,
  useUploadFile, downloadMediaUrl, thumbnailUrl, useUploads,
  type CaptionSettings, type Share, type ShareKind,
} from "@/lib/sharing"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Progress } from "@/components/ui/progress"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

const KINDS: ShareKind[] = ["post", "story", "video", "linkedin"]

// Caption settings UI: model options (ai_claude.py DEFAULT_MODEL + known alternative).
const MODEL_DEFAULT = "__varsayilan__"
function modelOptions(t: ReturnType<typeof useI18n>["t"]) {
  return [
    { value: MODEL_DEFAULT, label: t("components.sharing.shareModal.modelDefault") },
    { value: "claude-sonnet-5", label: "Sonnet" },
    { value: "claude-opus-4-8", label: "Opus" },
  ]
}

function numOrNull(v: string): number | null {
  if (v.trim() === "") return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// Combine caption + hashtags into a single text / split them back apart (the data
// model keeps them separate). Layout: <caption block>  ---  <hashtag set>. The middle
// '---' is display-only; it's consumed on save and re-added on load (round-trip safe).
function combineSet(caption: string, tags: string): string {
  return caption + (tags ? (caption ? "\n\n---\n\n" : "") + tags : "")
}
function splitSet(text: string): { caption: string; hashtag: string } {
  const t = text.replace(/\s+$/, "")
  // Split off the trailing (optional '---' separator +) consecutive #tag block as
  // hashtag; the rest is caption. A '---' inside the caption itself is preserved.
  const m = t.match(/^([\s\S]*?)(?:\n+---)?\s*(#[^\s#]+(?:\s+#[^\s#]+)*)$/)
  if (m && m[2].trim()) return { caption: m[1].replace(/\s+$/, ""), hashtag: m[2].trim() }
  return { caption: t.trim(), hashtag: "" }
}

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  clientId: number
  clientName: string
  weekIso: string
  share: Share | null // null => new
}

export function ShareModal({ open, onOpenChange, clientId, clientName, weekIso, share }: Props) {
  const { t } = useI18n()
  const MODEL_OPTIONS = useMemo(() => modelOptions(t), [t])
  const editing = share != null
  const [kind, setKind] = useState<ShareKind>("post")
  const [fileId, setFileId] = useState<string | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  // Caption + hashtag are edited in a SINGLE box; split into caption_text/hashtag_text on save.
  const [content, setContent] = useState("")
  const [sugTags, setSugTags] = useState("")  // shared hashtag set produced by the AI
  const [note, setNote] = useState("")
  const [plannedDate, setPlannedDate] = useState("")
  const [plannedTime, setPlannedTime] = useState("")

  const { data: uploads } = useUploads(open ? clientId : null, weekIso)
  const { canImpersonate: isSuperadmin } = useAuth()
  const deleteUpload = useDeleteUpload(clientId, weekIso)
  const create = useCreateShare()
  const updateById = useUpdateShareById()
  const action = useShareAction()
  // The first save() (e.g. "Generate" caption needs an id) creates a draft; we
  // keep the id here so subsequent saves UPDATE instead — otherwise "Publish"
  // would create a second share, resulting in a duplicate record.
  const [savedId, setSavedId] = useState<number | null>(null)
  const del = useDeleteShare()
  const [uploadPct, setUploadPct] = useState(0)
  const upload = useUploadFile(clientId, weekIso, setUploadPct)
  const revision = useRequestRevision()
  const genCaption = useGenerateCaption()
  const procMedia = useProcessMedia()
  const qc = useQueryClient()
  const [generating, setGenerating] = useState(false)
  const [genMsg, setGenMsg] = useState("")
  const [captionAlts, setCaptionAlts] = useState<string[]>([])
  const [feedback, setFeedback] = useState("")
  const [useTranscript, setUseTranscript] = useState(false)  // video: transcript is optional, off by default
  const fileInput = useRef<HTMLInputElement>(null)
  const busy = create.isPending || updateById.isPending || action.isPending || del.isPending

  // Caption generation settings (Phase 1b): pre-filled with the client's default,
  // changeable per generation (permanent writes only from the client settings page).
  const [showSettings, setShowSettings] = useState(false)
  const [settings, setSettings] = useState<CaptionSettings>({})
  const defaultsAppliedRef = useRef(false)
  const { data: clientDefaults } = useCaptionSettings(open ? clientId : null)

  // Caption generation (shared path for both the first generation and feedback-driven regeneration).
  async function runCaption(opts?: { feedback?: string; previousCaption?: string; skipMedia?: boolean }) {
    const s = await save()  // save first (id required)
    if (!s) return
    setGenerating(true)
    try {
      // If there's a file, process media first: frames (visual context) + OPTIONAL
      // transcript for video. On regenerate (skipMedia) frames already exist →
      // media isn't reprocessed (faster, no extra failure surface).
      if (!opts?.skipMedia && s.file_id) {
        setGenMsg(s.kind === "video" ? t("components.sharing.shareModal.processingVideo") : t("components.sharing.shareModal.processingImage"))
        const mjob = await procMedia.mutateAsync({ shareId: s.id, useTranscript: s.kind === "video" && useTranscript })
        await pollJob(mjob.id, { timeout: 300000 })
      }
      setGenMsg(opts?.feedback ? t("components.sharing.shareModal.regenerating") : t("components.sharing.shareModal.generatingCaption"))
      const job = await genCaption.mutateAsync({
        shareId: s.id, settings,
        feedback: opts?.feedback, previousCaption: opts?.previousCaption,
      })
      const done = await pollJob(job.id)
      if (done.status === "done" && done.result) {
        const alts = done.result.captions || (done.result.caption ? [done.result.caption] : [])
        const tags = done.result.hashtags || ""
        setCaptionAlts(alts)
        setSugTags(tags)
        if (alts.length) setContent(combineSet(alts[0], tags))
        setFeedback("")
        // Refresh the board cache: so suggestions appear without a reload after the modal is closed and reopened.
        qc.invalidateQueries({ queryKey: ["board"] })
        qc.invalidateQueries({ queryKey: ["designer-board"] })
        toast.success(t("components.sharing.shareModal.captionCreated", { clientName }))
      } else {
        toast.error(done.result?.error || t("components.sharing.shareModal.generationFailed"))
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("components.sharing.shareModal.generationError"))
    } finally {
      setGenerating(false)
      setGenMsg("")
    }
  }

  function onGenerate() { return runCaption() }

  function onRegenerate() {
    const fb = feedback.trim()
    if (!fb) return
    // The disliked previous caption = the currently selected/displayed one (caption part of content).
    const prev = splitSet(content).caption || captionAlts[0] || ""
    // Frames were already extracted on the first generation → don't reprocess media (skipMedia).
    return runCaption({ feedback: fb, previousCaption: prev, skipMedia: true })
  }

  async function onCopy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      toast.success(t("components.sharing.shareModal.copied"))
    } catch {
      toast.error(t("components.sharing.shareModal.copyFailed"))
    }
  }

  async function onRequestRevision() {
    if (!share) return
    const note = window.prompt(t("components.sharing.shareModal.revisionNotePrompt"))?.trim()
    if (!note) return
    try {
      await revision.mutateAsync({
        client_id: clientId, week_iso: weekIso,
        kind: share.kind === "video" ? "video" : "design",
        share_id: share.id, note,
      })
      toast.success(t("components.sharing.shareModal.revisionRequested"))
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("components.sharing.shareModal.revisionFailed"))
    }
  }

  const category = kind === "linkedin" ? "linkedin" : kind === "story" ? "story"
    : kind === "video" ? "video" : "post"

  async function onPickUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(t("components.sharing.shareModal.fileTooLarge", { maxMb: MAX_UPLOAD_MB }))
      return
    }
    setUploadPct(0)
    try {
      const up = await upload.mutateAsync({ file, category })
      setFileId(up.file_id)
      setFileName(up.file_name)
      toast.success(t("components.sharing.shareModal.uploaded"))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("components.sharing.shareModal.uploadFailed"))
    }
  }

  useEffect(() => {
    if (!open) return
    setSavedId(share?.id ?? null)  // new modal → clean; editing → existing id
    setKind(share?.kind ?? "post")
    setFileId(share?.file_id ?? null)
    setFileName(share?.file_name ?? null)
    setContent(combineSet(share?.caption_text ?? "", share?.hashtag_text ?? ""))
    setNote(share?.note ?? "")
    setPlannedDate(share?.planned_date ?? "")
    setPlannedTime(share?.planned_time ?? "")
    // Last generated caption suggestions + hashtag set (persistent) — stay visible even after closing/reopening the modal
    setCaptionAlts(share?.caption_suggestions?.captions ?? [])
    setSugTags(share?.caption_suggestions?.hashtags ?? "")
    setFeedback("")
    setUseTranscript(false)  // video transcript: off by default every time the modal opens
    // Generation settings start clean; once the client default loads, the effect
    // below applies it once (won't overwrite if the user changes it for this generation).
    setSettings({})
    defaultsAppliedRef.current = false
  }, [open, share])

  // Once the client default (caption_settings) loads, pre-fill the settings block once.
  useEffect(() => {
    if (!open || defaultsAppliedRef.current || !clientDefaults) return
    setSettings(clientDefaults)
    defaultsAppliedRef.current = true
  }, [open, clientDefaults])

  // Uploads matching the selected kind (loose category matching).
  const relevantUploads = useMemo(() => {
    const all = uploads ?? []
    const cat = kind === "linkedin" ? "linkedin" : kind === "story" ? "story" : kind === "video" ? "video" : "post"
    const matched = all.filter((u) => (u.category || "post") === cat)
    const pool = matched.length ? matched : all
    // A file that already has a card DOES NOT appear in the picker (2026-07-29) —
    // prevents opening a second card on the same file and producing duplicate content.
    // The ONE exception is the currently selected file: while editing it would also
    // be `used`, so it'd get filtered out and the user couldn't see what they'd chosen.
    return pool.filter((u) => !u.used || u.file_id === fileId)
  }, [uploads, kind, fileId])

  function body() {
    const { caption, hashtag } = splitSet(content)
    return {
      client_id: clientId, week_iso: weekIso, kind,
      file_id: fileId, file_name: fileName,
      caption_text: caption, hashtag_text: hashtag, note,
      planned_date: plannedDate || null, planned_time: plannedTime || null,
    }
  }

  async function save(): Promise<Share | null> {
    try {
      // Update the existing share (editing) or the draft just created in this
      // modal (savedId); if neither exists, create a new one and adopt its id.
      const targetId = editing ? share!.id : savedId
      if (targetId != null) return await updateById.mutateAsync({ id: targetId, body: body() })
      const s = await create.mutateAsync(body())
      setSavedId(s.id)
      return s
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("components.sharing.shareModal.saveFailed"))
      return null
    }
  }

  async function onSaveDraft() {
    const s = await save()
    if (s) {
      toast.success(t("components.sharing.shareModal.draftSaved"))
      onOpenChange(false)
    }
  }

  // Saves the current share and clears the form for a new share WITHOUT closing the
  // modal (each image is its own card; the previous one stays on the board). New-share flow only.
  async function onSaveAndNew() {
    const s = await save()
    if (!s) return
    toast.success(t("components.sharing.shareModal.savedNext"))
    setSavedId(null)  // let the next save() create a new card
    setFileId(null)
    setFileName(null)
    setContent("")
    setSugTags("")
    setCaptionAlts([])
    setNote("")
    setPlannedDate("")
    setPlannedTime("")
  }

  async function onPublish() {
    const s = await save()
    if (!s) return
    try {
      await action.mutateAsync({ id: s.id, action: "publish" })
      toast.success(t("components.sharing.shareModal.published"))
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("components.sharing.shareModal.publishFailed"))
    }
  }

  async function onUnpublish() {
    if (!share) return
    await action.mutateAsync({ id: share.id, action: "unpublish" })
    toast.success(t("components.sharing.shareModal.unpublished"))
    onOpenChange(false)
  }

  async function onDelete() {
    if (!share) return
    await del.mutateAsync(share.id)
    toast.success(t("components.sharing.shareModal.deleted"))
    onOpenChange(false)
  }

  const hasAlts = captionAlts.length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(
        "max-h-[90vh] overflow-y-auto transition-[max-width]",
        hasAlts ? "sm:max-w-5xl" : "sm:max-w-2xl",
      )}>
        <DialogHeader>
          <DialogTitle>
            {editing ? t("components.sharing.shareModal.editTitle") : t("components.sharing.shareModal.newTitle")} · {clientName}
          </DialogTitle>
          <DialogDescription>{t("components.sharing.shareModal.weekDescription", { weekIso })}</DialogDescription>
        </DialogHeader>

        <div className={cn("grid gap-5", hasAlts && "lg:grid-cols-2")}>
          {/* LEFT PANEL — input: kind, file, note, plan */}
          <div className="space-y-4">
          {/* Kind */}
          <div className="space-y-1.5">
            <Label>{t("components.sharing.shareModal.kind")}</Label>
            {editing ? (
              <div><Badge variant="secondary">{KIND_LABELS[kind]}</Badge></div>
            ) : (
              <Tabs value={kind} onValueChange={(v) => v && setKind(v as ShareKind)}>
                <TabsList>
                  {KINDS.map((k) => <TabsTrigger key={k} value={k}>{KIND_LABELS[k]}</TabsTrigger>)}
                </TabsList>
              </Tabs>
            )}
          </div>

          {/* File selection (upload pool) + upload */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>{t("components.sharing.shareModal.file")}</Label>
              <input ref={fileInput} type="file" className="hidden"
                accept="image/*,video/*" onChange={onPickUpload} />
              <Button type="button" variant="outline" size="sm"
                onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
                <Upload className="mr-1 h-3.5 w-3.5" />
                {upload.isPending ? t("components.sharing.shareModal.uploading") : t("components.sharing.shareModal.upload")}
              </Button>
            </div>
            {upload.isPending && (
              <div className="space-y-1">
                <Progress value={uploadPct} />
                <p className="text-[11px] text-muted-foreground">{t("components.sharing.shareModal.uploadingPct", { pct: uploadPct })}</p>
              </div>
            )}
            {relevantUploads.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("components.sharing.shareModal.noUploadsThisWeek")}</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {relevantUploads.map((u) => (
                  <div key={u.id} className="group relative">
                    <button
                      type="button"
                      onClick={() => {
                        // Switching to a different file invalidates the previous captions — clear them.
                        if (u.file_id !== fileId) { setCaptionAlts([]); setSugTags(""); setFeedback("") }
                        setFileId(u.file_id); setFileName(u.file_name)
                      }}
                      className={cn(
                        "flex w-24 flex-col overflow-hidden rounded-md border text-left transition-colors",
                        fileId === u.file_id
                          ? "border-primary ring-2 ring-primary/40"
                          : "hover:border-primary/50",
                      )}
                      title={u.file_name || ""}
                    >
                      {/* Drive thumbnail; if it fails to load, the icon behind it shows */}
                      <div className="relative flex h-16 w-full items-center justify-center bg-muted">
                        <ImageIcon className="absolute h-5 w-5 text-muted-foreground/40" />
                        {u.file_id && (
                          <img
                            src={thumbnailUrl(u.file_id, 200)}
                            alt=""
                            loading="lazy"
                            className="relative h-full w-full object-contain"
                            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none" }}
                          />
                        )}
                      </div>
                      <div className="truncate px-1.5 py-1 text-[10px] font-medium">
                        {u.file_name || u.file_id}
                      </div>
                    </button>
                    {isSuperadmin && (
                      <button
                        type="button"
                        title={t("components.sharing.shareModal.deleteUploadTitle")}
                        disabled={deleteUpload.isPending}
                        onClick={() => {
                          if (!window.confirm(t("components.sharing.shareModal.deleteUploadConfirm", { name: u.file_name || u.file_id || "" }))) return
                          deleteUpload.mutate(u.id, {
                            onSuccess: () => {
                              if (fileId === u.file_id) { setFileId(null); setFileName(null) }
                              toast.success(t("components.sharing.shareModal.uploadDeleted"))
                            },
                            onError: (e) => toast.error(e instanceof Error ? e.message : t("components.sharing.shareModal.deleteFailed")),
                          })
                        }}
                        className="absolute right-0.5 top-0.5 rounded bg-black/60 p-0.5 text-white opacity-0 transition-opacity hover:bg-red-600 group-hover:opacity-100"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
            {fileName && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="truncate">{t("components.sharing.shareModal.selected", { fileName })}</span>
                {fileId && (
                  <a href={downloadMediaUrl(fileId, fileName ?? undefined)}
                    download={fileName ?? ""}
                    className="ml-auto inline-flex shrink-0 items-center gap-1 font-medium text-primary hover:underline">
                    <Download className="h-3.5 w-3.5" /> {t("components.sharing.shareModal.downloadFullSize")}
                  </a>
                )}
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="note">{t("components.sharing.shareModal.note")}</Label>
            <Input id="note" value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={t("components.sharing.shareModal.notePlaceholder")} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="pd">{t("components.sharing.shareModal.plannedDate")}</Label>
              <Input id="pd" type="date" value={plannedDate}
                onChange={(e) => setPlannedDate(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pt">{t("components.sharing.shareModal.time")}</Label>
              <Input id="pt" type="time" value={plannedTime}
                onChange={(e) => setPlannedTime(e.target.value)} />
            </div>
          </div>
          </div>

          {/* RIGHT PANEL — Caption & Hashtag: generate, 3 alternative cards, feedback, editable text */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="content">{t("components.sharing.shareModal.captionHashtag")}</Label>
              <div className="flex items-center gap-1.5">
                <Button type="button" variant="ghost" size="sm"
                  onClick={() => setShowSettings((v) => !v)} title={t("components.sharing.shareModal.generationSettings")}>
                  <Settings2 className="mr-1 h-3.5 w-3.5" />
                  {t("components.sharing.shareModal.settings")}
                </Button>
                <Button type="button" variant="outline" size="sm" onClick={onGenerate}
                  disabled={busy || generating} title={t("components.sharing.shareModal.generateWithAiTitle")}>
                  <Sparkles className="mr-1 h-3.5 w-3.5" />
                  {generating
                    ? (genMsg || t("components.sharing.shareModal.generating"))
                    : hasAlts ? t("components.sharing.shareModal.regenerate") : t("components.sharing.shareModal.generate")}
                </Button>
              </div>
            </div>
            {kind === "video" && (
              <label className="flex items-center gap-2 text-sm"
                title={t("components.sharing.shareModal.transcriptTitle")}>
                <Switch checked={useTranscript} onCheckedChange={setUseTranscript} />
                🎤 {t("components.sharing.shareModal.transcriptLabel")}
              </label>
            )}
            {showSettings && (
              <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>{t("components.sharing.shareModal.model")}</Label>
                  <Select value={settings.model || MODEL_DEFAULT}
                    onValueChange={(v) => v && setSettings((s) => (
                      { ...s, model: v === MODEL_DEFAULT ? null : v }))}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {MODEL_OPTIONS.map((o) => (
                        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-tone">{t("components.sharing.shareModal.tone")}</Label>
                  <Input id="cs-tone" value={settings.tone ?? ""}
                    placeholder={t("components.sharing.shareModal.tonePlaceholder")}
                    onChange={(e) => setSettings((s) => ({ ...s, tone: e.target.value || null }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-emoji">{t("components.sharing.shareModal.emojiLimit")}</Label>
                  <Input id="cs-emoji" type="number" min={0} value={settings.emoji_limit ?? ""}
                    onChange={(e) => setSettings((s) => ({ ...s, emoji_limit: numOrNull(e.target.value) }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-hashtag">{t("components.sharing.shareModal.hashtagCount")}</Label>
                  <Input id="cs-hashtag" type="number" min={0} value={settings.hashtag_count ?? ""}
                    onChange={(e) => setSettings((s) => ({ ...s, hashtag_count: numOrNull(e.target.value) }))} />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("components.sharing.shareModal.language")}</Label>
                  <Select value={settings.lang || "TR"}
                    onValueChange={(v) => v && setSettings((s) => ({ ...s, lang: v as CaptionSettings["lang"] }))}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="TR">{t("components.sharing.shareModal.langTr")}</SelectItem>
                      <SelectItem value="EN">{t("components.sharing.shareModal.langEn")}</SelectItem>
                      <SelectItem value="TR+EN">{t("components.sharing.shareModal.langBoth")}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-charlimit">{t("components.sharing.shareModal.charLimit")}</Label>
                  <Input id="cs-charlimit" type="number" min={0} value={settings.char_limit ?? ""}
                    placeholder={t("components.sharing.shareModal.unlimited")}
                    onChange={(e) => setSettings((s) => ({ ...s, char_limit: numOrNull(e.target.value) }))} />
                </div>
                <label className="flex items-center gap-2 pt-1 text-sm sm:col-span-2">
                  <Switch checked={settings.use_brief ?? false}
                    onCheckedChange={(v) => setSettings((s) => ({ ...s, use_brief: v }))} />
                  {t("components.sharing.shareModal.useBrief")}
                </label>
              </div>
            )}

            {generating && (
              <div className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
                {genMsg || t("components.sharing.shareModal.generating")} — {t("components.sharing.shareModal.generatingHint")}
              </div>
            )}

            {!hasAlts && !generating && (
              <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
                {t("components.sharing.shareModal.emptyHintBefore")} <strong>✨ {t("components.sharing.shareModal.generate")}</strong>{t("components.sharing.shareModal.emptyHintAfter")}
              </p>
            )}

            {/* 3 generated alternatives — each with Select + Copy */}
            {hasAlts && (
              <div className="space-y-2">
                {captionAlts.map((alt, i) => {
                  const selected = content.startsWith(alt)
                  return (
                    <div key={i} className={cn(
                      "space-y-1.5 rounded-lg border p-2.5",
                      selected && "border-primary bg-primary/5 ring-1 ring-primary/30",
                    )}>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-muted-foreground">#{i + 1}</span>
                        <Button type="button" size="sm" variant={selected ? "default" : "outline"}
                          className="h-7" onClick={() => setContent(combineSet(alt, sugTags))}>
                          {selected
                            ? <><Check className="mr-1 h-3.5 w-3.5" />{t("components.sharing.shareModal.selectedBadge")}</>
                            : t("components.sharing.shareModal.select")}
                        </Button>
                        <Button type="button" size="sm" variant="ghost" className="ml-auto h-7"
                          title={t("components.sharing.shareModal.copyCaptionHashtag")}
                          onClick={() => onCopy(sugTags ? `${alt}\n\n${sugTags}` : alt)}>
                          <Copy className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      <p className="whitespace-pre-wrap text-sm">{alt}</p>
                    </div>
                  )
                })}
                {sugTags && (
                  <p className="text-xs text-muted-foreground">
                    <span className="font-medium">{t("components.sharing.shareModal.sharedHashtags")}</span> {sugTags}
                  </p>
                )}
              </div>
            )}

            {/* Regenerate with feedback (legacy flow) */}
            {hasAlts && (
              <div className="space-y-1.5 rounded-lg border border-dashed p-2.5">
                <p className="text-xs font-medium">{t("components.sharing.shareModal.feedbackPrompt")}</p>
                <div className="flex flex-wrap gap-1.5">
                  {[
                    t("components.sharing.shareModal.chipWarmer"),
                    t("components.sharing.shareModal.chipShorter"),
                    t("components.sharing.shareModal.chipFormal"),
                    t("components.sharing.shareModal.chipDifferentAngle"),
                  ].map((chip) => (
                    <button key={chip} type="button"
                      onClick={() => setFeedback((f) => (f ? `${f}, ${chip}` : chip))}
                      className="rounded-full border px-2 py-0.5 text-[11px] transition-colors hover:bg-muted">
                      {chip}
                    </button>
                  ))}
                </div>
                <div className="flex gap-1.5">
                  <Input value={feedback} onChange={(e) => setFeedback(e.target.value)}
                    placeholder={t("components.sharing.shareModal.feedbackPlaceholder")}
                    onKeyDown={(e) => { if (e.key === "Enter") onRegenerate() }} />
                  <Button type="button" size="sm" variant="outline" onClick={onRegenerate}
                    disabled={generating || !feedback.trim()}>
                    <RotateCw className="mr-1 h-3.5 w-3.5" /> {t("components.sharing.shareModal.regenerate")}
                  </Button>
                </div>
              </div>
            )}

            {/* Selected / editable full text (this is what gets saved) */}
            <div className="space-y-1.5">
              <Label htmlFor="content" className="text-xs text-muted-foreground">
                {t("components.sharing.shareModal.editableText")}
              </Label>
              <Textarea id="content" rows={hasAlts ? 8 : 12} value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={t("components.sharing.shareModal.contentPlaceholder")} />
            </div>
          </div>
        </div>

        <DialogFooter className="flex-wrap">
          {editing && (
            <Button variant="outline" className="mr-auto text-destructive"
              onClick={onDelete} disabled={busy}>{t("components.sharing.shareModal.delete")}</Button>
          )}
          {editing && (
            <Button variant="outline" className="text-red-600" onClick={onRequestRevision}
              disabled={busy || revision.isPending}>🔁 {t("components.sharing.shareModal.requestRevision")}</Button>
          )}
          {editing && share?.status === "published" ? (
            <Button variant="outline" onClick={onUnpublish} disabled={busy}>{t("components.sharing.shareModal.backToDraft")}</Button>
          ) : null}
          {!editing && (
            <Button variant="secondary" onClick={onSaveAndNew} disabled={busy || generating}
              title={t("components.sharing.shareModal.saveAndNewTitle")}>
              {t("components.sharing.shareModal.saveAndNew")}
            </Button>
          )}
          <Button variant="outline" onClick={onSaveDraft} disabled={busy}>{t("components.sharing.shareModal.saveDraft")}</Button>
          <Button onClick={onPublish} disabled={busy}>{t("components.sharing.shareModal.publish")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
