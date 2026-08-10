// Müşteri sayfasındaki "İlgili fontlar" bölümü (2026-08-05).
//
// Havuzun müşteriye süzülmüş hâli: o müşteriye atanmış fontlar, MÜŞTERİ ADIYLA
// önizlenmiş (tasarımcı markanın adını o fontla görsün) + indir. Yetkili kullanıcı
// buradan havuzdan font ekleyebilir veya doğrudan dosya yükleyebilir — "önce
// havuza git, sonra ata" iki adımına zorlamamak için.
import { useRef, useState } from "react"
import { ChevronDown, Download, Loader2, Plus, Type, Upload, X } from "lucide-react"
import { toast } from "sonner"

import {
  downloadFont, sonucOzeti, useAssignFont, useClientFonts, useFonts, useUploadFont,
} from "@/lib/fonts"
import { FontPreview } from "@/components/fonts/FontPreview"
import { useAuth } from "@/lib/auth"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

export function ClientFonts({ clientId, clientName }: { clientId: number; clientName: string }) {
  const { user, isManagement } = useAuth()
  const yazabilir = isManagement || user?.role === "designer"
  const { data: fonts, isLoading } = useClientFonts(clientId)
  const yukle = useUploadFont()
  const [acik, setAcik] = useState(true)
  const [havuzAcik, setHavuzAcik] = useState(false)
  const dosyaRef = useRef<HTMLInputElement>(null)

  async function dosyaSec(files: File[]) {
    for (const file of files) {
      try {
        // client_id ile: yükleme ve atama tek istekte olur.
        const d = await yukle.mutateAsync({ file, clientId })
        toast.success(sonucOzeti(d))
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Yüklenemedi"
        toast[msg.includes("zaten havuzda") ? "info" : "error"](`${file.name}: ${msg}`)
      }
    }
  }

  const liste = fonts ?? []

  return (
    <div className="rounded-lg border">
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
        <button type="button" onClick={() => setAcik((a) => !a)}
          className="flex items-center gap-1.5 text-sm font-medium">
          <ChevronDown className={cn("h-4 w-4 transition-transform", !acik && "-rotate-90")} />
          <Type className="h-4 w-4 text-muted-foreground" />
          İlgili fontlar
          {liste.length > 0 && <Badge variant="outline">{liste.length}</Badge>}
        </button>
        {yazabilir && (
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => setHavuzAcik(true)}>
              <Plus className="mr-1 h-3.5 w-3.5" /> Havuzdan ekle
            </Button>
            <Button variant="ghost" size="sm" disabled={yukle.isPending}
              onClick={() => dosyaRef.current?.click()}>
              {yukle.isPending
                ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                : <Upload className="mr-1 h-3.5 w-3.5" />}
              Yükle
            </Button>
            <input ref={dosyaRef} type="file" multiple hidden
              accept=".ttf,.otf,.woff,.woff2,.zip,font/*,application/zip"
              onChange={(e) => { dosyaSec(Array.from(e.target.files ?? [])); e.target.value = "" }} />
          </div>
        )}
      </div>

      {acik && (
        <div className="space-y-2 p-3">
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : liste.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Bu müşteriye font atanmamış.{yazabilir ? " Havuzdan ekleyebilir veya yükleyebilirsin." : ""}
            </p>
          ) : (
            liste.map((f) => (
              <div key={f.id} className="space-y-1">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{f.family}</span>
                  <span className="text-muted-foreground">{f.style}</span>
                  <Badge variant="secondary" className="text-[10px] uppercase">{f.format}</Badge>
                  <Button variant="ghost" size="sm" className="ml-auto"
                    onClick={() => downloadFont(f)}>
                    <Download className="mr-1 h-3.5 w-3.5" /> İndir
                  </Button>
                </div>
                {/* Önizleme metni müşterinin ADI: tasarımcının ilk sorusu "marka
                    adı bu fontla nasıl duruyor" oluyor. */}
                <FontPreview font={f} text={clientName} size={28} />
              </div>
            ))
          )}
        </div>
      )}

      {havuzAcik && (
        <HavuzSecici clientId={clientId} onClose={() => setHavuzAcik(false)} />
      )}
    </div>
  )
}

function HavuzSecici({ clientId, onClose }: { clientId: number; onClose: () => void }) {
  const { data: fonts, isLoading } = useFonts()
  const ata = useAssignFont()
  const [ara, setAra] = useState("")

  const liste = (fonts ?? []).filter((f) =>
    !ara.trim() || trFold(`${f.family} ${f.style}`).includes(trFold(ara)))

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Havuzdan font ekle</DialogTitle>
        </DialogHeader>
        <Input value={ara} onChange={(e) => setAra(e.target.value)} placeholder="Font ara…" />
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : liste.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Havuzda font yok.</p>
        ) : (
          <ul className="space-y-1">
            {liste.map((f) => {
              const atanmis = f.clients.some((c) => c.id === clientId)
              return (
                <li key={f.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm">
                  <span className="truncate">
                    <span className="font-medium">{f.family}</span>{" "}
                    <span className="text-muted-foreground">{f.style}</span>
                  </span>
                  <Button variant={atanmis ? "ghost" : "outline"} size="sm" className="ml-auto"
                    disabled={ata.isPending}
                    onClick={() => ata.mutate({ id: f.id, clientId, assigned: !atanmis })}>
                    {atanmis ? <><X className="mr-1 h-3.5 w-3.5" /> Kaldır</>
                      : <><Plus className="mr-1 h-3.5 w-3.5" /> Ekle</>}
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}
