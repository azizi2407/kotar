// Anons gönderme (2026-08-05, proje sahibi isteği) — yönetici elle bildirim yazar.
//
// Otomatik bildirimlerden farkı: önem derecesini veri değil GÖNDEREN seçer.
// "Kritik" seçilirse alıcının telefonu (ntfy eşiği kritik olanlar dahil) çalar —
// o yüzden varsayılan "normal" ve kritik seçimi görsel olarak ayrışıyor.
import { useEffect, useMemo, useState } from "react"
import { Loader2, Megaphone, Users } from "lucide-react"
import { toast } from "sonner"

import {
  fetchAnnounceRecipients, sendAnnouncement,
  type AnnounceUser, type Severity,
} from "@/lib/notifications"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

const ROL_ETIKET: Record<string, string> = {
  management: "Yönetim",
  designer: "Tasarımcı",
  videographer: "Videograf",
  content_creator: "İçerik Üretici",
}

const SEVIYE: { value: Severity; label: string; hint: string; cls: string }[] = [
  { value: "bilgi", label: "Bilgi", hint: "Yalnız panelde birikir", cls: "border-muted-foreground/40" },
  { value: "normal", label: "Normal", hint: "Eşiği normal olanların telefonuna", cls: "border-amber-500" },
  { value: "kritik", label: "Kritik", hint: "Telefonu çaldırır, sessiz saati deler", cls: "border-red-500" },
]

export function AnnounceDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const [users, setUsers] = useState<AnnounceUser[]>([])
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [severity, setSeverity] = useState<Severity>("normal")
  const [roles, setRoles] = useState<string[]>([])
  const [subs, setSubs] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    fetchAnnounceRecipients()
      .then((d) => setUsers(d.users))
      .catch(() => toast.error("Alıcı listesi yüklenemedi"))
  }, [open])

  // Rol seçimi kişileri de kapsar; sayaç kullanıcıya kaç kişiye gideceğini gösterir
  // (backend de aynı birleşimi tekilleştiriyor — burada yalnız önizleme).
  const hedefSayisi = useMemo(() => {
    const küme = new Set(subs)
    users.filter((u) => roles.includes(u.role)).forEach((u) => küme.add(u.sub))
    return küme.size
  }, [users, roles, subs])

  function toggle(list: string[], value: string, set: (v: string[]) => void) {
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value])
  }

  function kapat() {
    setTitle(""); setBody(""); setSeverity("normal"); setRoles([]); setSubs([])
    onOpenChange(false)
  }

  async function gonder() {
    if (!title.trim()) { toast.error("Başlık zorunlu"); return }
    if (hedefSayisi === 0) { toast.error("En az bir alıcı seçin"); return }
    setBusy(true)
    try {
      const d = await sendAnnouncement({ title, body, severity, roles, subs })
      toast.success(`Anons ${d.sent} kişiye gönderildi`)
      kapat()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Gönderilemedi")
    } finally {
      setBusy(false)
    }
  }

  const roller = useMemo(
    () => [...new Set(users.map((u) => u.role))].filter(Boolean), [users])

  return (
    <Dialog open={open} onOpenChange={(o) => !o && kapat()}>
      <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Megaphone className="h-4 w-4" /> Anons gönder
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <Input placeholder="Başlık" value={title} maxLength={200}
            onChange={(e) => setTitle(e.target.value)} disabled={busy} />
          <Textarea placeholder="Mesaj (isteğe bağlı)" value={body} rows={4} maxLength={2000}
            onChange={(e) => setBody(e.target.value)} disabled={busy} />

          <div className="space-y-1.5">
            <div className="text-sm font-medium">Önem</div>
            <div className="grid gap-1.5 sm:grid-cols-3">
              {SEVIYE.map((s) => (
                <button key={s.value} type="button" disabled={busy}
                  onClick={() => setSeverity(s.value)}
                  className={cn("rounded-md border-2 p-2 text-left transition-colors",
                    severity === s.value ? s.cls : "border-transparent bg-muted/40 hover:bg-muted")}>
                  <div className="text-sm font-medium">{s.label}</div>
                  <div className="text-[11px] text-muted-foreground">{s.hint}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="text-sm font-medium">Roller</div>
            <div className="flex flex-wrap gap-1.5">
              {roller.map((r) => (
                <button key={r} type="button" disabled={busy}
                  onClick={() => toggle(roles, r, setRoles)}
                  className={cn("rounded-md border px-2 py-1 text-xs transition-colors",
                    roles.includes(r) ? "border-primary bg-accent" : "hover:bg-muted")}>
                  {ROL_ETIKET[r] || r}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="text-sm font-medium">Kişiler</div>
            <div className="max-h-40 space-y-0.5 overflow-y-auto rounded-md border p-1.5">
              {users.map((u) => {
                const rolSecili = roles.includes(u.role)
                return (
                  <label key={u.sub}
                    className={cn("flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted",
                      rolSecili && "opacity-60")}>
                    <input type="checkbox" className="h-3.5 w-3.5 accent-primary"
                      checked={subs.includes(u.sub) || rolSecili}
                      disabled={busy || rolSecili}
                      onChange={() => toggle(subs, u.sub, setSubs)} />
                    <span className="truncate">{u.name}</span>
                    <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                      {ROL_ETIKET[u.role] || u.role}
                    </span>
                  </label>
                )
              })}
            </div>
          </div>

          <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Users className="h-3.5 w-3.5" />
            {hedefSayisi === 0 ? "Alıcı seçilmedi" : `${hedefSayisi} kişiye gidecek`}
            <span className="text-xs">(kendine gönderilmez)</span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={kapat} disabled={busy}>İptal</Button>
          <Button onClick={gonder} disabled={busy || !title.trim() || hedefSayisi === 0}>
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Megaphone className="mr-1 h-4 w-4" />}
            Gönder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
