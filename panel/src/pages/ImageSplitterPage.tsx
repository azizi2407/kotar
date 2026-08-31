// Image Splitter — splits a 3120×1350 wide image into 3 Instagram posts (1080×1350).
// Reels mode: all three become 1080×1920 video covers (with a play overlay) — the
// video tile's position (left/center/right) varies from post to post, so the user
// takes whichever one they need from the ZIP.
import { useRef, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Download, FileArchive, ImageDown, Upload, Film } from "lucide-react"
import { toast } from "sonner"

import { ApiError, apiUpload } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Switch } from "@/components/ui/switch"

interface Piece {
  name: string
  data_url: string
  is_cover?: boolean
}

interface SplitResult {
  pieces: Piece[]
  zip_name: string
  zip_data_url: string
}

export function ImageSplitterPage() {
  const { t } = useI18n()
  const fileInput = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<SplitResult | null>(null)
  const pieces = result?.pieces ?? []
  const [pct, setPct] = useState(0)
  const [reels, setReels] = useState(false)

  const split = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append("image", file)
      if (reels) form.append("mode", "reels")
      setPct(0)
      return apiUpload("/tools/image-split", form, setPct).then((d) => d as SplitResult)
    },
    onSuccess: (r) => {
      setResult(r)
      toast.success(reels ? t("pages.imageSplitter.reelsReady") : t("pages.imageSplitter.splitDone"))
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : t("pages.imageSplitter.failed")),
  })

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (file) { setResult(null); split.mutate(file) }
  }

  function download(p: Piece) {
    const a = document.createElement("a")
    a.href = p.data_url
    a.download = p.name
    a.click()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("pages.imageSplitter.title")}</h1>
        <p className="text-muted-foreground">
          {t("pages.imageSplitter.subtitle")}
        </p>
      </div>

      <label className="flex items-center gap-3 rounded-lg border p-3">
        <Switch checked={reels} onCheckedChange={setReels} disabled={split.isPending} />
        <div className="flex items-center gap-2">
          <Film className="h-4 w-4 text-muted-foreground" />
          <div>
            <div className="text-sm font-medium">{t("pages.imageSplitter.reelsToggleTitle")}</div>
            <div className="text-xs text-muted-foreground">
              {t("pages.imageSplitter.reelsToggleDesc")}
            </div>
          </div>
        </div>
      </label>

      <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-10 text-center">
        <ImageDown className="h-10 w-10 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          {reels ? t("pages.imageSplitter.pickReelsImage") : t("pages.imageSplitter.pickSplitImage")} (PNG/JPG, {t("pages.imageSplitter.exactSize")}).
        </p>
        <input ref={fileInput} type="file" accept="image/png,image/jpeg" className="hidden" onChange={onPick} />
        <Button onClick={() => fileInput.current?.click()} disabled={split.isPending}>
          <Upload className="mr-1 h-4 w-4" />
          {split.isPending ? t("pages.imageSplitter.processing") : t("pages.imageSplitter.pickImageButton")}
        </Button>
        {split.isPending && (
          <div className="w-full max-w-xs space-y-1">
            <Progress value={pct} />
            <p className="text-[11px] text-muted-foreground">{t("pages.imageSplitter.uploadingPct", { pct })}</p>
          </div>
        )}
      </div>

      {pieces.length > 0 && (
        <div className="flex justify-end">
          <Button onClick={() => {
            if (!result) return
            const a = document.createElement("a")
            a.href = result.zip_data_url
            a.download = result.zip_name
            a.click()
          }}>
            <FileArchive className="mr-1 h-4 w-4" /> {t("pages.imageSplitter.downloadZip")}
          </Button>
        </div>
      )}

      {pieces.length > 0 && (
        <div className="grid items-start gap-4 sm:grid-cols-3">
          {pieces.map((p, i) => (
            <div key={i} className="flex flex-col gap-2 rounded-lg border p-3">
              <div className="relative">
                <img src={p.data_url} alt={t("pages.imageSplitter.pieceAlt", { n: i + 1 })}
                  className="w-full rounded border bg-[repeating-conic-gradient(#e5e5e5_0_25%,#fff_0_50%)] bg-[length:16px_16px]" />
                {p.is_cover && (
                  <span className="absolute left-2 top-2 rounded bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground">
                    {t("pages.imageSplitter.videoCoverBadge")}
                  </span>
                )}
              </div>
              <Button variant="outline" size="sm" onClick={() => download(p)}>
                <Download className="mr-1 h-3.5 w-3.5" /> {p.is_cover ? t("pages.imageSplitter.downloadCover", { n: i + 1 }) : t("pages.imageSplitter.downloadPiece", { n: i + 1 })}
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
