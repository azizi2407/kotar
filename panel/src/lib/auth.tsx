// Identity is delegated to the OIDC provider (AUTH_MODE=oidc) or done via local
// email/password login (AUTH_MODE=local). Without a session, the browser is redirected
// to /auth/login. The session + CSRF token are fetched from /api/session in a single
// call (handed to the api via setCsrf). Role helpers (isManagement) are for permission-based UI.
//
// Impersonation ("view as this user"): superadmin only. session['user'] is switched to
// the target user (effective identity), the real identity is kept on the backend.
// A full page reload after impersonate/stop → all data refreshes under the new identity.
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

  // Full page redirect (not an SPA route) — the SSO flow is server-side.
  function login() {
    window.location.href = "/auth/login"
  }
  function logout() {
    window.location.href = "/auth/logout"
  }

  // Full page reload to reset all caches when identity changes. WHEN ENTERING
  // impersonation, go to the home page (/): the current page might be a management
  // page the target role can't access (e.g. Sharing Board is management-only) → staying
  // there would get us a 403. "/" is open to every role; the user starts browsing
  // from there with the target role's nav.
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
