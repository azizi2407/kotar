// Codex Görsel Üretimi (2026-08-10) — ChatGPT aboneliği üzerinden `codex exec` +
// `$imagegen`. Mevcut "AI Görsel Üretimi" sayfasından (Magnific hattı) AYRIDIR ve onu
// değiştirmez; hangisinin kullanılacağına kullanıcı sayfa seçerek karar verir.
//
// v1 kapsamı bilerek dar (kullanıcı kararı): üret → gör → indir. Varyasyon, arşiv,
// onay/red ve yeniden deneme düğmeleri YOK. Onay kapısı invaryantı, bu görsellerin
// hiçbir müşteri yüzeyine bağlanmamasıyla korunuyor — indirip mevcut akışa insan koyar.
import { useMemo, useState } from "react"
import { Download, ImageOff, Loader2, Sparkles, TriangleAlert } from "lucide-react"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import {
  ASPECTS,
  MAX_REFERENCES,
  STATUS_LABEL,
  VARIANT_LABEL,
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
  // --- parti üretimi durumu ---
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
      // Anahtarın durumu DB'den OKUNUR, varsayılana bırakılmaz — yoksa sayfa her
      // yenilendiğinde kapalı görünür, DB'de açık olur ve düğme sebepsiz pasif kalır.
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

  // "Bu hafta" BİLEREK varsayılan değil: brief'ler gerçek hafta + 2 için üretiliyor
  // (enqueue_briefs._target_week_iso), içinde bulunulan haftanın brief'i çoğu zaman yok.
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
      // Her iş tek tek biter; hepsini bekleyip sonra listeyi tazele.
      await Promise.allSettled(r.created.map((j) => pollImageJob(j.id)))
      await partiYukle(clientId, hafta)
    } catch (e) {
      setHata(e instanceof Error ? e.message : "Parti başlatılamadı.")
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
      // Codex 1-4 dk sürer; pollImageJob'un timeout'u bunun için 15 dk.
      const bitmis = await pollImageJob(image_job.id)
      if (bitmis.status === "completed") {
        setSonuc(bitmis)
      } else {
        setHata(bitmis.error_public ?? "Görsel üretilemedi.")
        setHataKodu(bitmis.error_code)
      }
      setGecmis(await listImageJobs(clientId))
    } catch (e) {
      setHata(e instanceof Error ? e.message : "Görsel üretimi başlatılamadı.")
    } finally {
      setUretiliyor(false)
    }
  }

  if (!isManagement) {
    return <div className="p-6 text-sm text-muted-foreground">Bu sayfa yalnız yönetim içindir.</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Codex Görsel Üretimi</h1>
        <p className="text-sm text-muted-foreground">
          ChatGPT aboneliği üzerinden görsel üretir (Magnific kredisi harcamaz). Üretim
          1-4 dakika sürer. Onaysız müşteride üretim yapılamaz (KVKK).
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

        {/* üretim formu + sonuç */}
        <div className="space-y-4">
          {clientId == null ? (
            <div className="text-sm text-muted-foreground">Başlamak için bir müşteri seçin.</div>
          ) : (
            <>
              {/* --- Haftalık parti üretimi: brief fikirlerinden toplu görsel --- */}
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
                    Haftalık görsel üretimi açık
                  </label>
                  <select
                    className={selectCls}
                    value={hafta}
                    onChange={(e) => haftaSec(e.target.value)}
                    aria-label="Hafta"
                  >
                    {haftalar.length === 0 && <option value="">— onaylı brief yok —</option>}
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
                    Bu haftayı üret
                  </Button>
                </div>

                {!kvkk && (
                  <p className="text-xs text-muted-foreground">
                    Bu müşteride KVKK görsel onayı yok — üretim yapılamaz.
                  </p>
                )}
                {logoYok && (
                  <p className="text-xs text-muted-foreground">
                    Bu müşteride logo yok — görseller referanssız üretildi.
                  </p>
                )}
                {partiCalisiyor && (
                  <p className="text-sm text-muted-foreground">
                    Parti üretiliyor — her görsel 1-4 dakika sürer, sayfayı kapatmayın.
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
                              <Badge variant="secondary">{VARIANT_LABEL[v]}</Badge>
                              <span>{STATUS_LABEL[job.status] ?? job.status}</span>
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
                                  indir
                                </a>
                              </>
                            ) : (
                              <p className="text-muted-foreground">
                                {job.error_public ?? "Bekliyor…"}
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
                <Label htmlFor="codex-istem">İstem</Label>
                <Textarea
                  id="codex-istem"
                  rows={4}
                  placeholder="Ne görmek istediğinizi yazın…"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </div>

              <div className="flex flex-wrap items-end gap-4">
                <div className="space-y-1">
                  <Label htmlFor="codex-oran">En-boy oranı</Label>
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
                  <Label htmlFor="codex-brief">Brief (opsiyonel)</Label>
                  <select
                    id="codex-brief"
                    className={selectCls}
                    value={briefId ?? ""}
                    onChange={(e) => setBriefId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">— yok —</option>
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
                  Üret
                </Button>
              </div>

              {(assets ?? []).length > 0 && (
                <div className="space-y-2">
                  <Label>
                    Referans görseller{" "}
                    <span className="text-muted-foreground">
                      (en fazla {MAX_REFERENCES})
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
                        {a.label || a.file_name || (a.kind === "logo" ? "Logo" : "Görsel")}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {uretiliyor && (
                <p className="text-sm text-muted-foreground">
                  Görsel üretiliyor — bu işlem 1-4 dakika sürebilir, sayfayı kapatmayın.
                </p>
              )}

              {hata && (
                <div className="flex gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                  <div>
                    {hata}
                    {hataKodu === "auth" && (
                      <p className="mt-1 font-medium">
                        Operatör müdahalesi gerekiyor — sunucuda Codex oturumu yenilenmeli.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {sonuc && (
                <div className="space-y-2 rounded-md border p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <Badge variant="secondary">{STATUS_LABEL[sonuc.status] ?? sonuc.status}</Badge>
                    {sonuc.output_meta && (
                      <span className="text-muted-foreground">
                        {sonuc.output_meta.width}×{sonuc.output_meta.height}
                      </span>
                    )}
                  </div>
                  <img
                    src={imageUrl(sonuc.id)}
                    alt="Üretilen görsel"
                    className="max-w-full rounded-lg border"
                  />
                  {/* Button `asChild` desteklemiyor (bu projedeki shadcn sürümü) →
                      indirme bağlantısı buttonVariants ile stillenir. */}
                  <a
                    href={imageUrl(sonuc.id)}
                    download={`codex-gorsel-${sonuc.id}.png`}
                    className={buttonVariants({ variant: "outline", size: "sm" })}
                  >
                    <Download className="mr-2 size-4" />
                    İndir
                  </a>
                </div>
              )}

              {gecmis.length > 0 && (
                <div className="space-y-2">
                  <h2 className="text-sm font-medium">Son üretimler</h2>
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
                            {STATUS_LABEL[g.status] ?? g.status}
                          </Badge>
                          {g.has_image && (
                            <a
                              href={imageUrl(g.id)}
                              download={`codex-gorsel-${g.id}.png`}
                              className="underline"
                            >
                              indir
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
