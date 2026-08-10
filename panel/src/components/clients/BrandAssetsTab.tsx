// Müşteri "Marka Görselleri" sekmesi: logo (tekil) + sabit standart görseller (çoklu;
// ör. ürün etiketleri). Drive'da 'Marka Görselleri' klasöründe saklanır; AI görsel
// üretiminde referans olarak seçilir (ImageGenPage → ReferencePicker).
import { useRef, useState } from "react"
import { Download, Image as ImageIcon, Loader2, Trash2, Upload } from "lucide-react"
import { toast } from "sonner"

import {
  downloadClientAsset, thumbnailUrl, useClientAssets, useDeleteClientAsset,
  useUploadClientAsset, type ClientAsset,
} from "@/lib/sharing"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"

function AssetThumb({ asset, onDelete, deleting }: {
  asset: ClientAsset; onDelete: () => void; deleting: boolean
}) {
  return (
    <div className="group relative overflow-hidden rounded-md border">
      <img
        src={thumbnailUrl(asset.file_id, 200)}
        alt={asset.label ?? asset.file_name ?? ""}
        loading="lazy"
        className="aspect-square w-full bg-muted object-cover"
        onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }}
      />
      <button
        type="button"
        onClick={() => downloadClientAsset(asset.client_id, asset.id)}
        title="İndir"
        className="absolute left-1 top-1 hidden rounded-full bg-background/90 p-1 shadow group-hover:block"
      >
        <Download className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={onDelete}
        disabled={deleting}
        title="Kaldır"
        className="absolute right-1 top-1 hidden rounded-full bg-background/90 p-1 text-destructive shadow group-hover:block"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
      <div className="truncate px-1.5 py-1 text-[11px] text-muted-foreground">
        {asset.label || asset.file_name}
      </div>
    </div>
  )
}

export function BrandAssetsTab({ clientId }: { clientId: number }) {
  const { data: assets, isLoading } = useClientAssets(clientId)
  const upload = useUploadClientAsset(clientId)
  const remove = useDeleteClientAsset(clientId)
  const [label, setLabel] = useState("")
  const logoInput = useRef<HTMLInputElement>(null)
  const stdInput = useRef<HTMLInputElement>(null)

  const logo = (assets ?? []).find((a) => a.kind === "logo") ?? null
  const standards = (assets ?? []).filter((a) => a.kind === "standard")

  function doUpload(file: File, kind: "logo" | "standard") {
    upload.mutate({ file, kind, label: kind === "standard" ? label.trim() || undefined : undefined }, {
      onSuccess: () => { if (kind === "standard") setLabel("") },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Yükleme başarısız"),
    })
  }

  if (isLoading) return <Skeleton className="h-48 w-full" />

  return (
    <div className="grid gap-4 md:grid-cols-[280px_1fr]">
      {/* logo (tekil) */}
      <Card>
        <CardHeader><CardTitle className="text-base">Logo</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {logo ? (
            <AssetThumb asset={logo} deleting={remove.isPending}
              onDelete={() => remove.mutate(logo.id)} />
          ) : (
            <div className="flex aspect-square w-full items-center justify-center rounded-md border border-dashed text-muted-foreground">
              <ImageIcon className="h-8 w-8" />
            </div>
          )}
          <Button variant="outline" size="sm" className="w-full"
            disabled={upload.isPending}
            onClick={() => logoInput.current?.click()}>
            {upload.isPending
              ? <Loader2 className="mr-1 size-4 animate-spin" />
              : <Upload className="mr-1 size-4" />}
            {logo ? "Logoyu değiştir" : "Logo yükle"}
          </Button>
          <input ref={logoInput} type="file" accept="image/*" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) doUpload(f, "logo"); e.target.value = "" }} />
        </CardContent>
      </Card>

      {/* sabit standart görseller (çoklu) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Standart Görseller</CardTitle>
          <p className="text-sm text-muted-foreground">
            AI üretiminde referans olarak kullanılacak sabit görseller (ör. ürün etiketleri).
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input placeholder="Etiket (ör. Beyaz peynir etiketi)" value={label}
              onChange={(e) => setLabel(e.target.value)} />
            <Button variant="outline" disabled={upload.isPending}
              onClick={() => stdInput.current?.click()}>
              {upload.isPending
                ? <Loader2 className="mr-1 size-4 animate-spin" />
                : <Upload className="mr-1 size-4" />}
              Yükle
            </Button>
            <input ref={stdInput} type="file" accept="image/*" multiple className="hidden"
              onChange={(e) => {
                Array.from(e.target.files ?? []).forEach((f) => doUpload(f, "standard"))
                e.target.value = ""
              }} />
          </div>
          {standards.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">Henüz standart görsel yok.</p>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
              {standards.map((a) => (
                <AssetThumb key={a.id} asset={a} deleting={remove.isPending}
                  onDelete={() => remove.mutate(a.id)} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
