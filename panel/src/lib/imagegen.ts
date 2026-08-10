// Codex görsel üretimi (2026-08-10) — `/api/imagegen/*` sarmalayıcıları.
//
// `lib/sharing.ts`'teki Magnific hattından AYRI: o hat Drive'a yazar, onay kapısı ve
// kredi rozeti taşır. Bu hat ChatGPT aboneliği üzerinden üretir, çıktı sunucuda lokal
// durur ve v1'de onay/varyasyon yüzeyi yoktur.
import { apiGet, apiJson } from "./api"

export type ImageJob = {
  id: number
  client_id: number
  brief_id: number | null
  status: string
  provider: string
  aspect_ratio: string
  original_user_prompt: string
  output_meta: { width: number; height: number; bytes: number } | null
  has_image: boolean
  error_code: string | null
  error_public: string | null
  requested_by: string | null
  created_at: string | null
  completed_at: string | null
}

export const ASPECTS: [string, string][] = [
  ["social_post_4_5", "POST (4:5)"],
  ["square_1_1", "KARE (1:1)"],
  ["social_story_9_16", "STORY (9:16)"],
]

export const MAX_REFERENCES = 3

// Durum → kullanıcıya gösterilecek etiket (backend STATUSES ile eş).
export const STATUS_LABEL: Record<string, string> = {
  queued: "Sırada",
  preparing: "Hazırlanıyor",
  running: "Üretiliyor",
  validating: "Doğrulanıyor",
  storing: "Kaydediliyor",
  completed: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal",
}

export async function listImageJobs(clientId: number): Promise<ImageJob[]> {
  const d = await apiGet(`/imagegen/jobs?client_id=${clientId}`)
  return d.image_jobs
}

export async function generateImage(body: {
  client_id: number
  prompt: string
  aspect_ratio: string
  brief_id?: number | null
  reference_asset_ids?: number[]
}): Promise<{ image_job: ImageJob }> {
  return apiJson("/imagegen/generate", body)
}

export async function imagegenHealth(): Promise<{ ok: boolean; detail: string; enabled: boolean }> {
  return apiGet("/imagegen/health")
}

/** İşi bitene dek yokla.
 *
 * `sharing.pollJob` BİLEREK kullanılmıyor: onun timeout'u 180 sn, Codex üretimi ise
 * spike'ta 3,5 dakika sürdü — o sarmalayıcı üretimi tam da biterken hata atardı. */
export async function pollImageJob(
  id: number,
  opts: { interval?: number; timeout?: number } = {},
): Promise<ImageJob> {
  const interval = opts.interval ?? 3000
  const timeout = opts.timeout ?? 900000 // 15 dk
  const start = Date.now()
  while (Date.now() - start < timeout) {
    const d = await apiGet(`/imagegen/jobs/${id}`)
    const ij: ImageJob = d.image_job
    if (ij.status === "completed" || ij.status === "failed" || ij.status === "cancelled") return ij
    await new Promise((r) => setTimeout(r, interval))
  }
  throw new Error("Üretim zaman aşımına uğradı")
}

export function imageUrl(id: number) {
  return `/api/imagegen/jobs/${id}/image`
}

// --- Haftalık parti üretimi (2026-08-10) ---

export type BriefWeek = { week_iso: string; brief_id: number; title: string | null }
export type SkippedIdea = { index: number; baslik: string; sebep: string }
export type BatchGroup = {
  index: number
  baslik: string
  jobs: Partial<Record<"with_text" | "clean", ImageJob>>
}

export const VARIANT_LABEL: Record<string, string> = {
  with_text: "Metinli",
  clean: "Metinsiz",
}

export async function listWeeks(clientId: number): Promise<BriefWeek[]> {
  const d = await apiGet(`/imagegen/weeks?client_id=${clientId}`)
  return d.weeks
}

export async function startBatch(
  clientId: number,
  weekIso: string,
): Promise<{
  created: ImageJob[]
  skipped: SkippedIdea[]
  already: unknown[]
  logo_missing: boolean
}> {
  return apiJson("/imagegen/batch", { client_id: clientId, week_iso: weekIso })
}

export async function listBatch(
  clientId: number,
  weekIso: string,
): Promise<{ groups: BatchGroup[]; skipped: SkippedIdea[] }> {
  return apiGet(`/imagegen/batch?client_id=${clientId}&week_iso=${weekIso}`)
}

export async function getClientSettings(
  clientId: number,
): Promise<{ auto_image_enabled: boolean; ai_image_consent: boolean }> {
  return apiGet(`/imagegen/client-settings?client_id=${clientId}`)
}

export async function setAutoImage(clientId: number, enabled: boolean) {
  return apiJson("/imagegen/client-settings",
    { client_id: clientId, auto_image_enabled: enabled }, "PATCH")
}
