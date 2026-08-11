// Mail account connection status + "Test connection".
//
// Why this exists: the `testAccount` endpoints and the `last_error` column have
// existed since 2026-07-23, but **were shown nowhere in the panel** — `testAccount`
// in `lib/mail.ts` wasn't called by any component. Result: when sending broke, all
// the user saw was a "Failed to send: …" toast; they couldn't tell whether the
// account's IMAP or SMTP was broken, whether it was a password or network issue, and
// couldn't fix it from the panel. This invisibility is also part of why SMTP looked
// "down" for months: the status was being recorded, nobody could see it.
import { useState } from "react"
import { AlertTriangle, CheckCircle2, KeyRound, PlugZap, ServerCrash } from "lucide-react"
import { toast } from "sonner"

import { testAccount, type MailAccount, type TestResult } from "@/lib/mail"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

export function MailAccountHealth({ account, onFixPassword }: {
  account: MailAccount
  onFixPassword?: () => void
}) {
  const { t } = useI18n()
  const [result, setResult] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    try {
      const r = await testAccount(account.id)
      setResult(r)
      if (r.imap_ok && r.smtp_ok) toast.success(t("components.mail.mailAccountHealth.bothWork"))
      else if (r.imap_ok) toast.warning(t("components.mail.mailAccountHealth.readOnlyWorks"))
      else toast.error(t("components.mail.mailAccountHealth.connectionFailed"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.mail.mailAccountHealth.testFailed"))
    } finally {
      setBusy(false)
    }
  }

  // If not tested, fall back to the last recorded status (`last_error`); if tested,
  // the fresh result wins.
  const stored = account.last_error
  const nothingWrong = result ? result.imap_ok && result.smtp_ok : !stored
  // Is it a password problem? The fresh result has a class; the stored one only has
  // text. `giriş başarısız` ("login failed") is also matched (2026-07-31): that's the
  // gateway's raw `MailAuthError` text and it doesn't contain the words "parola/kimli"
  // ("password/identity") — a filter that only looked for those two words would silently
  // hide the "Update password" button whenever a classification path skipped it.
  const authIssue = result
    ? result.imap_error_kind === "auth" || result.smtp_error_kind === "auth"
    : !!stored && /parola|kimli|giriş başarısız/i.test(stored)

  return (
    <div className={cn(
      "flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs",
      nothingWrong ? "border-emerald-500/40 bg-emerald-500/10"
                   : "border-amber-500/40 bg-amber-500/10")}>
      {nothingWrong ? (
        <span className="flex items-center gap-1.5 font-medium">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          {result ? t("components.mail.mailAccountHealth.bothWork") : t("components.mail.mailAccountHealth.noIssueReported")}
        </span>
      ) : (
        <span className="flex min-w-0 items-start gap-1.5">
          {authIssue ? <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                     : <ServerCrash className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />}
          <span className="min-w-0">
            <strong>{authIssue ? t("components.mail.mailAccountHealth.passwordIssue") : t("components.mail.mailAccountHealth.connectionIssue")}:</strong>{" "}
            {result ? (result.imap_error || result.smtp_error) : stored}
            {authIssue && (
              <span className="ml-1 text-muted-foreground">
                {t("components.mail.mailAccountHealth.reconnectHint")}
              </span>
            )}
          </span>
        </span>
      )}

      {result && !nothingWrong && (
        <span className="flex items-center gap-2">
          <Channel label={t("components.mail.mailAccountHealth.readChannel")} ok={result.imap_ok} />
          <Channel label={t("components.mail.mailAccountHealth.sendChannel")} ok={result.smtp_ok} />
        </span>
      )}

      <span className="ml-auto flex items-center gap-1.5">
        {authIssue && onFixPassword && (
          <Button size="sm" variant="outline" onClick={onFixPassword}>
            {t("components.mail.mailAccountHealth.updatePassword")}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={run} disabled={busy}>
          <PlugZap className="mr-1 h-3.5 w-3.5" />
          {busy ? t("components.mail.mailAccountHealth.testing") : t("components.mail.mailAccountHealth.testConnection")}
        </Button>
      </span>
    </div>
  )
}

function Channel({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5",
      ok ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
         : "bg-red-500/15 text-red-600 dark:text-red-400")}>
      {ok ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {label}
    </span>
  )
}
