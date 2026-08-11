// Notification settings card (2026-08-05) — a per-user ntfy channel.
//
// The in-panel bell keeps working for EVERYONE; this card only manages the PHONE
// channel and is deliberately opt-in: nothing is sent until `ntfy_enabled` is
// turned on. The topic is only shown to its owner (read access in ntfy relies on
// the name staying secret) — hence the "refresh" button: if the address leaks,
// rotating it is the only fix.
import { useEffect, useState } from "react"
import { Bell, BellOff, Check, Copy, Loader2, RefreshCw, Send } from "lucide-react"
import { toast } from "sonner"

import {
  fetchNotificationPrefs, saveNotificationPrefs, sendTestNotification,
  type NotificationPrefs, type Severity,
} from "@/lib/notifications"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

// Threshold labels in the user's language: someone who picks "critical" gets ONLY critical.
function esikSecenek(t: (key: string) => string): { value: Severity; label: string; hint: string }[] {
  return [
    { value: "kritik", label: t("components.notifications.notificationSettings.threshold.criticalOnlyLabel"), hint: t("components.notifications.notificationSettings.threshold.criticalOnlyHint") },
    { value: "normal", label: t("components.notifications.notificationSettings.threshold.criticalNormalLabel"), hint: t("components.notifications.notificationSettings.threshold.criticalNormalHint") },
    { value: "bilgi", label: t("components.notifications.notificationSettings.threshold.allLabel"), hint: t("components.notifications.notificationSettings.threshold.allHint") },
  ]
}

const SAATLER = Array.from({ length: 24 }, (_, i) => i)
const ss = (h: number) => `${String(h).padStart(2, "0")}:00`

// A labeled, one-click-to-copy field — the ntfy app asks for server and topic to
// be pasted into separate fields, so the user shouldn't have to pick pieces out of a URL.
function CopyRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
      <code className={cn("min-w-0 flex-1 truncate rounded border bg-background px-2 py-1 text-xs",
        mono && "font-mono tracking-tight")}>
        {value}
      </code>
      <Button variant="ghost" size="sm" onClick={() => {
        navigator.clipboard.writeText(value)
        toast.success(t("components.notifications.notificationSettings.copied", { label }))
      }}>
        <Copy className="mr-1 h-3.5 w-3.5" /> {t("components.notifications.notificationSettings.copy")}
      </Button>
    </div>
  )
}

export function NotificationSettings() {
  const { t } = useI18n()
  const ESIK_SECENEK = esikSecenek(t)
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null)
  const [subscribeUrl, setSubscribeUrl] = useState("")
  const [serverUrl, setServerUrl] = useState("")
  const [channelReady, setChannelReady] = useState(true)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchNotificationPrefs()
      .then((d) => {
        setPrefs(d.prefs)
        setSubscribeUrl(d.subscribe_url)
        setServerUrl(d.server_url)
        setChannelReady(d.channel_ready)
      })
      .catch(() => toast.error(t("components.notifications.notificationSettings.loadFailed")))
      .finally(() => setLoading(false))
  }, [])

  async function kaydet(patch: Partial<NotificationPrefs> & { rotate_topic?: boolean }) {
    setBusy(true)
    try {
      const d = await saveNotificationPrefs(patch)
      setPrefs(d.prefs)
      setSubscribeUrl(d.subscribe_url)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.notifications.notificationSettings.saveFailed"))
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    try {
      await sendTestNotification()
      toast.success(t("components.notifications.notificationSettings.testSent"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.notifications.notificationSettings.testFailed"))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Skeleton className="h-40 w-full" />
  if (!prefs) return null

  const sessizAcik = prefs.quiet_start != null && prefs.quiet_end != null

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 font-medium">
            {prefs.ntfy_enabled ? <Bell className="h-4 w-4 text-primary" /> : <BellOff className="h-4 w-4 text-muted-foreground" />}
            {t("components.notifications.notificationSettings.phoneNotifications")}
          </div>
          <p className="text-sm text-muted-foreground">
            {t("components.notifications.notificationSettings.phoneNotificationsHint")}
          </p>
        </div>
        <Button variant={prefs.ntfy_enabled ? "outline" : "default"} size="sm" disabled={busy}
          onClick={() => kaydet({ ntfy_enabled: !prefs.ntfy_enabled })}>
          {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          {prefs.ntfy_enabled ? t("components.notifications.notificationSettings.turnOff") : t("components.notifications.notificationSettings.turnOn")}
        </Button>
      </div>

      {!channelReady && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
          {t("components.notifications.notificationSettings.serverNotConfigured")}
        </p>
      )}

      {prefs.ntfy_enabled && (
        <>
          {/* setup — the ntfy app wants SERVER and TOPIC in separate fields,
              so each is on its own row and separately copyable. */}
          <div className="space-y-2 rounded-md bg-muted/40 p-3">
            <div className="text-sm font-medium">{t("components.notifications.notificationSettings.connectPhone")}</div>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
              <li><span className="font-medium text-foreground">ntfy</span> {t("components.notifications.notificationSettings.step1")}</li>
              <li>
                {t("components.notifications.notificationSettings.step2Prefix")}{" "}
                <span className="font-medium text-foreground">+</span>{" "}
                {t("components.notifications.notificationSettings.step2Mid")}{" "}
                <span className="font-medium text-foreground">"Use another server"</span>{" "}
                {t("components.notifications.notificationSettings.step2Suffix")}
              </li>
              <li>
                <span className="font-medium text-foreground">{t("components.notifications.notificationSettings.serverLabel")}</span>{" "}
                {t("components.notifications.notificationSettings.andConnector")}{" "}
                <span className="font-medium text-foreground">{t("components.notifications.notificationSettings.topicLabel")}</span>{" "}
                {t("components.notifications.notificationSettings.fieldsSuffix")}
              </li>
              <li>
                <span className="font-medium text-foreground">{t("components.notifications.notificationSettings.testNotifLabel")}</span>{" "}
                {t("components.notifications.notificationSettings.verifyButtonHint")}
              </li>
            </ol>

            <div className="space-y-1.5">
              <CopyRow label={t("components.notifications.notificationSettings.serverLabel")} value={serverUrl} />
              <CopyRow label={t("components.notifications.notificationSettings.topicLabel")} value={prefs.ntfy_topic} mono />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <a href={subscribeUrl} target="_blank" rel="noreferrer"
                className="text-xs text-primary hover:underline">
                {t("components.notifications.notificationSettings.openInBrowser")}
              </a>
              <Button variant="ghost" size="sm" disabled={busy} onClick={test}>
                <Send className="mr-1 h-3.5 w-3.5" /> {t("components.notifications.notificationSettings.testNotifLabel")}
              </Button>
              {/* The address works like a secret key: if it leaks, rotating it is the
                  only fix. Rotating unsubscribes old devices — hence the confirmation. */}
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => {
                if (!confirm(t("components.notifications.notificationSettings.rotateConfirm"))) return
                kaydet({ rotate_topic: true })
              }}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" /> {t("components.notifications.notificationSettings.rotateAddress")}
              </Button>
            </div>
          </div>

          {/* threshold */}
          <div className="space-y-1.5">
            <div className="text-sm font-medium">{t("components.notifications.notificationSettings.whichNotifications")}</div>
            <div className="grid gap-1.5 sm:grid-cols-3">
              {ESIK_SECENEK.map((o) => (
                <button key={o.value} type="button" disabled={busy}
                  onClick={() => kaydet({ min_severity: o.value })}
                  className={cn(
                    "rounded-md border p-2 text-left transition-colors hover:border-primary/50",
                    prefs.min_severity === o.value && "border-primary bg-accent/50",
                  )}>
                  <div className="flex items-center gap-1 text-sm font-medium">
                    {prefs.min_severity === o.value && <Check className="h-3.5 w-3.5 text-primary" />}
                    {o.label}
                  </div>
                  <div className="text-[11px] text-muted-foreground">{o.hint}</div>
                </button>
              ))}
            </div>
          </div>

          {/* quiet hours */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium">{t("components.notifications.notificationSettings.quietHours")}</div>
              <Button variant="ghost" size="sm" disabled={busy}
                onClick={() => kaydet(sessizAcik
                  ? { quiet_start: null, quiet_end: null }
                  : { quiet_start: 22, quiet_end: 8 })}>
                {sessizAcik ? t("components.notifications.notificationSettings.turnOff") : t("components.notifications.notificationSettings.turnOn")}
              </Button>
            </div>
            {sessizAcik ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Select value={String(prefs.quiet_start)} disabled={busy}
                  onValueChange={(v) => v && kaydet({ quiet_start: Number(v) })}>
                  <SelectTrigger size="sm" className="w-24"><SelectValue /></SelectTrigger>
                  <SelectContent className="max-h-64">
                    {SAATLER.map((h) => <SelectItem key={h} value={String(h)}>{ss(h)}</SelectItem>)}
                  </SelectContent>
                </Select>
                <span className="text-muted-foreground">→</span>
                <Select value={String(prefs.quiet_end)} disabled={busy}
                  onValueChange={(v) => v && kaydet({ quiet_end: Number(v) })}>
                  <SelectTrigger size="sm" className="w-24"><SelectValue /></SelectTrigger>
                  <SelectContent className="max-h-64">
                    {SAATLER.map((h) => <SelectItem key={h} value={String(h)}>{ss(h)}</SelectItem>)}
                  </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground">
                  {t("components.notifications.notificationSettings.quietHoursHintPrefix")}{" "}
                  <span className="font-medium">{t("components.notifications.notificationSettings.criticalWord")}</span>{" "}
                  {t("components.notifications.notificationSettings.quietHoursHintSuffix")}
                </span>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("components.notifications.notificationSettings.quietHoursOff")}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
