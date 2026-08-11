// Design working files (2026-08-07) — versioned source files, per client. Files are
// canonical on the server; downloads go through /api (NOT a Drive link) — permission
// is verified on every download.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson, apiUpload } from "./api"

export interface DesignVersion {
  id: number
  file_id: number
  version_no: number
  file_name: string
  mime_type: string | null
  file_size: number
  note: string | null
  sha256: string
  /** Was a Drive copy created — if false, the panel shows a "not copied to Drive" badge. */
  drive_ok: boolean
  uploader_name: string | null
  uploaded_at: string | null
  can_delete: boolean
  // Trash fields (2026-08-08) — always null/false in the live listing,
  // only meaningful in the trash response (`useTrash`).
  deleted_at: string | null
  deleted_by: string | null
  deleter_name: string | null
  /** Restore permission is computed on the BACKEND — the panel doesn't rebuild the rule. */
  can_restore: boolean
  /** Permanent-delete permission is management-only — computed on the BACKEND. */
  can_purge: boolean
}

export interface DesignFileItem {
  id: number
  client_id: number
  title: string
  tags: string[]
  creator_name: string | null
  created_at: string | null
  current: DesignVersion | null
  version_count: number
  /** Delete permission is computed on the BACKEND — the panel doesn't rebuild the rule. */
  can_delete: boolean
  // Trash fields (2026-08-08) — always null/false in the live listing.
  deleted_at: string | null
  deleted_by: string | null
  deleter_name: string | null
  can_restore: boolean
  can_purge: boolean
  /** The designer's "please purge permanently" request — a flag only, deletes nothing. */
  purge_requested_at: string | null
  purge_requested_by: string | null
  purge_requester_name: string | null
}

export interface DesignTrash {
  /** DELETED FILES (in the trash along with all their versions). */
  files: DesignFileItem[]
  /** INDIVIDUALLY deleted versions of files that are still alive. */
  versions: DesignVersion[]
  /** TOTAL size in trash (bytes) — includes fully deleted files + individually deleted versions. */
  trash_bytes: number
}

export interface Quota {
  used: number
  limit: number
  remaining: number
  pct: number
}

export function useDesignFiles(clientId: number | null) {
  return useQuery<{ files: DesignFileItem[]; quota: Quota }>({
    queryKey: ["design-files", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/design-files/client/${clientId}`),
  })
}

export function useFileVersions(fileId: number | null) {
  return useQuery<DesignVersion[]>({
    queryKey: ["design-file-versions", fileId],
    enabled: fileId != null,
    queryFn: () => apiGet(`/design-files/${fileId}/versions`).then((d) => d.versions),
  })
}

// Trash — called from a component that only mounts when the section is opened
// (same pattern as `SurumGecmisi`), doesn't fire a query while collapsed.
export function useTrash(clientId: number | null) {
  return useQuery<DesignTrash>({
    queryKey: ["design-files-trash", clientId],
    enabled: clientId != null,
    queryFn: () => apiGet(`/design-files/client/${clientId}/trash`),
  })
}

function useInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ["design-files"] })
    qc.invalidateQueries({ queryKey: ["design-file-versions"] })
    qc.invalidateQueries({ queryKey: ["design-files-trash"] })
  }
}

export function useUploadDesignFile(clientId: number) {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ file, title, tags, note, onProgress }: {
      file: File; title: string; tags: string[]; note?: string
      onProgress?: (pct: number) => void
    }) => {
      const form = new FormData()
      form.append("file", file)
      form.append("title", title)
      form.append("tags", JSON.stringify(tags))
      if (note) form.append("note", note)
      // retry DISABLED: this endpoint is NOT IDEMPOTENT — every POST creates a new
      // file (v1). If the connection drops while the server finishes committing and
      // returns the response (see the note in apiUpload), status 0 appears even
      // though the upload has ALREADY happened; an automatic retry would commit the
      // same content a second time, producing a duplicate file + double quota deduction.
      return apiUpload(`/design-files/client/${clientId}`, form, onProgress, false)
    },
    onSuccess: tazele,
  })
}

export function useUploadVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ fileId, file, note, onProgress }: {
      fileId: number; file: File; note?: string
      onProgress?: (pct: number) => void
    }) => {
      const form = new FormData()
      form.append("file", file)
      if (note) form.append("note", note)
      // retry DISABLED — same rationale: every POST creates a new version (not
      // idempotent), a retry would lead to a duplicate version + double quota deduction.
      return apiUpload(`/design-files/${fileId}/versions`, form, onProgress, false)
    },
    onSuccess: tazele,
  })
}

export function usePatchDesignFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ fileId, title, tags }: { fileId: number; title?: string; tags?: string[] }) =>
      apiJson(`/design-files/${fileId}`, { title, tags }, "PATCH"),
    onSuccess: tazele,
  })
}

export function useDeleteDesignFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (fileId: number) => apiDelete(`/design-files/${fileId}`),
    onSuccess: tazele,
  })
}

export function useDeleteVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (versionId: number) => apiDelete(`/design-files/versions/${versionId}`),
    onSuccess: tazele,
  })
}

// --- trash: restore / permanent delete ------------------------------------

export function useRestoreFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (fileId: number) => apiJson(`/design-files/${fileId}/restore`, {}),
    onSuccess: tazele,
  })
}

export function useRestoreVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (versionId: number) => apiJson(`/design-files/versions/${versionId}/restore`, {}),
    onSuccess: tazele,
  })
}

/** Set/clear the designer's "please purge permanently" flag — deletes nothing. */
export function usePurgeRequest() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: ({ fileId, requested }: { fileId: number; requested: boolean }) =>
      apiJson(`/design-files/${fileId}/purge-request`, { requested }),
    onSuccess: tazele,
  })
}

/** Purge permanently — management only, cannot be undone. */
export function usePurgeFile() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (fileId: number) => apiDelete(`/design-files/${fileId}/purge`, { confirm: true }),
    onSuccess: tazele,
  })
}

export function usePurgeVersion() {
  const tazele = useInvalidator()
  return useMutation({
    mutationFn: (versionId: number) =>
      apiDelete(`/design-files/versions/${versionId}/purge`, { confirm: true }),
    onSuccess: tazele,
  })
}

export function designFileDownloadUrl(versionId: number) {
  return `/api/design-files/versions/${versionId}/download`
}

export function formatBytes(n: number) {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

// Color by extension — so the designer doesn't have to scan the list looking for
// "where's the psd". `noExtFallback`: the label shown for extensionless files — since
// this file can't use a React hook, the calling component passes its own
// t("components.designFiles.clientDesignFiles.noExtension") translation; falls back
// to the English default if not given.
export function extBadge(fileName: string, noExtFallback = "FILE") {
  const ext = (fileName.split(".").pop() || "").toLowerCase()
  const renk: Record<string, string> = {
    psd: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
    ai: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
    indd: "bg-pink-500/15 text-pink-700 dark:text-pink-400",
    aep: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
    zip: "bg-muted text-muted-foreground",
  }
  return { ext: ext.toUpperCase() || noExtFallback, cls: renk[ext] ?? "bg-muted text-muted-foreground" }
}
