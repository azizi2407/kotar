// Videographer Depot (/videograf-deposu) — management + videographer.
// A SHARED 5 GB free file space: not tied to a client or week. Anyone can upload,
// anyone can delete; files live in one flat folder under the content root/Videographer Depot in Drive.
import { useState } from "react"
import {
  AlertTriangle, Copy, Download, ExternalLink, HardDrive, Search, Trash2, Upload,
} from "lucide-react"
import { toast } from "sonner"

import { DepotUploadDialog } from "@/components/depot/DepotUploadDialog"
import { QuotaBar } from "@/components/depot/QuotaBar"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { fmtBytes, useDeleteDepotFile, useDepotFiles, type DepotFile } from "@/lib/depot"
import { driveDownloadUrl, driveFileUrl } from "@/lib/sharing"

function fmtWhen(iso: string | null, lang: "tr" | "en") {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString(lang === "tr" ? "tr-TR" : "en-US", {
    day: "2-digit", month: "short", year: "numeric",
  })
}

export function DepotPage() {
  const { t, lang } = useI18n()
  const [q, setQ] = useState("")
  const [uploadOpen, setUploadOpen] = useState(false)
  const [confirm, setConfirm] = useState<DepotFile | null>(null)
  const { data, isLoading, isError } = useDepotFiles({ q })
  const del = useDeleteDepotFile()

  const files = data?.files ?? []
  const quota = data?.quota

  async function copyLink(fileId: string) {
    try {
      await navigator.clipboard.writeText(driveFileUrl(fileId))
      toast.success(t("pages.depot.linkCopied"))
    } catch {
      toast.error(t("pages.depot.copyFailed"))
    }
  }

  function doDelete() {
    if (!confirm) return
    del.mutate(confirm.id, {
      onSuccess: (r) => {
        setConfirm(null)
        if (r?.drive_ok === false) {
          toast.warning(t("pages.depot.deletedDriveFailed"))
        } else {
          toast.success(t("pages.depot.deleted"))
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : t("pages.depot.deleteFailed")),
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <HardDrive className="h-5 w-5" /> {t("pages.depot.title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("pages.depot.subtitle")}
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Upload className="mr-1 h-4 w-4" /> {t("pages.depot.uploadFile")}
        </Button>
      </div>

      {/* Permanent warning: files are public via link (user decision 2026-07-25) */}
      <div className="flex items-start gap-2 rounded-lg border border-amber-300/60 bg-amber-50 p-3 text-sm dark:border-amber-900/50 dark:bg-amber-900/20">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <p>
          <span className="font-medium">{t("pages.depot.sharedAreaLabel")}</span> {t("pages.depot.sharedAreaDesc")}{" "}
          <span className="font-medium">{t("pages.depot.anyoneCanDownload")}</span> {t("pages.depot.noPrivateDocs")}
        </p>
      </div>

      {quota && <QuotaBar quota={quota} />}

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder={t("pages.depot.searchPlaceholder")} value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("pages.depot.colFile")}</TableHead>
              <TableHead className="hidden sm:table-cell">{t("pages.depot.colSize")}</TableHead>
              <TableHead className="hidden md:table-cell">{t("pages.depot.colUploader")}</TableHead>
              <TableHead className="hidden lg:table-cell">{t("pages.depot.colDate")}</TableHead>
              <TableHead className="text-right">{t("pages.depot.colActions")}</TableHead>
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
                  {t("pages.depot.loadFailed")}
                </TableCell>
              </TableRow>
            )}
            {data && files.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  {q ? t("pages.depot.noMatch") : t("pages.depot.empty")}
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
                  {fmtBytes(f.file_size, lang)}
                </TableCell>
                <TableCell className="hidden text-muted-foreground md:table-cell">
                  {f.uploader_name || "—"}
                </TableCell>
                <TableCell className="hidden whitespace-nowrap text-muted-foreground lg:table-cell">
                  {fmtWhen(f.uploaded_at, lang)}
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    {/* Depot files are NOT COPIED to `media_store` (depot.py: 500 MB
                        files stream directly to Drive) → download comes straight from Drive.
                        The file has "anyone with the link" permission (grant_anyone_reader). */}
                    <a href={driveDownloadUrl(f.file_id)} download title={t("pages.depot.downloadFileTitle")}
                      className="inline-flex h-8 items-center rounded-md border px-2.5 text-sm font-medium hover:bg-muted">
                      <Download className="mr-1 h-3.5 w-3.5" /> {t("pages.depot.download")}
                    </a>
                    <Button size="sm" variant="outline" onClick={() => copyLink(f.file_id)}>
                      <Copy className="mr-1 h-3.5 w-3.5" /> {t("pages.depot.copy")}
                    </Button>
                    <a href={driveFileUrl(f.file_id)} target="_blank" rel="noreferrer"
                      title={t("pages.depot.openInDrive")}
                      className="p-1 text-muted-foreground hover:text-foreground">
                      <ExternalLink className="h-4 w-4" />
                    </a>
                    <button type="button" onClick={() => setConfirm(f)} title={t("pages.depot.delete")}
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
            <DialogHeader><DialogTitle>{t("pages.depot.deleteFileTitle")}</DialogTitle></DialogHeader>
            <p className="text-sm">
              <span className="font-medium">{confirm.file_name}</span> {t("pages.depot.deleteConfirmBody")}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setConfirm(null)}>{t("pages.depot.cancel")}</Button>
              <Button variant="destructive" onClick={doDelete} disabled={del.isPending}>
                {del.isPending ? t("pages.depot.deleting") : t("pages.depot.delete")}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
