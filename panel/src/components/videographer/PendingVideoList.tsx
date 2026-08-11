// This week's videos (videographer page).
//
// 2026-07-25: videos WAITING to be shared + a button that copies the Drive link to the clipboard.
// 2026-07-30: the list now shows ALL of this week's videos (shared ones get a
// "Shared" badge), and clicking a row plays the video IN THE PANEL. Previously
// there was a `<video>` tag but no `controls`, just a frozen 48×80 px frame — meaning
// the videographer couldn't watch what they uploaded, they had to go to Drive.
//
// 2026-07-31 (2): a **Delete** button on the row — PERMANENT delete (product owner's
// decision; not soft-delete). Permission comes from the backend via `can_delete`:
// unconditional for management, only their own uploads for the videographer. A
// confirmation dialog is mandatory, with an extra warning for shared videos.
//
// 2026-07-31: a "Download" button was added to the row (via the authenticated
// `/api/sharing/media?dl=1` endpoint; if the local copy has expired, the backend
// fetches the original from Drive). What "Direct" gives is no longer the raw file
// but a mini page with a player + Download button (`public_media.py`) — so an
// outside person receiving the link can also download it.
//
// "Copy" works because uploading a video grants "anyone with the link can view"
// permission on Drive (sharing.upload).
import { useState } from "react"
import { AlertTriangle, Copy, Download, ExternalLink, Link2, Play, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { VideoPlayerDialog } from "@/components/videographer/VideoPlayerDialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useI18n } from "@/lib/i18n"
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

async function copyLink(fileId: string, t: (key: string) => string) {
  try {
    await navigator.clipboard.writeText(driveFileUrl(fileId))
    toast.success(t("components.videographer.pendingVideoList.driveLinkCopied"))
  } catch {
    toast.error(t("components.videographer.pendingVideoList.copyFailedPermission"))
  }
}

// Direct link: opens a mini page serving the video from our own server (player +
// "Download" button) — not Drive's viewer page. Whoever gets the link watches and
// downloads without a session; once the local copy expires, the page falls back to Drive.
async function copyDirect(fileId: string, t: (key: string) => string) {
  try {
    await navigator.clipboard.writeText(publicMediaUrl(fileId))
    toast.success(t("components.videographer.pendingVideoList.directLinkCopied"))
  } catch {
    toast.error(t("components.videographer.pendingVideoList.copyFailedPermission"))
  }
}

export function PendingVideoList({ videos }: { videos: VideoUpload[] }) {
  const { t } = useI18n()
  // The player only navigates between videos that have a file (arrow keys).
  const playable = videos.filter((v) => v.file_id)
  const [playing, setPlaying] = useState<number | null>(null)
  const [silinecek, setSilinecek] = useState<VideoUpload | null>(null)

  if (!videos.length) {
    return (
      <p className="px-1 py-2 text-sm text-muted-foreground">
        {t("components.videographer.pendingVideoList.noVideos")}
      </p>
    )
  }
  return (
    <div className="space-y-2">
      {videos.map((v) => {
        const idx = playable.findIndex((p) => p.id === v.id)
        return (
          <div key={v.id}
            className="flex items-center gap-3 rounded-md border bg-background px-2 py-2">
            {/* Preview + play button is a single target: keep the clickable area large. */}
            <button type="button" disabled={idx < 0} onClick={() => setPlaying(idx)}
              title={t("components.videographer.pendingVideoList.watchTitle")}
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
              <div className="truncate text-sm font-medium">
                {v.file_name || t("components.videographer.pendingVideoList.videoFallback")}
              </div>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span>{fmtWhen(v.uploaded_at)}</span>
                {v.shared ? (
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">
                    {t("components.videographer.pendingVideoList.sharedBadge")}
                  </span>
                ) : (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                    {t("components.videographer.pendingVideoList.pendingBadge")}
                  </span>
                )}
              </div>
            </button>

            {v.file_id && (
              // shrink-0 + its own box: four actions shouldn't crush the file name on narrow screens.
              <div className="flex shrink-0 items-center gap-1.5">
                {/* Download goes through the authenticated endpoint (`?dl=1`): if the
                    local copy has expired, the backend fetches the original from
                    Drive → full size always downloads. */}
                <a href={downloadMediaUrl(v.file_id, v.file_name ?? undefined)} download
                  title={t("components.videographer.pendingVideoList.downloadTitle")}
                  className="inline-flex h-8 items-center rounded-md border px-2.5 text-sm font-medium hover:bg-muted">
                  <Download className="mr-1 h-3.5 w-3.5" /> {t("components.videographer.pendingVideoList.downloadBtn")}
                </a>
                <Button size="sm" variant="outline" onClick={() => copyDirect(v.file_id!, t)}
                  title={t("components.videographer.pendingVideoList.directLinkTitle")}>
                  <Link2 className="mr-1 h-3.5 w-3.5" /> {t("components.videographer.pendingVideoList.directBtn")}
                </Button>
                <Button size="sm" variant="outline" onClick={() => copyLink(v.file_id!, t)}
                  title={t("components.videographer.pendingVideoList.driveLinkTitle")}>
                  <Copy className="mr-1 h-3.5 w-3.5" /> Drive
                </Button>
                <a href={driveFileUrl(v.file_id)} target="_blank" rel="noreferrer"
                  title={t("components.videographer.pendingVideoList.openInDriveTitle")}
                  className="text-muted-foreground hover:text-foreground">
                  <ExternalLink className="h-4 w-4" />
                </a>
                {/* Permission comes from the backend (`can_delete`) — the panel
                    doesn't rebuild the rule. Without permission the button isn't rendered at all. */}
                {v.can_delete && (
                  <button type="button" onClick={() => setSilinecek(v)}
                    title={t("components.videographer.pendingVideoList.deleteTitle")}
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

// Permanent-delete confirmation. Confirmation is MANDATORY: if it were soft-delete
// it could be undone, but here the panel record is really gone (the Drive copy sits
// in trash for 30 days — that's the only way back).
function SilOnayi({ video, onClose }: { video: VideoUpload; onClose: () => void }) {
  const { t } = useI18n()
  const del = useDeleteVideoUpload()

  function sil() {
    del.mutate(video.id, {
      onSuccess: (r) => {
        onClose()
        if (r?.drive_ok === false) {
          toast.warning(t("components.videographer.pendingVideoList.deletedDriveWarn"))
        } else {
          toast.success(t("components.videographer.pendingVideoList.deletedSuccess"))
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("components.videographer.pendingVideoList.deleteFailed")),
    })
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>{t("components.videographer.pendingVideoList.deleteDialogTitle")}</DialogTitle></DialogHeader>
        <p className="text-sm">
          <span className="font-medium">
            {video.file_name || t("components.videographer.pendingVideoList.videoFallback")}
          </span>{" "}
          {t("components.videographer.pendingVideoList.deleteBody")}
        </p>
        {/* Deleting a shared video is NOT BLOCKED (product owner's decision) — but the
            confirmation dialog must state the consequence: the item on the client's
            approval page breaks. */}
        {video.shared && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm dark:border-amber-900/50 dark:bg-amber-900/20">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <p>
              <span className="font-medium">{t("components.videographer.pendingVideoList.sharedWarningBold")}</span>{" "}
              {t("components.videographer.pendingVideoList.sharedWarningRest")}
            </p>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={del.isPending}>
            {t("components.videographer.pendingVideoList.cancelBtn")}
          </Button>
          <Button variant="destructive" onClick={sil} disabled={del.isPending}>
            {del.isPending
              ? t("components.videographer.pendingVideoList.deletingLabel")
              : t("components.videographer.pendingVideoList.deleteConfirmBtn")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
