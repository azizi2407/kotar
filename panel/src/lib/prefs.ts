// Personal preferences — /api/prefs. A preference belongs to the USER (not the page);
// the backend takes `owner_sub` from the session, so another user's preference can't be addressed.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiGet, apiJson } from "@/lib/api"

export type PrefScope = "videographer_upload"

/** Client ids hidden within this scope. */
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
      // The response is the FULL set → write it directly without local derivation.
      qc.setQueryData(["hidden-clients", scope], ids)
      qc.invalidateQueries({ queryKey: ["videographer-board"] })
    },
  })
}
