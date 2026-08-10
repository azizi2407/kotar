// Kullanıcılar — svc-sso kullanıcı yönetimi (yalnız superadmin). Kullanıcı ekle
// (e-posta ile ön-oluştur), rol ata, aktif/pasif. Kimlik svc-sso'da; bu sayfa proxy.
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { UserPlus } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { createUser, listUsers, ROLE_LABELS, ROLES, updateUser, type SsoUser } from "@/lib/admin"
import { useAuth } from "@/lib/auth"

function fmtDate(s: string | null) {
  if (!s) return "—"
  return new Date(s).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" })
}

function RoleSelect({ value, onChange, disabled }: { value: string; onChange: (r: string) => void; disabled?: boolean }) {
  return (
    <select
      className="h-8 rounded-md border bg-background px-2 text-sm"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {ROLES.map((r) => (
        <option key={r} value={r}>
          {ROLE_LABELS[r] ?? r}
        </option>
      ))}
      {!ROLES.includes(value as (typeof ROLES)[number]) && <option value={value}>{value}</option>}
    </select>
  )
}

export function UsersAdminPage() {
  const { authMode } = useAuth()
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
    onError: (e) => toast.error(e instanceof Error ? e.message : "Güncellenemedi"),
  })

  const users = usersQ.data ?? []

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Kullanıcılar</h1>
          <p className="text-sm text-muted-foreground">
            {authMode === "local"
              ? "Panel kullanıcıları — rol ata, ekle, pasifleştir, parola sıfırla."
              : "SSO kullanıcıları — rol ata, ekle, pasifleştir. Rol değişikliği kullanıcının bir sonraki girişinde etkinleşir."}
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <UserPlus className="mr-1 h-4 w-4" /> Kullanıcı ekle
        </Button>
      </div>

      {usersQ.isLoading && <Skeleton className="h-64 w-full" />}
      {usersQ.isError && (
        <p className="text-sm text-red-600">
          {usersQ.error instanceof Error ? usersQ.error.message : "Kullanıcılar yüklenemedi"}
        </p>
      )}

      {usersQ.isSuccess && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2">E-posta</th>
                <th className="px-3 py-2">İsim</th>
                <th className="px-3 py-2">Rol</th>
                <th className="px-3 py-2">Durum</th>
                <th className="px-3 py-2">Son giriş</th>
                {authMode === "local" && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {users.map((u: SsoUser) => (
                <tr key={u.id} className="border-b last:border-0">
                  <td className="px-3 py-2">
                    <span className="font-medium">{u.email}</span>
                    {!u.linked && (
                      <span className="ml-1 text-xs text-amber-600" title="Henüz giriş yapmadı">
                        (bekliyor)
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
                      {u.status === "active" ? "Aktif" : "Pasif"}
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
                        Parolayı sıfırla
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
              <DialogTitle>Yeni geçici parola — {resetPwFor.email}</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Yalnız bir kez gösterilir, kaydedin ve kullanıcıya iletin.
              </p>
              <code className="block rounded-md border bg-muted px-3 py-2 text-sm font-mono">
                {resetPwFor.temp_password}
              </code>
              <div className="flex justify-end pt-1">
                <Button onClick={() => setResetPwFor(null)}>Kapat</Button>
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
  const [email, setEmail] = useState("")
  const [role, setRole] = useState("designer")
  const [busy, setBusy] = useState(false)
  const [tempPassword, setTempPassword] = useState<string | null>(null)

  async function submit() {
    if (!email.trim()) {
      toast.error("E-posta gerekli")
      return
    }
    setBusy(true)
    try {
      const user = await createUser({ email: email.trim().toLowerCase(), role })
      onAdded()
      if (user.temp_password) {
        // local mod: geçici parola yalnız bu yanıtta gelir — göstermeden kapatırsak
        // hesap kullanılamaz hale gelir, o yüzden dialog kapanmıyor.
        setTempPassword(user.temp_password)
      } else {
        toast.success("Kullanıcı eklendi — kişi bir kez giriş yapınca bağlanır")
        onClose()
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Eklenemedi")
    } finally {
      setBusy(false)
    }
  }

  if (tempPassword) {
    return (
      <Dialog open onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Kullanıcı eklendi</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Geçici parola — yalnız bir kez gösterilir, kaydedin ve kullanıcıya iletin.
              İlk girişten sonra "Parolamı değiştir"den kendi parolasını seçebilir.
            </p>
            <code className="block rounded-md border bg-muted px-3 py-2 text-sm font-mono">
              {tempPassword}
            </code>
            <div className="flex justify-end pt-1">
              <Button onClick={onClose}>Kapat</Button>
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
          <DialogTitle>Kullanıcı ekle</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>E-posta</Label>
            <Input type="email" placeholder="ad@ornek.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Rol</Label>
            <div>
              <RoleSelect value={role} onChange={setRole} />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {authMode === "local"
              ? "Bir geçici parola üretilir, bu ekranda bir kez gösterilir."
              : "Kişi bu e-postayla panele ilk kez giriş yapınca bu kayda bağlanır ve rolüyle gelir."}
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              İptal
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? "Ekleniyor…" : "Ekle"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
