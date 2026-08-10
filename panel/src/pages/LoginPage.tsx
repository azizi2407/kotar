// Yerel giriş sayfası (AUTH_MODE=local). AUTH_MODE=oidc iken bu sayfaya hiç
// yönlendirilmez — Flask /auth/login doğrudan sağlayıcıya (Google/Keycloak/...)
// gönderir. Buraya erişim yalnız AUTH_MODE=local'de, Flask /auth/login'in
// /panel/login'e 302'siyle olur (bkz. auth.py login()).
import { useEffect, useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"

export function LoginPage() {
  const { user, loading } = useAuth()
  const { t } = useI18n()
  const navigate = useNavigate()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!loading && user) navigate("/", { replace: true })
  }, [loading, user, navigate])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!email.trim() || !password) return
    setBusy(true)
    try {
      const res = await fetch("/auth/local-login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        toast.error(data?.error || t("login.failed"))
        setBusy(false)
        return
      }
      // Tam sayfa yenileme: AuthProvider'ın ilk /session çağrısı yeni oturumla tazelensin.
      window.location.href = "/panel/"
    } catch {
      toast.error(t("login.networkError"))
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-xl border bg-background p-6 shadow-sm"
      >
        <div className="space-y-1 text-center">
          <h1 className="text-lg font-semibold">{t("login.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("login.subtitle")}</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="login-email">{t("login.email")}</Label>
          <Input
            id="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="ad@ornek.com"
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="login-password">{t("login.password")}</Label>
          <Input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? t("login.submitting") : t("login.submit")}
        </Button>
      </form>
    </div>
  )
}
