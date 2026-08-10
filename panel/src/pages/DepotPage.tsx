// Videograf Deposu (/videograf-deposu) — management + videographer.
// ORTAK 5 GB serbest dosya alanı: müşteriye ve haftaya bağlı değil. Herkes yükler,
// herkes siler; dosyalar Drive'da içerik kökü/Videograf Deposu altında tek düz klasörde.
import { useState } from "react"
import {
  AlertTriangle, Copy, Download, ExternalLink, HardDrive, Search, Trash2, Upload,
} from "lucide-react"
import { toast } from "sonner"

import { DepotUploadDialog } from "@/components/depot/DepotUploadDialog"
import { QuotaBar } from "@/components/depot/QuotaBar"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { fmtBytes, useDeleteDepotFile, useDepotFiles, type DepotFile } from "@/lib/depot"
import { driveDownloadUrl, driveFileUrl } from "@/lib/sharing"

function fmtWhen(iso: string | null) {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("tr-TR", {
    day: "2-digit", month: "short", year: "numeric",
  })
}

async function copyLink(fileId: string) {
  try {
    await navigator.clipboard.writeText(driveFileUrl(fileId))
    toast.success("Dosya bağlantısı kopyalandı")
  } catch {
    toast.error("Kopyalanamadı — tarayıcı izin vermedi")
  }
}

export function DepotPage() {
  const [q, setQ] = useState("")
  const [uploadOpen, setUploadOpen] = useState(false)
  const [confirm, setConfirm] = useState<DepotFile | null>(null)
  const { data, isLoading, isError } = useDepotFiles({ q })
  const del = useDeleteDepotFile()

  const files = data?.files ?? []
  const quota = data?.quota

  function doDelete() {
    if (!confirm) return
    del.mutate(confirm.id, {
      onSuccess: (r) => {
        setConfirm(null)
        if (r?.drive_ok === false) {
          toast.warning("Panelden kaldırıldı, Drive'dan silinemedi — elle kontrol edin.")
        } else {
          toast.success("Dosya kaldırıldı (Drive çöp kutusunda 30 gün durur)")
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Silinemedi"),
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <HardDrive className="h-5 w-5" /> Videograf Deposu
          </h1>
          <p className="text-sm text-muted-foreground">
            Ekip için ortak serbest dosya alanı — müşteriye bağlı değil.
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Upload className="mr-1 h-4 w-4" /> Dosya yükle
        </Button>
      </div>

      {/* Kalıcı uyarı: dosyalar bağlantıyla herkese açık (kullanıcı kararı 2026-07-25) */}
      <div className="flex items-start gap-2 rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm dark:border-amber-900/50 dark:bg-amber-900/20">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <p>
          <span className="font-medium">Ortak alan.</span> Tüm videograflar ve yönetim aynı
          5 GB'ı paylaşır; herkes her dosyayı silebilir.{" "}
          <span className="font-medium">Bağlantıya sahip herkes indirebilir</span> — gizli
          veya kişisel belge koymayın.
        </p>
      </div>

      {quota && <QuotaBar quota={quota} />}

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder="Dosya adı ara…" value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Dosya</TableHead>
              <TableHead className="hidden sm:table-cell">Boyut</TableHead>
              <TableHead className="hidden md:table-cell">Yükleyen</TableHead>
              <TableHead className="hidden lg:table-cell">Tarih</TableHead>
              <TableHead className="text-right">İşlem</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && [...Array(4)].map((_, i) => (
              <TableRow key={i}>
                <TableCell colSpan={5}><Skeleton className="h-5 w-full" /></TableCell>
              </TableRow>
            ))}
            {isError && (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-destructive">
                  Depo yüklenemedi.
                </TableCell>
              </TableRow>
            )}
            {data && files.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  {q ? "Eşleşen dosya yok." : "Depo boş — ilk dosyayı yükleyin."}
                </TableCell>
              </TableRow>
            )}
            {files.map((f) => (
              <TableRow key={f.id}>
                <TableCell className="max-w-72">
                  <div className="truncate font-medium">{f.file_name}</div>
                  {f.note && (
                    <div className="truncate text-xs text-muted-foreground">{f.note}</div>
                  )}
                </TableCell>
                <TableCell className="hidden whitespace-nowrap text-muted-foreground sm:table-cell">
                  {fmtBytes(f.file_size)}
                </TableCell>
                <TableCell className="hidden text-muted-foreground md:table-cell">
                  {f.uploader_name || "—"}
                </TableCell>
                <TableCell className="hidden whitespace-nowrap text-muted-foreground lg:table-cell">
                  {fmtWhen(f.uploaded_at)}
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    {/* Depo dosyaları `media_store`'a KOPYALANMIYOR (depot.py: 500 MB'lık
                        dosyalar akıştan Drive'a gider) → indirme doğrudan Drive'dan.
                        Dosyada "bağlantıya sahip herkes" izni var (grant_anyone_reader). */}
                    <a href={driveDownloadUrl(f.file_id)} download title="Dosyayı indir"
                      className="inline-flex h-8 items-center rounded-md border px-2.5 text-sm font-medium hover:bg-muted">
                      <Download className="mr-1 h-3.5 w-3.5" /> İndir
                    </a>
                    <Button size="sm" variant="outline" onClick={() => copyLink(f.file_id)}>
                      <Copy className="mr-1 h-3.5 w-3.5" /> Kopyala
                    </Button>
                    <a href={driveFileUrl(f.file_id)} target="_blank" rel="noreferrer"
                      title="Drive'da aç"
                      className="p-1 text-muted-foreground hover:text-foreground">
                      <ExternalLink className="h-4 w-4" />
                    </a>
                    <button type="button" onClick={() => setConfirm(f)} title="Sil"
                      className="p-1 text-muted-foreground hover:text-destructive">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {uploadOpen && (
        <DepotUploadDialog quota={quota} onClose={() => setUploadOpen(false)} />
      )}

      {confirm && (
        <Dialog open onOpenChange={(o) => !o && setConfirm(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader><DialogTitle>Dosyayı sil</DialogTitle></DialogHeader>
            <p className="text-sm">
              <span className="font-medium">{confirm.file_name}</span> depodan kaldırılacak.
              Bu dosya ortak alanda — herkesin erişimi kalkar. Drive'da çöp kutusuna taşınır
              (30 gün geri alınabilir).
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setConfirm(null)}>Vazgeç</Button>
              <Button variant="destructive" onClick={doDelete} disabled={del.isPending}>
                {del.isPending ? "Siliniyor…" : "Sil"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
