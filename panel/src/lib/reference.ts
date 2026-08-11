// Client example (reference) accounts (2026-08-07). Contract: sharing.py.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiDelete, apiGet, apiJson } from "./api"

export interface ReferenceAccount {
  id: number
  client_id: number
  handle: string
  title: string | null
  note: string | null
  followers: number | null
  url: string
  source: "manual" | "research"
  /** The backend already returns only `approved` to production roles. */
  status: "candidate" | "approved" | "rejected"
  created_at: string | null
  decided_by: string | null
  decided_at: string | null
}

const anahtar = (clientId: number) => ["reference-accounts", clientId]

export function useReferenceAccounts(clientId: number | null) {
  return useQuery<ReferenceAccount[]>({
    queryKey: anahtar(clientId ?? 0),
    enabled: clientId != null,
    queryFn: () =>
      apiGet(`/sharing/clients/${clientId}/reference-accounts`).then((d) => d.accounts),
  })
}

export function useAddReferenceAccount(clientId: number) {
  const qc = useQueryClient()
  return useMutation<{ account: ReferenceAccount }, Error, { handle: string; title?: string; note?: string }>({
    mutationFn: (v) => apiJson(`/sharing/clients/${clientId}/reference-accounts`, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: anahtar(clientId) }),
  })
}

export function useUpdateReferenceAccount(clientId: number) {
  const qc = useQueryClient()
  return useMutation<{ account: ReferenceAccount }, Error,
    { id: number; status?: string; note?: string; title?: string }>({
    mutationFn: ({ id, ...alanlar }) =>
      apiJson(`/sharing/clients/${clientId}/reference-accounts/${id}`, alanlar, "PATCH"),
    onSuccess: () => qc.invalidateQueries({ queryKey: anahtar(clientId) }),
  })
}

export function useDeleteReferenceAccount(clientId: number) {
  const qc = useQueryClient()
  return useMutation<unknown, Error, number>({
    mutationFn: (id) => apiDelete(`/sharing/clients/${clientId}/reference-accounts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: anahtar(clientId) }),
  })
}
