// Video player (2026-07-30) — lets the videographer watch their uploaded video in the panel.
//
// TWO SOURCES, one interface:
//   * `local` → the server's 21-day original, played via `<video controls>` (seeking
//     works thanks to the Range-capable endpoint).
//   * NOT `local` → Drive's embedded player (iframe). Once the local copy's 21 days
//     expire, proxying the video through the server would cost bandwidth + Drive API
//     quota; Drive gives its own player for free, and "anyone with the link" permission
//     on the video is already granted at upload time.
//
// Navigate between videos in the list with ←/→, Esc closes.
import { useCallback, useEffect } from "react"
import {
  ChevronLeft, ChevronRight, Copy, Download, ExternalLink, Link2, X,
} from "lucide-react"
import { toast } from "sonner"

import { useI18n } from "@/lib/i18n"
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
  const { t } = useI18n()
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
      toast.success(t("components.videographer.videoPlayerDialog.driveLinkCopied"))
    } catch {
      toast.error(t("components.videographer.videoPlayerDialog.copyFailedPermission"))
    }
  }

  // Direct link — a persistent page with a player + "Download" button (public_media.py);
  // doesn't require a session, falls back to Drive's embedded player once it expires.
  async function copyDirect() {
    try {
      await navigator.clipboard.writeText(publicMediaUrl(fid))
      toast.success(t("components.videographer.videoPlayerDialog.directLinkCopied"))
    } catch {
      toast.error(t("components.videographer.videoPlayerDialog.copyFailedPermission"))
    }
  }

  return (
    // Plain overlay instead of the Dialog component: inside base-ui Dialog, `<video>`'s
    // focus trap was swallowing keyboard controls (space/arrows).
    <div className="fixed inset-0 z-50 flex flex-col bg-black/90 p-3 sm:p-6"
      onClick={onClose}>
      {/* min-h-0: a flex item's default `min-height:auto` uses the content's natural
          size as a floor → a tall (portrait) video was pushing the box outside the
          viewport. Needed on EVERY flex box in the chain. */}
      <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col gap-2"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex shrink-0 items-center gap-2 text-white">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">
              {v.file_name || t("components.videographer.videoPlayerDialog.videoFallback")}
            </div>
            <div className="text-xs text-white/60">
              {fmtWhen(v.uploaded_at)}
              {videos.length > 1 && ` · ${index + 1}/${videos.length}`}
              {v.shared && ` · ${t("components.videographer.videoPlayerDialog.sharedSuffix")}`}
              {!v.local && ` · ${t("components.videographer.videoPlayerDialog.viaDriveSuffix")}`}
            </div>
          </div>
          {/* `variant="outline"` produced white background + white text on the dark
              overlay (unreadable) → manually styled to fit the dark background. */}
          {/* Download goes through a session-backed endpoint: even if `v.local` is
              false (21 days elapsed), the backend fetches the original from Drive
              and serves it — the button always works. */}
          <a href={downloadMediaUrl(fid, v.file_name ?? undefined)} download
            title={t("components.videographer.videoPlayerDialog.downloadTitle")}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20">
            <Download className="h-3.5 w-3.5" /> {t("components.videographer.videoPlayerDialog.downloadBtn")}
          </a>
          <button type="button" onClick={copyDirect}
            title={t("components.videographer.videoPlayerDialog.directLinkTitle")}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20">
            <Link2 className="h-3.5 w-3.5" /> {t("components.videographer.videoPlayerDialog.directBtn")}
          </button>
          <button type="button" onClick={copyLink} title={t("components.videographer.videoPlayerDialog.driveLinkTitle")}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-white/20">
            <Copy className="h-3.5 w-3.5" /> Drive
          </button>
          <a href={driveFileUrl(fid)} target="_blank" rel="noreferrer" title={t("components.videographer.videoPlayerDialog.openInDriveTitle")}
            className="rounded p-2 text-white/70 hover:bg-white/10 hover:text-white">
            <ExternalLink className="h-4 w-4" />
          </a>
          <button type="button" onClick={onClose} title={t("components.videographer.videoPlayerDialog.closeTitle")}
            className="rounded p-2 text-white/70 hover:bg-white/10 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg bg-black">
          {v.local ? (
            // `key`: when the source changed, the browser was reusing the same <video>
            // and kept playing the old video.
            <video key={fid} src={mediaUrl(fid)} controls autoPlay playsInline
              className="max-h-full max-w-full object-contain" />
          ) : (
            <iframe key={fid} src={drivePreviewUrl(fid)} allow="autoplay; fullscreen"
              title={v.file_name || "video"} className="h-full w-full border-0" />
          )}

          {videos.length > 1 && (
            <>
              <button type="button" onClick={() => go(-1)} disabled={index === 0}
                title={t("components.videographer.videoPlayerDialog.prevTitle")}
                className="absolute left-1 rounded-full bg-black/50 p-2 text-white disabled:opacity-25">
                <ChevronLeft className="h-6 w-6" />
              </button>
              <button type="button" onClick={() => go(1)} disabled={index === videos.length - 1}
                title={t("components.videographer.videoPlayerDialog.nextTitle")}
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
