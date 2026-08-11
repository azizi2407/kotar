// Monthly Report (2026-08-07) — `/aylik-rapor`, management only.
//
// Upload Instagram CSVs exported from Meta → generate report → view as HTML,
// download PDF, send a link to the client. The calculation was ported from the
// product owner's desktop tool (backend `aylik_rapor.py`); this page replaces its tkinter UI.
//
// There are two upload modes, distinguished by the FOLDER:
//   • Pick a folder (`webkitdirectory`) → each subfolder is a client, bulk generation.
//     Exact equivalent of the desktop tool's bulk mode; the browser preserves the
//     folder path in each file's `webkitRelativePath` field.
//   • Pick a file → single client; the name comes from the adjacent input box.
import { useMemo, useRef, useState } from "react"
import {
  AlertTriangle, Check, Copy, Download, ExternalLink, FileSpreadsheet, FolderOpen,
  Link2, Loader2, Trash2, Upload,
} from "lucide-react"
import { toast } from "sonner"

import {
  gecenAy, periodLabel, reportHtmlUrl, reportPdfUrl, reportPublicUrl,
  useDeleteReport, useGenerateReports, useReportPeriods, useReports,
  useShareReport, type GenerateResult,
} from "@/lib/reports"
import { useI18n } from "@/lib/i18n"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

// The browser doesn't know the `webkitdirectory` attribute (no React type for it) —
// it needs to be passed as a string, hence this separate type.
type DirInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory?: string
}

function fmt(n: number | null | undefined) {
  if (n == null) return "—"
  return new Intl.NumberFormat("tr-TR").format(Math.round(n))
}

function SonucOzeti({ sonuc }: { sonuc: GenerateResult }) {
  const { t } = useI18n()
  return (
    <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Check className="h-4 w-4 text-emerald-600" />
        <span className="font-medium">{t("pages.monthlyReport.generatedCount", { count: sonuc.reports.length })}</span>
        {sonuc.skipped.length > 0 && (
          <Badge variant="outline" className="text-amber-600">
            {t("pages.monthlyReport.skippedCount", { count: sonuc.skipped.length })}
          </Badge>
        )}
      </div>
      {/* Skipped items aren't swallowed silently: assuming "I got the report" without
          seeing a missing CSV means sending the wrong report at the end of the month. */}
      {sonuc.skipped.map((s) => (
        <div key={s.client_name} className="flex gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" />
          <span><b>{s.client_name}</b> — {s.reason}</span>
        </div>
      ))}
    </div>
  )
}

function RaporSatiri({ r, onSil }: {
  r: import("@/lib/reports").ReportRow
  onSil: () => void
}) {
  const { t } = useI18n()
  const paylas = useShareReport()
  const paylasimAcik = !!r.token

  async function linkKopyala() {
    try {
      let token = r.token
      if (!token) {
        token = (await paylas.mutateAsync({ id: r.id, shared: true })).report.token
      }
      if (!token) throw new Error("token yok")
      await navigator.clipboard.writeText(reportPublicUrl(token))
      toast.success(t("pages.monthlyReport.linkCopied"))
    } catch {
      toast.error(t("pages.monthlyReport.linkGenerationFailed"))
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
      <div className="min-w-40 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{r.client_name}</span>
          {r.client_id == null && (
            <Badge variant="outline" className="text-[10px] text-muted-foreground"
              title={t("pages.monthlyReport.notInPanelTitle")}>
              {t("pages.monthlyReport.notInPanel")}
            </Badge>
          )}
          {r.reklam_var && <Badge variant="secondary" className="text-[10px]">{t("pages.monthlyReport.ads")}</Badge>}
          {paylasimAcik && (
            <Badge className="bg-teal-600 text-[10px] text-white">{t("pages.monthlyReport.shared")}</Badge>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-3 text-xs text-muted-foreground">
          <span>{t("pages.monthlyReport.views")} {fmt(r.ozet["Toplam Görüntüleme"])}</span>
          <span>{t("pages.monthlyReport.reach")} {fmt(r.ozet["Toplam Erişim"])}</span>
          <span>{t("pages.monthlyReport.engagement")} {fmt(r.ozet["Toplam Etkileşim"])}</span>
        </div>
        {r.warnings.length > 0 && (
          <div className="mt-1 flex gap-1.5 text-[11px] text-amber-600 dark:text-amber-500">
            <AlertTriangle className="h-3 w-3 shrink-0" />
            <span>{r.warnings.join(" · ")}</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" title={t("pages.monthlyReport.openReport")}
          render={<a href={reportHtmlUrl(r.id)} target="_blank" rel="noreferrer" />}>
          <ExternalLink className="mr-1 h-3.5 w-3.5" /> {t("pages.monthlyReport.open")}
        </Button>
        <Button variant="ghost" size="sm" title={t("pages.monthlyReport.downloadPdf")}
          render={<a href={reportPdfUrl(r.id)} />}>
          <Download className="mr-1 h-3.5 w-3.5" /> PDF
        </Button>
        <Button variant="ghost" size="sm" onClick={linkKopyala} disabled={paylas.isPending}
          title={paylasimAcik ? t("pages.monthlyReport.copyClientLink") : t("pages.monthlyReport.generateAndCopyLink")}>
          {paylasimAcik ? <Copy className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
        </Button>
        {paylasimAcik && (
          <Button variant="ghost" size="sm" title={t("pages.monthlyReport.closeSharing")}
            onClick={() => paylas.mutate({ id: r.id, shared: false }, {
              onSuccess: () => toast.success(t("pages.monthlyReport.sharingClosed")),
            })}>
            <Link2 className="h-3.5 w-3.5 text-destructive" />
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onSil} title={t("pages.monthlyReport.deleteReport")}>
          <Trash2 className="h-3.5 w-3.5 text-destructive" />
        </Button>
      </div>
    </div>
  )
}

export function AylikRaporPage() {
  const { t, lang } = useI18n()
  const [donem, setDonem] = useState(gecenAy())
  const [filtre, setFiltre] = useState<string>("")
  const [dosyalar, setDosyalar] = useState<File[]>([])
  const [tekilAd, setTekilAd] = useState("")
  const [sonuc, setSonuc] = useState<GenerateResult | null>(null)
  const dosyaRef = useRef<HTMLInputElement>(null)
  const klasorRef = useRef<HTMLInputElement>(null)

  const uret = useGenerateReports()
  const sil = useDeleteReport()
  const { data: donemler } = useReportPeriods()
  const { data: raporlar, isLoading } = useReports(filtre || undefined)

  // When picking a folder, files split into per-client subfolders; the user should
  // know how many clients they'll get BEFORE generating (a wrong folder pick is caught here).
  const gruplar = useMemo(() => {
    const m = new Map<string, number>()
    for (const f of dosyalar) {
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || ""
      const parcalar = rel.split("/").filter(Boolean)
      const ad = parcalar.length >= 2 ? parcalar[parcalar.length - 2] : ""
      m.set(ad, (m.get(ad) ?? 0) + 1)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], "tr"))
  }, [dosyalar])

  const topluMu = gruplar.length > 0 && gruplar[0][0] !== ""

  function dosyaSec(list: FileList | null) {
    if (!list) return
    const hepsi = [...list].filter((f) => f.name.toLowerCase().endsWith(".csv"))
    setDosyalar(hepsi)
    setSonuc(null)
    if (!hepsi.length) toast.error(t("pages.monthlyReport.noCsvSelected"))
  }

  async function uretVeKaydet() {
    if (!dosyalar.length) return
    const paths = dosyalar.map(
      (f) => (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name)
    try {
      const r = await uret.mutateAsync({
        files: dosyalar, paths, period: donem,
        client_name: tekilAd.trim() || t("pages.monthlyReport.defaultClientName"),
      })
      setSonuc(r)
      setDosyalar([])
      if (dosyaRef.current) dosyaRef.current.value = ""
      if (klasorRef.current) klasorRef.current.value = ""
      if (r.reports.length) toast.success(t("pages.monthlyReport.generatedCount", { count: r.reports.length }))
      else toast.error(t("pages.monthlyReport.noneGenerated"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("pages.monthlyReport.generateFailed"))
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">{t("pages.monthlyReport.title")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("pages.monthlyReport.subtitle")}
        </p>
      </div>

      {/* --- generation --- */}
      <div className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">{t("pages.monthlyReport.period")}</span>
            <Input type="month" value={donem} onChange={(e) => setDonem(e.target.value)}
              className="h-9 w-40" />
          </label>
          <Button variant="outline" onClick={() => klasorRef.current?.click()}>
            <FolderOpen className="mr-1 h-4 w-4" /> {t("pages.monthlyReport.pickFolder")}
          </Button>
          <Button variant="outline" onClick={() => dosyaRef.current?.click()}>
            <FileSpreadsheet className="mr-1 h-4 w-4" /> {t("pages.monthlyReport.pickFile")}
          </Button>
          <input ref={klasorRef} type="file" multiple hidden
            onChange={(e) => dosyaSec(e.target.files)}
            {...({ webkitdirectory: "" } as DirInputProps)} />
          <input ref={dosyaRef} type="file" multiple hidden accept=".csv,text/csv"
            onChange={(e) => dosyaSec(e.target.files)} />
        </div>

        <p className="text-xs text-muted-foreground">
          {t("pages.monthlyReport.bulkHint")}
        </p>

        {dosyalar.length > 0 && (
          <div className="space-y-2 rounded-md border bg-muted/30 p-3">
            <div className="text-sm font-medium">
              {t("pages.monthlyReport.csvSelected", { count: dosyalar.length })}
              {topluMu ? ` · ${t("pages.monthlyReport.clientFolders", { count: gruplar.length })}` : ` · ${t("pages.monthlyReport.singleClient")}`}
            </div>
            {topluMu ? (
              <div className="flex flex-wrap gap-1.5">
                {gruplar.map(([ad, n]) => (
                  <Badge key={ad} variant="outline" className="text-[11px]">{ad} ({n})</Badge>
                ))}
              </div>
            ) : (
              <label className="block space-y-1">
                <span className="text-xs text-muted-foreground">{t("pages.monthlyReport.clientName")}</span>
                <Input value={tekilAd} onChange={(e) => setTekilAd(e.target.value)}
                  placeholder={t("pages.monthlyReport.clientNamePlaceholder")} className="h-9 max-w-xs" />
              </label>
            )}
            <Button onClick={uretVeKaydet}
              disabled={uret.isPending || (!topluMu && !tekilAd.trim())}>
              {uret.isPending
                ? <><Loader2 className="mr-1 h-4 w-4 animate-spin" /> {t("pages.monthlyReport.generating")}</>
                : <><Upload className="mr-1 h-4 w-4" /> {t("pages.monthlyReport.createReport")}</>}
            </Button>
          </div>
        )}

        {sonuc && <SonucOzeti sonuc={sonuc} />}
      </div>

      {/* --- history --- */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-medium">{t("pages.monthlyReport.reports")}</h2>
          <Button size="sm" variant={filtre === "" ? "default" : "outline"}
            onClick={() => setFiltre("")}>{t("pages.monthlyReport.all")}</Button>
          {(donemler ?? []).map((d) => (
            <Button key={d.period} size="sm"
              variant={filtre === d.period ? "default" : "outline"}
              onClick={() => setFiltre(d.period)}>
              {periodLabel(d.period, lang)} ({d.count})
            </Button>
          ))}
        </div>

        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && !(raporlar ?? []).length && (
          <p className={cn("py-8 text-center text-sm text-muted-foreground")}>
            {t("pages.monthlyReport.noReportsYet")}
          </p>
        )}
        <div className="space-y-2">
          {(raporlar ?? []).map((r) => (
            <RaporSatiri key={r.id} r={r} onSil={() => {
              if (!confirm(t("pages.monthlyReport.deleteConfirm", { client: r.client_name, period: periodLabel(r.period, lang) }))) return
              sil.mutate(r.id, {
                onSuccess: () => toast.success(t("pages.monthlyReport.reportDeleted")),
                onError: () => toast.error(t("pages.monthlyReport.deleteFailed")),
              })
            }} />
          ))}
        </div>
      </div>
    </div>
  )
}
