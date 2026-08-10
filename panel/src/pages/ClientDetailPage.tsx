// Müşteri detayı: sekmeli görünüm (Genel · Anlaşma · Ekip · Kişiler/Lokasyon · Drive).
// Düzenle/Sil/Geri Al yalnız management. Silme reason'ı istenir.
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

// Caption ayar UI'ı: model seçenekleri ShareModal ile aynı (ai_claude.py DEFAULT_MODEL
// + bilinen alternatif). Bu sayfadaki kayıt kalıcı müşteri-varsayılanı olur (Faz 1b).
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

// Müşterinin caption üretim varsayılanları: görüntüleme (herkes) + düzenleme (management).
function CaptionSettingsSection({ clientId, canEdit }: { clientId: number; canEdit: boolean }) {
  const { data, isLoading } = useCaptionSettings(clientId)
  const save = useSaveCaptionSettings(clientId)
  const [form, setForm] = useState<CaptionSettings>({})

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  async function onSave() {
    try {
      await save.mutateAsync(form)
      toast.success("Caption varsayılanları kaydedildi")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Kaydedilemedi")
    }
  }

  if (isLoading) return <Skeleton className="h-40 w-full" />

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Caption Varsayılanları</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Bu ayarlar "✨ Üret" sırasında müşteri varsayılanı olarak ön-dolar; her üretimde
          o iş için ayrıca değiştirilebilir (kalıcı değişiklik yalnız burada kaydedilir).
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Model</Label>
            <Select value={form.model || MODEL_DEFAULT}
              disabled={!canEdit}
              onValueChange={(v) => v && setForm((f) => ({ ...f, model: v === MODEL_DEFAULT ? null : v }))}>
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
            <Input id="cs-tone" value={form.tone ?? ""} disabled={!canEdit}
              placeholder="Boşsa marka sesi (brand_voice) kullanılır"
              onChange={(e) => setForm((f) => ({ ...f, tone: e.target.value || null }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cs-emoji">Emoji limiti</Label>
            <Input id="cs-emoji" type="number" min={0} value={form.emoji_limit ?? ""} disabled={!canEdit}
              onChange={(e) => setForm((f) => ({ ...f, emoji_limit: numOrNull(e.target.value) }))} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cs-hashtag">Hashtag sayısı</Label>
            <Input id="cs-hashtag" type="number" min={0} value={form.hashtag_count ?? ""} disabled={!canEdit}
              onChange={(e) => setForm((f) => ({ ...f, hashtag_count: numOrNull(e.target.value) }))} />
          </div>
          <div className="space-y-1.5">
            <Label>Dil</Label>
            <Select value={form.lang || "TR"} disabled={!canEdit}
              onValueChange={(v) => v && setForm((f) => ({ ...f, lang: v as CaptionSettings["lang"] }))}>
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
            <Input id="cs-charlimit" type="number" min={0} value={form.char_limit ?? ""} disabled={!canEdit}
              placeholder="Sınırsız"
              onChange={(e) => setForm((f) => ({ ...f, char_limit: numOrNull(e.target.value) }))} />
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={form.use_brief ?? false} disabled={!canEdit}
            onCheckedChange={(v) => setForm((f) => ({ ...f, use_brief: v }))} />
          Brief'i bağlam olarak kullan
        </label>
        {canEdit && (
          <Button onClick={onSave} disabled={save.isPending}>
            {save.isPending ? "Kaydediliyor…" : "Kaydet"}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

// Ayar sabitleri — paylaşım günleri (kanonik sıra) + içerik dağılımı format anahtarları.
const GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"] as const
const MIX_KEYS = [
  { key: "carousel", label: "Carousel" },
  { key: "editorial_single", label: "Editorial (tek)" },
  { key: "reel", label: "Reel" },
] as const
const HASHTAG_GROUPS = [
  { key: "konu", label: "Konu" },
  { key: "marka", label: "Marka" },
  { key: "sektor", label: "Sektör" },
] as const

// Satır/virgül-ayrık metni temiz string listesine çevirir (boşlar düşer).
function toList(s: string): string[] {
  return s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean)
}

// Ayar düzenleme formu — brand_profile alanları (brief üretimini yönlendirir).
// caption_settings AYRI "Caption Ayarları" sekmesinde düzenlenir; burada dokunulmaz.
function AyarForm({ clientId, ayar, onDone }: { clientId: number; ayar: VaultAyar; onDone: () => void }) {
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
      toast.error("Haftalık fikir sayısı geçerli bir tam sayı olmalı")
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
      toast.success("Ayar kaydedildi")
      onDone()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Kaydedilemedi")
    }
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="ay-voice">Marka Sesi</Label>
          <Textarea id="ay-voice" value={brandVoice} onChange={(e) => setBrandVoice(e.target.value)}
            placeholder="Ör. Resmi, sıcak, güven veren" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ay-audience">Hedef Kitle</Label>
          <Textarea id="ay-audience" value={audience} onChange={(e) => setAudience(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ay-cta">Birincil CTA</Label>
          <Input id="ay-cta" value={cta} onChange={(e) => setCta(e.target.value)}
            placeholder="Ör. danışın, rezervasyon yapın" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ay-ideas">Haftalık Fikir Sayısı</Label>
          <Input id="ay-ideas" type="number" min={0} value={ideas}
            onChange={(e) => setIdeas(e.target.value)} />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>Paylaşım Günleri</Label>
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
        <Label htmlFor="ay-forbidden">Yasaklar</Label>
        <Textarea id="ay-forbidden" value={forbiddenText} onChange={(e) => setForbiddenText(e.target.value)}
          className="min-h-24" placeholder="Her satıra bir madde" />
        <p className="text-xs text-muted-foreground">Her satır ayrı bir madde olarak kaydedilir.</p>
      </div>

      <div className="space-y-1.5">
        <Label>Renk Paleti</Label>
        <div className="space-y-2">
          {palette.map((hex, i) => (
            <div key={i} className="flex items-center gap-2">
              <input type="color" value={/^#[0-9a-fA-F]{6}$/.test(hex) ? hex : "#000000"}
                onChange={(e) => setPalette((p) => p.map((h, j) => (j === i ? e.target.value : h)))}
                className="h-9 w-10 cursor-pointer rounded border bg-transparent p-0.5" />
              <Input value={hex} placeholder="#RRGGBB"
                onChange={(e) => setPalette((p) => p.map((h, j) => (j === i ? e.target.value : h)))}
                className="max-w-40" />
              <Button type="button" variant="ghost" size="icon"
                onClick={() => setPalette((p) => p.filter((_, j) => j !== i))}>
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={() => setPalette((p) => [...p, "#000000"])}>
            <Plus className="mr-1 h-3.5 w-3.5" /> Renk ekle
          </Button>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>İçerik Dağılımı</Label>
        <div className="grid gap-3 sm:grid-cols-3">
          {MIX_KEYS.map((m) => (
            <div key={m.key} className="space-y-1">
              <Label htmlFor={`ay-mix-${m.key}`} className="text-xs text-muted-foreground">{m.label}</Label>
              <Input id={`ay-mix-${m.key}`} type="number" min={0} value={mix[m.key] ?? ""}
                onChange={(e) => setMix((cur) => ({ ...cur, [m.key]: Number(e.target.value) || 0 }))} />
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <Label>Hashtag Setleri</Label>
        <div className="grid gap-3 sm:grid-cols-3">
          {HASHTAG_GROUPS.map((g) => (
            <div key={g.key} className="space-y-1">
              <Label htmlFor={`ay-hash-${g.key}`} className="text-xs text-muted-foreground">{g.label}</Label>
              <Textarea id={`ay-hash-${g.key}`} value={hashText[g.key]}
                onChange={(e) => hashSetters[g.key](e.target.value)}
                className="min-h-24" placeholder="Satır ya da virgülle ayır" />
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ay-guide">Marka Rehberi (guide_md)</Label>
        <Textarea id="ay-guide" value={guide} onChange={(e) => setGuide(e.target.value)}
          className="min-h-32" />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="ay-pillars">İçerik Sütunları (content_pillars)</Label>
        <Textarea id="ay-pillars" value={pillars} onChange={(e) => setPillars(e.target.value)}
          className="min-h-24" />
      </div>

      <div className="flex gap-2">
        <Button onClick={onSave} disabled={put.isPending}>
          {put.isPending ? "Kaydediliyor…" : "Kaydet"}
        </Button>
        <Button variant="outline" onClick={onDone} disabled={put.isPending}>Vazgeç</Button>
      </div>
    </div>
  )
}

// Ayar sekmesi gövdesi: görüntüle ↔ düzenle geçişi (management-only).
function AyarPanel({ clientId, ayar }: { clientId: number; ayar: VaultAyar }) {
  const [editing, setEditing] = useState(false)
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">Ayar</CardTitle>
        {!editing && (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            <Pencil className="mr-1.5 h-3.5 w-3.5" /> Düzenle
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

// Müşterinin vault Ayar dosyası: salt-görüntüleme + brief aç/kapa + catch-up + onboarding.
// Uçların tümü management-only (write=True) → sekme yalnız management'a gösterilir.
function VaultAyarSection({ client }: { client: ClientDetail }) {
  const clientId = client.id
  const { data, isLoading, isError, error } = useVaultAyar(clientId)
  const onboarding = !!data?.onboarding
  const promptQ = useOnboardingPrompt(clientId, onboarding)
  const setBriefEnabled = useSetBriefEnabled(clientId)
  const catchUp = useCatchUp(clientId)

  async function onToggle(v: boolean) {
    try {
      await setBriefEnabled.mutateAsync(v)
      toast.success(v ? "Brief üretimi açıldı" : "Brief üretimi kapatıldı")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Değiştirilemedi")
    }
  }

  async function onCatchUp() {
    try {
      const r = await catchUp.mutateAsync()
      if (r.enqueued === 0) toast.info("Eksik hafta yok — tümü güncel")
      else toast.success(`${r.enqueued} hafta kuyruklandı: ${r.weeks.join(", ")}`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Kuyruğa alınamadı")
    }
  }

  async function copyPrompt() {
    if (!promptQ.data) return
    try {
      await navigator.clipboard.writeText(promptQ.data)
      toast.success("Prompt kopyalandı")
    } catch {
      toast.error("Kopyalanamadı")
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
              <span className="font-medium">Brief üretimi</span>
              <span className="block text-xs text-muted-foreground">
                Kapalıyken rutin ve catch-up bu müşteriye otomatik brief üretmez.
              </span>
            </span>
          </label>
          {client.brief_enabled && !onboarding && (
            <Button variant="outline" size="sm" onClick={onCatchUp} disabled={catchUp.isPending}>
              {catchUp.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              Eksik haftaları kuyrukla
            </Button>
          )}
        </CardContent>
      </Card>

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">
            Ayar okunamadı: {error instanceof ApiError ? error.message : "bilinmeyen hata"}
          </CardContent>
        </Card>
      ) : data ? (
        <>
          {onboarding && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Onboarding bekliyor</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Bu müşterinin Ayar'ı henüz boş. Aşağıdaki alanları elle doldurabilir ya da başlangıç
                  prompt'unu taze bir Claude oturumuna yapıştırıp Ayar'ı ürettirip buraya işleyebilirsin.
                </p>
                {promptQ.isLoading ? (
                  <Skeleton className="h-40 w-full" />
                ) : promptQ.data ? (
                  <div className="space-y-2">
                    <div className="flex justify-end">
                      <Button variant="outline" size="sm" onClick={copyPrompt}>
                        <Copy className="mr-1.5 h-3.5 w-3.5" /> Kopyala
                      </Button>
                    </div>
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-xs">
                      {promptQ.data}
                    </pre>
                  </div>
                ) : (
                  <p className="text-sm text-destructive">Prompt getirilemedi.</p>
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

function yesno(v: boolean | null | undefined) {
  return v == null ? "—" : v ? "Evet" : "Hayır"
}

export function ClientDetailPage() {
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
        <p className="text-destructive">Müşteri bulunamadı.</p>
        <Button variant="outline" render={<Link to="/clients" />}>
          <ArrowLeft className="mr-1 h-4 w-4" /> Listeye dön
        </Button>
      </div>
    )
  }

  async function doDelete() {
    try {
      await del.mutateAsync({ id: clientId, reason: reason.trim() || undefined })
      toast.success("Müşteri arşivlendi")
      setConfirmDel(false)
      navigate("/clients")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Silme başarısız")
    }
  }
  async function doRestore() {
    try {
      await restore.mutateAsync(clientId)
      toast.success("Müşteri geri alındı")
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Geri alma başarısız")
    }
  }

  const ct = c.contract

  return (
    <div className="space-y-6">
      <div>
        <Link to="/clients" className="mb-2 inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="mr-1 h-4 w-4" /> Müşteriler
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{c.name}</h1>
            {c.status === "deleted"
              ? <Badge variant="outline" className="text-muted-foreground">Silinmiş</Badge>
              : <Badge variant="secondary">Aktif</Badge>}
          </div>
          {isManagement && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setEditing(true)}>
                <Pencil className="mr-1 h-4 w-4" /> Düzenle
              </Button>
              {c.status === "active" ? (
                <Button variant="outline" className="text-destructive"
                  onClick={() => { setReason(""); setConfirmDel(true) }}>
                  <Trash2 className="mr-1 h-4 w-4" /> Sil
                </Button>
              ) : (
                <Button variant="outline" onClick={doRestore} disabled={restore.isPending}>
                  <RotateCcw className="mr-1 h-4 w-4" /> Geri Al
                </Button>
              )}
            </div>
          )}
        </div>
        {c.status === "deleted" && c.deleted_reason && (
          <p className="mt-1 text-sm text-muted-foreground">Silme nedeni: {c.deleted_reason}</p>
        )}
      </div>

      <Tabs defaultValue="genel">
        <TabsList>
          <TabsTrigger value="genel">Genel</TabsTrigger>
          <TabsTrigger value="anlasma">Anlaşma & Çekim</TabsTrigger>
          <TabsTrigger value="ekip">Ekip</TabsTrigger>
          <TabsTrigger value="kisiler">Kişiler & Lokasyon</TabsTrigger>
          <TabsTrigger value="drive">Drive</TabsTrigger>
          <TabsTrigger value="caption">Caption Ayarları</TabsTrigger>
          {isManagement && <TabsTrigger value="marka">Marka Görselleri</TabsTrigger>}
          {isManagement && <TabsTrigger value="vault">Vault Ayar</TabsTrigger>}
        </TabsList>

        <TabsContent value="genel">
          <Card>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
              <Field label="Sektör" value={c.sector} />
              <Field label="E-posta" value={c.client_email} />
              <Field label="Instagram" value={
                c.instagram_url
                  ? <a className="text-primary hover:underline" href={c.instagram_url} target="_blank" rel="noreferrer">{c.instagram_url}</a>
                  : null} />
              <Field label="Notlar" value={c.notes} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="anlasma">
          <Card>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
              <Field label="Haftalık İçerik" value={ct?.weekly_content_count} />
              <Field label="Post" value={ct?.post_count} />
              <Field label="Story" value={ct?.story_count} />
              <Field label="KDV %" value={ct?.vat_rate} />
              <Field label="İçerik Planı" value={ct?.content_plan} />
              <Field label="Ücret Başlangıç" value={ct?.fee_effective_date} />
              <Field label="Video Çekimi" value={yesno(ct?.video_shooting_enabled)} />
              <Field label="Haftalık Video" value={ct?.weekly_video_count} />
              <Field label="Foto Çekimi" value={yesno(ct?.photo_shooting_enabled)} />
              <Field label="Haftalık Foto" value={ct?.weekly_photo_count} />
              <Field label="Drone" value={yesno(ct?.drone_usage)} />
              <Field label="Açıklama" value={ct?.description} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ekip">
          <Card>
            <CardContent className="grid gap-4 pt-6 sm:grid-cols-2">
              {ROLE_SLOTS.map((slot) => (
                <Field key={slot.key} label={slot.label}
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
              <CardHeader><CardTitle className="text-base">Kişiler</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {(c.contacts ?? []).length === 0 && <p className="text-sm text-muted-foreground">Kayıtlı kişi yok.</p>}
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
              <CardHeader><CardTitle className="text-base">Lokasyonlar</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {(c.locations ?? []).length === 0 && <p className="text-sm text-muted-foreground">Kayıtlı lokasyon yok.</p>}
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
              <Field label="Ana Drive Klasörü" value={
                c.google_drive_url
                  ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={c.google_drive_url} target="_blank" rel="noreferrer">Klasörü aç <ExternalLink className="h-3 w-3" /></a>
                  : null} />
              <div>
                <div className="mb-2 text-xs text-muted-foreground">Hafta Klasörleri ({c.week_folders.length})</div>
                <div className="flex flex-wrap gap-2">
                  {c.week_folders.length === 0 && <span className="text-sm text-muted-foreground">—</span>}
                  {c.week_folders.map((w) => (
                    w.link
                      ? <a key={w.week_number} href={w.link} target="_blank" rel="noreferrer"
                          className="rounded border px-2 py-1 text-xs hover:bg-muted">Hafta {w.week_number}</a>
                      : <span key={w.week_number} className="rounded border px-2 py-1 text-xs text-muted-foreground">Hafta {w.week_number}</span>
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
            <DialogTitle>Müşteriyi sil</DialogTitle>
            <DialogDescription>
              "{c.name}" arşivlenecek (soft-delete). İstersen bir neden ekle.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="reason">Neden (opsiyonel)</Label>
            <Input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDel(false)}>İptal</Button>
            <Button className="bg-destructive text-white hover:bg-destructive/90"
              onClick={doDelete} disabled={del.isPending}>
              {del.isPending ? "Siliniyor…" : "Sil"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
