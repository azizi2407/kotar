// AI görsel üretimi — tek referans slotu (yapı VEYA stil). Kaynak: videograf fotoğrafı
// (Drive file_id), elle URL, veya anında yükleme (base64). Seçim ImageRef olarak dışarı
// verilir; Mystic tarafında base64'e backend çözer (structure_reference/style_reference).
import { useRef, useState } from "react"
import { X } from "lucide-react"
import { toast } from "sonner"

import { thumbnailUrl, useClientAssets, useVgPhotos, type ImageRef } from "@/lib/sharing"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const MAX_REF_MB = 8

export function ReferencePicker({
  label,
  clientId,
  value,
  onChange,
}: {
  label: string
  clientId: number
  value: ImageRef | null
  onChange: (ref: ImageRef | null) => void
}) {
  const { data: photos } = useVgPhotos(clientId)
  const { data: assets } = useClientAssets(clientId)
  // Yalnız gerçek görseller: marka görselleri arasında PDF/SVG/EPS logolar da var
  // (2026-08-04 toplu aktarımı). Backend referansı ham bytes indirip görsel bekler
  // (ai_worker._resolve_reference_bytes) — bunlar üretimi kırar. İndirilebilir
  // kalırlar, yalnız bu listede çıkmazlar.
  // (SVG de image/* ama raster değil — o da elenir.)
  const imageAssets = (assets ?? []).filter(
    (a) => (a.mime_type ?? "").startsWith("image/") && a.mime_type !== "image/svg+xml",
  )
  const [url, setUrl] = useState("")
  const fileInput = useRef<HTMLInputElement>(null)

  function pickFile(file: File) {
    if (file.size > MAX_REF_MB * 1024 * 1024) {
      toast.error(`Referans ${MAX_REF_MB} MB sınırını aşıyor`)
      return
    }
    const reader = new FileReader()
    reader.onload = () =>
      onChange({ kind: "base64", value: String(reader.result), label: file.name })
    reader.readAsDataURL(file)
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <Label>{label}</Label>
        {value && (
          <button
            type="button"
            onClick={() => onChange(null)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-destructive"
          >
            <X className="size-3" /> Kaldır
          </button>
        )}
      </div>

      {value ? (
        <div className="flex items-center gap-2">
          <img
            src={value.kind === "drive" ? thumbnailUrl(value.value, 120) : value.value}
            alt=""
            className="size-16 rounded bg-muted object-cover"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }}
          />
          <span className="truncate text-xs text-muted-foreground">
            {value.label || (value.kind === "url" ? value.value : value.kind)}
          </span>
        </div>
      ) : (
        <>
          {imageAssets.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] font-medium text-muted-foreground">Marka görselleri</div>
              <div className="grid max-h-32 grid-cols-4 gap-1 overflow-auto">
                {imageAssets.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() =>
                      onChange({ kind: "drive", value: a.file_id,
                        label: a.label ?? a.file_name ?? (a.kind === "logo" ? "Logo" : undefined) })}
                    className="overflow-hidden rounded border hover:border-primary"
                    title={a.label ?? a.file_name ?? (a.kind === "logo" ? "Logo" : "")}
                  >
                    <img
                      src={thumbnailUrl(a.file_id, 100)}
                      alt=""
                      loading="lazy"
                      className="aspect-square w-full bg-muted object-cover"
                      onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }}
                    />
                  </button>
                ))}
              </div>
            </div>
          )}
          {(photos ?? []).filter((p) => p.file_id).length > 0 && (
            <div className="grid max-h-40 grid-cols-4 gap-1 overflow-auto">
              {(photos ?? []).filter((p) => p.file_id).map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() =>
                    onChange({ kind: "drive", value: p.file_id!, label: p.file_name ?? undefined })}
                  className="overflow-hidden rounded border hover:border-primary"
                  title={p.file_name ?? ""}
                >
                  <img
                    src={thumbnailUrl(p.file_id!, 100)}
                    alt=""
                    loading="lazy"
                    className="aspect-square w-full bg-muted object-cover"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden" }}
                  />
                </button>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <Input
              placeholder="Görsel URL'si"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && url.trim()) onChange({ kind: "url", value: url.trim() })
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => url.trim() && onChange({ kind: "url", value: url.trim() })}
            >
              Ekle
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
              Yükle
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) pickFile(f); e.target.value = "" }}
            />
          </div>
        </>
      )}
    </div>
  )
}
