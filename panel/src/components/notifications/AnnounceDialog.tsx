// Sending announcements (2026-08-05, project owner's request) — an admin manually writes a notification.
//
// Difference from automatic notifications: the SENDER picks the severity, not the
// data. If "Critical" is selected, the recipient's phone rings (ntfy threshold
// includes critical) — that's why the default is "normal" and the critical option
// is visually set apart.
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
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

function rolEtiket(t: (key: string) => string): Record<string, string> {
  return {
    management: t("components.notifications.announceDialog.role.management"),
    designer: t("components.notifications.announceDialog.role.designer"),
    videographer: t("components.notifications.announceDialog.role.videographer"),
    content_creator: t("components.notifications.announceDialog.role.contentCreator"),
  }
}

function seviye(t: (key: string) => string): { value: Severity; label: string; hint: string; cls: string }[] {
  return [
    { value: "bilgi", label: t("components.notifications.announceDialog.severity.infoLabel"), hint: t("components.notifications.announceDialog.severity.infoHint"), cls: "border-muted-foreground/40" },
    { value: "normal", label: t("components.notifications.announceDialog.severity.normalLabel"), hint: t("components.notifications.announceDialog.severity.normalHint"), cls: "border-amber-500" },
    { value: "kritik", label: t("components.notifications.announceDialog.severity.criticalLabel"), hint: t("components.notifications.announceDialog.severity.criticalHint"), cls: "border-red-500" },
  ]
}

export function AnnounceDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const { t } = useI18n()
  const ROL_ETIKET = rolEtiket(t)
  const SEVIYE = seviye(t)
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
      .catch(() => toast.error(t("components.notifications.announceDialog.recipientsLoadFailed")))
  }, [open])

  // Role selection also covers individuals; the counter shows the user how many
  // people it'll reach (the backend also deduplicates the same union — this is just a preview).
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
    if (!title.trim()) { toast.error(t("components.notifications.announceDialog.titleRequired")); return }
    if (hedefSayisi === 0) { toast.error(t("components.notifications.announceDialog.recipientRequired")); return }
    setBusy(true)
    try {
      const d = await sendAnnouncement({ title, body, severity, roles, subs })
      toast.success(t("components.notifications.announceDialog.sentTo", { count: d.sent }))
      kapat()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.notifications.announceDialog.sendFailed"))
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
            <Megaphone className="h-4 w-4" /> {t("components.notifications.announceDialog.title")}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <Input placeholder={t("components.notifications.announceDialog.titlePlaceholder")} value={title} maxLength={200}
            onChange={(e) => setTitle(e.target.value)} disabled={busy} />
          <Textarea placeholder={t("components.notifications.announceDialog.messagePlaceholder")} value={body} rows={4} maxLength={2000}
            onChange={(e) => setBody(e.target.value)} disabled={busy} />

          <div className="space-y-1.5">
            <div className="text-sm font-medium">{t("components.notifications.announceDialog.severityLabel")}</div>
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
            <div className="text-sm font-medium">{t("components.notifications.announceDialog.rolesLabel")}</div>
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
            <div className="text-sm font-medium">{t("components.notifications.announceDialog.peopleLabel")}</div>
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
            {hedefSayisi === 0 ? t("components.notifications.announceDialog.noRecipient") : t("components.notifications.announceDialog.willGoTo", { count: hedefSayisi })}
            <span className="text-xs">{t("components.notifications.announceDialog.notToSelf")}</span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={kapat} disabled={busy}>{t("components.notifications.announceDialog.cancel")}</Button>
          <Button onClick={gonder} disabled={busy || !title.trim() || hedefSayisi === 0}>
            {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Megaphone className="mr-1 h-4 w-4" />}
            {t("components.notifications.announceDialog.send")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
