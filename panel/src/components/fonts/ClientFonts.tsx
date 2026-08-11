// "Related Fonts" section on the client page (2026-08-05).
//
// A client-filtered view of the pool: fonts assigned to that client, previewed
// WITH THE CLIENT'S NAME (so the designer sees the brand name in that font) + download.
// An authorized user can add a font from the pool here or upload a file directly —
// so they aren't forced into the two-step "first go to the pool, then assign".
import { useRef, useState } from "react"
import { ChevronDown, Download, Loader2, Plus, Type, Upload, X } from "lucide-react"
import { toast } from "sonner"

import {
  downloadFont, sonucOzeti, useAssignFont, useClientFonts, useFonts, useUploadFont,
} from "@/lib/fonts"
import { FontPreview } from "@/components/fonts/FontPreview"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
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
  const { t } = useI18n()
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
        // With client_id: upload and assignment happen in a single request.
        const d = await yukle.mutateAsync({ file, clientId })
        toast.success(sonucOzeti(d, t))
      } catch (e) {
        const msg = e instanceof Error ? e.message : t("components.fonts.clientFonts.uploadFailed")
        toast[msg.includes("already in the pool") ? "info" : "error"](`${file.name}: ${msg}`)
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
          {t("components.fonts.clientFonts.relatedFonts")}
          {liste.length > 0 && <Badge variant="outline">{liste.length}</Badge>}
        </button>
        {yazabilir && (
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => setHavuzAcik(true)}>
              <Plus className="mr-1 h-3.5 w-3.5" /> {t("components.fonts.clientFonts.addFromPool")}
            </Button>
            <Button variant="ghost" size="sm" disabled={yukle.isPending}
              onClick={() => dosyaRef.current?.click()}>
              {yukle.isPending
                ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                : <Upload className="mr-1 h-3.5 w-3.5" />}
              {t("components.fonts.clientFonts.upload")}
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
              {t("components.fonts.clientFonts.noFontsAssigned")}{yazabilir ? t("components.fonts.clientFonts.addOrUploadHint") : ""}
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
                    <Download className="mr-1 h-3.5 w-3.5" /> {t("components.fonts.clientFonts.download")}
                  </Button>
                </div>
                {/* The preview text is the client's NAME: the designer's first
                    question is always "how does the brand name look in this font". */}
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
  const { t } = useI18n()
  const { data: fonts, isLoading } = useFonts()
  const ata = useAssignFont()
  const [ara, setAra] = useState("")

  const liste = (fonts ?? []).filter((f) =>
    !ara.trim() || trFold(`${f.family} ${f.style}`).includes(trFold(ara)))

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("components.fonts.clientFonts.addFromPoolTitle")}</DialogTitle>
        </DialogHeader>
        <Input value={ara} onChange={(e) => setAra(e.target.value)} placeholder={t("components.fonts.clientFonts.searchPlaceholder")} />
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : liste.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">{t("components.fonts.clientFonts.poolEmpty")}</p>
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
                    {atanmis ? <><X className="mr-1 h-3.5 w-3.5" /> {t("components.fonts.clientFonts.remove")}</>
                      : <><Plus className="mr-1 h-3.5 w-3.5" /> {t("components.fonts.clientFonts.add")}</>}
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
