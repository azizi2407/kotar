// Codex image generation (2026-08-10) — wrappers for `/api/imagegen/*`.
//
// SEPARATE from the Magnific pipeline in `lib/sharing.ts`: that pipeline writes to
// Drive and carries an approval gate and a credit badge. This pipeline generates via
// the ChatGPT subscription, output stays local on the server, and there's no
// approval/variation surface in v1.
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

// Status → translation key (matches backend STATUSES). The label text is produced
// in the page component with t() (this file can't use React hooks) — see media-tools.ts
// "pages.codexImage.status.*".
export const STATUS_LABEL_KEY: Record<string, string> = {
  queued: "pages.codexImage.status.queued",
  preparing: "pages.codexImage.status.preparing",
  running: "pages.codexImage.status.running",
  validating: "pages.codexImage.status.validating",
  storing: "pages.codexImage.status.storing",
  completed: "pages.codexImage.status.completed",
  failed: "pages.codexImage.status.failed",
  cancelled: "pages.codexImage.status.cancelled",
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

/** The message of the Error thrown on timeout — a fixed/untranslated marker (this
 *  file can't use React hooks, so it can't translate via t()). The calling component
 *  catches this marker and shows it to the user with its own t() call. */
export const IMAGEGEN_TIMEOUT_MARKER = "IMAGEGEN_TIMEOUT"

/** Poll until the job finishes.
 *
 * `sharing.pollJob` is DELIBERATELY not used: its timeout is 180s, while Codex
 * generation took 3.5 minutes at a spike — that wrapper would throw an error right as
 * the generation finished. */
export async function pollImageJob(
  id: number,
  opts: { interval?: number; timeout?: number } = {},
): Promise<ImageJob> {
  const interval = opts.interval ?? 3000
  const timeout = opts.timeout ?? 900000 // 15 min
  const start = Date.now()
  while (Date.now() - start < timeout) {
    const d = await apiGet(`/imagegen/jobs/${id}`)
    const ij: ImageJob = d.image_job
    if (ij.status === "completed" || ij.status === "failed" || ij.status === "cancelled") return ij
    await new Promise((r) => setTimeout(r, interval))
  }
  throw new Error(IMAGEGEN_TIMEOUT_MARKER)
}

export function imageUrl(id: number) {
  return `/api/imagegen/jobs/${id}/image`
}

// --- Weekly batch generation (2026-08-10) ---

export type BriefWeek = { week_iso: string; brief_id: number; title: string | null }
export type SkippedIdea = { index: number; baslik: string; sebep: string }
export type BatchGroup = {
  index: number
  baslik: string
  jobs: Partial<Record<"with_text" | "clean", ImageJob>>
}

// Variant → translation key (see media-tools.ts "pages.codexImage.variant.*").
export const VARIANT_LABEL_KEY: Record<string, string> = {
  with_text: "pages.codexImage.variant.withText",
  clean: "pages.codexImage.variant.clean",
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
