// Video oynatıcı (2026-07-30) — videograf yüklediği videoyu panelde izler.
//
// İKİ KAYNAK, tek arayüz:
//   * `local` → sunucudaki 21 günlük orijinal, `<video controls>` ile (Range
//     destekli uç sayesinde ileri sarma çalışır).
//   * `local` DEĞİL → Drive'ın gömülü oynatıcısı (iframe). Lokal kopyanın süresi
//     dolduğunda videoyu sunucudan proxy'lemek bant genişliği + Drive API kotası
//     harcardı; Drive kendi oynatıcısını bedavaya veriyor ve videoya "bağlantıya
//     sahip herkes" izni yükleme anında zaten verilmiş oluyor.
//
// Listedeki videolar arasında ←/→ ile geçilir, Esc kapatır.
import { useCallback, useEffect } from "react"
import {
  ChevronLeft, ChevronRight, Copy, Download, ExternalLink, Link2, X,
} from "lucide-react"
import { toast } from "sonner"

import {
  downloadMediaUrl, driveFileUrl, drivePreviewUrl, mediaUrl, publicMediaUrl,
  type VideoUpload,
} from "@/lib/sharing"

function fmtWhen(iso: string | null) {
  if (!iso) return ""
  return new Date(iso).toLocaleString("tr-TR", {
    day: "2-digit", month: "long", hour: "2-digit", minute: "2-digit",
  })
}

export function VideoPlayerDialog({ videos, index, onIndex, onClose }: {
  videos: VideoUpload[]
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const v = videos[index]
  const go = useCallback((delta: number) => {
    const next = index + delta
    if (next >= 0 && next < videos.length) onIndex(next)
  }, [index, videos.length, onIndex])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
      else if (e.key === "ArrowLeft") go(-1)
      else if (e.key === "ArrowRight") go(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [go, onClose])

  if (!v || !v.file_id) return null
  const fid = v.file_id

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(driveFileUrl(fid))
      toast.success("Drive bağlantısı kopyalandı")
    } catch {
      toast.error("Kopyalanamadı — tarayıcı izin vermedi")
    }
  }

  // Doğrudan bağlantı — oynatıcı + "İndir" düğmeli kalıcı sayfa (public_media.py);
  // oturum gerektirmez, süresi dolunca Drive gömülü oynatıcısına düşer.
  async function copyDirect() {
    try {
      await navigator.clipboard.writeText(publicMediaUrl(fid))
      toast.success("Doğrudan bağlantı kopyalandı")
    } catch {
      toast.error("Kopyalanamadı — tarayıcı izin vermedi")
    }
  }

  return (
    // Dialog bileşeni yerine düz overlay: base-ui Dialog içinde `<video>`
    // odak tuzağı yüzünden klavye kontrollerini (boşluk/oklar) yutuyor.
    <div className="fixed inset-0 z-50 flex flex-col bg-black/90 p-3 sm:p-6"
      onClick={onClose}>
      {/* min-h-0: flex item'ın varsayılan `min-height:auto`'su içeriğin doğal
          boyutunu taban yapar → uzun (dikey) video kutuyu viewport'un dışına
          taşırıyordu. Zincirdeki HER flex kutusuna gerekli. */}
      <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col gap-2"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex shrink-0 items-center gap-2 text-white">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{v.file_name || "video"}</div>
            <div className="text-xs text-white/60">
              {fmtWhen(v.uploaded_at)}
              {videos.length > 1 && ` · ${index + 1}/${videos.length}`}
              {v.shared && " · paylaşıldı"}
              {!v.local && " · Drive üzerinden"}
            </div>
          </div>
          {/* `variant="outline"` koyu overlay üzerinde beyaz zemin + beyaz metin
              veriyordu (okunmuyordu) → koyu zemine uygun elle stil. */}
          {/* İndirme oturumlu uçtan: `v.local` false olsa (21 gün doldu) bile
              backend orijinali Drive'dan çekip verir — düğme hep çalışır. */}
          <a href={downloadMediaUrl(fid, v.file_name ?? undefined)} download
            title="Videoyu bilgisayara indir (tam boyut)"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20">
            <Download className="h-3.5 w-3.5" /> İndir
          </a>
          <button type="button" onClick={copyDirect}
            title="Kalıcı izleme/indirme sayfasının bağlantısı (oturum gerekmez; süresi dolarsa Drive'a düşer)"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20">
            <Link2 className="h-3.5 w-3.5" /> Doğrudan
          </button>
          <button type="button" onClick={copyLink} title="Drive dosya sayfasının bağlantısı"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20">
            <Copy className="h-3.5 w-3.5" /> Drive
          </button>
          <a href={driveFileUrl(fid)} target="_blank" rel="noreferrer" title="Drive'da aç"
            className="rounded p-2 text-white/70 hover:bg-white/10 hover:text-white">
            <ExternalLink className="h-4 w-4" />
          </a>
          <button type="button" onClick={onClose} title="Kapat (Esc)"
            className="rounded p-2 text-white/70 hover:bg-white/10 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg bg-black">
          {v.local ? (
            // `key`: kaynak değişince tarayıcı aynı <video>'yu yeniden kullanıp
            // eski videoyu oynatmaya devam ediyordu.
            <video key={fid} src={mediaUrl(fid)} controls autoPlay playsInline
              className="max-h-full max-w-full object-contain" />
          ) : (
            <iframe key={fid} src={drivePreviewUrl(fid)} allow="autoplay; fullscreen"
              title={v.file_name || "video"} className="h-full w-full border-0" />
          )}

          {videos.length > 1 && (
            <>
              <button type="button" onClick={() => go(-1)} disabled={index === 0}
                title="Önceki (←)"
                className="absolute left-1 rounded-full bg-black/50 p-2 text-white disabled:opacity-25">
                <ChevronLeft className="h-6 w-6" />
              </button>
              <button type="button" onClick={() => go(1)} disabled={index === videos.length - 1}
                title="Sonraki (→)"
                className="absolute right-1 rounded-full bg-black/50 p-2 text-white disabled:opacity-25">
                <ChevronRight className="h-6 w-6" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
