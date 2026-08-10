// Font havuzu (2026-08-05) — merkezi havuz + müşteri ataması + önizleme yükleyici.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson, apiUpload } from "./api"

export interface FontClientRef {
  id: number
  name: string
}

export interface FontItem {
  id: number
  family: string
  style: string
  file_name: string
  format: "ttf" | "otf" | "woff" | "woff2"
  file_size: number
  sha256: string
  uploader_name: string | null
  uploaded_at: string | null
  /** Silme yetkisi BACKEND'te hesaplanır (yönetim ayrımsız / tasarımcı yalnız
   *  kendi yüklediği). Panel kuralı yeniden kurmaz — ayrışırsa düğme yalan söyler. */
  can_delete: boolean
  clients: FontClientRef[]
}

export function useFonts() {
  return useQuery<FontItem[]>({
    queryKey: ["fonts"],
    queryFn: () => apiGet("/fonts").then((d) => d.fonts),
  })
}

export function useClientFonts(clientId: number | null) {
  return useQuery<FontItem[]>({
    queryKey: ["client-fonts", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/clients/${clientId}/fonts`).then((d) => d.fonts),
  })
}

// Havuz ve müşteri listesi aynı veriden besleniyor → her mutasyon ikisini de tazeler.
function useFontInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["fonts"] })
    qc.invalidateQueries({ queryKey: ["client-fonts"] })
  }
}

// Yanıt İKİ şekilli: tek dosyada `{font}`, ZIP'te `{fonts, skipped}` (2026-08-06).
// Çağıran `sonucOzeti` ile ikisini de tek metne indirger.
export interface UploadResult {
  font?: FontItem
  fonts?: FontItem[]
  skipped?: string[]
}

export function useUploadFont() {
  const tazele = useFontInvalidator()
  return useMutation({
    mutationFn: ({ file, family, style, clientId }: {
      file: File; family?: string; style?: string; clientId?: number
    }) => {
      const form = new FormData()
      form.append("file", file)
      if (family) form.append("family", family)
      if (style) form.append("style", style)
      if (clientId != null) form.append("client_id", String(clientId))
      return apiUpload("/fonts", form) as Promise<UploadResult>
    },
    onSuccess: tazele,
  })
}

// "2 font eklendi (Bigbelow) · 3 dosya atlandı" — zip'ten ne alındığı ve NEyin
// atlandığı görünür olmalı; sessizce yutulan dosya kullanıcıyı şaşırtır.
export function sonucOzeti(d: UploadResult): string {
  if (d.font) return `${d.font.family} ${d.font.style} eklendi`
  const eklenen = d.fonts ?? []
  const aileler = [...new Set(eklenen.map((f) => f.family))].join(", ")
  const parcalar = [`${eklenen.length} font eklendi${aileler ? ` (${aileler})` : ""}`]
  if (d.skipped?.length) parcalar.push(`${d.skipped.length} dosya atlandı`)
  return parcalar.join(" · ")
}

export function useRenameFont() {
  const tazele = useFontInvalidator()
  return useMutation({
    mutationFn: ({ id, family, style }: { id: number; family?: string; style?: string }) =>
      apiJson(`/fonts/${id}`, { family, style }, "PUT").then((d) => d.font as FontItem),
    onSuccess: tazele,
  })
}

export function useDeleteFont() {
  const tazele = useFontInvalidator()
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/fonts/${id}`),
    onSuccess: tazele,
  })
}

export function useAssignFont() {
  const tazele = useFontInvalidator()
  return useMutation({
    mutationFn: ({ id, clientId, assigned }: { id: number; clientId: number; assigned: boolean }) =>
      apiJson(`/fonts/${id}/clients`, { client_id: clientId, assigned })
        .then((d) => d.font as FontItem),
    onSuccess: tazele,
  })
}

export function fontFileUrl(id: number) {
  return `/api/fonts/${id}/file`
}

export function downloadFont(font: FontItem) {
  const a = document.createElement("a")
  a.href = `/api/fonts/${font.id}/download`
  a.download = font.file_name
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// Tarayıcıya font yükleme (önizleme). `FontFace` API'si CSS enjekte etmekten
// temiz: aynı font iki kez eklenmez, yükleme sözü (promise) beklenebilir ve
// kaldırma mümkün. Aile adı ID'yle benzersizleştirilir — havuzda aynı isimli
// iki dosya olabilir ("Montserrat" Regular ve Bold ayrı satır).
const YUKLENEN = new Map<number, Promise<void>>()

export function cssFamily(font: FontItem) {
  return `pfont-${font.id}`
}

export function loadFontFace(font: FontItem): Promise<void> {
  const mevcut = YUKLENEN.get(font.id)
  if (mevcut) return mevcut
  const p = (async () => {
    const face = new FontFace(cssFamily(font), `url(${fontFileUrl(font.id)})`)
    await face.load()
    document.fonts.add(face)
  })().catch((e) => {
    YUKLENEN.delete(font.id)      // hata kalıcı olmasın, sonraki denemede tekrar dene
    throw e
  })
  YUKLENEN.set(font.id, p)
  return p
}

export function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.round(bytes / 1024)} KB`
}
