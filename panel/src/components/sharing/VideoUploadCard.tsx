// Board kart şeridinde bir video yükleme kartı (videografçının o hafta yüklediği
// video — 2026-07-21). Özel gün kartıyla aynı desen; mor/menekşe tema. Lokal kopya
// varken (ilk 21 gün) video kart içinde oynar; süresi dolunca Drive linkine döner.
import { Clapperboard } from "lucide-react"

import { mediaUrl, thumbnailUrl, type VideoUpload } from "@/lib/sharing"
import { cn } from "@/lib/utils"

const CARD_CLS =
  "flex shrink-0 flex-col overflow-hidden rounded-lg border border-violet-400/60 bg-violet-50 transition-colors hover:border-violet-500 dark:border-violet-500/40 dark:bg-violet-950/30"

function Header({ date }: { date: string | null }) {
  return (
    <div className="flex items-center gap-1 px-2 py-1 text-violet-700 dark:text-violet-400">
      <Clapperboard className="h-3.5 w-3.5 shrink-0" />
      <span className="text-[9px] font-semibold uppercase tracking-wide">Video</span>
      {date && <span className="ml-auto text-[9px] text-violet-600 dark:text-violet-400">{date}</span>}
    </div>
  )
}

export function VideoUploadCard({ vu, className }: { vu: VideoUpload; className?: string }) {
  const href = vu.file_id ? `https://drive.google.com/file/d/${vu.file_id}/view` : undefined
  const date = vu.uploaded_at
    ? new Date(vu.uploaded_at).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit" })
    : null

  if (vu.local && vu.file_id) {
    return (
      <div title={vu.file_name ?? "Video"} className={cn(CARD_CLS, className)}>
        <Header date={date} />
        <video
          src={mediaUrl(vu.file_id)}
          controls
          playsInline
          preload="metadata"
          className="min-h-0 w-full flex-1 bg-black object-contain"
        />
        <div className="flex items-center justify-between gap-1 px-2 py-1">
          <span className="line-clamp-1 text-[10px] leading-tight font-medium text-violet-900 dark:text-violet-200">
            {vu.file_name || "Video"}
          </span>
          {href && (
            <a href={href} target="_blank" rel="noreferrer"
              className="shrink-0 text-[9px] text-violet-600 underline dark:text-violet-400">
              Drive
            </a>
          )}
        </div>
      </div>
    )
  }

  return (
    <a href={href} target="_blank" rel="noreferrer" title={vu.file_name ?? "Video"}
      className={cn(CARD_CLS, className)}>
      <Header date={date} />
      {vu.file_id && (
        <img
          src={thumbnailUrl(vu.file_id, 200)}
          alt=""
          loading="lazy"
          className="h-14 w-full shrink-0 bg-violet-100 object-contain dark:bg-violet-900/40"
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none" }}
        />
      )}
      <div className="flex flex-1 items-end p-2">
        <span className="line-clamp-2 text-[11px] leading-tight font-medium text-violet-900 dark:text-violet-200">
          {vu.file_name || "Video"}
        </span>
      </div>
    </a>
  )
}
