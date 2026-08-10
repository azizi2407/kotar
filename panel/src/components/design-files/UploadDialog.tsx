// Çalışma dosyası yükleme diyaloğu (2026-08-07) — iki kipte çalışır:
// yeni dosya (başlık + etiket ister) ve yeni sürüm (yalnız not).
//
// İlerleme çubuğu şart: 1 GB'lık dosya yavaş bağlantıda dakikalarca sürer,
// geri bildirimsiz bir bekleme kullanıcıya "panel dondu" dedirtir.
import { useRef, useState } from "react"
import { Loader2, Upload } from "lucide-react"
import { toast } from "sonner"

import { useUploadDesignFile, useUploadVersion } from "@/lib/design-files"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"

export function UploadDialog({ open, onClose, clientId, fileId, fileTitle }: {
  open: boolean
  onClose: () => void
  clientId: number
  /** Doluysa "yeni sürüm" kipi; boşsa yeni dosya. */
  fileId?: number
  fileTitle?: string
}) {
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
      toast.success(yeniSurum ? "Yeni sürüm yüklendi" : "Dosya yüklendi")
      kapat()
    } catch (e) {
      setPct(0)
      toast.error(e instanceof Error ? e.message : "Yüklenemedi")
    }
  }

  const gonderilebilir = dosya && (yeniSurum || baslik.trim()) && !calisiyor

  return (
    <Dialog open={open} onOpenChange={(o) => !o && kapat()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {yeniSurum ? `Yeni sürüm — ${fileTitle}` : "Yeni çalışma dosyası"}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <input ref={inputRef} type="file" className="hidden"
            onChange={(e) => setDosya(e.target.files?.[0] ?? null)} />
          <Button type="button" variant="outline" className="w-full"
            onClick={() => inputRef.current?.click()} disabled={calisiyor}>
            <Upload className="mr-1 h-4 w-4" />
            {dosya ? dosya.name : "Dosya seç (en fazla 1 GB)"}
          </Button>

          {!yeniSurum && (
            <>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Başlık</label>
                <Input value={baslik} onChange={(e) => setBaslik(e.target.value)}
                  placeholder="Ana Şablon" disabled={calisiyor} />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">
                  Etiketler (virgülle ayır)
                </label>
                <Input value={etiketler} onChange={(e) => setEtiketler(e.target.value)}
                  placeholder="şablon, kurumsal" disabled={calisiyor} />
              </div>
            </>
          )}

          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">
              Not {yeniSurum && "(bu sürümde ne değişti?)"}
            </label>
            <Input value={not} onChange={(e) => setNot(e.target.value)}
              placeholder={yeniSurum ? "logo güncellendi" : "ilk sürüm"}
              disabled={calisiyor} />
          </div>

          {calisiyor && (
            <div className="space-y-1">
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-primary transition-all"
                  style={{ width: `${pct}%` }} />
              </div>
              <p className="text-xs text-muted-foreground">Yükleniyor… %{pct}</p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={kapat} disabled={calisiyor}>Vazgeç</Button>
          <Button onClick={gonder} disabled={!gonderilebilir}>
            {calisiyor && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            Yükle
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
