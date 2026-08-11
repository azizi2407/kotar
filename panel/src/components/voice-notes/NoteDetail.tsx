// Voice note body (2026-08-09): summary, bullet points, task suggestions, transcript.
//
// APPROVAL GATE: tasks the agent generates do NOT go to the board on their own. The
// user selects them, optionally corrects client/assignee/date, then clicks "Add to
// board". This is a repo invariant (AI output is born as a draft) and the user's
// established preference.
import { useMemo, useState } from "react"
import { ChevronDown, Loader2, Send, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { apiGet, apiJson } from "@/lib/api"
import { useClients, useUsers } from "@/lib/clients"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { DEFAULT_SIZE, type PlanningItem } from "@/lib/planlama"
import {
  DURUM_METNI_KEY, sureMetni, useDeleteVoiceNote, usePatchVoiceNote, useVoiceNote,
  voiceNoteAudioUrl, type Gorev,
} from "@/lib/voice-notes"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** The task key is derived from the task's CONTENT, NOT from the array index.
 *
 *  The previous version used `vn-<noteId>-<i>`: `normalize_structured` DROPS tasks
 *  with empty `metin` from the list, so indices can shift. Concrete bug: note 5 has
 *  A(0)/B(1)/C(2), C gets pushed to the board (`vn-5-2`), then A is deleted and
 *  saved, the list becomes [B,C], C is now index 1 → its key becomes `vn-5-1` →
 *  not found in `pushed_item_keys` → the checkbox reopens → the SAME task lands on
 *  the board a second time (or the reverse: the wrong item shows "on board ✓" and
 *  can never be added). A content-derived key stays STABLE even if the list shifts.
 *
 *  The hash is a plain djb2 + base36 — it doesn't produce Turkish characters,
 *  whitespace, +/-, so it's compatible with the board's `item_key` pattern
 *  ([A-Za-z0-9_-]{1,64}). If two tasks in the same note have IDENTICAL text, their
 *  keys collide; this is an acceptable edge case (identical text = already "the
 *  same task" from the user's perspective).
 *
 *  A SINGLE function: the same one is used both when writing "Add to board" and
 *  when checking "on board ✓" — if the two diverged, the keys wouldn't recognize
 *  each other. */
function _hashMetni(s: string) {
  let h = 5381
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0
  }
  return (h >>> 0).toString(36)
}

function gorevAnahtari(noteId: number, gorev: Gorev) {
  return `vn-${noteId}-${_hashMetni(gorev.metin)}`
}

export function NoteDetail({ noteId }: { noteId: number }) {
  const { t, lang } = useI18n()
  const { data: not, isLoading } = useVoiceNote(noteId)
  // useClients REQUIRES parameters (status/q) — its signature is useClients({status, q}).
  const { data: clients } = useClients({ status: "active", q: "" })
  const { data: users } = useUsers()
  const { user } = useAuth()
  const patch = usePatchVoiceNote()
  const sil = useDeleteVoiceNote()
  const [secili, setSecili] = useState<Set<number>>(new Set())
  const [duzeltme, setDuzeltme] = useState<Record<number, Gorev>>({})
  const [pano, setPano] = useState<string>("")
  const [transkriptAcik, setTranskriptAcik] = useState(false)
  const [gonderiliyor, setGonderiliyor] = useState(false)

  const panoSecenekleri = useMemo(() => [
    { key: `user:${user?.sub ?? ""}`, label: t("components.voiceNotes.noteDetail.myBoard") },
    { key: "management", label: t("components.voiceNotes.noteDetail.managementBoard") },
  ], [user?.sub, t])

  if (isLoading || !not) return <Skeleton className="h-64 w-full" />

  /** Deletion must be reachable in EVERY state.
   *
   *  The button used to only exist on the `done` branch; a `failed` record got
   *  permanently stuck in the panel (user-reported, 2026-08-09) and the only fix
   *  was touching the database directly. The server side already does NOT check
   *  status (soft-delete) — the only thing missing was the UI. This also covers
   *  `queued`/`running`: if the worker crashes, the record would stay stuck on
   *  "preparing" forever and couldn't be deleted. Deleting an in-flight job is
   *  harmless — the worker writes its result to the soft-deleted row, and the list
   *  already filters on `deleted_at`. */
  async function notuSil(id: number) {
    if (!confirm(t("components.voiceNotes.noteDetail.deleteConfirm"))) return
    try {
      await sil.mutateAsync(id)
      toast.success(t("components.voiceNotes.noteDetail.deleted"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.voiceNotes.noteDetail.deleteFailed"))
    }
  }

  if (not.status === "queued" || not.status === "running") {
    return (
      <div className="flex items-center justify-between gap-2 rounded-lg border p-6">
        <span className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("components.voiceNotes.noteDetail.preparing", { status: t(DURUM_METNI_KEY[not.status]) })}
        </span>
        <Button variant="ghost" size="sm" title={t("components.voiceNotes.noteDetail.deleteNote")} aria-label={t("components.voiceNotes.noteDetail.deleteNote")}
          onClick={() => void notuSil(not!.id)}>
          <Trash2 className="h-4 w-4 text-destructive" />
        </Button>
      </div>
    )
  }
  if (not.status === "failed") {
    return (
      <div className="space-y-2 rounded-lg border border-destructive/50 p-6">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="font-medium text-destructive">{t("components.voiceNotes.noteDetail.createFailed")}</p>
            <p className="text-sm text-muted-foreground">{not.error ?? t("components.voiceNotes.noteDetail.unknownError")}</p>
          </div>
          <Button variant="ghost" size="sm" title={t("components.voiceNotes.noteDetail.deleteNote")} aria-label={t("components.voiceNotes.noteDetail.deleteNote")}
            onClick={() => void notuSil(not!.id)}>
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
        {not.transcript && (
          <div className="rounded bg-muted/40 p-3 text-sm">{not.transcript}</div>
        )}
        {/* Audio STAYS: even if the transcript failed, the user should be able to
            listen to the recording, remember what they said, then delete it. */}
        <audio controls src={voiceNoteAudioUrl(not.id)} className="w-full" />
      </div>
    )
  }

  const gorevler = not.structured.gorevler
  const eklendi = new Set(not.pushed_item_keys)

  function gorevAl(i: number): Gorev {
    return duzeltme[i] ?? gorevler[i]
  }

  function guncelle(i: number, yama: Partial<Gorev>) {
    setDuzeltme((d) => ({ ...d, [i]: { ...gorevAl(i), ...yama } }))
  }

  async function panoyaEkle() {
    const hedef = pano || panoSecenekleri[0].key
    const secim = [...secili].filter((i) => !eklendi.has(gorevAnahtari(not!.id, gorevAl(i))))
    if (!secim.length) {
      toast.error(t("components.voiceNotes.noteDetail.noTaskSelected"))
      return
    }
    setGonderiliyor(true)
    try {
      // Placement: a fixed (40,40) base is NOT used — cards from successively added
      // notes would stack exactly on top of each other (they'd all start from the
      // same corner; the user would think their cards disappeared). Instead, the
      // target board's EXISTING items are read and new cards are placed below the
      // lowest item, in an empty spot. If reading the board fails (network/403), the
      // transfer is NOT blocked: it falls back to the old fixed (40,40) base — so the
      // user doesn't lose their task.
      let tabanY = 40
      try {
        const mevcutPano = await apiGet(`/planning/boards/${encodeURIComponent(hedef)}`) as
          { items: PlanningItem[] }
        if (mevcutPano.items.length) {
          const enAltKenar = Math.max(...mevcutPano.items.map((it) => {
            // height can be NULL (the type default is decided in the panel) — fall
            // back to the per-type default to avoid landing in undefined arithmetic.
            const yukseklik = it.height ?? DEFAULT_SIZE[it.type]?.height ?? 120
            return it.y + yukseklik
          }))
          tabanY = enAltKenar + 40 // gap between cards
        }
      } catch {
        // couldn't read the board — fall back to the fixed (40,40) base, don't block the transfer
      }
      // 200px spacing so cards don't stack on top of each other (board card width is ~240px).
      const upsert = secim.map((i, sira) => {
        const g = gorevAl(i)
        return {
          item_key: gorevAnahtari(not!.id, g),
          type: "card",
          title: g.metin,
          text: not!.structured.baslik
            ? t("components.voiceNotes.noteDetail.voiceNotePrefix", { title: not!.structured.baslik })
            : null,
          x: 40, y: tabanY + sira * 200,
          client_id: g.client_id, assignee_sub: g.assignee_sub, due_date: g.due_date,
        }
      })
      // The existing hook `usePlanningPatch(boardKey)` is PINNED to a boardKey; here
      // the target board is chosen at runtime, so apiJson is used directly (same
      // endpoint, same body). `base_version` is not sent: we're only adding NEW
      // items, not touching existing ones → no conflict.
      await apiJson(`/planning/boards/${encodeURIComponent(hedef)}/items`,
                    { upsert }, "PATCH")
      await patch.mutateAsync({
        id: not!.id,
        pushedItemKeys: secim.map((i) => gorevAnahtari(not!.id, gorevAl(i))),
      })
      setSecili(new Set())
      toast.success(t("components.voiceNotes.noteDetail.pushedToBoard", { count: secim.length }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.voiceNotes.noteDetail.pushFailed"))
    } finally {
      setGonderiliyor(false)
    }
  }

  async function duzeltmeyiKaydet() {
    const yeni = { ...not!.structured, gorevler: gorevler.map((_, i) => gorevAl(i)) }
    try {
      await patch.mutateAsync({ id: not!.id, structured: yeni })
      setDuzeltme({})
      toast.success(t("components.voiceNotes.noteDetail.updated"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.voiceNotes.noteDetail.updateFailed"))
    }
  }

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{not.structured.baslik || t("components.voiceNotes.noteDetail.voiceNoteFallback")}</h2>
          <p className="text-xs text-muted-foreground">
            {sureMetni(not.duration_sec)} · {new Date(not.created_at ?? "").toLocaleString(lang === "tr" ? "tr-TR" : "en-US")}
          </p>
        </div>
        <Button variant="ghost" size="sm" title={t("components.voiceNotes.noteDetail.deleteNote")} aria-label={t("components.voiceNotes.noteDetail.deleteNote")}
          onClick={() => void notuSil(not!.id)}>
          <Trash2 className="h-4 w-4 text-destructive" />
        </Button>
      </div>

      {not.structured.ozet && <p className="text-sm">{not.structured.ozet}</p>}

      {not.structured.maddeler.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {not.structured.maddeler.map((m, i) => <li key={i}>{m}</li>)}
        </ul>
      )}

      {gorevler.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">{t("components.voiceNotes.noteDetail.extractedTasks")}</p>
          {gorevler.map((_, i) => {
            const g = gorevAl(i)
            const anahtar = gorevAnahtari(not.id, g)
            const zatenEklendi = eklendi.has(anahtar)
            return (
              <div key={i} className={cn("flex flex-wrap items-center gap-2 rounded border p-2",
                zatenEklendi && "opacity-60")}>
                <input type="checkbox" disabled={zatenEklendi}
                  checked={secili.has(i)}
                  onChange={(e) => setSecili((s) => {
                    const n = new Set(s)
                    if (e.target.checked) n.add(i)
                    else n.delete(i)
                    return n
                  })} />
                <Input value={g.metin} className="h-8 min-w-48 flex-1"
                  onChange={(e) => guncelle(i, { metin: e.target.value })} />
                <select className="h-8 rounded border bg-background px-2 text-sm"
                  value={g.client_id ?? ""}
                  onChange={(e) => guncelle(i, {
                    client_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">{t("components.voiceNotes.noteDetail.noClient")}</option>
                  {(clients ?? []).map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
                <select className="h-8 rounded border bg-background px-2 text-sm"
                  value={g.assignee_sub ?? ""}
                  onChange={(e) => guncelle(i, { assignee_sub: e.target.value || null })}>
                  <option value="">{t("components.voiceNotes.noteDetail.noAssignee")}</option>
                  {(users ?? []).map((u) => (
                    <option key={u.sub} value={u.sub}>{u.name}</option>
                  ))}
                </select>
                <Input type="date" className="h-8 w-36" value={g.due_date ?? ""}
                  onChange={(e) => guncelle(i, { due_date: e.target.value || null })} />
                {zatenEklendi && <Badge variant="outline">{t("components.voiceNotes.noteDetail.onBoard")}</Badge>}
              </div>
            )
          })}

          <div className="flex flex-wrap items-center gap-2">
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={pano} onChange={(e) => setPano(e.target.value)}>
              {panoSecenekleri.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
            <Button onClick={panoyaEkle} disabled={gonderiliyor || secili.size === 0}>
              {gonderiliyor ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                : <Send className="mr-1 h-4 w-4" />}
              {t("components.voiceNotes.noteDetail.addToBoard", { count: secili.size })}
            </Button>
            {Object.keys(duzeltme).length > 0 && (
              <Button variant="outline" onClick={duzeltmeyiKaydet} disabled={patch.isPending}>
                {t("components.voiceNotes.noteDetail.saveCorrections")}
              </Button>
            )}
          </div>
        </div>
      )}

      <div>
        <button type="button" onClick={() => setTranskriptAcik((a) => !a)}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("h-4 w-4 transition-transform",
            !transkriptAcik && "-rotate-90")} />
          {t("components.voiceNotes.noteDetail.transcript")}
        </button>
        {transkriptAcik && (
          <div className="mt-2 whitespace-pre-wrap rounded bg-muted/40 p-3 text-sm">
            {not.transcript}
          </div>
        )}
      </div>

      <audio controls src={voiceNoteAudioUrl(not.id)} className="w-full" />
    </div>
  )
}
