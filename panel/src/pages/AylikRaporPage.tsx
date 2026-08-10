// Aylık Rapor (2026-08-07) — `/aylik-rapor`, yalnız yönetim.
//
// Meta'dan inen Instagram CSV'lerini yükle → rapor üret → HTML olarak görüntüle,
// PDF indir, müşteriye link gönder. Hesaplama proje sahibi'in masaüstü aracından taşındı
// (backend `aylik_rapor.py`); bu sayfa onun tkinter arayüzünün yerini alıyor.
//
// İki yükleme kipi var ve ayrımı KLASÖRDEN geliyor:
//   • Klasör seç (`webkitdirectory`) → her alt klasör bir müşteri, toplu üretim.
//     Masaüstü aracının bulk modunun birebir karşılığı; tarayıcı dosyaların
//     `webkitRelativePath` alanında klasör yolunu koruyor.
//   • Dosya seç → tek müşteri; ad yandaki kutudan gelir.
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

// Tarayıcı `webkitdirectory` özniteliğini bilmiyor (React tipi yok) — string olarak
// geçirilmesi gerekiyor, bu yüzden ayrı bir tip.
type DirInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory?: string
}

function fmt(n: number | null | undefined) {
  if (n == null) return "—"
  return new Intl.NumberFormat("tr-TR").format(Math.round(n))
}

function SonucOzeti({ sonuc }: { sonuc: GenerateResult }) {
  return (
    <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Check className="h-4 w-4 text-emerald-600" />
        <span className="font-medium">{sonuc.reports.length} rapor üretildi</span>
        {sonuc.skipped.length > 0 && (
          <Badge variant="outline" className="text-amber-600">
            {sonuc.skipped.length} atlandı
          </Badge>
        )}
      </div>
      {/* Atlananlar sessizce yutulmaz: eksik CSV'yi görmeden "raporu aldım"
          sanmak, ay sonunda yanlış rapor göndermek demek. */}
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
      toast.success("Rapor linki panoya kopyalandı")
    } catch {
      toast.error("Link üretilemedi")
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
      <div className="min-w-40 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{r.client_name}</span>
          {r.client_id == null && (
            <Badge variant="outline" className="text-[10px] text-muted-foreground"
              title="Bu ad panel müşteri listesiyle eşleşmedi — rapor yine de üretildi">
              panelde yok
            </Badge>
          )}
          {r.reklam_var && <Badge variant="secondary" className="text-[10px]">reklam</Badge>}
          {paylasimAcik && (
            <Badge className="bg-teal-600 text-[10px] text-white">paylaşımda</Badge>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-3 text-xs text-muted-foreground">
          <span>Görüntüleme {fmt(r.ozet["Toplam Görüntüleme"])}</span>
          <span>Erişim {fmt(r.ozet["Toplam Erişim"])}</span>
          <span>Etkileşim {fmt(r.ozet["Toplam Etkileşim"])}</span>
        </div>
        {r.warnings.length > 0 && (
          <div className="mt-1 flex gap-1.5 text-[11px] text-amber-600 dark:text-amber-500">
            <AlertTriangle className="h-3 w-3 shrink-0" />
            <span>{r.warnings.join(" · ")}</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" title="Raporu aç"
          render={<a href={reportHtmlUrl(r.id)} target="_blank" rel="noreferrer" />}>
          <ExternalLink className="mr-1 h-3.5 w-3.5" /> Aç
        </Button>
        <Button variant="ghost" size="sm" title="PDF indir"
          render={<a href={reportPdfUrl(r.id)} />}>
          <Download className="mr-1 h-3.5 w-3.5" /> PDF
        </Button>
        <Button variant="ghost" size="sm" onClick={linkKopyala} disabled={paylas.isPending}
          title={paylasimAcik ? "Müşteri linkini kopyala" : "Public link üret ve kopyala"}>
          {paylasimAcik ? <Copy className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
        </Button>
        {paylasimAcik && (
          <Button variant="ghost" size="sm" title="Paylaşımı kapat"
            onClick={() => paylas.mutate({ id: r.id, shared: false }, {
              onSuccess: () => toast.success("Paylaşım kapatıldı — link artık çalışmıyor"),
            })}>
            <Link2 className="h-3.5 w-3.5 text-destructive" />
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onSil} title="Raporu sil">
          <Trash2 className="h-3.5 w-3.5 text-destructive" />
        </Button>
      </div>
    </div>
  )
}

export function AylikRaporPage() {
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

  // Klasör seçiminde dosyalar müşteri klasörlerine bölünür; kullanıcı üretimden
  // ÖNCE kaç müşteri göreceğini bilsin (yanlış klasör seçimi burada fark edilir).
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
    if (!hepsi.length) toast.error("Seçimde CSV dosyası yok")
  }

  async function uretVeKaydet() {
    if (!dosyalar.length) return
    const paths = dosyalar.map(
      (f) => (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name)
    try {
      const r = await uret.mutateAsync({
        files: dosyalar, paths, period: donem,
        client_name: tekilAd.trim() || "Rapor",
      })
      setSonuc(r)
      setDosyalar([])
      if (dosyaRef.current) dosyaRef.current.value = ""
      if (klasorRef.current) klasorRef.current.value = ""
      if (r.reports.length) toast.success(`${r.reports.length} rapor üretildi`)
      else toast.error("Hiç rapor üretilemedi")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Rapor üretilemedi")
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Aylık Rapor</h1>
        <p className="text-sm text-muted-foreground">
          Meta'dan indirdiğiniz Instagram istatistiklerini (ve varsa reklam raporunu)
          yükleyin; rapor otomatik oluşsun.
        </p>
      </div>

      {/* --- üretim --- */}
      <div className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">Dönem</span>
            <Input type="month" value={donem} onChange={(e) => setDonem(e.target.value)}
              className="h-9 w-40" />
          </label>
          <Button variant="outline" onClick={() => klasorRef.current?.click()}>
            <FolderOpen className="mr-1 h-4 w-4" /> Klasör seç (toplu)
          </Button>
          <Button variant="outline" onClick={() => dosyaRef.current?.click()}>
            <FileSpreadsheet className="mr-1 h-4 w-4" /> Dosya seç (tek müşteri)
          </Button>
          <input ref={klasorRef} type="file" multiple hidden
            onChange={(e) => dosyaSec(e.target.files)}
            {...({ webkitdirectory: "" } as DirInputProps)} />
          <input ref={dosyaRef} type="file" multiple hidden accept=".csv,text/csv"
            onChange={(e) => dosyaSec(e.target.files)} />
        </div>

        <p className="text-xs text-muted-foreground">
          Toplu üretim için her müşterinin CSV'leri kendi klasöründe olsun; üst klasörü seçin.
          Dosya adları Türkçe ya da İngilizce olabilir (görüntüleme/views, erişim/reach…).
        </p>

        {dosyalar.length > 0 && (
          <div className="space-y-2 rounded-md border bg-muted/30 p-3">
            <div className="text-sm font-medium">
              {dosyalar.length} CSV seçildi
              {topluMu ? ` · ${gruplar.length} müşteri klasörü` : " · tek müşteri"}
            </div>
            {topluMu ? (
              <div className="flex flex-wrap gap-1.5">
                {gruplar.map(([ad, n]) => (
                  <Badge key={ad} variant="outline" className="text-[11px]">{ad} ({n})</Badge>
                ))}
              </div>
            ) : (
              <label className="block space-y-1">
                <span className="text-xs text-muted-foreground">Müşteri adı</span>
                <Input value={tekilAd} onChange={(e) => setTekilAd(e.target.value)}
                  placeholder="Rapor başlığında görünecek ad" className="h-9 max-w-xs" />
              </label>
            )}
            <Button onClick={uretVeKaydet}
              disabled={uret.isPending || (!topluMu && !tekilAd.trim())}>
              {uret.isPending
                ? <><Loader2 className="mr-1 h-4 w-4 animate-spin" /> Üretiliyor…</>
                : <><Upload className="mr-1 h-4 w-4" /> Rapor oluştur</>}
            </Button>
          </div>
        )}

        {sonuc && <SonucOzeti sonuc={sonuc} />}
      </div>

      {/* --- geçmiş --- */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-medium">Raporlar</h2>
          <Button size="sm" variant={filtre === "" ? "default" : "outline"}
            onClick={() => setFiltre("")}>Tümü</Button>
          {(donemler ?? []).map((d) => (
            <Button key={d.period} size="sm"
              variant={filtre === d.period ? "default" : "outline"}
              onClick={() => setFiltre(d.period)}>
              {periodLabel(d.period)} ({d.count})
            </Button>
          ))}
        </div>

        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && !(raporlar ?? []).length && (
          <p className={cn("py-8 text-center text-sm text-muted-foreground")}>
            Henüz rapor yok. Yukarıdan CSV yükleyerek başlayın.
          </p>
        )}
        <div className="space-y-2">
          {(raporlar ?? []).map((r) => (
            <RaporSatiri key={r.id} r={r} onSil={() => {
              if (!confirm(`"${r.client_name}" ${periodLabel(r.period)} raporu silinsin mi?`)) return
              sil.mutate(r.id, {
                onSuccess: () => toast.success("Rapor silindi"),
                onError: () => toast.error("Silinemedi"),
              })
            }} />
          ))}
        </div>
      </div>
    </div>
  )
}
