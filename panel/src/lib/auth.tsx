// Kimlik OIDC sağlayıcısına delege edilir (AUTH_MODE=oidc) veya yerel e-posta/
// parola girişiyle yapılır (AUTH_MODE=local). Oturum yoksa tarayıcı /auth/login'e
// yönlenir. Oturum + CSRF token'ı /api/session'dan tek çağrıda alınır (setCsrf ile
// api'ye verilir). Rol yardımcıları (isManagement) yetki-bazlı UI için.
//
// Impersonation ("kullanıcı gözünden bak"): yalnız superadmin. session['user']
// hedef kullanıcıya çevrilir (etkin kimlik), gerçek kimlik backend'de saklanır.
// impersonate/stop sonrası tam sayfa yenileme → tüm veri yeni kimlikle tazelenir.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

import { apiGet, apiJson, setCsrf } from "./api"
import type { User } from "./types"

interface AuthState {
  user: User | null
  loading: boolean
  isManagement: boolean
  impersonating: boolean
  realUser: User | null
  canImpersonate: boolean
  isAgencyOwner: boolean
  authMode: "local" | "oidc" | null
  impersonate: (sub: string) => Promise<void>
  stopImpersonate: () => Promise<void>
  login: () => void
  logout: () => void
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [realUser, setRealUser] = useState<User | null>(null)
  const [impersonating, setImpersonating] = useState(false)
  const [canImpersonate, setCanImpersonate] = useState(false)
  const [isAgencyOwner, setIsAgencyOwner] = useState(false)
  const [authMode, setAuthMode] = useState<"local" | "oidc" | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiGet("/session")
      .then((d) => {
        setUser(d.user)
        setRealUser(d.real_user ?? d.user)
        setImpersonating(Boolean(d.impersonating))
        setCanImpersonate(Boolean(d.can_impersonate))
        setIsAgencyOwner(Boolean(d.is_agency_owner))
        setAuthMode(d.auth_mode ?? null)
        setCsrf(d.csrf)
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  // Tam sayfa yönlendirme (SPA route değil) — SSO akışı sunucu taraflı.
  function login() {
    window.location.href = "/auth/login"
  }
  function logout() {
    window.location.href = "/auth/logout"
  }

  // Kimlik değişince tüm cache'leri sıfırlamak için tam sayfa yenile. Impersonation'a
  // GİRERKEN ana sayfaya (/) git: mevcut sayfa hedef rolün erişemediği bir yönetim
  // sayfası olabilir (ör. Sharing Board yalnız management) → orada kalırsak 403 alırdık.
  // "/" her role açık; kullanıcı oradan hedef rolün nav'ıyla gezmeye başlar.
  async function impersonate(sub: string) {
    await apiJson("/impersonate", { sub })
    window.location.href = import.meta.env.BASE_URL || "/panel/"
  }
  async function stopImpersonate() {
    await apiJson("/impersonate/stop", {})
    window.location.reload()
  }

  return (
    <Ctx.Provider
      value={{
        user, loading, isManagement: user?.role === "management",
        impersonating, realUser, canImpersonate, isAgencyOwner, authMode,
        impersonate, stopImpersonate, login, logout,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

export function useAuth() {
  const c = useContext(Ctx)
  if (!c) throw new Error("useAuth AuthProvider içinde kullanılmalı")
  return c
}
