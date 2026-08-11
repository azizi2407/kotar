// Font pool (2026-08-05, product owner's request) — `/fontlar`.
//
// It does two things: gathers fonts in one place (downloadable) and shows **how the
// typed text looks in those fonts**. Fonts are grouped by family; the same family's
// Regular/Bold/Italic files stack so weights can be compared.
//
// The role gate comes from the ROUTE (AppLayout nav table: four production roles).
// Write actions (upload/assign/rename) are also hidden here with `isManagement ||
// designer` — the backend already enforces it, this just reduces UI noise. DELETE is
// separate: the flag comes from the backend per font (`can_delete`) — a designer can
// only remove what they uploaded themselves (2026-08-06).
import { useMemo, useRef, useState } from "react"
import {
  Check, Columns2, Download, Loader2, Pencil, Search, Trash2, Type, Upload, Users,
} from "lucide-react"
import { toast } from "sonner"

import {
  downloadFont, formatSize, sonucOzeti, useAssignFont, useDeleteFont, useFonts,
  useRenameFont, useUploadFont, type FontItem,
} from "@/lib/fonts"
import { useClients } from "@/lib/clients"
import { FontPreview } from "@/components/fonts/FontPreview"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const ILK_GORUNEN = 10
const SAYFA = 20

export function FontsPage() {
  const { t } = useI18n()
  const { user, isManagement } = useAuth()
  const yazabilir = isManagement || user?.role === "designer"

  const { data: fonts, isLoading } = useFonts()
  const { data: clients } = useClients({ status: "active", q: "" })
  const yukle = useUploadFont()
  const sil = useDeleteFont()
  const ata = useAssignFont()
  const adlandir = useRenameFont()

  const [metin, setMetin] = useState(() => t("pages.fonts.defaultPreviewText"))
  const [punto, setPunto] = useState(32)
  const [koyu, setKoyu] = useState(false)
  const [ara, setAra] = useState("")
  // The pool can exceed 200 fonts; rendering all of them on first open strains both
  // the eye and the browser (each row downloads a separate font file). Default is 10, user expands it.
  const [limit, setLimit] = useState(ILK_GORUNEN)
  // Compare mode: a separate mode so selected fonts appear side by side on one screen
  // with the SAME text and size — otherwise you'd have to keep scrolling a long list.
  const [secili, setSecili] = useState<Set<number>>(new Set())
  const [karsilastir, setKarsilastir] = useState(false)
  const dosyaRef = useRef<HTMLInputElement>(null)

  function secimDegistir(id: number) {
    setSecili((s) => {
      const yeni = new Set(s)
      if (yeni.has(id)) yeni.delete(id)
      else yeni.add(id)
      return yeni
    })
  }

  // Two separate modes: in NORMAL mode search + limit are applied; in COMPARE mode
  // neither is — the user already wants to see exactly what they selected.
  const suzulen = useMemo(() => {
    // Search is NEVER applied in compare mode: the mode means "show everything I
    // selected", and a filter hiding part of the selection would break that promise.
    // The search box is also disabled at that point (see `disabled` below), so this
    // isn't a silent ignore.
    if (karsilastir) return (fonts ?? []).filter((f) => secili.has(f.id))
    return (fonts ?? []).filter((f) => {
      if (!ara.trim()) return true
      const needle = trFold(ara)
      return trFold(`${f.family} ${f.style}`).includes(needle)
        || f.clients.some((c) => trFold(c.name).includes(needle))
    })
  }, [fonts, ara, karsilastir, secili])

  const gosterilecek = karsilastir ? suzulen : suzulen.slice(0, limit)
  const kalan = suzulen.length - gosterilecek.length

  // Group by family; styles within a group aren't alphabetical — they stay in
  // natural order since the backend returns them ordered by (family, style).
  const gruplar = useMemo(() => {
    const map = new Map<string, FontItem[]>()
    gosterilecek.forEach((f) => map.set(f.family, [...(map.get(f.family) ?? []), f]))
    return [...map.entries()]
  }, [gosterilecek])

  async function dosyaSec(files: File[]) {
    for (const file of files) {
      try {
        const d = await yukle.mutateAsync({ file })
        toast.success(sonucOzeti(d, t))
      } catch (e) {
        // 409 = same file already in the pool; this isn't an error, it's informational.
        const msg = e instanceof Error ? e.message : t("pages.fonts.uploadFailed")
        toast[msg.includes("already in the pool") ? "info" : "error"](`${file.name}: ${msg}`)
      }
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("pages.fonts.title")}</h1>
          <p className="text-muted-foreground">
            {t("pages.fonts.subtitle")}
          </p>
        </div>
        {yazabilir && (
          <>
            <Button size="sm" disabled={yukle.isPending}
              onClick={() => dosyaRef.current?.click()}>
              {yukle.isPending
                ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                : <Upload className="mr-1 h-4 w-4" />}
              {t("pages.fonts.uploadButton")}
            </Button>
            <input ref={dosyaRef} type="file" multiple hidden
              accept=".ttf,.otf,.woff,.woff2,.zip,font/*,application/zip"
              onChange={(e) => {
                dosyaSec(Array.from(e.target.files ?? []))
                e.target.value = ""
              }} />
          </>
        )}
      </div>

      {/* preview controls — stay accessible while the page scrolls */}
      <div className="sticky top-2 z-20 space-y-2 rounded-lg border bg-background/95 p-3 shadow-sm backdrop-blur">
        <div className="flex items-center gap-2">
          <Type className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input value={metin} onChange={(e) => setMetin(e.target.value)}
            placeholder={t("pages.fonts.previewPlaceholder")} className="flex-1" />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            {t("pages.fonts.fontSize")}
            <input type="range" min={12} max={96} value={punto} className="accent-primary"
              onChange={(e) => setPunto(Number(e.target.value))} />
            <span className="w-10 tabular-nums text-foreground">{punto}px</span>
          </label>
          <Button variant="outline" size="sm" onClick={() => setKoyu((k) => !k)}>
            {koyu ? t("pages.fonts.lightBg") : t("pages.fonts.darkBg")}
          </Button>
          <div className="relative ml-auto w-52">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={ara} onChange={(e) => setAra(e.target.value)}
              disabled={karsilastir}
              placeholder={karsilastir ? t("pages.fonts.searchDisabledInCompare") : t("pages.fonts.searchPlaceholder")}
              className="pl-7" />
          </div>
        </div>
      </div>

      {/* selection strip — visible only when something is selected, so it doesn't take up space needlessly */}
      {secili.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-accent/40 p-2">
          <Check className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">{t("pages.fonts.selectedCount", { count: secili.size })}</span>
          <Button size="sm" variant={karsilastir ? "default" : "outline"}
            onClick={() => {
              // The SEARCH IS CLEARED when switching to compare mode: the user finds and
              // selects fonts via search, but their selections may come from different
              // searches — a left-open search would hide part of what they selected.
              // "Compare" means "show everything I selected".
              if (!karsilastir) setAra("")
              setKarsilastir((k) => !k)
            }}>
            <Columns2 className="mr-1 h-3.5 w-3.5" />
            {karsilastir ? t("pages.fonts.backToPool") : t("pages.fonts.compareSelected")}
          </Button>
          <Button size="sm" variant="ghost"
            onClick={() => { setSecili(new Set()); setKarsilastir(false) }}>
            {t("pages.fonts.clearSelection")}
          </Button>
          {karsilastir && (
            <span className="text-xs text-muted-foreground">
              {t("pages.fonts.compareHint")}
            </span>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
        </div>
      ) : gruplar.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {(fonts ?? []).length === 0
            ? t("pages.fonts.emptyPool")
            : karsilastir ? t("pages.fonts.emptyCompareSelection") : t("pages.fonts.emptySearch")}
        </p>
      ) : (
        <div className="space-y-4">
          {gruplar.map(([aile, liste]) => (
            <div key={aile} className="space-y-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{aile}</span>
                <Badge variant="outline">{t("pages.fonts.styleCount", { count: liste.length })}</Badge>
              </div>
              {liste.map((f) => (
                <FontRow key={f.id} font={f} metin={metin} punto={punto} koyu={koyu}
                  yazabilir={yazabilir} clients={clients ?? []}
                  secili={secili.has(f.id)} onSecim={() => secimDegistir(f.id)}
                  onSil={() => {
                    if (!confirm(t("pages.fonts.confirmDelete", { family: f.family, style: f.style }))) return
                    sil.mutate(f.id, {
                      onSuccess: () => toast.success(t("pages.fonts.deleted")),
                      onError: () => toast.error(t("pages.fonts.deleteFailed")),
                    })
                  }}
                  onAta={(clientId, assigned) => ata.mutate({ id: f.id, clientId, assigned })}
                  onAdlandir={(family, style) => adlandir.mutate({ id: f.id, family, style }, {
                    onSuccess: () => toast.success(t("pages.fonts.renamed")),
                  })} />
              ))}
            </div>
          ))}

          {/* Pagination: meaningless in compare mode (user already made their selection). */}
          {!karsilastir && kalan > 0 && (
            <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
              <span className="text-sm text-muted-foreground">
                {t("pages.fonts.showingCount", { shown: gosterilecek.length, total: suzulen.length })}
              </span>
              <Button variant="outline" size="sm" onClick={() => setLimit((l) => l + SAYFA)}>
                {kalan < SAYFA ? t("pages.fonts.showRemaining", { count: kalan }) : t("pages.fonts.showMore", { count: SAYFA })}
              </Button>
              {kalan > SAYFA && (
                <Button variant="ghost" size="sm" onClick={() => setLimit(suzulen.length)}>
                  {t("pages.fonts.showAll", { count: suzulen.length })}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FontRow({ font, metin, punto, koyu, yazabilir, clients, secili, onSecim,
                  onSil, onAta, onAdlandir }: {
  font: FontItem
  metin: string
  punto: number
  koyu: boolean
  yazabilir: boolean
  clients: { id: number; name: string }[]
  secili: boolean
  onSecim: () => void
  onSil: () => void
  onAta: (clientId: number, assigned: boolean) => void
  onAdlandir: (family: string, style: string) => void
}) {
  const { t } = useI18n()
  const [duzenle, setDuzenle] = useState(false)
  const [family, setFamily] = useState(font.family)
  const [style, setStyle] = useState(font.style)
  const atanmis = new Set(font.clients.map((c) => c.id))

  return (
    <div className={cn("space-y-1.5 rounded-md transition-colors",
      secili && "bg-accent/40 p-2 ring-1 ring-primary/30")}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {/* Compare selection — available to everyone (read access is enough), unrelated to write permission. */}
        <input type="checkbox" checked={secili} onChange={onSecim}
          title={t("pages.fonts.selectToCompare")}
          className="h-4 w-4 shrink-0 cursor-pointer accent-primary" />
        {duzenle ? (
          <>
            <Input value={family} onChange={(e) => setFamily(e.target.value)}
              className="h-8 w-40" placeholder={t("pages.fonts.familyPlaceholder")} />
            <Input value={style} onChange={(e) => setStyle(e.target.value)}
              className="h-8 w-32" placeholder={t("pages.fonts.stylePlaceholder")} />
            <Button size="sm" onClick={() => { onAdlandir(family, style); setDuzenle(false) }}>
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDuzenle(false)}>{t("pages.fonts.cancel")}</Button>
          </>
        ) : (
          <>
            <span className="font-medium">{font.style}</span>
            <Badge variant="secondary" className="text-[10px] uppercase">{font.format}</Badge>
            <span className="text-xs text-muted-foreground">{formatSize(font.file_size)}</span>
            {font.clients.map((c) => (
              <Badge key={c.id} variant="outline" className="text-[10px]">{c.name}</Badge>
            ))}
          </>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => downloadFont(font)} title={t("pages.fonts.download")}>
            <Download className="h-3.5 w-3.5" />
          </Button>
          {yazabilir && !duzenle && (
            <>
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="sm" title={t("pages.fonts.assignToClient")} />}>
                  <Users className="h-3.5 w-3.5" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
                  {clients.map((c) => (
                    <DropdownMenuItem key={c.id}
                      onClick={() => onAta(c.id, !atanmis.has(c.id))}>
                      <span className={cn("mr-2 w-3.5", atanmis.has(c.id) && "text-primary")}>
                        {atanmis.has(c.id) ? "✓" : ""}
                      </span>
                      <span className="truncate">{c.name}</span>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="ghost" size="sm" onClick={() => setDuzenle(true)} title={t("pages.fonts.editName")}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              {/* The panel does NOT REBUILD the delete rule: the flag comes from the
                  backend (management unconditionally / designer only what they uploaded,
                  2026-08-06). If there's no permission, the button isn't rendered at all —
                  clicking and getting a 403 is a worse experience than the button not existing. */}
              {font.can_delete && (
                <Button variant="ghost" size="sm" onClick={onSil} title={t("pages.fonts.removeFromPool")}>
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              )}
            </>
          )}
        </div>
      </div>
      <FontPreview font={font} text={metin} size={punto} dark={koyu} />
    </div>
  )
}
