// Sesli not (2026-08-09) — ses → transkript → yapılandırılmış not.
// İşleme kuyrukta olduğu için liste ve tek not YOKLANIR (3 sn); WebSocket yok.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson, apiUpload } from "./api"

export type VoiceStatus = "queued" | "running" | "done" | "failed"

export interface Gorev {
  metin: string
  client_id: number | null
  assignee_sub: string | null
  due_date: string | null
}

export interface Structured {
  baslik: string
  ozet: string
  maddeler: string[]
  gorevler: Gorev[]
}

export interface VoiceNoteItem {
  id: number
  status: VoiceStatus
  baslik: string
  duration_sec: number | null
  file_size: number
  error: string | null
  created_at: string | null
}

export interface VoiceNoteFull extends VoiceNoteItem {
  transcript: string
  structured: Structured
  /** Panoya aktarılmış görevlerin anahtarları — tekrar eklemeyi engeller. */
  pushed_item_keys: string[]
}

/** İş bitene kadar yoklanır; biten notta yoklama durur (gereksiz istek yok). */
const YOKLAMA_MS = 3000

// `failed` GÖRÜNDÜKTEN SONRA da bir süre yoklamaya devam edilir: `jobqueue.fail`
// geçici hataları (kota aşımı, aşırı yük) en fazla 3 denemeye kadar backoff'la
// (60 sn + 300 sn = en kötü toplam 360 sn bekleme, üçüncü deneme kalıcı başarısız
// olursa artık requeue YOK) yeniden kuyruğa alır — bu sırada `voice_note_handler`
// notu tekrar `running`'e, sonra `done`'a çevirebilir. Burada yoklamayı hemen
// KESERSEK panel kalıcı "Not oluşturulamadı" gösterirken not arkada `done`'a
// dönebilir (kullanıcı hiç haberdar olmaz). Öte yandan GERÇEKTEN kalıcı
// başarısız olmuş bir notu SONSUZA dek yoklamak boşuna istek demek — üst sınır
// olarak not oluşturulduktan sonraki 15 dk'yı seçtik: en kötü backoff toplamının
// (360 sn) rahat üstünde, iki denemenin işlenme süresi payı da eklenmiş.
const FAILED_YOKLAMA_SINIRI_MS = 15 * 60 * 1000

/** Bu not hâlâ "taze" mi — yoklamaya değer mi? Liste ve tekil not sorgusu AYNI
 *  kuralı kullanır (ikisi ayrışırsa biri gereksiz yoklar, öteki erken keser). */
function _yoklamayaDeger(n: { status: VoiceStatus; created_at: string | null }) {
  if (n.status === "queued" || n.status === "running") return true
  if (n.status === "failed" && n.created_at) {
    return Date.now() - new Date(n.created_at).getTime() < FAILED_YOKLAMA_SINIRI_MS
  }
  return false
}

export function useVoiceNotes() {
  return useQuery<VoiceNoteItem[]>({
    queryKey: ["voice-notes"],
    queryFn: () => apiGet("/voice-notes").then((d) => d.notes),
    refetchInterval: (q) =>
      (q.state.data ?? []).some(_yoklamayaDeger) ? YOKLAMA_MS : false,
  })
}

export function useVoiceNote(id: number | null) {
  return useQuery<VoiceNoteFull>({
    queryKey: ["voice-note", id],
    enabled: id != null,
    queryFn: () => apiGet(`/voice-notes/${id}`).then((d) => d.note),
    refetchInterval: (q) =>
      q.state.data && _yoklamayaDeger(q.state.data) ? YOKLAMA_MS : false,
  })
}

function useInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["voice-notes"] })
    qc.invalidateQueries({ queryKey: ["voice-note"] })
  }
}

export function useUploadVoiceNote() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ blob, adi, onProgress }: {
      blob: Blob; adi: string; onProgress?: (pct: number) => void
    }) => {
      const form = new FormData()
      form.append("audio", blob, adi)
      // retry KAPALI: bu uç idempotent değil — her POST yeni bir not yaratır
      // (design-files yüklemesinde aynı gerekçe).
      return apiUpload("/voice-notes", form, onProgress, false) as Promise<{ note: VoiceNoteFull }>
    },
    onSuccess: tazele,
  })
}

export function usePatchVoiceNote() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ id, structured, pushedItemKeys }: {
      id: number; structured?: Structured; pushedItemKeys?: string[]
    }) => apiJson(`/voice-notes/${id}`,
      { structured, pushed_item_keys: pushedItemKeys }, "PATCH"),
    onSuccess: tazele,
  })
}

export function useDeleteVoiceNote() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/voice-notes/${id}`),
    onSuccess: tazele,
  })
}

export function voiceNoteAudioUrl(id: number) {
  return `/api/voice-notes/${id}/audio`
}

export function sureMetni(sn: number | null) {
  if (!sn && sn !== 0) return "—"
  const d = Math.floor(sn / 60)
  const s = sn % 60
  return `${d}:${String(s).padStart(2, "0")}`
}

export const DURUM_METNI: Record<VoiceStatus, string> = {
  queued: "sırada",
  running: "işleniyor",
  done: "hazır",
  failed: "başarısız",
}
