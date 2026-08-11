// Font pool (2026-08-05) — central pool + client assignment + preview loader.
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
  /** Delete permission is computed on the BACKEND (management: unrestricted / designer:
   *  only what they uploaded). The panel does not re-derive the rule — if it drifted, the button would lie. */
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

// The pool and the client list are fed from the same data → every mutation invalidates both.
function useFontInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["fonts"] })
    qc.invalidateQueries({ queryKey: ["client-fonts"] })
  }
}

// The response has TWO shapes: `{font}` for a single file, `{fonts, skipped}` for a ZIP (2026-08-06).
// The caller collapses both into a single message via `sonucOzeti`.
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

// "2 fonts added (Bigbelow) · 3 files skipped" — what was taken from the zip and
// WHAT was skipped must be visible; a silently swallowed file would confuse the user.
export function sonucOzeti(d: UploadResult, t: (key: string, vars?: Record<string, string | number>) => string): string {
  if (d.font) return t("lib.fonts.singleAdded", { family: d.font.family, style: d.font.style })
  const eklenen = d.fonts ?? []
  const aileler = [...new Set(eklenen.map((f) => f.family))].join(", ")
  const parcalar = [aileler
    ? t("lib.fonts.multiAddedWithFamilies", { count: eklenen.length, families: aileler })
    : t("lib.fonts.multiAdded", { count: eklenen.length })]
  if (d.skipped?.length) parcalar.push(t("lib.fonts.skippedCount", { count: d.skipped.length }))
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

// Loading a font into the browser (preview). The `FontFace` API is cleaner than
// injecting CSS: the same font isn't added twice, the load promise can be awaited,
// and removal is possible. The family name is made unique with the ID — the pool
// can have two files with the same name ("Montserrat" Regular and Bold are separate rows).
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
    YUKLENEN.delete(font.id)      // the failure shouldn't be permanent, retry on the next attempt
    throw e
  })
  YUKLENEN.set(font.id, p)
  return p
}

export function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.round(bytes / 1024)} KB`
}
