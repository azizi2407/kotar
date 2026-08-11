// TanStack Query hooks + constants for clients. API contract is api.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson } from "./api"
import type { ClientDetail, ClientListItem, User } from "./types"

// Fixed team role slots (legacy team_assignments keys). `label` here is the
// default (TR) text — the UI uses the t()-translated version (see
// dictionaries/clients.ts components.clients.roleSlots.*); the raw `label` is
// only used where t() isn't available (e.g. a type default).
export const ROLE_SLOTS = [
  { key: "designer", label: "Tasarımcı", labelKey: "components.clients.roleSlots.designer" },
  { key: "content_creator", label: "İçerik Üretici", labelKey: "components.clients.roleSlots.contentCreator" },
  { key: "videographer_shoot", label: "Videografçı (Çekim)", labelKey: "components.clients.roleSlots.videographerShoot" },
  { key: "videographer_edit", label: "Videografçı (Kurgu)", labelKey: "components.clients.roleSlots.videographerEdit" },
] as const

export interface ClientForm {
  name: string
  sector?: string | null
  notes?: string | null
  client_email?: string | null
  instagram_url?: string | null
  google_drive_url?: string | null
  contract?: Partial<ClientDetail["contract"]> | null
  contacts?: ClientDetail["contacts"]
  locations?: ClientDetail["locations"]
  team_assignments?: Record<string, string>
}

export function useClients(params: { status: string; q: string }) {
  const qs = new URLSearchParams({ status: params.status })
  if (params.q) qs.set("q", params.q)
  return useQuery<ClientListItem[]>({
    queryKey: ["clients", params.status, params.q],
    queryFn: () => apiGet(`/clients?${qs}`).then((d) => d.clients),
  })
}

export function useClient(id: number | null) {
  return useQuery<ClientDetail>({
    queryKey: ["client", id],
    enabled: id != null,
    queryFn: () => apiGet(`/clients/${id}`).then((d) => d.client),
  })
}

export function useUsers() {
  return useQuery<User[]>({
    queryKey: ["users"],
    queryFn: () => apiGet("/users").then((d) => d.users),
  })
}

// Bulk designer assignment — only touches the 'designer' slot (other team slots
// are preserved). user_id null → assignment removed. Contract: api.py /clients/assign-designer.
export function useAssignDesigner() {
  const qc = useQueryClient()
  return useMutation<{ updated: number }, Error, { client_id: number; user_id: string | null }[]>({
    mutationFn: (assignments) => apiJson("/clients/assign-designer", { assignments }),
    onSuccess: () => invalidateAll(qc),
  })
}

function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["clients"] })
  qc.invalidateQueries({ queryKey: ["client"] })
}

export function useCreateClient() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ClientForm) => apiJson("/clients", body).then((d) => d.client),
    onSuccess: () => invalidateAll(qc),
  })
}

export function useUpdateClient(id: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Partial<ClientForm>) =>
      apiJson(`/clients/${id}`, body, "PATCH").then((d) => d.client),
    onSuccess: () => invalidateAll(qc),
  })
}

export function useDeleteClient() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      apiDelete(`/clients/${id}`, reason ? { reason } : undefined),
    onSuccess: () => invalidateAll(qc),
  })
}

export function useRestoreClient() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiJson(`/clients/${id}/restore`, {}),
    onSuccess: () => invalidateAll(qc),
  })
}

// --- Phase 3 / Option A: EDITABLE Ayar (vault retired — panel DB is the sole authority) ---
// Contract: api.py (/clients/:id/{brief-enabled,vault-ayar,catch-up,onboarding-prompt}).

// Ayar schema — a flat view of `brand_profile` (JSON) + `caption_settings`
// (separate column). GET returns sensible defaults ('' / [] / {}) for empty/missing
// fields; PUT does a partial merge.
export interface VaultAyar {
  brand_voice: string
  target_audience: string
  cta: string
  forbidden: string[]
  color_palette: string[]
  content_mix: Record<string, number>
  content_pillars: string
  guide_md: string
  hashtags: Record<string, string[]>
  posting_days: string[]
  ideas_per_week: number
  caption_settings: Record<string, unknown>
}
// Backend always returns 200 + {ayar}; an extra `onboarding:true` flag if brand_profile is empty.
export type VaultAyarResult = { ayar: VaultAyar; onboarding: boolean }

export function useVaultAyar(clientId: number | null) {
  return useQuery<VaultAyarResult>({
    queryKey: ["vault-ayar", clientId],
    enabled: clientId != null,
    retry: false,
    queryFn: async () => {
      const d = await apiGet(`/clients/${clientId}/vault-ayar`)
      return { ayar: d.ayar as VaultAyar, onboarding: !!d.onboarding }
    },
  })
}

// Save Ayar (PUT, partial merge) — management + CSRF. Refreshes the Ayar + client
// cache (brief/caption generation reads these fields; effective immediately on save).
export function usePutVaultAyar(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Partial<VaultAyar>) =>
      apiJson(`/clients/${clientId}/vault-ayar`, body, "PUT").then((d) => d.ayar as VaultAyar),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vault-ayar", clientId] })
      qc.invalidateQueries({ queryKey: ["client", clientId] })
    },
  })
}

// Onboarding starter prompt (only fetched when Ayar doesn't exist yet) — plain text.
export function useOnboardingPrompt(clientId: number | null, enabled: boolean) {
  return useQuery<string>({
    queryKey: ["onboarding-prompt", clientId],
    enabled: enabled && clientId != null,
    queryFn: () => apiGet(`/clients/${clientId}/onboarding-prompt`).then((d) => d.prompt as string),
  })
}

// Brief on/off toggle → Client.brief_enabled; refreshes the client + list cache.
export function useSetBriefEnabled(clientId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) =>
      apiJson(`/clients/${clientId}/brief-enabled`, { enabled }, "PATCH").then((d) => d.client as ClientDetail),
    onSuccess: () => invalidateAll(qc),
  })
}

// Catch-up: enqueues brief jobs for missing weeks → {enqueued, weeks}.
export function useCatchUp(clientId: number) {
  return useMutation<{ enqueued: number; weeks: string[] }, Error, void>({
    mutationFn: () => apiJson(`/clients/${clientId}/catch-up`, {}),
  })
}
