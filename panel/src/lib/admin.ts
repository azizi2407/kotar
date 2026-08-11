// User management API — /api/admin/* (AUTH_MODE=local: local table;
// AUTH_MODE=oidc: proxies an external provider, see admin_api.py). Superadmin only.
import { apiGet, apiJson } from "@/lib/api"
import { useI18n } from "@/lib/i18n"

export interface SsoUser {
  id: number
  email: string
  name: string | null
  role: string
  status: string // active | disabled
  linked: boolean // has logged in at least once (google_sub set) — always true under AUTH_MODE=local
  last_login: string | null
  created_at: string | null
  // AUTH_MODE=local: returned ONCE in the create/password-reset response (see local_admin.py).
  temp_password?: string
}

export const ROLES = [
  "management",
  "designer",
  "videographer",
  "content_creator",
  "client",
  "restaurant_owner",
  "pending",
] as const

export function useRoleLabels(): Record<string, string> {
  const { t } = useI18n()
  return {
    management: t("pages.users.role.management"),
    designer: t("pages.users.role.designer"),
    videographer: t("pages.users.role.videographer"),
    content_creator: t("pages.users.role.contentCreator"),
    client: t("pages.users.role.client"),
    restaurant_owner: t("pages.users.role.restaurantOwner"),
    pending: t("pages.users.role.pending"),
  }
}

export const listUsers = () => apiGet("/admin/users").then((r) => r.users as SsoUser[])

export const createUser = (body: { email: string; role: string; name?: string }) =>
  apiJson("/admin/users", body).then((r) => r.user as SsoUser)

export const updateUser = (id: number, body: { role?: string; status?: string; name?: string }) =>
  apiJson(`/admin/users/${id}`, body, "PATCH").then((r) => r.user as SsoUser)
