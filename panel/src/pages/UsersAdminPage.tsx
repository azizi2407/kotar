// Users — svc-sso user management (superadmin only). Add a user (pre-create by
// email), assign a role, active/disabled. Identity lives in svc-sso; this page proxies it.
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { UserPlus } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { createUser, listUsers, ROLES, updateUser, useRoleLabels, type SsoUser } from "@/lib/admin"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"

function fmtDate(s: string | null) {
  if (!s) return "—"
  return new Date(s).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" })
}

function RoleSelect({ value, onChange, disabled }: { value: string; onChange: (r: string) => void; disabled?: boolean }) {
  const roleLabels = useRoleLabels()
  return (
    <select
      className="h-8 rounded-md border bg-background px-2 text-sm"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {ROLES.map((r) => (
        <option key={r} value={r}>
          {roleLabels[r] ?? r}
        </option>
      ))}
      {!ROLES.includes(value as (typeof ROLES)[number]) && <option value={value}>{value}</option>}
    </select>
  )
}

export function UsersAdminPage() {
  const { authMode } = useAuth()
  const { t } = useI18n()
  const qc = useQueryClient()
  const usersQ = useQuery({ queryKey: ["admin-users"], queryFn: listUsers })
  const [addOpen, setAddOpen] = useState(false)
  const [resetPwFor, setResetPwFor] = useState<SsoUser | null>(null)

  const patch = useMutation({
    mutationFn: (v: { id: number; body: { role?: string; status?: string; reset_password?: boolean } }) =>
      updateUser(v.id, v.body),
    onSuccess: (user) => {
      qc.invalidateQueries({ queryKey: ["admin-users"] })
      if (user.temp_password) setResetPwFor(user)
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : t("pages.users.updateFailed")),
  })

  const users = usersQ.data ?? []

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t("pages.users.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {authMode === "local"
              ? t("pages.users.subtitleLocal")
              : t("pages.users.subtitleSso")}
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <UserPlus className="mr-1 h-4 w-4" /> {t("pages.users.addUser")}
        </Button>
      </div>

      {usersQ.isLoading && <Skeleton className="h-64 w-full" />}
      {usersQ.isError && (
        <p className="text-sm text-red-600">
          {usersQ.error instanceof Error ? usersQ.error.message : t("pages.users.loadFailed")}
        </p>
      )}

      {usersQ.isSuccess && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2">{t("pages.users.table.email")}</th>
                <th className="px-3 py-2">{t("pages.users.table.name")}</th>
                <th className="px-3 py-2">{t("pages.users.table.role")}</th>
                <th className="px-3 py-2">{t("pages.users.table.status")}</th>
                <th className="px-3 py-2">{t("pages.users.table.lastLogin")}</th>
                {authMode === "local" && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {users.map((u: SsoUser) => (
                <tr key={u.id} className="border-b last:border-0">
                  <td className="px-3 py-2">
                    <span className="font-medium">{u.email}</span>
                    {!u.linked && (
                      <span className="ml-1 text-xs text-amber-600" title={t("pages.users.notLoggedInYet")}>
                        {t("pages.users.pending")}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{u.name || "—"}</td>
                  <td className="px-3 py-2">
                    <RoleSelect
                      value={u.role}
                      disabled={patch.isPending}
                      onChange={(role) => patch.mutate({ id: u.id, body: { role } })}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <Button
                      size="sm"
                      variant={u.status === "active" ? "outline" : "secondary"}
                      className={u.status === "active" ? "" : "text-red-600"}
                      disabled={patch.isPending}
                      onClick={() =>
                        patch.mutate({
                          id: u.id,
                          body: { status: u.status === "active" ? "disabled" : "active" },
                        })
                      }
                    >
                      {u.status === "active" ? t("pages.users.active") : t("pages.users.inactive")}
                    </Button>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{fmtDate(u.last_login)}</td>
                  {authMode === "local" && (
                    <td className="px-3 py-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={patch.isPending}
                        onClick={() => patch.mutate({ id: u.id, body: { reset_password: true } })}
                      >
                        {t("pages.users.resetPassword")}
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {addOpen && <AddUserDialog onClose={() => setAddOpen(false)} onAdded={() => qc.invalidateQueries({ queryKey: ["admin-users"] })} />}
      {resetPwFor?.temp_password && (
        <Dialog open onOpenChange={(o) => !o && setResetPwFor(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("pages.users.newTempPasswordTitle", { email: resetPwFor.email })}</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {t("pages.users.tempPasswordHint")}
              </p>
              <code className="block rounded-md border bg-muted px-3 py-2 text-sm font-mono">
                {resetPwFor.temp_password}
              </code>
              <div className="flex justify-end pt-1">
                <Button onClick={() => setResetPwFor(null)}>{t("pages.users.close")}</Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}

function AddUserDialog({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const { authMode } = useAuth()
  const { t } = useI18n()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState("designer")
  const [busy, setBusy] = useState(false)
  const [tempPassword, setTempPassword] = useState<string | null>(null)

  async function submit() {
    if (!email.trim()) {
      toast.error(t("pages.users.emailRequired"))
      return
    }
    setBusy(true)
    try {
      const user = await createUser({ email: email.trim().toLowerCase(), role })
      onAdded()
      if (user.temp_password) {
        // local mode: the temporary password only comes back in this response — if we
        // close without showing it, the account becomes unusable, so the dialog stays open.
        setTempPassword(user.temp_password)
      } else {
        toast.success(t("pages.users.userAdded"))
        onClose()
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("pages.users.addFailed"))
    } finally {
      setBusy(false)
    }
  }

  if (tempPassword) {
    return (
      <Dialog open onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("pages.users.userAddedTitle")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {t("pages.users.tempPasswordFullHint")}
            </p>
            <code className="block rounded-md border bg-muted px-3 py-2 text-sm font-mono">
              {tempPassword}
            </code>
            <div className="flex justify-end pt-1">
              <Button onClick={onClose}>{t("pages.users.close")}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("pages.users.addUser")}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t("pages.users.email")}</Label>
            <Input type="email" placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t("pages.users.role")}</Label>
            <div>
              <RoleSelect value={role} onChange={setRole} />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {authMode === "local"
              ? t("pages.users.addHintLocal")
              : t("pages.users.addHintSso")}
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {t("pages.users.cancel")}
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? t("pages.users.adding") : t("pages.users.add")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
