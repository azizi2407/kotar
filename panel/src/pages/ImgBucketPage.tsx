// Image Bucket (img-bucket) — admin uploads images and embeds them into external
// sites via the /img/<name> public URL. Upload / copy / convert to webp / delete.
import { useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Copy, FileImage, RefreshCw, Trash2, Upload } from "lucide-react"
import { toast } from "sonner"

import { ApiError, apiGet, apiJson, apiUpload } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Progress } from "@/components/ui/progress"

interface BucketFile { name: string; url: string; size: number; mtime: number }
interface BucketList { files: BucketFile[]; usage: { used: number; total: number } }

function humanSize(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function ImgBucketPage() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const { data, isLoading } = useQuery<BucketList>({
    queryKey: ["img-bucket"],
    queryFn: () => apiGet("/tools/img-bucket/list"),
  })
  const inv = () => qc.invalidateQueries({ queryKey: ["img-bucket"] })

  const [pct, setPct] = useState(0)
  const upload = useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData()
      files.forEach((f) => form.append("files", f))
      setPct(0)
      return apiUpload("/tools/img-bucket/upload", form, setPct)
    },
    onSuccess: (r: { saved: unknown[]; errors: string[] }) => {
      inv()
      if (r.errors?.length) toast.error(r.errors.join(", "))
      if (r.saved?.length) toast.success(t("pages.imgBucket.uploadedCount", { count: r.saved.length }))
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : t("pages.imgBucket.uploadFailed")),
  })
  const remove = useMutation({
    mutationFn: (name: string) => apiJson("/tools/img-bucket/delete", { name }),
    onSuccess: inv,
  })
  const convert = useMutation({
    mutationFn: (name: string) => apiJson("/tools/img-bucket/convert", { name }),
    onSuccess: () => { inv(); toast.success(t("pages.imgBucket.convertedToWebp")) },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : t("pages.imgBucket.convertFailed")),
  })

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    // e.target.files is a live FileList; input.value="" clears it. So COPY the
    // files into a real array FIRST, THEN reset the input (otherwise the upload
    // silently never fires).
    const files = Array.from(e.target.files ?? [])
    e.target.value = ""
    if (files.length) upload.mutate(files)
  }
  function copy(url: string) {
    navigator.clipboard.writeText(url)
    toast.success(t("pages.imgBucket.urlCopied"))
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("pages.imgBucket.title")}</h1>
          <p className="text-muted-foreground">{t("pages.imgBucket.subtitle")}</p>
        </div>
        <input ref={fileInput} type="file" accept="image/*" multiple className="hidden" onChange={onPick} />
        <Button onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
          <Upload className="mr-1 h-4 w-4" />
          {upload.isPending ? t("pages.imgBucket.uploading") : t("pages.imgBucket.uploadButton")}
        </Button>
      </div>

      {upload.isPending && (
        <div className="space-y-1">
          <Progress value={pct} />
          <p className="text-[11px] text-muted-foreground">{t("pages.imgBucket.uploadingPct", { pct })}</p>
        </div>
      )}
      {data && (
        <div className="text-sm text-muted-foreground">
          {t("pages.imgBucket.summary", {
            count: data.files.length,
            used: humanSize(data.usage.used),
            total: humanSize(data.usage.total),
          })}
        </div>
      )}

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-48 w-full" />)}
        </div>
      ) : data && data.files.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">{t("pages.imgBucket.empty")}</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {data?.files.map((f) => (
            <div key={f.name} className="flex flex-col overflow-hidden rounded-lg border">
              <div className="flex aspect-square items-center justify-center bg-muted">
                {/\.svg$/i.test(f.name)
                  ? <FileImage className="h-10 w-10 text-muted-foreground" />
                  : <img src={f.url} alt={f.name} loading="lazy" className="h-full w-full object-cover" />}
              </div>
              <div className="flex flex-col gap-1.5 p-2">
                <div className="truncate text-xs font-medium" title={f.name}>{f.name}</div>
                <Badge variant="outline" className="w-fit text-[10px] text-muted-foreground">{humanSize(f.size)}</Badge>
                <div className="flex gap-1">
                  <Button variant="outline" size="sm" className="flex-1" onClick={() => copy(f.url)}>
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                  {!/\.(webp|svg)$/i.test(f.name) && (
                    <Button variant="outline" size="sm" onClick={() => convert.mutate(f.name)}
                      title={t("pages.imgBucket.convertToWebp")} disabled={convert.isPending}>
                      <RefreshCw className="h-3.5 w-3.5" />
                    </Button>
                  )}
                  <Button variant="outline" size="sm" className="text-destructive"
                    onClick={() => remove.mutate(f.name)} title={t("pages.imgBucket.delete")}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
