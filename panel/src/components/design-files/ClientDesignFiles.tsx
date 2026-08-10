// Müşteri sayfasındaki "Çalışma Dosyaları" bölümü (2026-08-07).
//
// Tasarımcıların kaynak dosyaları (.psd/.ai/.indd…) burada SÜRÜMLÜ durur:
// listede yalnız güncel sürüm görünür, geçmiş satır açılınca gelir. "Güncellik
// takibi" gereksinimi buradaki `v3` rozeti + "2 gün önce" ile karşılanıyor.
//
// Rol kapısı ROUTE'tan geliyor (`/designer/...` → management + designer), bileşen
// kendi rol kontrolünü yapmaz; backend uçları ayrıca zorluyor.
import { useMemo, useState } from "react"
import {
  AlertTriangle, ChevronDown, Download, FolderOpen, History, Loader2,
  Pencil, Plus, RotateCcw, Trash2,
} from "lucide-react"
import { toast } from "sonner"

import {
  designFileDownloadUrl, extBadge, formatBytes, useDeleteDesignFile,
  useDeleteVersion, useDesignFiles, useFileVersions, usePatchDesignFile,
  usePurgeFile, usePurgeRequest, usePurgeVersion, useRestoreFile,
  useRestoreVersion, useTrash,
  type DesignFileItem, type DesignVersion,
} from "@/lib/design-files"
import { UploadDialog } from "@/components/design-files/UploadDialog"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

function gecenSure(iso: string | null) {
  if (!iso) return "—"
  const gun = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (gun <= 0) return "bugün"
  if (gun === 1) return "dün"
  if (gun < 30) return `${gun} gün önce`
  return new Date(iso).toLocaleDateString("tr-TR", { day: "numeric", month: "short" })
}

function SurumGecmisi({ fileId }: { fileId: number }) {
  const { data, isLoading } = useFileVersions(fileId)
  const sil = useDeleteVersion()
  if (isLoading) return <Skeleton className="h-16 w-full" />
  const liste = data ?? []
  return (
    <div className="space-y-1 border-t bg-muted/20 px-3 py-2">
      {liste.map((v) => (
        <div key={v.id} className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline" className="font-mono">v{v.version_no}</Badge>
          <span className="text-muted-foreground">{gecenSure(v.uploaded_at)}</span>
          <span className="text-muted-foreground">·</span>
          <span>{v.uploader_name ?? "—"}</span>
          {v.note && <span className="text-muted-foreground">· “{v.note}”</span>}
          <span className="text-muted-foreground">· {formatBytes(v.file_size)}</span>
          <a href={designFileDownloadUrl(v.id)}
            className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">
            <Download className="h-3 w-3" /> İndir
          </a>
          {v.can_delete && liste.length > 1 && (
            <button type="button" title="Bu sürümü sil"
              onClick={async () => {
                try {
                  await sil.mutateAsync(v.id)
                  toast.success(`v${v.version_no} çöp kutusuna taşındı`)
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Silinemedi")
                }
              }}
              className="text-muted-foreground hover:text-destructive">
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function DosyaSatiri({ f, onYeniSurum }: {
  f: DesignFileItem
  onYeniSurum: (f: DesignFileItem) => void
}) {
  const [acik, setAcik] = useState(false)
  const [duzenle, setDuzenle] = useState(false)
  const [baslik, setBaslik] = useState(f.title)
  const patch = usePatchDesignFile()
  const sil = useDeleteDesignFile()
  const v = f.current
  const rozet = extBadge(v?.file_name ?? "")

  async function kaydet() {
    try {
      await patch.mutateAsync({ fileId: f.id, title: baslik.trim() })
      setDuzenle(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Kaydedilemedi")
    }
  }

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-bold", rozet.cls)}>
          {rozet.ext}
        </span>

        {duzenle ? (
          <Input value={baslik} onChange={(e) => setBaslik(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && kaydet()}
            className="h-7 max-w-56" autoFocus />
        ) : (
          <span className="font-medium">{f.title}</span>
        )}

        {v && <Badge variant="outline" className="font-mono">v{v.version_no}</Badge>}
        {v && <span className="text-xs text-muted-foreground">{formatBytes(v.file_size)}</span>}
        {v && !v.drive_ok && (
          <Badge variant="outline" className="text-amber-700 dark:text-amber-400"
            title="Dosya sunucuda güvende; yalnız Drive yedeği alınamadı">
            Drive'a kopyalanmadı
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-1">
          {duzenle ? (
            <Button size="sm" onClick={kaydet} disabled={patch.isPending}>Kaydet</Button>
          ) : (
            <>
              {v && (
                <a href={designFileDownloadUrl(v.id)}
                  className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-sm text-primary hover:bg-muted">
                  <Download className="h-3.5 w-3.5" /> İndir
                </a>
              )}
              <Button variant="ghost" size="sm" onClick={() => onYeniSurum(f)}>
                <Plus className="mr-1 h-3.5 w-3.5" /> Yeni sürüm
              </Button>
              <Button variant="ghost" size="sm" title="Sürüm geçmişi"
                onClick={() => setAcik((a) => !a)}>
                <History className="h-3.5 w-3.5" />
                <ChevronDown className={cn("ml-0.5 h-3 w-3 transition-transform",
                  !acik && "-rotate-90")} />
              </Button>
              <Button variant="ghost" size="sm" title="Adı düzenle"
                onClick={() => setDuzenle(true)}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              {f.can_delete && (
                <Button variant="ghost" size="sm" title="Dosyayı sil"
                  onClick={async () => {
                    if (!confirm(`"${f.title}" çöp kutusuna taşınsın mı? Tüm sürümleriyle birlikte geri alınabilir.`)) return
                    try {
                      await sil.mutateAsync(f.id)
                      toast.success("Dosya çöp kutusuna taşındı")
                    } catch (e) {
                      toast.error(e instanceof Error ? e.message : "Silinemedi")
                    }
                  }}>
                  {sil.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Trash2 className="h-3.5 w-3.5 text-destructive" />}
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-3 pb-2 text-xs text-muted-foreground">
        <span className="truncate">{v?.file_name ?? "sürüm yok"}</span>
        {v && <span>· {v.uploader_name ?? "—"} · {gecenSure(v.uploaded_at)}</span>}
        {f.version_count > 1 && <span>· {f.version_count} sürüm</span>}
        {f.tags.map((t) => (
          <Badge key={t} variant="outline" className="text-[10px]">{t}</Badge>
        ))}
      </div>

      {acik && <SurumGecmisi fileId={f.id} />}
    </div>
  )
}

// --- çöp kutusu -------------------------------------------------------------
//
// Silinen dosya/sürümler geri alınabilir bir onay kuyruğuna düşer (proje sahibi kararı,
// 2026-08-08) — yönetim ya geri alır ya kalıcı siler; tasarımcı yalnız "kalıcı
// silinsin" işareti bırakabilir. Yetki bayrakları (`can_restore`/`can_purge`)
// BACKEND'ten gelir, burada yeniden kurulmaz.

function CopKutusuDosyaSatiri({ f }: { f: DesignFileItem }) {
  const restore = useRestoreFile()
  const purgeReq = usePurgeRequest()
  const purge = usePurgeFile()
  const talepVar = !!f.purge_requested_at
  const v = f.current
  const rozet = extBadge(v?.file_name ?? "")

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
      {v && (
        <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-bold", rozet.cls)}>
          {rozet.ext}
        </span>
      )}
      <span className="font-medium">{f.title}</span>
      {v && <span className="text-xs text-muted-foreground">{formatBytes(v.file_size)}</span>}
      {f.version_count > 1 && (
        <span className="text-xs text-muted-foreground">· {f.version_count} sürüm</span>
      )}
      <span className="text-xs text-muted-foreground">
        {f.deleter_name ?? "—"} · {gecenSure(f.deleted_at)} sildi
      </span>
      {talepVar && (
        <Badge variant="outline" className="gap-1 text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3 w-3" />
          {f.purge_requester_name ?? "birisi"} kalıcı silinmesini istedi
        </Badge>
      )}

      <div className="ml-auto flex items-center gap-1">
        {f.can_restore && (
          <Button variant="ghost" size="sm" disabled={restore.isPending}
            onClick={async () => {
              try {
                await restore.mutateAsync(f.id)
                toast.success(`"${f.title}" geri alındı`)
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Geri alınamadı")
              }
            }}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> Geri al
          </Button>
        )}
        {!f.can_purge && (
          <Button variant="ghost" size="sm" disabled={purgeReq.isPending}
            onClick={async () => {
              try {
                await purgeReq.mutateAsync({ fileId: f.id, requested: !talepVar })
                toast.success(talepVar ? "Kalıcı silme talebi geri çekildi" : "Kalıcı silme talep edildi")
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "İşlem başarısız")
              }
            }}>
            {talepVar ? "Talebi geri çek" : "Kalıcı silme iste"}
          </Button>
        )}
        {f.can_purge && (
          <Button variant="ghost" size="sm" title="Kalıcı sil" disabled={purge.isPending}
            onClick={async () => {
              if (!confirm(`"${f.title}" ve tüm sürümleri KALICI olarak silinsin mi? Bu işlem GERİ ALINAMAZ.`)) return
              try {
                await purge.mutateAsync(f.id)
                toast.success(`"${f.title}" kalıcı silindi`)
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Kalıcı silinemedi")
              }
            }}>
            {purge.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Trash2 className="h-3.5 w-3.5 text-destructive" />}
          </Button>
        )}
      </div>
    </div>
  )
}

function CopKutusuSurumSatiri({ v }: { v: DesignVersion }) {
  const restore = useRestoreVersion()
  const purge = usePurgeVersion()

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
      <Badge variant="outline" className="font-mono">v{v.version_no}</Badge>
      <span className="truncate">{v.file_name}</span>
      <span className="text-xs text-muted-foreground">
        {v.deleter_name ?? "—"} · {gecenSure(v.deleted_at)} sildi
      </span>
      <span className="text-xs text-muted-foreground">· {formatBytes(v.file_size)}</span>

      <div className="ml-auto flex items-center gap-1">
        {v.can_restore && (
          <Button variant="ghost" size="sm" disabled={restore.isPending}
            onClick={async () => {
              try {
                await restore.mutateAsync(v.id)
                toast.success(`v${v.version_no} geri alındı`)
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Geri alınamadı")
              }
            }}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> Geri al
          </Button>
        )}
        {v.can_purge && (
          <Button variant="ghost" size="sm" title="Kalıcı sil" disabled={purge.isPending}
            onClick={async () => {
              if (!confirm(`v${v.version_no} (${v.file_name}) KALICI olarak silinsin mi? Bu işlem GERİ ALINAMAZ.`)) return
              try {
                await purge.mutateAsync(v.id)
                toast.success(`v${v.version_no} kalıcı silindi`)
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Kalıcı silinemedi")
              }
            }}>
            {purge.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Trash2 className="h-3.5 w-3.5 text-destructive" />}
          </Button>
        )}
      </div>
    </div>
  )
}

function CopKutusu({ clientId }: { clientId: number }) {
  const { data, isLoading, isError } = useTrash(clientId)
  const dosyalar = data?.files ?? []
  const surumler = data?.versions ?? []
  const toplamBoyut = data?.trash_bytes ?? 0

  if (isLoading) return <Skeleton className="m-3 h-16" />
  if (isError) return <p className="px-3 py-2 text-sm text-destructive">Çöp kutusu yüklenemedi.</p>
  if (dosyalar.length === 0 && surumler.length === 0) {
    return <p className="px-3 py-2 text-sm text-muted-foreground">Çöp kutusu boş.</p>
  }

  return (
    <div className="space-y-1.5 p-3">
      <p className="text-xs text-muted-foreground">
        {dosyalar.length + surumler.length} öğe · en az {formatBytes(toplamBoyut)}
      </p>
      {dosyalar.map((f) => <CopKutusuDosyaSatiri key={`f${f.id}`} f={f} />)}
      {surumler.map((v) => <CopKutusuSurumSatiri key={`v${v.id}`} v={v} />)}
    </div>
  )
}

export function ClientDesignFiles({ clientId }: { clientId: number }) {
  const { data, isLoading, isError } = useDesignFiles(clientId)
  const [acik, setAcik] = useState(true)
  const [copAcik, setCopAcik] = useState(false)
  const [yukleAcik, setYukleAcik] = useState(false)
  const [surumIcin, setSurumIcin] = useState<DesignFileItem | null>(null)
  const [etiket, setEtiket] = useState<string | null>(null)

  const files = data?.files ?? []
  const quota = data?.quota

  // Süzgeç çipleri O MÜŞTERİDEKİ etiketlerden türer — sabit liste yok.
  const etiketler = useMemo(() => {
    const g = new Map<string, string>()
    for (const f of files) for (const t of f.tags) if (!g.has(trFold(t))) g.set(trFold(t), t)
    return [...g.values()].sort((a, b) => trFold(a).localeCompare(trFold(b), "tr"))
  }, [files])

  const gorunen = etiket
    ? files.filter((f) => f.tags.some((t) => trFold(t) === trFold(etiket)))
    : files

  return (
    <div className="space-y-3">
      <div className="rounded-lg border">
        <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
          <button type="button" onClick={() => setAcik((a) => !a)}
            className="flex items-center gap-1.5 text-sm font-medium">
            <ChevronDown className={cn("h-4 w-4 transition-transform", !acik && "-rotate-90")} />
            <FolderOpen className="h-4 w-4 text-muted-foreground" />
            Çalışma Dosyaları
            {files.length > 0 && <Badge variant="outline">{files.length}</Badge>}
          </button>

          {quota && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                <div className={cn("h-full transition-all",
                  quota.pct >= 90 ? "bg-destructive"
                    : quota.pct >= 70 ? "bg-amber-500" : "bg-primary")}
                  style={{ width: `${Math.min(100, quota.pct)}%` }} />
              </div>
              <span>{formatBytes(quota.used)} / {formatBytes(quota.limit)}</span>
            </div>
          )}

          <Button variant="ghost" size="sm" className="ml-auto"
            onClick={() => setYukleAcik(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" /> Yeni dosya
          </Button>
        </div>

        {acik && (
          <div className="space-y-2 p-3">
            {etiketler.length > 0 && (
              <div className="flex flex-wrap gap-1">
                <button type="button" onClick={() => setEtiket(null)}
                  className={cn("rounded-full border px-2 py-0.5 text-xs",
                    !etiket && "border-primary bg-primary/10 text-primary")}>
                  tümü
                </button>
                {etiketler.map((t) => (
                  <button key={t} type="button"
                    onClick={() => setEtiket(etiket === t ? null : t)}
                    className={cn("rounded-full border px-2 py-0.5 text-xs",
                      etiket === t && "border-primary bg-primary/10 text-primary")}>
                    {t}
                  </button>
                ))}
              </div>
            )}

            {isLoading && <Skeleton className="h-20 w-full" />}
            {isError && <p className="text-sm text-destructive">Çalışma dosyaları yüklenemedi.</p>}
            {!isLoading && gorunen.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Bu müşteri için çalışma dosyası yok — .psd, .ai gibi kaynak dosyaları
                buraya yükleyebilirsin.
              </p>
            )}
            {gorunen.map((f) => (
              <DosyaSatiri key={f.id} f={f} onYeniSurum={setSurumIcin} />
            ))}
          </div>
        )}
      </div>

      <div className="rounded-lg border">
        <button type="button" onClick={() => setCopAcik((a) => !a)}
          className={cn("flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm font-medium",
            copAcik && "border-b bg-muted/30")}>
          <ChevronDown className={cn("h-4 w-4 transition-transform", !copAcik && "-rotate-90")} />
          <Trash2 className="h-4 w-4 text-muted-foreground" />
          Çöp kutusu
        </button>
        {copAcik && <CopKutusu clientId={clientId} />}
      </div>

      <UploadDialog open={yukleAcik} onClose={() => setYukleAcik(false)}
        clientId={clientId} />
      {surumIcin && (
        <UploadDialog open onClose={() => setSurumIcin(null)} clientId={clientId}
          fileId={surumIcin.id} fileTitle={surumIcin.title} />
      )}
    </div>
  )
}
