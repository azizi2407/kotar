// Voice note (2026-08-09) — audio → transcript → structured note.
// Since processing happens in a queue, the list and single note are POLLED (3s); no WebSocket.
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
  /** Keys of tasks already pushed to the board — prevents re-adding them. */
  pushed_item_keys: string[]
}

/** Polled until the job finishes; polling stops once the note is done (no wasted requests). */
const YOKLAMA_MS = 3000

// Polling continues for a while EVEN AFTER `failed` is shown: `jobqueue.fail`
// requeues transient errors (quota exceeded, overload) with backoff up to 3
// attempts (60s + 300s = 360s worst-case total wait; once the third attempt
// fails permanently there's no more requeue) — during this time
// `voice_note_handler` can flip the note back to `running`, then `done`. If we
// STOPPED polling right away, the panel would keep showing "Note could not be
// created" while the note quietly turns `done` in the background (the user
// never finds out). On the other hand, polling a note that has REALLY failed
// permanently FOREVER is pointless requests — as an upper bound we picked 15
// minutes after note creation: comfortably above the worst-case backoff total
// (360s), with margin added for the processing time of the two attempts.
const FAILED_YOKLAMA_SINIRI_MS = 15 * 60 * 1000

/** Is this note still "fresh" — worth polling? The list and single-note query
 *  use the SAME rule (if they diverged, one would poll needlessly while the other cuts off too early). */
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
      // retry DISABLED: this endpoint is not idempotent — every POST creates a
      // new note (same reasoning as the design-files upload).
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

// Status → translation key (this file cannot use React hooks — see
// media-tools.ts "pages.voiceNote.status.*"). Label text is produced in the component with t().
export const DURUM_METNI_KEY: Record<VoiceStatus, string> = {
  queued: "pages.voiceNote.status.queued",
  running: "pages.voiceNote.status.running",
  done: "pages.voiceNote.status.done",
  failed: "pages.voiceNote.status.failed",
}
