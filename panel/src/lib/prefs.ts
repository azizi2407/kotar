// Kişisel tercihler — /api/prefs. Tercih KULLANICIYA aittir (sayfaya değil);
// backend `owner_sub`'ı oturumdan alır, başka kullanıcının tercihi adreslenemez.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiGet, apiJson } from "@/lib/api"

export type PrefScope = "videographer_upload"

/** Bu kapsamda gizlenen müşteri id'leri. */
export function useHiddenClients(scope: PrefScope) {
  return useQuery<number[]>({
    queryKey: ["hidden-clients", scope],
    queryFn: () => apiGet(`/prefs/hidden-clients?scope=${scope}`).then((d) => d.client_ids),
  })
}

export function useSetClientHidden(scope: PrefScope) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { client_id: number; hidden: boolean }) =>
      apiJson("/prefs/hidden-clients", { scope, ...v }, "PUT")
        .then((d) => d.client_ids as number[]),
    onSuccess: (ids) => {
      // Yanıt TAM küme → yerel türetme yapmadan doğrudan yaz.
      qc.setQueryData(["hidden-clients", scope], ids)
      qc.invalidateQueries({ queryKey: ["videographer-board"] })
    },
  })
}
