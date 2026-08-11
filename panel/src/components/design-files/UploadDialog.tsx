// Working file upload dialog (2026-08-07) — operates in two modes:
// new file (requires a title + tags) and new version (note only).
//
// A progress bar is essential: a 1 GB file can take minutes on a slow connection,
// and a wait with no feedback makes the user think "the panel froze".
import { useRef, useState } from "react"
import { Loader2, Upload } from "lucide-react"
import { toast } from "sonner"

import { useUploadDesignFile, useUploadVersion } from "@/lib/design-files"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"

export function UploadDialog({ open, onClose, clientId, fileId, fileTitle }: {
  open: boolean
  onClose: () => void
  clientId: number
  /** If set, "new version" mode; if empty, a new file. */
  fileId?: number
  fileTitle?: string
}) {
  const { t } = useI18n()
  const yeniSurum = fileId != null
  const yeniDosya = useUploadDesignFile(clientId)
  const yeniSurumM = useUploadVersion()
  const [dosya, setDosya] = useState<File | null>(null)
  const [baslik, setBaslik] = useState("")
  const [etiketler, setEtiketler] = useState("")
  const [not, setNot] = useState("")
  const [pct, setPct] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const calisiyor = yeniDosya.isPending || yeniSurumM.isPending

  function kapat() {
    if (calisiyor) return
    setDosya(null); setBaslik(""); setEtiketler(""); setNot(""); setPct(0)
    onClose()
  }

  async function gonder() {
    if (!dosya) return
    try {
      if (yeniSurum) {
        await yeniSurumM.mutateAsync({ fileId: fileId!, file: dosya, note: not,
          onProgress: setPct })
      } else {
        const tags = etiketler.split(",").map((t) => t.trim()).filter(Boolean)
        await yeniDosya.mutateAsync({ file: dosya, title: baslik.trim(), tags, note: not,
          onProgress: setPct })
      }
      toast.success(yeniSurum ? t("components.designFiles.uploadDialog.versionUploaded") : t("components.designFiles.uploadDialog.fileUploaded"))
      kapat()
    } catch (e) {
      setPct(0)
      toast.error(e instanceof Error ? e.message : t("components.designFiles.uploadDialog.uploadFailed"))
    }
  }

  const gonderilebilir = dosya && (yeniSurum || baslik.trim()) && !calisiyor

  return (
    <Dialog open={open} onOpenChange={(o) => !o && kapat()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {yeniSurum ? t("components.designFiles.uploadDialog.newVersionTitle", { title: fileTitle ?? "" }) : t("components.designFiles.uploadDialog.newFileTitle")}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <input ref={inputRef} type="file" className="hidden"
            onChange={(e) => setDosya(e.target.files?.[0] ?? null)} />
          <Button type="button" variant="outline" className="w-full"
            onClick={() => inputRef.current?.click()} disabled={calisiyor}>
            <Upload className="mr-1 h-4 w-4" />
            {dosya ? dosya.name : t("components.designFiles.uploadDialog.pickFile")}
          </Button>

          {!yeniSurum && (
            <>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">{t("components.designFiles.uploadDialog.titleLabel")}</label>
                <Input value={baslik} onChange={(e) => setBaslik(e.target.value)}
                  placeholder={t("components.designFiles.uploadDialog.titlePlaceholder")} disabled={calisiyor} />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">
                  {t("components.designFiles.uploadDialog.tagsLabel")}
                </label>
                <Input value={etiketler} onChange={(e) => setEtiketler(e.target.value)}
                  placeholder={t("components.designFiles.uploadDialog.tagsPlaceholder")} disabled={calisiyor} />
              </div>
            </>
          )}

          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">
              {t("components.designFiles.uploadDialog.noteLabel")} {yeniSurum && t("components.designFiles.uploadDialog.noteVersionHint")}
            </label>
            <Input value={not} onChange={(e) => setNot(e.target.value)}
              placeholder={yeniSurum ? t("components.designFiles.uploadDialog.notePlaceholderVersion") : t("components.designFiles.uploadDialog.notePlaceholderFirst")}
              disabled={calisiyor} />
          </div>

          {calisiyor && (
            <div className="space-y-1">
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-primary transition-all"
                  style={{ width: `${pct}%` }} />
              </div>
              <p className="text-xs text-muted-foreground">{t("components.designFiles.uploadDialog.uploadingPct", { pct })}</p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={kapat} disabled={calisiyor}>{t("components.designFiles.uploadDialog.cancel")}</Button>
          <Button onClick={gonder} disabled={!gonderilebilir}>
            {calisiyor && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            {t("components.designFiles.uploadDialog.upload")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
