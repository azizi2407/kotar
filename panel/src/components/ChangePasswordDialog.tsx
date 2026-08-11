// Change password — only shown under AUTH_MODE=local (see AppLayout).
// Here the user replaces the temporary password an admin created with one of
// their own choosing (see auth.py POST /auth/change-password).
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useI18n } from "@/lib/i18n"

export function ChangePasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n()
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await fetch("/auth/change-password", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        toast.error(data?.error || t("changePassword.failed"))
        return
      }
      toast.success(t("changePassword.success"))
      setCurrent(""); setNext("")
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("changePassword.title")}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="cp-current">{t("changePassword.current")}</Label>
            <Input id="cp-current" type="password" value={current}
                  onChange={(e) => setCurrent(e.target.value)} required autoComplete="current-password" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cp-next">{t("changePassword.new")}</Label>
            <Input id="cp-next" type="password" value={next} minLength={8}
                  onChange={(e) => setNext(e.target.value)} required autoComplete="new-password" />
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? t("changePassword.saving") : t("changePassword.save")}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
