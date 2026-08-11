// Mailbox connection — self-service. The user only enters email + password;
// host/port are pre-filled with the default mail server (editable under "Advanced").
//
// TWO MODES (2026-07-31): if `account` is given, **update password**; otherwise
// connect a new account. Previously there was a single mode and the "Update
// password" button in the health strip also opened this dialog → `createAccount`
// (POST) got called for an existing account, returning a **500** from the
// `uq_mail_owner_email` violation. So the button was structurally broken; the
// correct path (`PATCH /accounts/:id` + `password`) existed in the backend and
// `lib/mail.ts` but was never called from anywhere.
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useI18n } from "@/lib/i18n"
import { createAccount, updateAccount, type MailAccount } from "@/lib/mail"

const DEFAULTS = {
  imap_host: "mail.example.com",
  imap_port: 993,
  smtp_host: "mail.example.com",
  smtp_port: 465,
}

export function ConnectAccountDialog({
  open,
  onClose,
  onConnected,
  isSuperadmin,
  account,
}: {
  open: boolean
  onClose: () => void
  onConnected: () => void
  isSuperadmin: boolean
  /** If given, the dialog opens in "update password" mode (does NOT create a new account). */
  account?: MailAccount | null
}) {
  const { t } = useI18n()
  const editing = !!account
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [advanced, setAdvanced] = useState(false)
  const [cfg, setCfg] = useState(DEFAULTS)
  const [isShared, setIsShared] = useState(false)
  const [pollEnabled, setPollEnabled] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (editing) {
      if (!password) {
        toast.error(t("components.mail.connectAccountDialog.enterNewPassword"))
        return
      }
      setBusy(true)
      try {
        // The backend verifies via IMAP BEFORE saving the password → an error
        // landing here really means a wrong password/access, no silent broken record.
        await updateAccount(account!.id, { password })
        toast.success(t("components.mail.connectAccountDialog.passwordUpdated"))
        setPassword("")
        onConnected()
        onClose()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t("components.mail.connectAccountDialog.passwordUpdateFailed"))
      } finally {
        setBusy(false)
      }
      return
    }

    if (!email || !password) {
      toast.error(t("components.mail.connectAccountDialog.emailPasswordRequired"))
      return
    }
    setBusy(true)
    try {
      await createAccount({
        email,
        password,
        imap_host: cfg.imap_host,
        imap_port: cfg.imap_port,
        smtp_host: cfg.smtp_host,
        smtp_port: cfg.smtp_port,
        smtp_security: "ssl",
        is_shared: isSuperadmin ? isShared : false,
        poll_enabled: isSuperadmin ? pollEnabled : false,
      })
      toast.success(t("components.mail.connectAccountDialog.connected"))
      onConnected()
      onClose()
    } catch (e) {
      // The backend returns understandable errors like "already connected"; we
      // don't overwrite it with our own guess (the old text pointed suspicion at
      // the password even when it was correct).
      toast.error(e instanceof Error ? e.message : t("components.mail.connectAccountDialog.connectFailed"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {editing ? t("components.mail.connectAccountDialog.updatePasswordTitle") : t("components.mail.connectAccountDialog.connectTitle")}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t("components.mail.connectAccountDialog.email")}</Label>
            {/* The account is fixed in update mode — changing the email would mean
                "a different account", which is the job of the connect flow. */}
            <Input
              type="email"
              placeholder="name@company.com"
              value={editing ? account!.email : email}
              disabled={editing}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{editing ? t("components.mail.connectAccountDialog.newPassword") : t("components.mail.connectAccountDialog.password")}</Label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {editing && (
            <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
              {t("components.mail.connectAccountDialog.updatePasswordHint")}
            </p>
          )}

          {!editing && isSuperadmin && (
            <div className="space-y-2 rounded-md border p-3">
              <div className="flex items-center justify-between">
                <Label className="text-sm">{t("components.mail.connectAccountDialog.sharedMailbox")}</Label>
                <Switch checked={isShared} onCheckedChange={setIsShared} />
              </div>
              <div className="flex items-center justify-between">
                <Label className="text-sm">{t("components.mail.connectAccountDialog.backgroundSync")}</Label>
                <Switch checked={pollEnabled} onCheckedChange={setPollEnabled} />
              </div>
            </div>
          )}

          {!editing && (
            <button
              type="button"
              className="text-xs text-muted-foreground underline"
              onClick={() => setAdvanced((v) => !v)}
            >
              {advanced ? t("components.mail.connectAccountDialog.hideAdvanced") : t("components.mail.connectAccountDialog.showAdvanced")}
            </button>
          )}
          {!editing && advanced && (
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">{t("components.mail.connectAccountDialog.imapServer")}</Label>
                <Input
                  value={cfg.imap_host}
                  onChange={(e) => setCfg({ ...cfg, imap_host: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">{t("components.mail.connectAccountDialog.imapPort")}</Label>
                <Input
                  type="number"
                  value={cfg.imap_port}
                  onChange={(e) => setCfg({ ...cfg, imap_port: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">{t("components.mail.connectAccountDialog.smtpServer")}</Label>
                <Input
                  value={cfg.smtp_host}
                  onChange={(e) => setCfg({ ...cfg, smtp_host: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">{t("components.mail.connectAccountDialog.smtpPort")}</Label>
                <Input
                  type="number"
                  value={cfg.smtp_port}
                  onChange={(e) => setCfg({ ...cfg, smtp_port: Number(e.target.value) })}
                />
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {t("components.mail.connectAccountDialog.cancel")}
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? (editing ? t("components.mail.connectAccountDialog.verifying") : t("components.mail.connectAccountDialog.connecting"))
                    : (editing ? t("components.mail.connectAccountDialog.saveAndVerify") : t("components.mail.connectAccountDialog.connect"))}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
