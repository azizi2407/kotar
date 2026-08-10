// Sesli Not sayfası (2026-08-09) — /panel/sesli-not.
//
// Solda kayıt düğmesi + not listesi, sağda seçili notun gövdesi. Rol kapısı
// ROUTE'tan gelir (nav tablosunda roles: ["management"]); backend ayrıca zorlar.
import { useEffect, useState } from "react"
import { Mic } from "lucide-react"

import { DURUM_METNI, sureMetni, useVoiceNotes } from "@/lib/voice-notes"
import { NoteDetail } from "@/components/voice-notes/NoteDetail"
import { Recorder } from "@/components/voice-notes/Recorder"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export function VoiceNotePage() {
  const { data: notlar, isLoading } = useVoiceNotes()
  const [secili, setSecili] = useState<number | null>(null)

  // İlk not otomatik seçilsin — boş sağ panel kafa karıştırıyor. AYNI efekt
  // seçili notun listeden DÜŞTÜĞÜ durumu da temizler: not silinince liste
  // tazelenir ama `secili` eski id'yi tutmaya devam ederdi → `NoteDetail` artık
  // 404 veren bir id'yi yoklar ve sonsuza dek <Skeleton> gösterirdi (`enabled`
  // true kalır, veri hiç gelmez). `notlar` yüklenmeden (undefined) hiçbir şey
  // yapılmaz — henüz karar verecek veri yok.
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
          <Mic className="h-6 w-6" /> Sesli Not
        </h1>
        <p className="text-muted-foreground">
          Konuş, gerisini sistem yazsın. Çıkan görevleri planlama panona ekleyebilirsin.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-3">
          <Recorder onUploaded={setSecili} />
          {isLoading && <Skeleton className="h-24 w-full" />}
          {!isLoading && !notlar?.length && (
            <p className="text-sm text-muted-foreground">Henüz sesli not yok.</p>
          )}
          <div className="space-y-1">
            {(notlar ?? []).map((n) => (
              <button key={n.id} type="button" onClick={() => setSecili(n.id)}
                className={cn("w-full rounded-lg border p-2 text-left text-sm transition",
                  secili === n.id ? "border-primary bg-primary/5" : "hover:bg-muted/50")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">
                    {n.baslik || "(başlıksız)"}
                  </span>
                  {n.status !== "done" && (
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      {DURUM_METNI[n.status]}
                    </Badge>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  {sureMetni(n.duration_sec)} ·{" "}
                  {new Date(n.created_at ?? "").toLocaleDateString("tr-TR")}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div>
          {secili != null
            ? <NoteDetail noteId={secili} />
            : <p className="text-sm text-muted-foreground">Soldan bir not seç.</p>}
        </div>
      </div>
    </div>
  )
}
