// Kullanıcı yönetimi API'si — /api/admin/* (AUTH_MODE=local: yerel tablo;
// AUTH_MODE=oidc: dış sağlayıcı proxy'si, bkz. admin_api.py). Yalnız superadmin.
import { apiGet, apiJson } from "@/lib/api"

export interface SsoUser {
  id: number
  email: string
  name: string | null
  role: string
  status: string // active | disabled
  linked: boolean // en az bir kez giriş yaptı mı (google_sub dolu) — AUTH_MODE=local'de her zaman true
  last_login: string | null
  created_at: string | null
  // AUTH_MODE=local: oluşturma/parola sıfırlama yanıtında BİR KEZ döner (bkz. local_admin.py).
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

export const ROLE_LABELS: Record<string, string> = {
  management: "Yönetim",
  designer: "Tasarımcı",
  videographer: "Videografçı",
  content_creator: "İçerik Üretici",
  client: "Müşteri",
  restaurant_owner: "Restoran Sahibi",
  pending: "Beklemede",
}

export const listUsers = () => apiGet("/admin/users").then((r) => r.users as SsoUser[])

export const createUser = (body: { email: string; role: string; name?: string }) =>
  apiJson("/admin/users", body).then((r) => r.user as SsoUser)

export const updateUser = (id: number, body: { role?: string; status?: string; name?: string }) =>
  apiJson(`/admin/users/${id}`, body, "PATCH").then((r) => r.user as SsoUser)
