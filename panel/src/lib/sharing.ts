// Sharing Board veri hook'ları (TanStack Query). Sözleşme: sharing.py.
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

// Bu haftaya düşen, müşterinin seçtiği özel gün (board kart şeridinde bilgi kartı).
export interface SpecialDayInWeek {
  event_id: number
  day_name: string | null
  date_label: string
  type: "day" | "week"
}

// Bu haftanın video yüklemesi — yönetim board'unda bilgi kartı (özel gün kartı gibi).
export interface VideoUpload {
  id: number
  file_id: string | null
  file_name: string | null
  uploaded_at: string | null
  local?: boolean
  /** Bu video bir paylaşım kartında kullanılmış mı (videograf listesinde rozet). */
  shared?: boolean
  /** Silme yetkisi BACKEND'te hesaplanır (management ayrımsız / videographer yalnız
   *  kendi yüklediği). Panel kuralı yeniden kurmaz — kural değişince ayrışırdı. */
  can_delete?: boolean
}

export interface BoardRow {
  client: { id: number; name: string; instagram_url: string | null }
  // Designer board'da satır "Müşterilerim" (true) / "Diğer Müşteriler" (false) ayrımı;
  // management görünümünde her zaman true.
  assigned: boolean
  special_days: SpecialDayInWeek[]
  shares: Share[]
  special_cards: SpecialCard[]
  priority: boolean
  published_count: number
  total_count: number
  upload_count: number
  // Ön-onay (yönetim iç kapısı) kararları
  pre_approved_count: number
  pre_revision_count: number
  // Onay sayfasındaki müşteri kararları (yükleme bazında, 2026-07-24)
  upload_approved_count: number
  upload_revision_count: number
  open_revision_count: number
  revision_share_ids: number[]
  video_uploads: VideoUpload[]
  /** Haftanın tüm video sayısı (backend türetir) */
  video_total_count?: number
  /** Paylaşılmayı BEKLEYEN gerçek sayı — video_pending 5 ile kırpılır, bu kırpılmaz */
  video_pending_count?: number
  /** Bekleyenlerin en yeni 5'i (paylaşılmış olanlar hariç) */
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
  // Bu dosya için ZATEN bir paylaşım kartı var mı (taslak dahil). ShareModal'ın
  // dosya seçicisi kullanılmışları gizler — aynı dosyaya ikinci kart açılmasın.
  used?: boolean
  // Elle taşındıysa geldiği hafta (müşteri medya sayfası rozeti). Yalnız
  // `/clients/:id/media` yanıtında dolu.
  moved_from_week_iso?: string | null
}

// Müşteri medya sayfasının hafta bloğu. Dosyası OLMAYAN haftalar da döner —
// sürükle-bırak hedefi olarak render edilirler.
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

// Yönetici board: sahip (OWNER_EMAIL) bir müşteriyi "benim" işaretler/kaldırır.
// Başarıda board'u tazele → gruplama (Müşterilerim/Diğer) yeniden hesaplanır.
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

// Videografçı video-yükleme board'u (management+videographer; assigned=shoot|edit atama).
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

// Müşteri medya sayfası: müşterinin TÜM yüklemeleri, hafta hafta gruplu.
// `useUploads`'un ikizi DEĞİL — o tek hafta + management-only (ShareModal'ın
// dosya seçici havuzu); bu tasarımcıya da açık ve boş hedef haftaları da içerir.
export function useClientMedia(clientId: number | null) {
  return useQuery<ClientMediaWeek[]>({
    queryKey: ["client-media", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/sharing/clients/${clientId}/media`).then((d) => d.weeks),
  })
}

// Seçili yüklemeleri hedef haftaya taşı (Drive klasöründe de). Kısmi başarı
// döndürebilir: `moved` sayısı + hata veren dosyaların `errors` listesi.
// Board sorguları da tazelenir — taşınan dosya orada hafta değiştirir.
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

// Yüklenmiş kartı sil — yalnız superadmin (backend zorlar). Soft-delete.
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
  // `uploads` da tazelenir (2026-07-29): her share mutasyonu dosyanın `used`
  // bayrağını değiştirir — kart açılınca dosya seçiciden düşmeli, kart silinince
  // geri gelmeli. Yalnız `board` tazelenirken "Kaydet ve Yeni" akışında az önce
  // kullanılan dosya listede kalmaya devam ediyordu.
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

// id çalışma anında belli olduğunda (yeni oluşturulan taslağı güncelleme) kullanılır.
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

// Caption üretim ayarları (Faz 1b) — müşteri-varsayılanı ⊕ üret-anı override
// sözleşmesi (bkz. ai_context.resolve_caption_settings). Tüm alanlar opsiyonel.
export interface CaptionSettings {
  model?: string | null
  tone?: string | null
  emoji_limit?: number | null
  hashtag_count?: number | null
  lang?: "TR" | "EN" | "TR+EN"
  use_brief?: boolean
  char_limit?: number | null
}

// Müşterinin caption üretim varsayılanları (client düzenleme yüzeyinde okunur/yazılır).
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

// Job'u done/failed olana dek poll et (caption üretimi async).
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
  throw new Error("Üretim zaman aşımına uğradı")
}

// Ön-onay linki (2026-07-24): tasarımcı üretir, yöneticiye gönderir. Yönetici
// onaylayınca içerik müşteri onay linkinde görünür hale gelir (kademeli kapı).
export function usePreApprovalLink() {
  return useMutation({
    mutationFn: (body: { client_id: number; week_iso: string }) =>
      apiJson("/sharing/pre-approval-link", body).then(
        (d) => ({ token: d.token as string, shareCount: (d.share_count ?? 0) as number }),
      ),
  })
}

// NOT: `useReviewLink` / `reviewShareMessage` 2026-08-06'da KALDIRILDI — eski
// otomatik kapsamlı "Onay Linki" düğmeleri her iki board'dan da çıktı, yerlerini
// aşağıdaki elle-seçim akışı aldı. Backend'deki `/sharing/review-link` ucu DURUYOR:
// `/review/<token>` sayfası ve ön-onay akışı canlı, dağıtılmış eski linkler çalışıyor.

// --- müşteri onay linki (elle seçim, 2026-08-06) ---
// Kaldırılan `useReviewLink` (müşteri, hafta) kapsamını otomatik gönderiyordu;
// bu akışta içerikleri modalda tek tek seçersin ve seçim linke dondurulur.

export interface ApprovalCandidate extends Upload {
  // Daha önce bir onay linkine konmuş mu — süzmez, yalnız rozet (revize sonrası
  // aynı tasarımı tekrar göndermek meşru).
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

// Müşterinin bu linklere yazdığı notu panelde okumanın tek yolu.
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

// Müşteriye gönderilen onay mesajı; içerik sayısı seçimden gelir (tekil/çoğul).
export function approvalShareMessage(url: string, count: number) {
  const noun = count > 1 ? "İçeriklerimiz" : "İçeriğimiz"
  return `Merhabalar, ${noun} hazır:\n${url}`
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

// --- çekim planı (videographer Kanban) ---

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

// Takvim görünümü: ayın etkinlikleri + her birini seçen/sahiplenen marka adları.
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

// Onay kapısı: taslak (AI üretimi) özel günü onayla → status='approved'.
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

// `includeDraft`: yalnız management'ta anlamlı (backend dışındaki roller için yok sayar) —
// taslak (AI üretimi, onaylanmamış) brief'i de görüp onaylayabilsin diye.
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

// Onay kapısı: taslak (AI üretimi) brief'i onayla → status='approved'.
export function useApproveBrief() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      apiJson(`/sharing/brief/${id}/approve`, {}).then((d) => d.brief as Brief),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brief"] }),
  })
}

// Hafta Notları writeback (Faz 3) — panel→DB kısmi merge (gönderilen anahtar üzerine yazılır).
// Sözleşme: sharing.py POST /brief/<id>/notes (yalnız management).
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

// Elle üret/yeniden üret (management) → job enqueue; sonucu `pollJob` ile bekle.
// `force`: brief zaten varsa üzerine yaz. Olmadan backend idempotent → hiçbir şey olmaz
// (2026-07-30 öncesi "Yeniden üret" düğmesinin sessizce işlevsiz olmasının nedeni buydu).
export function useGenerateBrief() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { client_id: number; week_iso: string; force?: boolean }) =>
      apiJson("/sharing/brief/generate", body).then((d) => d.job as { id: number }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["brief"] }),
  })
}

// Tüm müşterilerin drive sayısını TEK istekte al (board açılışta ~40 istek yerine).
// React Query aynı queryKey'i dedupler → tüm satırlar tek fetch paylaşır.
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
      // Her dosya AYRI istek: parti tek gövdede 512 MB istek tavanına çarpmaz
      // (47 foto → 413 vakası), kısmi hata izolasyonu ve gerçek toplam ilerleme sağlar.
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
          errors.push(`${f.name}: ${e instanceof Error ? e.message : "yükleme hatası"}`)
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

// Video yüklemesini KALICI sil (2026-07-31, proje sahibi kararı: soft-delete DEĞİL).
// Satır + bağlı onay kayıtları gerçekten silinir, sunucu kopyası diskten kalkar,
// Drive dosyası çöp kutusuna gider (30 gün geri alınabilir). `drive_ok:false`
// dönerse panel kaydı silinmiştir ama Drive dosyası elde kalmıştır.
export function useDeleteVideoUpload() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean; drive_ok: boolean }, Error, number>({
    mutationFn: (id: number) => apiDelete(`/sharing/videographer/uploads/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["videographer-board"] })
      qc.invalidateQueries({ queryKey: ["board"] })   // yönetim board'undaki video kartı
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

// Designer/management: fotoğrafı 'kullanıldı' işaretle/kaldır.
export function useMarkVgPhotoUsed() {
  const qc = useQueryClient()
  return useMutation<{ used: boolean }, Error, { id: number; used: boolean }>({
    mutationFn: ({ id, used }) =>
      apiJson(`/sharing/videographer/photos/${id}/used`, { used }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

// Fotoğrafı yeniden adlandır (Drive + DB). Uzantı backend'de korunur.
export function useRenameVgPhoto() {
  const qc = useQueryClient()
  return useMutation<{ file_name: string }, Error, { id: number; name: string }>({
    mutationFn: ({ id, name }) =>
      apiJson(`/sharing/videographer/photos/${id}/rename`, { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-photos"] }),
  })
}

// Tek fotoğrafı indir — GET ucu (CSRF gerekmez); anchor ile kaydettir.
export function downloadVgPhoto(id: number) {
  const a = document.createElement("a")
  a.href = `/api/sharing/videographer/photos/${id}/download`
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// Seçili fotoğrafları zip olarak indir (tarayıcıda anchor ile kaydettir).
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

// --- videographer öneri botu (Faz 5, step 17) ---

export interface VideographerIdea {
  id: number
  client_id: number
  reference_link: string | null
  reason: string | null
  shoot_idea: string | null
  status: string
  created_at: string | null
}

// Müşterinin AI trend-önerileri (varsayılan yalnız 'new' kartlar).
export function useVideographerIdeas(clientId: number | null) {
  return useQuery<VideographerIdea[]>({
    queryKey: ["vg-ideas", clientId],
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/videographer/ideas?client_id=${clientId}`).then((d) => d.ideas),
  })
}

// Öneri botunu elle tetikle (müşteri-tetikli) → videographer_ideas job; sonucu pollJob ile bekle.
export function useGenerateIdeas() {
  return useMutation({
    mutationFn: (clientId: number) =>
      apiJson("/sharing/videographer/ideas/generate", { client_id: clientId })
        .then((d) => d.job as { id: number }),
  })
}

// "Beğen → çekim listesine ekle": öneriden ShootTask üretir + öneriyi accepted işaretler.
// Hem öneri listesini hem çekim planını tazeler (useShootMutations domain'i).
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

// "Atla": öneriyi skipped işaretle (listeden düşer).
export function useSkipIdea(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiJson(`/sharing/videographer/ideas/${id}/skip`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vg-ideas", clientId] }),
  })
}

// --- AI görsel üretimi (Faz 6, step 19) ---
// GATE 18: üretim backend'de Magnific/Freepik REST + API-key ile (MCP YOK). Bu hook'lar
// yalnız işi tetikler/listeler/onaylar. Üretim onay bekler (status='pending'); management
// onaylar (approved) ya da yeniden üretir (8→4 döngü).
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

// Referans görsel kaynağı: Drive dosyası (file_id), URL, veya anında yükleme (base64).
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

// Kalan Magnific kredisi (üst bar rozeti; management). Backend cache'ten okur —
// refreshing=true iken 20 sn'de bir yeniden sorar (tazeleme job'u bitince değer oturur).
export function useMagnificCredits(enabled = true) {
  return useQuery<{ credits: MagnificCredits | null; refreshing: boolean }>({
    queryKey: ["magnific-credits"],
    enabled,
    queryFn: () => apiGet("/sharing/magnific-credits"),
    staleTime: 5 * 60_000,
    refetchInterval: (q) => (q.state.data?.refreshing ? 20_000 : false),
  })
}

// Brief fikirlerinden 3 örnek görsel istemi üret (claude worker'da, job+poll; Magnific
// kredisi harcamaz). Sonuç job.result.examples.
export function usePromptExamples() {
  return useMutation({
    mutationFn: async (v: { client_id: number; brief_id: number }) => {
      const d = await apiJson("/sharing/image-gen/prompt-examples", v)
      const done = await pollJob(d.job.id)
      if (done.status === "failed") throw new Error(done.result?.error || "Örnekler üretilemedi")
      return ((done.result as { examples?: string[] })?.examples ?? []) as string[]
    },
  })
}

// İstemi İngilizce + yapılandırılmış JSON'a dönüştür (claude worker'da, job+poll).
export function useConvertPrompt() {
  return useMutation({
    mutationFn: async (v: { prompt: string }) => {
      const d = await apiJson("/sharing/image-gen/convert-prompt", v)
      const done = await pollJob(d.job.id)
      if (done.status === "failed") throw new Error(done.result?.error || "Dönüşüm başarısız")
      const p = (done.result as { prompt?: string })?.prompt
      if (!p) throw new Error("Dönüşüm boş sonuç döndürdü")
      return p
    },
  })
}

// --- müşteri marka görselleri (logo + sabit standart görseller) ---

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

// Marka görseli indirme (2026-08-04). BİLEREK `driveDownloadUrl` değil: logolar
// ajans hesabının klasöründe, tasarımcı/videografın kendi Drive'ında o dosyaya
// erişimi yok — Drive linki onlarda çalışmaz. Backend servis hesabıyla indirip
// stream eder, yetki panelin rol kapısında.
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

// AI görsel formu: müşterinin brief listesi (taslak dahil, en yeni önce).
export function useImageGenBriefs(clientId: number | null) {
  return useQuery<ImageGenBrief[]>({
    queryKey: ["image-gen-briefs", clientId],
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/image-gen/briefs?client_id=${clientId}`).then((d) => d.briefs),
  })
}

// Müşterinin AI görsel üretimleri (en yeni önce; ?status ile filtrelenebilir).
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

// Görsel üretimini elle tetikle → image_gen job; sonucu pollJob ile bekle.
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

// Onayla: üretilen görseli status='approved' yap.
export function useApproveImage(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiJson(`/sharing/image-generations/${id}/approve`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["image-generations", clientId] }),
  })
}

// Yeniden üret: mevcut üretimi rejected işaretle + yeni image_gen job kuyruğa al.
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

// Kart thumbnail URL'i (aynı origin <img> ile tüketilir; oturum cookie'siyle korunur).
export function thumbnailUrl(fileId: string, w = 300) {
  return `/api/sharing/thumbnail/${encodeURIComponent(fileId)}?w=${w}`
}

// Drive dosya sayfası — "Kopyala"/"Drive'da aç" bağlantısı. Link üç ayrı yerde
// elle türetiliyordu (VideoUploadCard, review.py, onay sayfası); tek yer burası.
// NOT: bu linkin ajans dışında açılması için dosyada "bağlantıya sahip herkes"
// izni olmalı — video yüklemesi (2026-07-25) ve depo yüklemesi bunu veriyor.
export function driveFileUrl(fileId: string) {
  return `https://drive.google.com/file/d/${fileId}/view`
}

// Drive'ın DOĞRUDAN indirme adresi (2026-07-31). `media_store` kopyası olmayan
// dosyalar için tek indirme yolu — örn. Videograf Deposu bilerek media_store
// kullanmıyor (500 MB'lık dosyalar akıştan Drive'a gider, bkz. depot.py).
// NOT: Drive büyük dosyalarda araya virüs-taraması onay sayfası koyabilir.
export function driveDownloadUrl(fileId: string) {
  return `https://drive.google.com/uc?export=download&id=${fileId}`
}

// KALICI DOĞRUDAN bağlantı (2026-07-30) — `driveFileUrl`'ün ikizi. Fark: Drive'ın
// görüntüleyici SAYFASINI değil, dosyanın KENDİSİNİ açar (sunucumuzdaki 21 günlük
// kopyadan). Kopya süresi dolunca uç sessizce Drive'a 302 yönlendirir → aynı link
// ömür boyu çalışır, kopyalanmış bir bağlantı asla ölmez.
// Public: panel oturumu GEREKMEZ (uç yalnız Drive'da zaten herkese-açık olan videograf
// videolarını + çekim fotoğraflarını servis eder; bkz. public_media.py).
// `window.location.origin` şart — panoya kopyalanan link mutlak olmalı.
export function publicMediaUrl(fileId: string) {
  return `${window.location.origin}/m/${fileId}`
}

// Drive'ın GÖMÜLÜ oynatıcısı (iframe) — 21 günlük lokal kopya süresi dolmuş
// videolar için. `/view` tam sayfa Drive arayüzü açar, `/preview` yalnız
// oynatıcıyı verir. CSP'de `frame-src ... https://drive.google.com` zaten var.
// Videoya "bağlantıya sahip herkes okuyabilir" izni yükleme anında veriliyor
// (sharing.upload), o yüzden çerçeve ajans dışında da açılır.
export function drivePreviewUrl(fileId: string) {
  return `https://drive.google.com/file/d/${fileId}/preview`
}

// Lokal orijinal (21 gün penceresi) — video oynatma / tam çözünürlük için.
export function mediaUrl(fileId: string) {
  return `/api/sharing/media/${encodeURIComponent(fileId)}`
}

// Tam boyut indirme URL'i (?dl=1): tarayıcı dosyayı kaydeder. Lokal kopya
// süresi dolmuşsa backend Drive'dan orijinali çeker — indirme her zaman çalışır.
export function downloadMediaUrl(fileId: string, name?: string) {
  const q = name ? `?dl=1&name=${encodeURIComponent(name)}` : "?dl=1"
  return `/api/sharing/media/${encodeURIComponent(fileId)}${q}`
}
