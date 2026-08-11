// "Working Files" section on the client page (2026-08-07).
//
// Designers' source files (.psd/.ai/.indd…) live here VERSIONED: only the
// current version shows in the list, history appears when the row is expanded.
// The "recency tracking" requirement is met by the `v3` badge here + "2 days ago".
//
// The role gate comes from the ROUTE (`/designer/...` → management + designer),
// the component does not do its own role check; the backend endpoints also enforce it.
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
import { useI18n } from "@/lib/i18n"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

function gecenSure(iso: string | null, t: (key: string, vars?: Record<string, string | number>) => string, lang: "tr" | "en") {
  if (!iso) return "—"
  const gun = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (gun <= 0) return t("components.designFiles.clientDesignFiles.today")
  if (gun === 1) return t("components.designFiles.clientDesignFiles.yesterday")
  if (gun < 30) return t("components.designFiles.clientDesignFiles.daysAgo", { count: gun })
  return new Date(iso).toLocaleDateString(lang === "tr" ? "tr-TR" : "en-US", { day: "numeric", month: "short" })
}

function SurumGecmisi({ fileId }: { fileId: number }) {
  const { t, lang } = useI18n()
  const { data, isLoading } = useFileVersions(fileId)
  const sil = useDeleteVersion()
  if (isLoading) return <Skeleton className="h-16 w-full" />
  const liste = data ?? []
  return (
    <div className="space-y-1 border-t bg-muted/20 px-3 py-2">
      {liste.map((v) => (
        <div key={v.id} className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline" className="font-mono">v{v.version_no}</Badge>
          <span className="text-muted-foreground">{gecenSure(v.uploaded_at, t, lang)}</span>
          <span className="text-muted-foreground">·</span>
          <span>{v.uploader_name ?? "—"}</span>
          {v.note && <span className="text-muted-foreground">· “{v.note}”</span>}
          <span className="text-muted-foreground">· {formatBytes(v.file_size)}</span>
          <a href={designFileDownloadUrl(v.id)}
            className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">
            <Download className="h-3 w-3" /> {t("components.designFiles.clientDesignFiles.download")}
          </a>
          {v.can_delete && liste.length > 1 && (
            <button type="button" title={t("components.designFiles.clientDesignFiles.deleteVersionTitle")}
              onClick={async () => {
                try {
                  await sil.mutateAsync(v.id)
                  toast.success(t("components.designFiles.clientDesignFiles.versionMovedToTrash", { n: v.version_no }))
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.deleteFailed"))
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
  const { t, lang } = useI18n()
  const [acik, setAcik] = useState(false)
  const [duzenle, setDuzenle] = useState(false)
  const [baslik, setBaslik] = useState(f.title)
  const patch = usePatchDesignFile()
  const sil = useDeleteDesignFile()
  const v = f.current
  const rozet = extBadge(v?.file_name ?? "", t("components.designFiles.clientDesignFiles.noExtension"))

  async function kaydet() {
    try {
      await patch.mutateAsync({ fileId: f.id, title: baslik.trim() })
      setDuzenle(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.saveFailed"))
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
            title={t("components.designFiles.clientDesignFiles.driveBackupFailedTitle")}>
            {t("components.designFiles.clientDesignFiles.notCopiedToDrive")}
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-1">
          {duzenle ? (
            <Button size="sm" onClick={kaydet} disabled={patch.isPending}>{t("components.designFiles.clientDesignFiles.save")}</Button>
          ) : (
            <>
              {v && (
                <a href={designFileDownloadUrl(v.id)}
                  className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-sm text-primary hover:bg-muted">
                  <Download className="h-3.5 w-3.5" /> {t("components.designFiles.clientDesignFiles.download")}
                </a>
              )}
              <Button variant="ghost" size="sm" onClick={() => onYeniSurum(f)}>
                <Plus className="mr-1 h-3.5 w-3.5" /> {t("components.designFiles.clientDesignFiles.newVersion")}
              </Button>
              <Button variant="ghost" size="sm" title={t("components.designFiles.clientDesignFiles.versionHistory")}
                onClick={() => setAcik((a) => !a)}>
                <History className="h-3.5 w-3.5" />
                <ChevronDown className={cn("ml-0.5 h-3 w-3 transition-transform",
                  !acik && "-rotate-90")} />
              </Button>
              <Button variant="ghost" size="sm" title={t("components.designFiles.clientDesignFiles.editName")}
                onClick={() => setDuzenle(true)}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              {f.can_delete && (
                <Button variant="ghost" size="sm" title={t("components.designFiles.clientDesignFiles.deleteFileTitle")}
                  onClick={async () => {
                    if (!confirm(t("components.designFiles.clientDesignFiles.deleteFileConfirm", { title: f.title }))) return
                    try {
                      await sil.mutateAsync(f.id)
                      toast.success(t("components.designFiles.clientDesignFiles.movedToTrash"))
                    } catch (e) {
                      toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.deleteFailed"))
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
        <span className="truncate">{v?.file_name ?? t("components.designFiles.clientDesignFiles.noVersion")}</span>
        {v && <span>· {v.uploader_name ?? "—"} · {gecenSure(v.uploaded_at, t, lang)}</span>}
        {f.version_count > 1 && <span>· {t("components.designFiles.clientDesignFiles.versionCount", { count: f.version_count })}</span>}
        {f.tags.map((tag) => (
          <Badge key={tag} variant="outline" className="text-[10px]">{tag}</Badge>
        ))}
      </div>

      {acik && <SurumGecmisi fileId={f.id} />}
    </div>
  )
}

// --- trash --------------------------------------------------------------------
//
// Deleted files/versions land in a reversible approval queue (project owner's
// decision, 2026-08-08) — management either restores or permanently deletes;
// the designer can only leave a "permanently delete" flag. Permission flags
// (`can_restore`/`can_purge`) come from the BACKEND, not re-derived here.

function CopKutusuDosyaSatiri({ f }: { f: DesignFileItem }) {
  const { t, lang } = useI18n()
  const restore = useRestoreFile()
  const purgeReq = usePurgeRequest()
  const purge = usePurgeFile()
  const talepVar = !!f.purge_requested_at
  const v = f.current
  const rozet = extBadge(v?.file_name ?? "", t("components.designFiles.clientDesignFiles.noExtension"))

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
        <span className="text-xs text-muted-foreground">· {t("components.designFiles.clientDesignFiles.versionCount", { count: f.version_count })}</span>
      )}
      <span className="text-xs text-muted-foreground">
        {t("components.designFiles.clientDesignFiles.deletedBy", { who: f.deleter_name ?? "—", when: gecenSure(f.deleted_at, t, lang) })}
      </span>
      {talepVar && (
        <Badge variant="outline" className="gap-1 text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3 w-3" />
          {t("components.designFiles.clientDesignFiles.purgeRequestedBy", { who: f.purge_requester_name ?? t("components.designFiles.clientDesignFiles.someone") })}
        </Badge>
      )}

      <div className="ml-auto flex items-center gap-1">
        {f.can_restore && (
          <Button variant="ghost" size="sm" disabled={restore.isPending}
            onClick={async () => {
              try {
                await restore.mutateAsync(f.id)
                toast.success(t("components.designFiles.clientDesignFiles.restored", { title: f.title }))
              } catch (e) {
                toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.restoreFailed"))
              }
            }}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> {t("components.designFiles.clientDesignFiles.restore")}
          </Button>
        )}
        {!f.can_purge && (
          <Button variant="ghost" size="sm" disabled={purgeReq.isPending}
            onClick={async () => {
              try {
                await purgeReq.mutateAsync({ fileId: f.id, requested: !talepVar })
                toast.success(talepVar
                  ? t("components.designFiles.clientDesignFiles.purgeRequestWithdrawn")
                  : t("components.designFiles.clientDesignFiles.purgeRequested"))
              } catch (e) {
                toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.actionFailed"))
              }
            }}>
            {talepVar ? t("components.designFiles.clientDesignFiles.withdrawRequest") : t("components.designFiles.clientDesignFiles.requestPurge")}
          </Button>
        )}
        {f.can_purge && (
          <Button variant="ghost" size="sm" title={t("components.designFiles.clientDesignFiles.purgeTitle")} disabled={purge.isPending}
            onClick={async () => {
              if (!confirm(t("components.designFiles.clientDesignFiles.purgeFileConfirm", { title: f.title }))) return
              try {
                await purge.mutateAsync(f.id)
                toast.success(t("components.designFiles.clientDesignFiles.purged", { title: f.title }))
              } catch (e) {
                toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.purgeFailed"))
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
  const { t, lang } = useI18n()
  const restore = useRestoreVersion()
  const purge = usePurgeVersion()

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
      <Badge variant="outline" className="font-mono">v{v.version_no}</Badge>
      <span className="truncate">{v.file_name}</span>
      <span className="text-xs text-muted-foreground">
        {t("components.designFiles.clientDesignFiles.deletedBy", { who: v.deleter_name ?? "—", when: gecenSure(v.deleted_at, t, lang) })}
      </span>
      <span className="text-xs text-muted-foreground">· {formatBytes(v.file_size)}</span>

      <div className="ml-auto flex items-center gap-1">
        {v.can_restore && (
          <Button variant="ghost" size="sm" disabled={restore.isPending}
            onClick={async () => {
              try {
                await restore.mutateAsync(v.id)
                toast.success(t("components.designFiles.clientDesignFiles.versionRestored", { n: v.version_no }))
              } catch (e) {
                toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.restoreFailed"))
              }
            }}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> {t("components.designFiles.clientDesignFiles.restore")}
          </Button>
        )}
        {v.can_purge && (
          <Button variant="ghost" size="sm" title={t("components.designFiles.clientDesignFiles.purgeTitle")} disabled={purge.isPending}
            onClick={async () => {
              if (!confirm(t("components.designFiles.clientDesignFiles.purgeVersionConfirm", { n: v.version_no, name: v.file_name }))) return
              try {
                await purge.mutateAsync(v.id)
                toast.success(t("components.designFiles.clientDesignFiles.versionPurged", { n: v.version_no }))
              } catch (e) {
                toast.error(e instanceof Error ? e.message : t("components.designFiles.clientDesignFiles.purgeFailed"))
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
  const { t } = useI18n()
  const { data, isLoading, isError } = useTrash(clientId)
  const dosyalar = data?.files ?? []
  const surumler = data?.versions ?? []
  const toplamBoyut = data?.trash_bytes ?? 0

  if (isLoading) return <Skeleton className="m-3 h-16" />
  if (isError) return <p className="px-3 py-2 text-sm text-destructive">{t("components.designFiles.clientDesignFiles.trashLoadFailed")}</p>
  if (dosyalar.length === 0 && surumler.length === 0) {
    return <p className="px-3 py-2 text-sm text-muted-foreground">{t("components.designFiles.clientDesignFiles.trashEmpty")}</p>
  }

  return (
    <div className="space-y-1.5 p-3">
      <p className="text-xs text-muted-foreground">
        {t("components.designFiles.clientDesignFiles.trashSummary", {
          count: dosyalar.length + surumler.length, size: formatBytes(toplamBoyut),
        })}
      </p>
      {dosyalar.map((f) => <CopKutusuDosyaSatiri key={`f${f.id}`} f={f} />)}
      {surumler.map((v) => <CopKutusuSurumSatiri key={`v${v.id}`} v={v} />)}
    </div>
  )
}

export function ClientDesignFiles({ clientId }: { clientId: number }) {
  const { t } = useI18n()
  const { data, isLoading, isError } = useDesignFiles(clientId)
  const [acik, setAcik] = useState(true)
  const [copAcik, setCopAcik] = useState(false)
  const [yukleAcik, setYukleAcik] = useState(false)
  const [surumIcin, setSurumIcin] = useState<DesignFileItem | null>(null)
  const [etiket, setEtiket] = useState<string | null>(null)

  const files = data?.files ?? []
  const quota = data?.quota

  // Filter chips are derived from the tags on THAT CLIENT — no fixed list.
  const etiketler = useMemo(() => {
    const g = new Map<string, string>()
    for (const f of files) for (const tag of f.tags) if (!g.has(trFold(tag))) g.set(trFold(tag), tag)
    return [...g.values()].sort((a, b) => trFold(a).localeCompare(trFold(b), "tr"))
  }, [files])

  const gorunen = etiket
    ? files.filter((f) => f.tags.some((tag) => trFold(tag) === trFold(etiket)))
    : files

  return (
    <div className="space-y-3">
      <div className="rounded-lg border">
        <div className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
          <button type="button" onClick={() => setAcik((a) => !a)}
            className="flex items-center gap-1.5 text-sm font-medium">
            <ChevronDown className={cn("h-4 w-4 transition-transform", !acik && "-rotate-90")} />
            <FolderOpen className="h-4 w-4 text-muted-foreground" />
            {t("components.designFiles.clientDesignFiles.workingFiles")}
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
            <Plus className="mr-1 h-3.5 w-3.5" /> {t("components.designFiles.clientDesignFiles.newFile")}
          </Button>
        </div>

        {acik && (
          <div className="space-y-2 p-3">
            {etiketler.length > 0 && (
              <div className="flex flex-wrap gap-1">
                <button type="button" onClick={() => setEtiket(null)}
                  className={cn("rounded-full border px-2 py-0.5 text-xs",
                    !etiket && "border-primary bg-primary/10 text-primary")}>
                  {t("components.designFiles.clientDesignFiles.allTags")}
                </button>
                {etiketler.map((tag) => (
                  <button key={tag} type="button"
                    onClick={() => setEtiket(etiket === tag ? null : tag)}
                    className={cn("rounded-full border px-2 py-0.5 text-xs",
                      etiket === tag && "border-primary bg-primary/10 text-primary")}>
                    {tag}
                  </button>
                ))}
              </div>
            )}

            {isLoading && <Skeleton className="h-20 w-full" />}
            {isError && <p className="text-sm text-destructive">{t("components.designFiles.clientDesignFiles.loadFailed")}</p>}
            {!isLoading && gorunen.length === 0 && (
              <p className="text-sm text-muted-foreground">
                {t("components.designFiles.clientDesignFiles.emptyState")}
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
          {t("components.designFiles.clientDesignFiles.trash")}
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
