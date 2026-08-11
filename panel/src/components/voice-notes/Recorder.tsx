// Voice note recorder (2026-08-09) — single button, duration counter.
//
// The browser's MediaRecorder format varies by device: Chrome/Android uses
// `audio/webm;codecs=opus`, Safari/iOS uses `audio/mp4`. The FIRST supported format
// is picked; ffmpeg on the server can read both.
//
// Uploads automatically once recording stops — the user shouldn't have to press a second button.
import { useEffect, useRef, useState } from "react"
import { Loader2, Mic, Square } from "lucide-react"
import { toast } from "sonner"

import { useUploadVoiceNote } from "@/lib/voice-notes"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const BICIMLER = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]

function desteklenenBicim() {
  if (typeof MediaRecorder === "undefined") return null
  return BICIMLER.find((b) => MediaRecorder.isTypeSupported(b)) ?? null
}

export function Recorder({ onUploaded }: { onUploaded?: (id: number) => void }) {
  const { t } = useI18n()
  const [kaydediyor, setKaydediyor] = useState(false)
  const [saniye, setSaniye] = useState(0)
  const [pct, setPct] = useState(0)
  const recRef = useRef<MediaRecorder | null>(null)
  const parcalarRef = useRef<Blob[]>([])
  const yukle = useUploadVoiceNote()

  useEffect(() => {
    if (!kaydediyor) return
    const iv = setInterval(() => setSaniye((s) => s + 1), 1000)
    return () => clearInterval(iv)
  }, [kaydediyor])

  // Release the microphone when the tab closes (so the red recording indicator doesn't stay stuck on).
  useEffect(() => () => {
    recRef.current?.stream.getTracks().forEach((track) => track.stop())
  }, [])

  async function basla() {
    const bicim = desteklenenBicim()
    if (!bicim) {
      toast.error(t("components.voiceNotes.recorder.notSupported"))
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      toast.error(t("components.voiceNotes.recorder.micDenied"))
      return
    }
    parcalarRef.current = []
    const rec = new MediaRecorder(stream, { mimeType: bicim })
    rec.ondataavailable = (e) => { if (e.data.size) parcalarRef.current.push(e.data) }
    rec.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop())
      const blob = new Blob(parcalarRef.current, { type: bicim })
      if (blob.size === 0) {
        toast.error(t("components.voiceNotes.recorder.emptyRecording"))
        return
      }
      const uzanti = bicim.includes("mp4") ? "mp4" : bicim.includes("ogg") ? "ogg" : "webm"
      try {
        const d = await yukle.mutateAsync({
          blob, adi: `kayit.${uzanti}`, onProgress: setPct,
        })
        toast.success(t("components.voiceNotes.recorder.recorded"))
        onUploaded?.(d.note.id)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t("components.voiceNotes.recorder.uploadFailed"))
      } finally {
        setPct(0)
      }
    }
    rec.start()
    recRef.current = rec
    setSaniye(0)
    setKaydediyor(true)
  }

  function dur() {
    recRef.current?.stop()
    recRef.current = null
    setKaydediyor(false)
  }

  const dk = Math.floor(saniye / 60)
  const sn = saniye % 60

  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border bg-card p-6">
      <Button
        size="lg"
        variant={kaydediyor ? "destructive" : "default"}
        disabled={yukle.isPending}
        onClick={kaydediyor ? dur : basla}
        className={cn("h-20 w-20 rounded-full", kaydediyor && "animate-pulse")}>
        {yukle.isPending ? <Loader2 className="h-8 w-8 animate-spin" />
          : kaydediyor ? <Square className="h-7 w-7" />
            : <Mic className="h-8 w-8" />}
      </Button>

      {kaydediyor ? (
        <p className="font-mono text-lg tabular-nums">
          {dk}:{String(sn).padStart(2, "0")}
        </p>
      ) : yukle.isPending ? (
        <p className="text-sm text-muted-foreground">{t("components.voiceNotes.recorder.uploadingPct", { pct })}</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          {t("components.voiceNotes.recorder.pressToStart")}
        </p>
      )}
    </div>
  )
}
