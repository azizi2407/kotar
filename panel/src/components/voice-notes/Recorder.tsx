// Sesli not kaydedici (2026-08-09) — tek düğme, süre sayacı.
//
// Tarayıcı MediaRecorder biçimi cihaza göre değişiyor: Chrome/Android
// `audio/webm;codecs=opus`, Safari/iOS `audio/mp4`. Desteklenen İLK biçim
// seçilir; ffmpeg sunucuda ikisini de okuyor.
//
// Kayıt bitince otomatik yüklenir — kullanıcı ikinci bir düğmeye basmasın.
import { useEffect, useRef, useState } from "react"
import { Loader2, Mic, Square } from "lucide-react"
import { toast } from "sonner"

import { useUploadVoiceNote } from "@/lib/voice-notes"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const BICIMLER = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]

function desteklenenBicim() {
  if (typeof MediaRecorder === "undefined") return null
  return BICIMLER.find((b) => MediaRecorder.isTypeSupported(b)) ?? null
}

export function Recorder({ onUploaded }: { onUploaded?: (id: number) => void }) {
  const [kaydediyor, setKaydediyor] = useState(false)
  const [saniye, setSaniye] = useState(0)
  const [pct, setPct] = useState(0)
  const recRef = useRef<MediaRecorder | null>(null)
  const parcalarRef = useRef<Blob[]>([])
  const yukle = useUploadVoiceNote()

  useEffect(() => {
    if (!kaydediyor) return
    const t = setInterval(() => setSaniye((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [kaydediyor])

  // Sekme kapanırken mikrofonu bırak (kırmızı kayıt göstergesi asılı kalmasın).
  useEffect(() => () => {
    recRef.current?.stream.getTracks().forEach((t) => t.stop())
  }, [])

  async function basla() {
    const bicim = desteklenenBicim()
    if (!bicim) {
      toast.error("Bu tarayıcı ses kaydını desteklemiyor.")
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      toast.error("Mikrofon izni verilmedi. Tarayıcı ayarlarından izin ver.")
      return
    }
    parcalarRef.current = []
    const rec = new MediaRecorder(stream, { mimeType: bicim })
    rec.ondataavailable = (e) => { if (e.data.size) parcalarRef.current.push(e.data) }
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      const blob = new Blob(parcalarRef.current, { type: bicim })
      if (blob.size === 0) {
        toast.error("Kayıt boş — bir şey duyulmadı.")
        return
      }
      const uzanti = bicim.includes("mp4") ? "mp4" : bicim.includes("ogg") ? "ogg" : "webm"
      try {
        const d = await yukle.mutateAsync({
          blob, adi: `kayit.${uzanti}`, onProgress: setPct,
        })
        toast.success("Kayıt alındı, not hazırlanıyor…")
        onUploaded?.(d.note.id)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Yüklenemedi")
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
        <p className="text-sm text-muted-foreground">Yükleniyor… %{pct}</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          Konuşmaya başlamak için bas — bitince tekrar bas.
        </p>
      )}
    </div>
  )
}
