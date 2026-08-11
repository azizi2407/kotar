// Voice Note page (2026-08-09) — /panel/sesli-not.
//
// Record button + note list on the left, the selected note's body on the right.
// The role gate comes from the ROUTE (roles: ["management"] in the nav table);
// the backend also enforces it.
import { useEffect, useState } from "react"
import { Mic } from "lucide-react"

import { DURUM_METNI_KEY, sureMetni, useVoiceNotes } from "@/lib/voice-notes"
import { NoteDetail } from "@/components/voice-notes/NoteDetail"
import { Recorder } from "@/components/voice-notes/Recorder"
import { useI18n } from "@/lib/i18n"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export function VoiceNotePage() {
  const { t, lang } = useI18n()
  const { data: notlar, isLoading } = useVoiceNotes()
  const [secili, setSecili] = useState<number | null>(null)

  // The first note is auto-selected — an empty right panel is confusing. The SAME
  // effect also cleans up the case where the selected note DROPS OUT of the list:
  // when a note is deleted the list refreshes, but `secili` used to keep holding
  // the old id → `NoteDetail` would then poll an id that returns 404 and show
  // <Skeleton> forever (`enabled` stays true, data never arrives). Nothing happens
  // before `notlar` has loaded (undefined) — there's no data to decide from yet.
  useEffect(() => {
    if (!notlar) return
    if (secili == null) {
      if (notlar.length) setSecili(notlar[0].id)
      return
    }
    if (!notlar.some((n) => n.id === secili)) setSecili(notlar[0]?.id ?? null)
  }, [notlar, secili])

  return (
    <div className="space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Mic className="h-6 w-6" /> {t("pages.voiceNote.title")}
        </h1>
        <p className="text-muted-foreground">
          {t("pages.voiceNote.subtitle")}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-3">
          <Recorder onUploaded={setSecili} />
          {isLoading && <Skeleton className="h-24 w-full" />}
          {!isLoading && !notlar?.length && (
            <p className="text-sm text-muted-foreground">{t("pages.voiceNote.noneYet")}</p>
          )}
          <div className="space-y-1">
            {(notlar ?? []).map((n) => (
              <button key={n.id} type="button" onClick={() => setSecili(n.id)}
                className={cn("w-full rounded-lg border p-2 text-left text-sm transition",
                  secili === n.id ? "border-primary bg-primary/5" : "hover:bg-muted/50")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">
                    {n.baslik || t("pages.voiceNote.untitled")}
                  </span>
                  {n.status !== "done" && (
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      {t(DURUM_METNI_KEY[n.status])}
                    </Badge>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  {sureMetni(n.duration_sec)} ·{" "}
                  {new Date(n.created_at ?? "").toLocaleDateString(lang === "tr" ? "tr-TR" : "en-US")}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div>
          {secili != null
            ? <NoteDetail noteId={secili} />
            : <p className="text-sm text-muted-foreground">{t("pages.voiceNote.selectFromLeft")}</p>}
        </div>
      </div>
    </div>
  )
}
