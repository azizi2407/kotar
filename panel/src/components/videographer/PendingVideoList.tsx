// Haftanın videoları (videograf sayfası).
//
// 2026-07-25: paylaşılmayı BEKLEYEN videolar + Drive linkini panoya alan düğme.
// 2026-07-30: liste artık haftanın TÜM videolarını gösteriyor (paylaşılmışlar
// "Paylaşıldı" rozetiyle) ve satıra tıklayınca video PANELDE oynuyor. Eskiden
// `<video>` etiketi vardı ama `controls` yoktu ve 48×80 px'lik donuk bir kareydi
// — yani videograf yüklediği videoyu izleyemiyordu, Drive'a gitmesi gerekiyordu.
//
// 2026-07-31 (2): satıra **Sil** düğmesi — KALICI silme (proje sahibi kararı; soft-delete
// değil). Yetki backend'ten `can_delete` ile gelir: management ayrımsız, videograf
// yalnız kendi yüklediğini. Onay penceresi zorunlu, paylaşılmış videoda ek uyarı.
//
// 2026-07-31: satıra "İndir" düğmesi eklendi (oturumlu `/api/sharing/media?dl=1`;
// lokal kopya süresi dolmuşsa backend orijinali Drive'dan çeker). "Doğrudan"ın
// verdiği link artık ham dosya değil, oynatıcı + İndir düğmeli mini sayfa
// (`public_media.py`) — linki alan dış kişi de indirebilsin diye.
//
// "Kopyala" işe yarıyor çünkü video yüklemesi Drive'da "bağlantıya sahip herkes
// okuyabilir" izni veriyor (sharing.upload).
import { useState } from "react"
import { AlertTriangle, Copy, Download, ExternalLink, Link2, Play, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { VideoPlayerDialog } from "@/components/videographer/VideoPlayerDialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  downloadMediaUrl, driveFileUrl, publicMediaUrl, thumbnailUrl,
  useDeleteVideoUpload, type VideoUpload,
} from "@/lib/sharing"
import { cn } from "@/lib/utils"

function fmtWhen(iso: string | null) {
  if (!iso) return ""
  return new Date(iso).toLocaleString("tr-TR", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  })
}

async function copyLink(fileId: string) {
  try {
    await navigator.clipboard.writeText(driveFileUrl(fileId))
    toast.success("Drive bağlantısı kopyalandı")
  } catch {
    toast.error("Kopyalanamadı — tarayıcı izin vermedi")
  }
}

// Doğrudan bağlantı: videoyu kendi sunucumuzdan gösteren mini sayfayı açar
// (oynatıcı + "İndir" düğmesi) — Drive'ın görüntüleyici sayfasını değil. Linki alan
// kişi oturumsuz izler ve indirir; kopya süresi dolunca sayfa Drive'a düşer.
async function copyDirect(fileId: string) {
  try {
    await navigator.clipboard.writeText(publicMediaUrl(fileId))
    toast.success("Doğrudan bağlantı kopyalandı")
  } catch {
    toast.error("Kopyalanamadı — tarayıcı izin vermedi")
  }
}

export function PendingVideoList({ videos }: { videos: VideoUpload[] }) {
  // Oynatıcı yalnız dosyası olan videolar arasında gezinir (ok tuşları).
  const playable = videos.filter((v) => v.file_id)
  const [playing, setPlaying] = useState<number | null>(null)
  const [silinecek, setSilinecek] = useState<VideoUpload | null>(null)

  if (!videos.length) {
    return (
      <p className="px-1 py-2 text-sm text-muted-foreground">Bu hafta video yok.</p>
    )
  }
  return (
    <div className="space-y-2">
      {videos.map((v) => {
        const idx = playable.findIndex((p) => p.id === v.id)
        return (
          <div key={v.id}
            className="flex items-center gap-3 rounded-md border bg-background px-2 py-2">
            {/* Önizleme + oynat düğmesi tek hedef: tıklanabilir alan büyük olsun. */}
            <button type="button" disabled={idx < 0} onClick={() => setPlaying(idx)}
              title="İzle"
              className={cn("group relative h-12 w-20 shrink-0 overflow-hidden rounded bg-muted",
                            idx >= 0 && "cursor-pointer")}>
              {v.file_id && (
                <img src={thumbnailUrl(v.file_id, 160)} alt=""
                  className="h-full w-full object-cover" />
              )}
              {idx >= 0 && (
                <span className="absolute inset-0 flex items-center justify-center bg-black/30 transition-colors group-hover:bg-black/50">
                  <Play className="h-5 w-5 fill-white text-white" />
                </span>
              )}
            </button>

            <button type="button" disabled={idx < 0} onClick={() => setPlaying(idx)}
              className="min-w-0 flex-1 text-left">
              <div className="truncate text-sm font-medium">{v.file_name || "video"}</div>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span>{fmtWhen(v.uploaded_at)}</span>
                {v.shared ? (
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">
                    Paylaşıldı
                  </span>
                ) : (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">bekliyor</span>
                )}
              </div>
            </button>

            {v.file_id && (
              // shrink-0 + kendi kutusu: dört aksiyon dar ekranda dosya adını ezmesin.
              <div className="flex shrink-0 items-center gap-1.5">
                {/* İndirme oturumlu uçtan (`?dl=1`): lokal kopya süresi dolmuşsa
                    backend orijinali Drive'dan çeker → tam boyut her zaman iner. */}
                <a href={downloadMediaUrl(v.file_id, v.file_name ?? undefined)} download
                  title="Videoyu bilgisayara indir (tam boyut)"
                  className="inline-flex h-8 items-center rounded-md border px-2.5 text-sm font-medium hover:bg-muted">
                  <Download className="mr-1 h-3.5 w-3.5" /> İndir
                </a>
                <Button size="sm" variant="outline" onClick={() => copyDirect(v.file_id!)}
                  title="Kalıcı izleme/indirme sayfasının bağlantısı (oturum gerekmez; kopya süresi dolarsa Drive'a düşer)">
                  <Link2 className="mr-1 h-3.5 w-3.5" /> Doğrudan
                </Button>
                <Button size="sm" variant="outline" onClick={() => copyLink(v.file_id!)}
                  title="Drive dosya sayfasının bağlantısı">
                  <Copy className="mr-1 h-3.5 w-3.5" /> Drive
                </Button>
                <a href={driveFileUrl(v.file_id)} target="_blank" rel="noreferrer"
                  title="Drive'da aç"
                  className="text-muted-foreground hover:text-foreground">
                  <ExternalLink className="h-4 w-4" />
                </a>
                {/* Yetki backend'ten gelir (`can_delete`) — panel kuralı yeniden
                    kurmaz. Yetkisizde düğme hiç render edilmez. */}
                {v.can_delete && (
                  <button type="button" onClick={() => setSilinecek(v)} title="Videoyu kalıcı sil"
                    className="p-1 text-muted-foreground hover:text-destructive">
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            )}
          </div>
        )
      })}

      {playing !== null && (
        <VideoPlayerDialog videos={playable} index={playing}
          onIndex={setPlaying} onClose={() => setPlaying(null)} />
      )}

      {silinecek && (
        <SilOnayi video={silinecek} onClose={() => setSilinecek(null)} />
      )}
    </div>
  )
}

// Kalıcı silme onayı. Onay ZORUNLU: soft-delete olsaydı geri alınabilirdi, burada
// panel kaydı gerçekten gidiyor (Drive kopyası 30 gün çöpte durur, tek geri dönüş o).
function SilOnayi({ video, onClose }: { video: VideoUpload; onClose: () => void }) {
  const del = useDeleteVideoUpload()

  function sil() {
    del.mutate(video.id, {
      onSuccess: (r) => {
        onClose()
        if (r?.drive_ok === false) {
          toast.warning("Panelden silindi, Drive'dan kaldırılamadı — elle kontrol edin.")
        } else {
          toast.success("Video silindi (Drive çöp kutusunda 30 gün durur)")
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Silinemedi"),
    })
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>Videoyu sil</DialogTitle></DialogHeader>
        <p className="text-sm">
          <span className="font-medium">{video.file_name || "video"}</span> kalıcı olarak
          silinecek: panel kaydı ve sunucudaki kopya gider, Drive dosyası çöp kutusuna
          taşınır (30 gün geri alınabilir). Kopyalanmış doğrudan bağlantılar çalışmaz olur.
        </p>
        {/* Paylaşılmış videoda silme ENGELLENMİYOR (proje sahibi kararı) — ama onay
            penceresi sonucu söylemeli: müşterinin onay sayfasındaki öğe kırılır. */}
        {video.shared && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm dark:border-amber-900/50 dark:bg-amber-900/20">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <p>
              <span className="font-medium">Bu video müşteriyle paylaşıldı.</span> Silersen
              onay sayfasındaki karşılığı kırık kalır — önce paylaşımdan çıkarmak daha güvenli.
            </p>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={del.isPending}>Vazgeç</Button>
          <Button variant="destructive" onClick={sil} disabled={del.isPending}>
            {del.isPending ? "Siliniyor…" : "Kalıcı sil"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
