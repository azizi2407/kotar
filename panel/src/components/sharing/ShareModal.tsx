// Paylaşım oluştur/düzenle modalı. Tek bir paylaşımı (share) yönetir.
// Dosya, upload havuzundan seçilir (Drive thumbnail'leri Faz 2b). AI caption
// üretimi aktif (ai_worker job kuyruğu üzerinden); "Üretim ayarları" bloğu
// müşteri varsayılanıyla ön-dolu, bu üretime özel override edilebilir (Faz 1b).
import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Check, Copy, Download, Image as ImageIcon, RotateCw, Settings2, Sparkles, Upload, X } from "lucide-react"
import { toast } from "sonner"

import { ApiError, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "@/lib/api"
import { useAuth } from "@/lib/auth"
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

// Caption ayar UI'ı: model seçenekleri (ai_claude.py DEFAULT_MODEL + bilinen alternatif).
const MODEL_DEFAULT = "__varsayilan__"
const MODEL_OPTIONS = [
  { value: MODEL_DEFAULT, label: "Varsayılan (Sonnet)" },
  { value: "claude-sonnet-5", label: "Sonnet" },
  { value: "claude-opus-4-8", label: "Opus" },
]

function numOrNull(v: string): number | null {
  if (v.trim() === "") return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// Caption + hashtag'i tek metinde birleştir / geri böl (veri modeli ayrı tutar).
// Düzen: <caption bloğu>  ---  <hashtag seti>. Ortadaki '---' yalnız görünüm;
// kaydederken tüketilir, açarken yeniden eklenir (round-trip bozulmaz).
function combineSet(caption: string, tags: string): string {
  return caption + (tags ? (caption ? "\n\n---\n\n" : "") + tags : "")
}
function splitSet(text: string): { caption: string; hashtag: string } {
  const t = text.replace(/\s+$/, "")
  // Sondaki (opsiyonel '---' ayırıcı +) ardışık #etiket bloğunu hashtag olarak
  // ayır; geri kalanı caption. Caption içindeki '---' (TR/EN ayırıcısı) korunur.
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
  share: Share | null // null => yeni
}

export function ShareModal({ open, onOpenChange, clientId, clientName, weekIso, share }: Props) {
  const editing = share != null
  const [kind, setKind] = useState<ShareKind>("post")
  const [fileId, setFileId] = useState<string | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  // Caption + hashtag TEK kutuda düzenlenir; kaydederken caption_text/hashtag_text'e bölünür.
  const [content, setContent] = useState("")
  const [sugTags, setSugTags] = useState("")  // AI'nın ürettiği paylaşımlı hashtag seti
  const [note, setNote] = useState("")
  const [plannedDate, setPlannedDate] = useState("")
  const [plannedTime, setPlannedTime] = useState("")

  const { data: uploads } = useUploads(open ? clientId : null, weekIso)
  const { canImpersonate: isSuperadmin } = useAuth()
  const deleteUpload = useDeleteUpload(clientId, weekIso)
  const create = useCreateShare()
  const updateById = useUpdateShareById()
  const action = useShareAction()
  // İlk save() (ör. "Üret" caption için id gerektirir) bir taslak oluşturur;
  // id'yi burada tutup sonraki save'ler UPDATE etsin — yoksa "Paylaş" ikinci bir
  // share yaratıp çift kayıt oluyordu.
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
  const [useTranscript, setUseTranscript] = useState(false)  // video: transkript opsiyonel, varsayılan kapalı
  const fileInput = useRef<HTMLInputElement>(null)
  const busy = create.isPending || updateById.isPending || action.isPending || del.isPending

  // Caption üretim ayarları (Faz 1b): müşteri varsayılanıyla ön-dolu, bu üretime
  // özel değiştirilebilir (kalıcı yazım yalnız müşteri ayar sayfasından).
  const [showSettings, setShowSettings] = useState(false)
  const [settings, setSettings] = useState<CaptionSettings>({})
  const defaultsAppliedRef = useRef(false)
  const { data: clientDefaults } = useCaptionSettings(open ? clientId : null)

  // Caption üretimi (ilk üretim ve feedback'li yeniden üret ortak yol).
  async function runCaption(opts?: { feedback?: string; previousCaption?: string; skipMedia?: boolean }) {
    const s = await save()  // önce kaydet (id gerekli)
    if (!s) return
    setGenerating(true)
    try {
      // Dosya varsa önce media: kareler (görsel bağlam) + video ise OPSİYONEL transkript.
      // Yeniden üret'te (skipMedia) kareler zaten var → medya tekrar işlenmez (hız + hata yüzeyi yok).
      if (!opts?.skipMedia && s.file_id) {
        setGenMsg(s.kind === "video" ? "Video işleniyor…" : "Görsel hazırlanıyor…")
        const mjob = await procMedia.mutateAsync({ shareId: s.id, useTranscript: s.kind === "video" && useTranscript })
        await pollJob(mjob.id, { timeout: 300000 })
      }
      setGenMsg(opts?.feedback ? "Yeniden üretiliyor…" : "Caption üretiliyor…")
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
        // Board cache'ini tazele: modal kapanıp açılınca öneriler yenilemesiz görünsün.
        qc.invalidateQueries({ queryKey: ["board"] })
        qc.invalidateQueries({ queryKey: ["designer-board"] })
        toast.success(`${clientName} için caption oluşturuldu`)
      } else {
        toast.error(done.result?.error || "Üretilemedi")
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Üretim başarısız")
    } finally {
      setGenerating(false)
      setGenMsg("")
    }
  }

  function onGenerate() { return runCaption() }

  function onRegenerate() {
    const fb = feedback.trim()
    if (!fb) return
    // Beğenilmeyen önceki caption = şu an seçili/gösterilen (content'ten caption kısmı).
    const prev = splitSet(content).caption || captionAlts[0] || ""
    // Kareler ilk üretimde çıkarıldı → medyayı tekrar işleme (skipMedia).
    return runCaption({ feedback: fb, previousCaption: prev, skipMedia: true })
  }

  async function onCopy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      toast.success("Kopyalandı")
    } catch {
      toast.error("Kopyalanamadı")
    }
  }

  async function onRequestRevision() {
    if (!share) return
    const note = window.prompt("Revize notu (ne değişmeli?)")?.trim()
    if (!note) return
    try {
      await revision.mutateAsync({
        client_id: clientId, week_iso: weekIso,
        kind: share.kind === "video" ? "video" : "design",
        share_id: share.id, note,
      })
      toast.success("Revize istendi")
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Revize isteği başarısız")
    }
  }

  const category = kind === "linkedin" ? "linkedin" : kind === "story" ? "story"
    : kind === "video" ? "video" : "post"

  async function onPickUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(`Dosya ${MAX_UPLOAD_MB} MB sınırını aşıyor`)
      return
    }
    setUploadPct(0)
    try {
      const up = await upload.mutateAsync({ file, category })
      setFileId(up.file_id)
      setFileName(up.file_name)
      toast.success("Yüklendi")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Yükleme başarısız")
    }
  }

  useEffect(() => {
    if (!open) return
    setSavedId(share?.id ?? null)  // yeni modal → temiz; düzenleme → mevcut id
    setKind(share?.kind ?? "post")
    setFileId(share?.file_id ?? null)
    setFileName(share?.file_name ?? null)
    setContent(combineSet(share?.caption_text ?? "", share?.hashtag_text ?? ""))
    setNote(share?.note ?? "")
    setPlannedDate(share?.planned_date ?? "")
    setPlannedTime(share?.planned_time ?? "")
    // Son üretilen caption önerileri + hashtag seti (kalıcı) — modal kapanıp açılsa bile görünür
    setCaptionAlts(share?.caption_suggestions?.captions ?? [])
    setSugTags(share?.caption_suggestions?.hashtags ?? "")
    setFeedback("")
    setUseTranscript(false)  // video transkripti: her modal açılışında varsayılan kapalı
    // Üretim ayarları temiz başlar; müşteri varsayılanı yüklenince aşağıdaki
    // effect bir kez uygular (kullanıcı bu üretime özel değiştirirse üstüne yazmaz).
    setSettings({})
    defaultsAppliedRef.current = false
  }, [open, share])

  // Müşteri varsayılanı (caption_settings) yüklenince ayar bloğunu bir kez ön-doldur.
  useEffect(() => {
    if (!open || defaultsAppliedRef.current || !clientDefaults) return
    setSettings(clientDefaults)
    defaultsAppliedRef.current = true
  }, [open, clientDefaults])

  // Seçili kind'a uyan yüklemeler (kategori eşleşmesi gevşek).
  const relevantUploads = useMemo(() => {
    const all = uploads ?? []
    const cat = kind === "linkedin" ? "linkedin" : kind === "story" ? "story" : kind === "video" ? "video" : "post"
    const matched = all.filter((u) => (u.category || "post") === cat)
    const pool = matched.length ? matched : all
    // Kartı olan dosya seçicide GÖRÜNMEZ (2026-07-29) — aynı dosyaya ikinci kart
    // açılıp çift içerik üretilmesin. TEK istisna şu an seçili dosya: düzenlemede
    // o da `used` olduğu için süzülür ve kullanıcı ne seçtiğini göremezdi.
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
      // Var olan share'i (editing) ya da bu modalda az önce oluşturulan taslağı
      // (savedId) güncelle; ikisi de yoksa yeni oluştur ve id'sini benimse.
      const targetId = editing ? share!.id : savedId
      if (targetId != null) return await updateById.mutateAsync({ id: targetId, body: body() })
      const s = await create.mutateAsync(body())
      setSavedId(s.id)
      return s
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Kaydedilemedi")
      return null
    }
  }

  async function onSaveDraft() {
    const s = await save()
    if (s) {
      toast.success("Taslak kaydedildi")
      onOpenChange(false)
    }
  }

  // Mevcut paylaşımı kaydeder ve modalı KAPATMADAN yeni bir paylaşım için temizler
  // (her görsel ayrı bir karttır; önceki board'da durur). Yalnız yeni-paylaşım akışında.
  async function onSaveAndNew() {
    const s = await save()
    if (!s) return
    toast.success("Kaydedildi — yeni paylaşıma geçebilirsin")
    setSavedId(null)  // sonraki save() yeni kart oluştursun
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
      toast.success("Paylaşıldı")
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Paylaşılamadı")
    }
  }

  async function onUnpublish() {
    if (!share) return
    await action.mutateAsync({ id: share.id, action: "unpublish" })
    toast.success("Taslağa alındı")
    onOpenChange(false)
  }

  async function onDelete() {
    if (!share) return
    await del.mutateAsync(share.id)
    toast.success("Silindi")
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
            {editing ? "Paylaşımı Düzenle" : "Yeni Paylaşım"} · {clientName}
          </DialogTitle>
          <DialogDescription>{weekIso} haftası.</DialogDescription>
        </DialogHeader>

        <div className={cn("grid gap-5", hasAlts && "lg:grid-cols-2")}>
          {/* SOL PANEL — girdi: tür, dosya, not, plan */}
          <div className="space-y-4">
          {/* Tür */}
          <div className="space-y-1.5">
            <Label>Tür</Label>
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

          {/* Dosya seçimi (upload havuzu) + yükleme */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Dosya</Label>
              <input ref={fileInput} type="file" className="hidden"
                accept="image/*,video/*" onChange={onPickUpload} />
              <Button type="button" variant="outline" size="sm"
                onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
                <Upload className="mr-1 h-3.5 w-3.5" />
                {upload.isPending ? "Yükleniyor…" : "Yükle"}
              </Button>
            </div>
            {upload.isPending && (
              <div className="space-y-1">
                <Progress value={uploadPct} />
                <p className="text-[11px] text-muted-foreground">Yükleniyor… %{uploadPct}</p>
              </div>
            )}
            {relevantUploads.length === 0 ? (
              <p className="text-sm text-muted-foreground">Bu hafta için yükleme yok — "Yükle" ile ekle.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {relevantUploads.map((u) => (
                  <div key={u.id} className="group relative">
                    <button
                      type="button"
                      onClick={() => {
                        // Farklı dosyaya geçince önceki caption'lar geçersiz — temizle.
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
                      {/* Drive thumbnail; yüklenmezse arkadaki ikon görünür */}
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
                        title="Yüklemeyi sil (yalnız superadmin)"
                        disabled={deleteUpload.isPending}
                        onClick={() => {
                          if (!window.confirm(`"${u.file_name || u.file_id}" yüklemesi silinsin mi? (geri alınabilir)`)) return
                          deleteUpload.mutate(u.id, {
                            onSuccess: () => {
                              if (fileId === u.file_id) { setFileId(null); setFileName(null) }
                              toast.success("Yükleme silindi")
                            },
                            onError: (e) => toast.error(e instanceof Error ? e.message : "Silinemedi"),
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
                <span className="truncate">Seçili: {fileName}</span>
                {fileId && (
                  <a href={downloadMediaUrl(fileId, fileName ?? undefined)}
                    download={fileName ?? ""}
                    className="ml-auto inline-flex shrink-0 items-center gap-1 font-medium text-primary hover:underline">
                    <Download className="h-3.5 w-3.5" /> Tam boyutu indir
                  </a>
                )}
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="note">Not</Label>
            <Input id="note" value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Dosyasız paylaşımda not zorunlu" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="pd">Planlanan tarih</Label>
              <Input id="pd" type="date" value={plannedDate}
                onChange={(e) => setPlannedDate(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pt">Saat</Label>
              <Input id="pt" type="time" value={plannedTime}
                onChange={(e) => setPlannedTime(e.target.value)} />
            </div>
          </div>
          </div>

          {/* SAĞ PANEL — Caption & Hashtag: üret, 3 alternatif kartı, geri bildirim, düzenlenebilir metin */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="content">Caption &amp; Hashtag</Label>
              <div className="flex items-center gap-1.5">
                <Button type="button" variant="ghost" size="sm"
                  onClick={() => setShowSettings((v) => !v)} title="Üretim ayarları">
                  <Settings2 className="mr-1 h-3.5 w-3.5" />
                  Ayarlar
                </Button>
                <Button type="button" variant="outline" size="sm" onClick={onGenerate}
                  disabled={busy || generating} title="AI ile caption üret (claude)">
                  <Sparkles className="mr-1 h-3.5 w-3.5" />
                  {generating ? (genMsg || "Üretiliyor…") : hasAlts ? "Yeniden ✨ Üret" : "✨ Üret"}
                </Button>
              </div>
            </div>
            {kind === "video" && (
              <label className="flex items-center gap-2 text-sm"
                title="Videonun sesini whisper ile çözüp transkripti caption bağlamına ekler — biraz daha uzun sürer">
                <Switch checked={useTranscript} onCheckedChange={setUseTranscript} />
                🎤 Videonun sesini de kullan (transkript) — varsayılan kapalı
              </label>
            )}
            {showSettings && (
              <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Model</Label>
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
                  <Label htmlFor="cs-tone">Ton</Label>
                  <Input id="cs-tone" value={settings.tone ?? ""}
                    placeholder="Boşsa marka sesi (brand_voice) kullanılır"
                    onChange={(e) => setSettings((s) => ({ ...s, tone: e.target.value || null }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-emoji">Emoji limiti</Label>
                  <Input id="cs-emoji" type="number" min={0} value={settings.emoji_limit ?? ""}
                    onChange={(e) => setSettings((s) => ({ ...s, emoji_limit: numOrNull(e.target.value) }))} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-hashtag">Hashtag sayısı</Label>
                  <Input id="cs-hashtag" type="number" min={0} value={settings.hashtag_count ?? ""}
                    onChange={(e) => setSettings((s) => ({ ...s, hashtag_count: numOrNull(e.target.value) }))} />
                </div>
                <div className="space-y-1.5">
                  <Label>Dil</Label>
                  <Select value={settings.lang || "TR"}
                    onValueChange={(v) => v && setSettings((s) => ({ ...s, lang: v as CaptionSettings["lang"] }))}>
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="TR">Türkçe</SelectItem>
                      <SelectItem value="EN">İngilizce</SelectItem>
                      <SelectItem value="TR+EN">İkisi</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cs-charlimit">Karakter limiti</Label>
                  <Input id="cs-charlimit" type="number" min={0} value={settings.char_limit ?? ""}
                    placeholder="Sınırsız"
                    onChange={(e) => setSettings((s) => ({ ...s, char_limit: numOrNull(e.target.value) }))} />
                </div>
                <label className="flex items-center gap-2 pt-1 text-sm sm:col-span-2">
                  <Switch checked={settings.use_brief ?? false}
                    onCheckedChange={(v) => setSettings((s) => ({ ...s, use_brief: v }))} />
                  Brief'i bağlam olarak kullan
                </label>
              </div>
            )}

            {generating && (
              <div className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
                {genMsg || "Üretiliyor…"} — modalı kapatıp geri dönebilirsin, kaybolmaz.
              </div>
            )}

            {!hasAlts && !generating && (
              <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">
                Bir görsel seç ve <strong>✨ Üret</strong>'e bas — 3 caption alternatifi + ortak hashtag seti gelir.
              </p>
            )}

            {/* Üretilen 3 alternatif — her biri Seç + Kopyala ile */}
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
                          {selected ? <><Check className="mr-1 h-3.5 w-3.5" />Seçili</> : "Seç"}
                        </Button>
                        <Button type="button" size="sm" variant="ghost" className="ml-auto h-7"
                          title="Caption + hashtag'i kopyala"
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
                    <span className="font-medium">Ortak hashtag:</span> {sugTags}
                  </p>
                )}
              </div>
            )}

            {/* Geri bildirimle yeniden üret (eski akış) */}
            {hasAlts && (
              <div className="space-y-1.5 rounded-lg border border-dashed p-2.5">
                <p className="text-xs font-medium">Beğenmedin mi? Geri bildirimle yeniden üret</p>
                <div className="flex flex-wrap gap-1.5">
                  {["daha samimi", "daha kısa", "daha kurumsal", "farklı bir açı"].map((chip) => (
                    <button key={chip} type="button"
                      onClick={() => setFeedback((f) => (f ? `${f}, ${chip}` : chip))}
                      className="rounded-full border px-2 py-0.5 text-[11px] transition-colors hover:bg-muted">
                      {chip}
                    </button>
                  ))}
                </div>
                <div className="flex gap-1.5">
                  <Input value={feedback} onChange={(e) => setFeedback(e.target.value)}
                    placeholder="örn. fiyattan bahsetme, soru ile aç…"
                    onKeyDown={(e) => { if (e.key === "Enter") onRegenerate() }} />
                  <Button type="button" size="sm" variant="outline" onClick={onRegenerate}
                    disabled={generating || !feedback.trim()}>
                    <RotateCw className="mr-1 h-3.5 w-3.5" /> Yeniden üret
                  </Button>
                </div>
              </div>
            )}

            {/* Seçili / düzenlenebilir tam metin (kaydedilen budur) */}
            <div className="space-y-1.5">
              <Label htmlFor="content" className="text-xs text-muted-foreground">
                Seçili metin (düzenlenebilir — kaydedilen budur)
              </Label>
              <Textarea id="content" rows={hasAlts ? 8 : 12} value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={"Açılış cümlesi\n\nGövde (2-3 cümle)\n\nKapanış · CTA\n\n#hashtag'ler"} />
            </div>
          </div>
        </div>

        <DialogFooter className="flex-wrap">
          {editing && (
            <Button variant="outline" className="mr-auto text-destructive"
              onClick={onDelete} disabled={busy}>Sil</Button>
          )}
          {editing && (
            <Button variant="outline" className="text-red-600" onClick={onRequestRevision}
              disabled={busy || revision.isPending}>🔁 Revize İste</Button>
          )}
          {editing && share?.status === "published" ? (
            <Button variant="outline" onClick={onUnpublish} disabled={busy}>Taslağa Geri Al</Button>
          ) : null}
          {!editing && (
            <Button variant="secondary" onClick={onSaveAndNew} disabled={busy || generating}
              title="Bu paylaşımı kaydet, aynı hafta için yeni bir paylaşıma geç">
              Kaydet ve Yeni
            </Button>
          )}
          <Button variant="outline" onClick={onSaveDraft} disabled={busy}>Taslak Kaydet</Button>
          <Button onClick={onPublish} disabled={busy}>Paylaş</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
