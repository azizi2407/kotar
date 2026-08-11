// Sharing Board data hooks (TanStack Query). Contract: sharing.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiBlob, apiDelete, apiGet, apiJson, apiUpload } from "./api"

export type ShareKind = "post" | "story" | "video" | "linkedin"
export type ShareStatus = "draft" | "published"

export interface Share {
  id: number
  client_id: number
  week_iso: string
  kind: ShareKind
  status: ShareStatus
  file_id: string | null
  file_name: string | null
  original_name: string | null
  caption_text: string | null
  hashtag_text: string | null
  note: string | null
  published_day_name: string | null
  published_at: string | null
  planned_date: string | null
  planned_time: string | null
  platforms: Record<string, unknown>
  client_review: { status?: string; note?: string; at?: string } | null
  revision: number
  has_transcript?: boolean
  caption_suggestions?: { captions?: string[]; hashtags?: string } | null
  local?: boolean
}

export interface SpecialCard {
  id: number
  event_id: number
  week_iso: string
  published_at: string | null
}

// Special day falling in this week, chosen by the client (info card in the board card strip).
export interface SpecialDayInWeek {
  event_id: number
  day_name: string | null
  date_label: string
  type: "day" | "week"
}

// This week's video upload — info card on the management board (like the special day card).
export interface VideoUpload {
  id: number
  file_id: string | null
  file_name: string | null
  uploaded_at: string | null
  local?: boolean
  /** Whether this video has been used in a share card (badge in the videographer list). */
  shared?: boolean
  /** Delete permission is computed on the BACKEND (management: unrestricted / videographer:
   *  only what they uploaded). The panel does not re-derive the rule — it would drift if the rule changed. */
  can_delete?: boolean
}

export interface BoardRow {
  client: { id: number; name: string; instagram_url: string | null }
  // On the designer board this splits rows into "My Clients" (true) / "Other Clients" (false);
  // always true in the management view.
  assigned: boolean
  special_days: SpecialDayInWeek[]
  shares: Share[]
  special_cards: SpecialCard[]
  priority: boolean
  published_count: number
  total_count: number
  upload_count: number
  // Pre-approval (internal management gate) decisions
  pre_approved_count: number
  pre_revision_count: number
  // Client decisions on the approval page (per-upload, 2026-07-24)
  upload_approved_count: number
  upload_revision_count: number
  open_revision_count: number
  revision_share_ids: number[]
  video_uploads: VideoUpload[]
  /** Total video count for the week (derived by the backend) */
  video_total_count?: number
  /** Real count of videos AWAITING sharing — video_pending is capped at 5, this is not */
  video_pending_count?: number
  /** The newest 5 pending videos (excludes ones already shared) */
  video_pending?: VideoUpload[]
  photos_folder_url: string | null
  drive_folder_url: string | null
}

export interface MovableFile { id: string; name: string | null; mime: string | null }
export interface MovableWeek { week_iso: string; folder_id: string | null; files: MovableFile[] }
export interface MovableFiles { previous: MovableWeek; next: MovableWeek }

export function useMovableFiles(clientId: number | null, weekIso: string, enabled: boolean) {
  return useQuery<MovableFiles>({
    queryKey: ["movable-files", clientId, weekIso],
    enabled: enabled && clientId != null,
    queryFn: () => apiGet(
      `/sharing/movable-files?client_id=${clientId}&week_iso=${encodeURIComponent(weekIso)}`),
  })
}

export function useMoveFiles() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { client_id: number; from_week_iso: string; to_week_iso: string; file_ids: string[] }) =>
      apiJson("/sharing/move-files", v),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["movable-files"] })
      qc.invalidateQueries({ queryKey: ["board", v.to_week_iso] })
      qc.invalidateQueries({ queryKey: ["drive-counts"] })
    },
  })
}

export interface Upload {
  id: number
  client_id: number
  week_iso: string
  card_index: number | null
  category: string | null
  file_id: string | null
  file_name: string | null
  mime_type: string | null
  file_size: number | null
  local?: boolean
  // Whether a share card ALREADY exists for this file (including drafts). ShareModal's
  // file picker hides used ones — so a second card can't be opened for the same file.
  used?: boolean
  // The week it came from if manually moved (client media page badge). Only
  // populated in the `/clients/:id/media` response.
  moved_from_week_iso?: string | null
}

// Week block for the client media page. Weeks WITHOUT files are also returned —
// they render as drag-and-drop targets.
export interface ClientMediaWeek {
  week_iso: string
  uploads: Upload[]
}

export const KIND_LABELS: Record<ShareKind, string> = {
  post: "Post",
  story: "Story",
  video: "Video",
  linkedin: "LinkedIn",
}

export function useBoard(weekIso: string) {
  return useQuery<{ week_iso: string; rows: BoardRow[] }>({
    queryKey: ["board", weekIso],
    queryFn: () => apiGet(`/sharing/cards?week_iso=${encodeURIComponent(weekIso)}`),
  })
}

// Management board: the owner (OWNER_EMAIL) marks/unmarks a client as "mine".
// On success the board is refreshed → grouping (My Clients/Other) is recomputed.
export function useSetManagerClient(weekIso: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { client_id: number; owned: boolean }) =>
      apiJson("/sharing/manager-clients", v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["board", weekIso] }),
  })
}

export function useDesignerBoard(weekIso: string) {
  return useQuery<{ week_iso: string; rows: BoardRow[] }>({
    queryKey: ["designer-board", weekIso],
    queryFn: () => apiGet(`/sharing/designer/cards?week_iso=${encodeURIComponent(weekIso)}`),
  })
}

// Videographer video-upload board (management+videographer; assigned=shoot|edit assignment).
export function useVideographerBoard(weekIso: string) {
  return useQuery<{ week_iso: string; rows: BoardRow[];
                    hidden_client_ids: number[]; hidden_count: number }>({
    queryKey: ["videographer-board", weekIso],
    queryFn: () => apiGet(`/sharing/videographer/cards?week_iso=${encodeURIComponent(weekIso)}`),
  })
}

export function useUploads(clientId: number | null, weekIso: string) {
  return useQuery<Upload[]>({
    queryKey: ["uploads", clientId, weekIso],
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/uploads?client_id=${clientId}&week_iso=${encodeURIComponent(weekIso)}`)
        .then((d) => d.uploads),
  })
}

// Client media page: ALL of the client's uploads, grouped week by week.
// NOT a twin of `useUploads` — that one is single-week + management-only (ShareModal's
// file picker pool); this one is also open to designers and includes empty target weeks.
export function useClientMedia(clientId: number | null) {
  return useQuery<ClientMediaWeek[]>({
    queryKey: ["client-media", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/sharing/clients/${clientId}/media`).then((d) => d.weeks),
  })
}

// Move the selected uploads to the target week (also in the Drive folder). Can
// return a partial success: the `moved` count + an `errors` list of files that failed.
// Board queries are also invalidated — the moved file changes week there too.
export function useMoveUploadsToWeek(clientId: number | null) {
  const qc = useQueryClient()
  return useMutation<{ moved: number; errors: string[] }, Error,
    { upload_ids: number[]; to_week_iso: string }>({
    mutationFn: (v) => apiJson("/sharing/uploads/move-week", v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["client-media", clientId] })
      qc.invalidateQueries({ queryKey: ["board"] })
      qc.invalidateQueries({ queryKey: ["designer-board"] })
      qc.invalidateQueries({ queryKey: ["uploads"] })
    },
  })
}

// Delete an uploaded card — superadmin only (enforced by the backend). Soft-delete.
export function useDeleteUpload(clientId: number | null, weekIso: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (uploadId: number) => apiDelete(`/sharing/uploads/${uploadId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["uploads", clientId, weekIso] })
      qc.invalidateQueries({ queryKey: ["board", weekIso] })
    },
  })
}

function useBoardInvalidator() {
  const qc = useQueryClient()
  // `uploads` is also invalidated (2026-07-29): every share mutation changes the file's
  // `used` flag — opening a card should drop it from the file picker, deleting a card
  // should bring it back. When only `board` was invalidated, the file just used in the
  // "Save and New" flow kept lingering in the list.
  return () => {
    qc.invalidateQueries({ queryKey: ["board"] })
    qc.invalidateQueries({ queryKey: ["uploads"] })
  }
}

export function useCreateShare() {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiJson("/sharing/shares", body).then((d) => d.share as Share),
    onSuccess: inv,
  })
}

export function useUpdateShare(id: number) {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiJson(`/sharing/shares/${id}`, body, "PATCH").then((d) => d.share as Share),
    onSuccess: inv,
  })
}

// Used when the id is only known at runtime (updating a newly created draft).
export function useUpdateShareById() {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      apiJson(`/sharing/shares/${id}`, body, "PATCH").then((d) => d.share as Share),
    onSuccess: inv,
  })
}

export function useShareAction() {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: ({ id, action, body }: { id: number; action: string; body?: unknown }) =>
      apiJson(`/sharing/shares/${id}/${action}`, body ?? {}),
    onSuccess: inv,
  })
}

export function useDeleteShare() {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/sharing/shares/${id}`),
    onSuccess: inv,
  })
}

export function usePriorityToggle() {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: (body: { client_id: number; week_iso: string }) =>
      apiJson("/sharing/priority", body),
    onSuccess: inv,
  })
}

export function useRequestRevision() {
  const inv = useBoardInvalidator()
  return useMutation({
    mutationFn: (body: { client_id: number; week_iso: string; kind: "design" | "video"; share_id?: number; note?: string }) =>
      apiJson("/sharing/revision-request", body),
    onSuccess: inv,
  })
}

// Caption generation settings (Phase 1b) — client-default ⊕ generate-time override
// contract (see ai_context.resolve_caption_settings). All fields are optional.
export interface CaptionSettings {
  model?: string | null
  tone?: string | null
  emoji_limit?: number | null
  hashtag_count?: number | null
  lang?: "TR" | "EN" | "TR+EN"
  use_brief?: boolean
  char_limit?: number | null
}

// The client's caption generation defaults (read/written on the client edit surface).
export function useCaptionSettings(clientId: number | null) {
  return useQuery<CaptionSettings>({
    queryKey: ["caption-settings", clientId],
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/clients/${clientId}/caption-settings`).then((d) => d.caption_settings ?? {}),
  })
}

export function useSaveCaptionSettings(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (settings: CaptionSettings) =>
      apiJson(`/sharing/clients/${clientId}/caption-settings`, settings, "PUT")
        .then((d) => d.caption_settings as CaptionSettings),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["caption-settings", clientId] }),
  })
}

export function useGenerateCaption() {
  return useMutation({
    mutationFn: ({ shareId, settings, feedback, previousCaption }: {
      shareId: number; settings?: CaptionSettings; feedback?: string; previousCaption?: string
    }) => {
      const body: Record<string, unknown> = {}
      if (settings && Object.keys(settings).length) body.settings = settings
      if (feedback) { body.feedback = feedback; if (previousCaption) body.previous_caption = previousCaption }
      return apiJson(`/sharing/shares/${shareId}/caption`, body).then((d) => d.job as { id: number })
    },
  })
}

export function useProcessMedia() {
  return useMutation({
    mutationFn: ({ shareId, useTranscript }: { shareId: number; useTranscript?: boolean }) =>
      apiJson(`/sharing/shares/${shareId}/media`, useTranscript ? { use_transcript: true } : {})
        .then((d) => d.job as { id: number }),
  })
}

// Poll the job until it's done/failed (caption generation is async).
export async function pollJob(
  jobId: number,
  opts: { interval?: number; timeout?: number } = {},
): Promise<{ status: string; result: { captions?: string[]; caption?: string; hashtags?: string; error?: string } | null }> {
  const interval = opts.interval ?? 2000
  const timeout = opts.timeout ?? 180000
  const start = Date.now()
  while (Date.now() - start < timeout) {
    const d = await apiGet(`/sharing/jobs/${jobId}`)
    if (d.job.status === "done" || d.job.status === "failed") return d.job
    await new Promise((r) => setTimeout(r, interval))
  }
  throw new Error("Generation timed out")
}

// Pre-approval link (2026-07-24): the designer generates it, sends it to the manager.
// Once the manager approves, content becomes visible on the client approval link (staged gate).
export function usePreApprovalLink() {
  return useMutation({
    mutationFn: (body: { client_id: number; week_iso: string }) =>
      apiJson("/sharing/pre-approval-link", body).then(
        (d) => ({ token: d.token as string, shareCount: (d.share_count ?? 0) as number }),
      ),
  })
}

// NOTE: `useReviewLink` / `reviewShareMessage` were REMOVED on 2026-08-06 — the old
// automatic full-scope "Approval Link" buttons were removed from both boards, replaced
// by the manual-selection flow below. The backend's `/sharing/review-link` endpoint STAYS:
// the `/review/<token>` page and the pre-approval flow are live, and already-distributed old links still work.

// --- client approval link (manual selection, 2026-08-06) ---
// The removed `useReviewLink` sent the (client, week) scope automatically;
// in this flow you pick items one by one in the modal and the selection is frozen into the link.

export interface ApprovalCandidate extends Upload {
  // Whether this was already put in an approval link before — doesn't filter, just a
  // badge (resending the same design after a revision is legitimate).
  sent_before: boolean
  review: { status: string; note: string | null; at: string | null } | null
}

export interface ApprovalWeek {
  week_iso: string
  uploads: ApprovalCandidate[]
}

export interface ApprovalLinkRow {
  id: number
  token: string
  upload_ids: number[]
  count: number
  note: string | null
  note_at: string | null
  revoked: boolean
  created_at: string | null
}

export function useApprovalCandidates(clientId: number | null, weekIso: string, enabled: boolean) {
  return useQuery<ApprovalWeek[]>({
    queryKey: ["approval-candidates", clientId, weekIso],
    enabled: enabled && clientId != null,
    queryFn: () =>
      apiGet(`/sharing/approval-candidates?client_id=${clientId}&week_iso=${encodeURIComponent(weekIso)}`)
        .then((d) => d.weeks),
  })
}

// The only way to read in the panel the note the client wrote on these links.
export function useApprovalLinks(clientId: number | null, enabled: boolean) {
  return useQuery<ApprovalLinkRow[]>({
    queryKey: ["approval-links", clientId],
    enabled: enabled && clientId != null,
    queryFn: () => apiGet(`/sharing/approval-links?client_id=${clientId}`).then((d) => d.links),
  })
}

export function useApprovalLink(clientId: number | null) {
  const qc = useQueryClient()
  return useMutation<{ token: string; count: number; reused: boolean }, Error,
    { client_id: number; upload_ids: number[] }>({
    mutationFn: (v) => apiJson("/sharing/approval-link", v),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approval-candidates", clientId] })
      qc.invalidateQueries({ queryKey: ["approval-links", clientId] })
    },
  })
}

// Approval message sent to the client; content count comes from the selection (singular/plural).
// `t`: the translation function the caller got from useI18n() — this file (lib/sharing.ts)
// cannot call useI18n() directly per React hook rules (it's not a component/hook).
export function approvalShareMessage(url: string, count: number, t: (key: string, vars?: Record<string, string | number>) => string) {
  const key = count > 1
    ? "components.sharing.clientApprovalModal.shareMessagePlural"
    : "components.sharing.clientApprovalModal.shareMessageSingular"
  return t(key, { url })
}

export function useUploadFile(clientId: number, weekIso: string, onProgress?: (pct: number) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ file, category }: { file: File; category: string }) => {
      const form = new FormData()
      form.append("client_id", String(clientId))
      form.append("week_iso", weekIso)
      form.append("category", category)
      form.append("file", file)
      return apiUpload("/sharing/upload", form, onProgress).then((d) => d.upload as Upload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["uploads", clientId, weekIso] })
      qc.invalidateQueries({ queryKey: ["board"] })
      qc.invalidateQueries({ queryKey: ["designer-board"] })
      qc.invalidateQueries({ queryKey: ["drive-counts", weekIso] })
    },
  })
}

// --- shoot plan (videographer Kanban) ---

export interface ShootTask {
  id: number
  client_id: number | null
  client_name: string | null
  title: string | null
  content_type: string | null
  scheduled_date: string | null
  start_time: string | null
  end_time: string | null
  status: "pending" | "completed"
  priority: string
  location_note: string | null
}

export interface ShootPlan {
  week_iso: string
  days: { date: string; tasks: ShootTask[] }[]
  pool: { id: number; name: string }[]
}

export function useShootPlan(weekIso: string) {
  return useQuery<ShootPlan>({
    queryKey: ["shoot-plan", weekIso],
    queryFn: () => apiGet(`/sharing/shoot-plan?week_iso=${encodeURIComponent(weekIso)}`),
  })
}

export function useShootMutations(weekIso: string) {
  const qc = useQueryClient()
  const inv = { onSettled: () => qc.invalidateQueries({ queryKey: ["shoot-plan", weekIso] }) }
  return {
    create: useMutation({
      mutationFn: (b: { client_id: number; scheduled_date: string }) =>
        apiJson("/sharing/shoot-plan", b).then((d) => d.task as ShootTask),
      ...inv,
    }),
    reschedule: useMutation({
      mutationFn: ({ id, date }: { id: number; date: string }) =>
        apiJson(`/sharing/shoot-plan/${id}`, { scheduled_date: date }, "PATCH"),
      ...inv,
    }),
    reorder: useMutation({
      mutationFn: (taskIds: number[]) =>
        apiJson("/sharing/shoot-plan/order", { task_ids: taskIds }, "PATCH"),
      ...inv,
    }),
    done: useMutation({
      mutationFn: (id: number) => apiJson(`/sharing/shoot-plan/${id}/done`, {}),
      ...inv,
    }),
    remove: useMutation({
      mutationFn: (id: number) => apiDelete(`/sharing/shoot-plan/${id}`),
      ...inv,
    }),
  }
}


export interface SpecialDayEvent {
  id: number
  day_name: string | null
  description: string | null
  date_num: number | null
  date_start: number | null
  date_end: number | null
  month: number | null
  year: number | null
  client_id: number | null
  status?: string
  generated_by?: string
}

// Calendar view: the month's events + the brand names that picked/own each one.
export interface SpecialDayOverviewItem extends SpecialDayEvent { client_names: string[] }

export function useSpecialDayOverview(month: number, year: number) {
  return useQuery<SpecialDayOverviewItem[]>({
    queryKey: ["sd-overview", month, year],
    queryFn: () =>
      apiGet(`/sharing/special-days/overview?month=${month}&year=${year}`).then((d) => d.items),
  })
}

export function useSpecialDayEvents(month: number, year: number) {
  return useQuery<SpecialDayEvent[]>({
    queryKey: ["sd-events", month, year],
    queryFn: () => apiGet(`/sharing/special-days/events?month=${month}&year=${year}`).then((d) => d.events),
  })
}

export function useSpecialDayMutations(month: number, year: number) {
  const qc = useQueryClient()
  const inv = { onSuccess: () => qc.invalidateQueries({ queryKey: ["sd-events", month, year] }) }
  return {
    create: useMutation({
      mutationFn: (b: Record<string, unknown>) =>
        apiJson("/sharing/special-days/events", { month, year, type: "day", ...b }),
      ...inv,
    }),
    remove: useMutation({
      mutationFn: (id: number) => apiDelete(`/sharing/special-days/events/${id}`),
      ...inv,
    }),
    link: useMutation({
      mutationFn: (b: { client_id: number; month: number; year: number }) =>
        apiJson("/sharing/special-days/selection-link", b).then((d) => d.token as string),
    }),
  }
}

// Approval gate: approve a draft (AI-generated) special day → status='approved'.
export function useApproveSpecialDay(month: number, year: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      apiJson(`/sharing/special-day-events/${id}/approve`, {}).then((d) => d.event as SpecialDayEvent),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sd-events", month, year] }),
  })
}

export interface PinterestLink { url: string; label?: string | null }
export interface BriefIdea {
  number?: number
  title?: string
  ornek_baslik?: string
  icerik?: string
  slogan_cta?: string
  gorsel_tarzi?: string
  gorselde_bulunmasi_gerekenler?: string[]
  izlenecek_tarz_referans?: string
  pinterest_links?: PinterestLink[]
  [k: string]: unknown
}
export interface Brief {
  id: number
  client_id: number
  week_iso: string
  title: string | null
  intro: string | null
  raw_md: string | null
  ideas: BriefIdea[]
  frontmatter: Record<string, unknown>
  week_notes: Record<string, unknown>
  status?: string
  generated_by?: string
}

// `includeDraft`: only meaningful for management (the backend ignores it for other roles) —
// so they can also see and approve the draft (AI-generated, unapproved) brief.
export function useBrief(clientId: number | null, weekIso: string, enabled: boolean, includeDraft = false) {
  return useQuery<Brief | null>({
    queryKey: ["brief", clientId, weekIso, includeDraft],
    enabled: enabled && clientId != null,
    queryFn: () =>
      apiGet(`/sharing/brief?client_id=${clientId}&week_iso=${encodeURIComponent(weekIso)}`
        + (includeDraft ? "&include_draft=1" : ""))
        .then((d) => d.brief),
  })
}

// Approval gate: approve a draft (AI-generated) brief → status='approved'.
export function useApproveBrief() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      apiJson(`/sharing/brief/${id}/approve`, {}).then((d) => d.brief as Brief),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brief"] }),
  })
}

// Week Notes writeback (Phase 3) — panel→DB partial merge (only sent keys are overwritten).
// Contract: sharing.py POST /brief/<id>/notes (management only).
export interface WeekNotesInput {
  durum?: string
  onay_tarihi?: string
  secilen_fikirler?: string[]
  gun_atamasi?: string
  geri_bildirim?: string
}

export function useSaveWeekNotes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ briefId, notes }: { briefId: number; notes: WeekNotesInput }) =>
      apiJson(`/sharing/brief/${briefId}/notes`, notes).then((d) => d.brief as Brief),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brief"] }),
  })
}

// Manual generate/regenerate (management) → enqueues a job; wait for the result with `pollJob`.
// `force`: overwrite if a brief already exists. Without it the backend is idempotent → nothing
// happens (this was why the "Regenerate" button was silently a no-op before 2026-07-30).
export function useGenerateBrief() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { client_id: number; week_iso: string; force?: boolean }) =>
      apiJson("/sharing/brief/generate", body).then((d) => d.job as { id: number }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brief"] }),
  })
}

// Fetch the drive count for ALL clients in a SINGLE request (instead of ~40 requests
// on board open). React Query dedupes the same queryKey → all rows share one fetch.
export interface Business { client_id: number; client_name: string; has_video: boolean }
export interface VgPhoto {
  id: number; client_id: number; client_name: string | null
  file_id: string | null; file_name: string | null; shoot_date: string | null
  file_size: number | null; used: boolean; used_at: string | null
}

export function useBusinesses(weekIso: string) {
  return useQuery<Business[]>({
    queryKey: ["vg-businesses", weekIso],
    queryFn: () => apiGet(`/sharing/videographer/businesses?week_iso=${encodeURIComponent(weekIso)}`).then((d) => d.businesses),
  })
}

export function useMarkBusiness(weekIso: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (b: { client_id: number; has_video: boolean }) =>
      apiJson("/sharing/videographer/businesses/mark", { ...b, week_iso: weekIso }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-businesses", weekIso] }),
  })
}

export function useVgPhotos(clientId?: number) {
  return useQuery<VgPhoto[]>({
    queryKey: ["vg-photos", clientId ?? "all"],
    queryFn: () => apiGet(`/sharing/videographer/photos${clientId ? `?client_id=${clientId}` : ""}`).then((d) => d.photos),
  })
}

export function useUploadVgPhotos() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { client_id: number; shoot_date?: string; files: File[]; onProgress?: (p: number) => void }) => {
      // Each file is a SEPARATE request: avoids hitting the 512 MB request cap with a single
      // batch body (47-photo → 413 case), gives partial error isolation, and real total progress.
      const total = v.files.reduce((s, f) => s + f.size, 0) || 1
      let done = 0
      const saved: unknown[] = []
      const errors: string[] = []
      for (const f of v.files) {
        const form = new FormData()
        form.append("client_id", String(v.client_id))
        if (v.shoot_date) form.append("shoot_date", v.shoot_date)
        form.append("files", f)
        try {
          const r = await apiUpload("/sharing/videographer/photos/upload", form,
            (pct) => v.onProgress?.(Math.round(((done + (f.size * pct) / 100) / total) * 100)))
          saved.push(...(r?.saved ?? []))
          errors.push(...(r?.errors ?? []))
        } catch (e) {
          errors.push(`${f.name}: ${e instanceof Error ? e.message : "upload error"}`)
        }
        done += f.size
      }
      return { saved, errors }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

export function useDeleteVgPhoto() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/sharing/videographer/photos/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

// PERMANENTLY delete a video upload (2026-07-31, project owner decision: NOT soft-delete).
// The row + linked approval records are actually deleted, the server copy is removed from
// disk, and the Drive file goes to trash (recoverable for 30 days). If `drive_ok:false`
// comes back, the panel record was deleted but the Drive file remains.
export function useDeleteVideoUpload() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean; drive_ok: boolean }, Error, number>({
    mutationFn: (id: number) => apiDelete(`/sharing/videographer/uploads/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["videographer-board"] })
      qc.invalidateQueries({ queryKey: ["board"] })   // the video card on the management board
    },
  })
}

export function useBulkDeleteVgPhotos() {
  const qc = useQueryClient()
  return useMutation<{ deleted: number; errors: string[] }, Error, number[]>({
    mutationFn: (ids: number[]) =>
      apiJson("/sharing/videographer/photos/bulk-delete", { ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

// Designer/management: mark/unmark a photo as 'used'.
export function useMarkVgPhotoUsed() {
  const qc = useQueryClient()
  return useMutation<{ used: boolean }, Error, { id: number; used: boolean }>({
    mutationFn: ({ id, used }) =>
      apiJson(`/sharing/videographer/photos/${id}/used`, { used }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

// Rename a photo (Drive + DB). The extension is preserved by the backend.
export function useRenameVgPhoto() {
  const qc = useQueryClient()
  return useMutation<{ file_name: string }, Error, { id: number; name: string }>({
    mutationFn: ({ id, name }) =>
      apiJson(`/sharing/videographer/photos/${id}/rename`, { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

// Download a single photo — GET endpoint (no CSRF needed); save it via an anchor.
export function downloadVgPhoto(id: number) {
  const a = document.createElement("a")
  a.href = `/api/sharing/videographer/photos/${id}/download`
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// Download the selected photos as a zip (save it in the browser via an anchor).
export async function downloadVgPhotosZip(ids: number[]) {
  const blob = await apiBlob("/sharing/videographer/photos/download-zip", { ids })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = "fotograflar.zip"
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// --- videographer suggestion bot (Phase 5, step 17) ---

export interface VideographerIdea {
  id: number
  client_id: number
  reference_link: string | null
  reason: string | null
  shoot_idea: string | null
  status: string
  created_at: string | null
}

// The client's AI trend suggestions (default: only 'new' cards).
export function useVideographerIdeas(clientId: number | null) {
  return useQuery<VideographerIdea[]>({
    queryKey: ["vg-ideas", clientId],
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/videographer/ideas?client_id=${clientId}`).then((d) => d.ideas),
  })
}

// Manually trigger the suggestion bot (client-triggered) → videographer_ideas job; wait for the result with pollJob.
export function useGenerateIdeas() {
  return useMutation({
    mutationFn: (clientId: number) =>
      apiJson("/sharing/videographer/ideas/generate", { client_id: clientId })
        .then((d) => d.job as { id: number }),
  })
}

// "Like → add to shoot list": generates a ShootTask from the suggestion + marks the suggestion accepted.
// Invalidates both the suggestion list and the shoot plan (useShootMutations domain).
export function useLikeIdea(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, scheduled_date }: { id: number; scheduled_date?: string }) =>
      apiJson(`/sharing/videographer/ideas/${id}/like`, scheduled_date ? { scheduled_date } : {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vg-ideas", clientId] })
      qc.invalidateQueries({ queryKey: ["shoot-plan"] })
    },
  })
}

// "Skip": mark the suggestion as skipped (drops from the list).
export function useSkipIdea(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiJson(`/sharing/videographer/ideas/${id}/skip`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-ideas", clientId] }),
  })
}

// --- AI image generation (Phase 6, step 19) ---
// GATE 18: generation happens on the backend via Magnific/Freepik REST + API key (NO MCP). These
// hooks only trigger/list/approve the job. Generation awaits approval (status='pending'); management
// either approves (approved) or regenerates (8→4 loop).
export interface ImageGeneration {
  id: number
  client_id: number
  brief_id: number | null
  prompt: string | null
  refs: string[]
  settings: Record<string, unknown>
  asset_id: string | null
  result_url: string | null
  drive_file_id: string | null
  drive_file_name: string | null
  status: string
  created_at: string | null
  created_by: string | null
}

// Reference image source: a Drive file (file_id), a URL, or an inline upload (base64).
export type ImageRef = { kind: "drive" | "url" | "base64"; value: string; label?: string }

export interface ImageGenSettings {
  model?: string | null
  aspect_ratio?: string          // 4:5=social_post_4_5 | 9:16=social_story_9_16
  engine?: string                // automatic | magnific_illusio | _sharpy | _sparkle
  resolution?: string            // 1k | 2k | 4k
  structure_ref?: ImageRef | null
  style_ref?: ImageRef | null
  refine?: boolean
  prompt?: string
}

export interface MagnificCredits {
  available: number | null
  total_plan?: number | null
  spent?: number | null
  at?: string
}

// Remaining Magnific credits (top bar badge; management). The backend reads from cache —
// while refreshing=true it re-queries every 20s (the value settles once the refresh job finishes).
export function useMagnificCredits(enabled = true) {
  return useQuery<{ credits: MagnificCredits | null; refreshing: boolean }>({
    queryKey: ["magnific-credits"],
    enabled,
    queryFn: () => apiGet("/sharing/magnific-credits"),
    staleTime: 5 * 60_000,
    refetchInterval: (q) => (q.state.data?.refreshing ? 20_000 : false),
  })
}

// Generate 3 example image prompts from the brief ideas (in the claude worker, job+poll;
// does not spend Magnific credits). Result is job.result.examples.
export function usePromptExamples() {
  return useMutation({
    mutationFn: async (v: { client_id: number; brief_id: number }) => {
      const d = await apiJson("/sharing/image-gen/prompt-examples", v)
      const done = await pollJob(d.job.id)
      if (done.status === "failed") throw new Error(done.result?.error || "Failed to generate examples")
      return ((done.result as { examples?: string[] })?.examples ?? []) as string[]
    },
  })
}

// Convert the prompt into English + structured JSON (in the claude worker, job+poll).
export function useConvertPrompt() {
  return useMutation({
    mutationFn: async (v: { prompt: string }) => {
      const d = await apiJson("/sharing/image-gen/convert-prompt", v)
      const done = await pollJob(d.job.id)
      if (done.status === "failed") throw new Error(done.result?.error || "Conversion failed")
      const p = (done.result as { prompt?: string })?.prompt
      if (!p) throw new Error("Conversion returned an empty result")
      return p
    },
  })
}

// --- client brand assets (logo + fixed standard images) ---

export interface ClientAsset {
  id: number
  client_id: number
  kind: "logo" | "standard"
  file_id: string
  file_name: string | null
  mime_type: string | null
  file_size: number | null
  label: string | null
  uploaded_at: string | null
}

export function useClientAssets(clientId: number | null) {
  return useQuery<ClientAsset[]>({
    queryKey: ["client-assets", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/sharing/clients/${clientId}/assets`).then((d) => d.assets),
  })
}

export function useUploadClientAsset(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ file, kind, label }: { file: File; kind: "logo" | "standard"; label?: string }) => {
      const form = new FormData()
      form.append("kind", kind)
      if (label) form.append("label", label)
      form.append("file", file)
      return apiUpload(`/sharing/clients/${clientId}/assets`, form).then((d) => d.asset as ClientAsset)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["client-assets", clientId] }),
  })
}

export function useDeleteClientAsset(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (assetId: number) => apiDelete(`/sharing/clients/${clientId}/assets/${assetId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["client-assets", clientId] }),
  })
}

// Brand asset download (2026-08-04). DELIBERATELY not `driveDownloadUrl`: logos live in
// the agency account's folder, and the designer/videographer's own Drive has no access to
// that file — the Drive link wouldn't work for them. The backend downloads and streams it
// with the service account; authorization lives in the panel's role gate.
export function clientAssetDownloadUrl(clientId: number, assetId: number) {
  return `/api/sharing/clients/${clientId}/assets/${assetId}/download`
}

export function downloadClientAsset(clientId: number, assetId: number) {
  const a = document.createElement("a")
  a.href = clientAssetDownloadUrl(clientId, assetId)
  document.body.appendChild(a)
  a.click()
  a.remove()
}

export interface ImageGenBrief { id: number; week_iso: string; title: string | null; status?: string }

// AI image form: the client's brief list (including drafts, newest first).
export function useImageGenBriefs(clientId: number | null) {
  return useQuery<ImageGenBrief[]>({
    queryKey: ["image-gen-briefs", clientId],
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/image-gen/briefs?client_id=${clientId}`).then((d) => d.briefs),
  })
}

// The client's AI image generations (newest first; can be filtered with ?status).
export function useImageGenerations(clientId: number | null, status?: string) {
  return useQuery<ImageGeneration[]>({
    queryKey: ["image-generations", clientId, status ?? "all"],
    enabled: clientId != null,
    queryFn: () => {
      const qs = status ? `&status=${encodeURIComponent(status)}` : ""
      return apiGet(`/sharing/image-generations?client_id=${clientId}${qs}`).then(
        (d) => d.image_generations,
      )
    },
  })
}

// Manually trigger image generation → image_gen job; wait for the result with pollJob.
export function useGenerateImage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      client_id: number
      refs?: string[]
      brief_id?: number | null
      settings?: ImageGenSettings
    }) => apiJson("/sharing/image-generations/generate", body).then((d) => d.job as { id: number }),
    onSuccess: (_d, v) =>
      qc.invalidateQueries({ queryKey: ["image-generations", v.client_id] }),
  })
}

// Approve: set the generated image's status to 'approved'.
export function useApproveImage(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiJson(`/sharing/image-generations/${id}/approve`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["image-generations", clientId] }),
  })
}

// Regenerate: mark the existing generation as rejected + enqueue a new image_gen job.
export function useRegenerateImage(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      apiJson(`/sharing/image-generations/${id}/regenerate`, {}).then((d) => d.job as { id: number }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["image-generations", clientId] }),
  })
}

export function useDriveCounts(weekIso: string) {
  return useQuery<Record<string, number>>({
    queryKey: ["drive-counts", weekIso],
    queryFn: () =>
      apiGet(`/sharing/drive-counts?week_iso=${encodeURIComponent(weekIso)}`).then((d) => d.counts),
    staleTime: 60_000,
  })
}

// Card thumbnail URL (consumed via a same-origin <img>; protected by the session cookie).
export function thumbnailUrl(fileId: string, w = 300) {
  return `/api/sharing/thumbnail/${encodeURIComponent(fileId)}?w=${w}`
}

// Drive file page — the "Copy"/"Open in Drive" link. This link used to be derived
// manually in three separate places (VideoUploadCard, review.py, the approval page); this is now the single source.
// NOTE: for this link to open outside the agency, the file needs "anyone with the
// link" permission — both video upload (2026-07-25) and depot upload grant this.
export function driveFileUrl(fileId: string) {
  return `https://drive.google.com/file/d/${fileId}/view`
}

// Drive's DIRECT download address (2026-07-31). The only download path for files
// that have no `media_store` copy — e.g. the Videographer Depot deliberately doesn't
// use media_store (500 MB files stream directly to Drive, see depot.py).
// NOTE: Drive may interpose a virus-scan confirmation page for large files.
export function driveDownloadUrl(fileId: string) {
  return `https://drive.google.com/uc?export=download&id=${fileId}`
}

// PERMANENT DIRECT link (2026-07-30) — twin of `driveFileUrl`. Difference: opens the
// file ITSELF, not Drive's viewer PAGE (from our server's 21-day copy). Once the copy
// expires, the endpoint silently 302-redirects to Drive → the same link works forever,
// a copied link never dies.
// Public: does NOT require a panel session (the endpoint only serves videographer
// videos + shoot photos that are already public on Drive; see public_media.py).
// `window.location.origin` is required — the link copied to the clipboard must be absolute.
export function publicMediaUrl(fileId: string) {
  return `${window.location.origin}/m/${fileId}`
}

// Drive's EMBEDDED player (iframe) — for videos whose 21-day local copy has expired.
// `/view` opens the full-page Drive UI, `/preview` gives just the player.
// `frame-src ... https://drive.google.com` is already in the CSP.
// The video gets "anyone with the link can view" permission at upload time
// (sharing.upload), so the frame also opens outside the agency.
export function drivePreviewUrl(fileId: string) {
  return `https://drive.google.com/file/d/${fileId}/preview`
}

// Local original (21-day window) — for video playback / full resolution.
export function mediaUrl(fileId: string) {
  return `/api/sharing/media/${encodeURIComponent(fileId)}`
}

// Full-size download URL (?dl=1): the browser saves the file. If the local copy
// has expired, the backend pulls the original from Drive — download always works.
export function downloadMediaUrl(fileId: string, name?: string) {
  const q = name ? `?dl=1&name=${encodeURIComponent(name)}` : "?dl=1"
  return `/api/sharing/media/${encodeURIComponent(fileId)}${q}`
}
