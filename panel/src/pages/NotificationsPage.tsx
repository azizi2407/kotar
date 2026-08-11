// Notifications page — all notifications (read + unread) in detail + history.
// The bell (AppLayout) only shows the last few; this is the full list + pagination.
import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Megaphone } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"

import {
  fetchNotifications, markAllNotificationsRead, markNotificationRead,
  type Notification, type Severity,
} from "@/lib/notifications"
import { AnnounceDialog } from "@/components/notifications/AnnounceDialog"
import { NotificationSettings } from "@/components/notifications/NotificationSettings"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const PAGE = 25

// Severity badge (2026-08-05). Color coding is only meaningful for critical and
// normal — "info" stays neutral so it doesn't drown the list in noise.
function severityStil(t: (key: string) => string): Record<Severity, { label: string; cls: string }> {
  return {
    kritik: { label: t("pages.notifications.severity.critical"), cls: "bg-red-500/15 text-red-700 dark:text-red-400" },
    normal: { label: t("pages.notifications.severity.normal"), cls: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
    bilgi: { label: t("pages.notifications.severity.info"), cls: "bg-muted text-muted-foreground" },
  }
}

function filtreler(t: (key: string) => string): { value: "hepsi" | Severity; label: string }[] {
  return [
    { value: "hepsi", label: t("pages.notifications.severity.all") },
    { value: "kritik", label: t("pages.notifications.severity.critical") },
    { value: "normal", label: t("pages.notifications.severity.normal") },
    { value: "bilgi", label: t("pages.notifications.severity.info") },
  ]
}

// Notification kind → label (badge).
function kindLabel(t: (key: string) => string): Record<string, string> {
  return {
    revision_requested: t("pages.notifications.kind.revisionRequested"),
    revision_resolved: t("pages.notifications.kind.revisionResolved"),
    client_review: t("pages.notifications.kind.clientReview"),
    ops_digest_report: t("pages.notifications.kind.opsDigestReport"),
    job_failed: t("pages.notifications.kind.jobFailed"),
    job_stuck: t("pages.notifications.kind.jobStuck"),
    provision_failed: t("pages.notifications.kind.provisionFailed"),
    pre_approval: t("pages.notifications.kind.preApproval"),
    old_video_removed: t("pages.notifications.kind.oldVideoRemoved"),
    old_video_kept: t("pages.notifications.kind.oldVideoKept"),
    similarity_report: t("pages.notifications.kind.similarityReport"),
    mail: t("pages.notifications.kind.mail"),
    // Added with 2026-08-05
    announcement: t("pages.notifications.kind.announcement"),
    priority_marked: t("pages.notifications.kind.priorityMarked"),
    planning_changed: t("pages.notifications.kind.planningChanged"),
    photos_uploaded: t("pages.notifications.kind.photosUploaded"),
    video_uploaded: t("pages.notifications.kind.videoUploaded"),
    content_uploaded: t("pages.notifications.kind.contentUploaded"),
    special_day_soon: t("pages.notifications.kind.specialDaySoon"),
    ad_ending: t("pages.notifications.kind.adEnding"),
    depot_quota: t("pages.notifications.kind.depotQuota"),
  }
}

// Routes that actually exist in the panel — the "Go" button is shown if the
// notification link resolves to one of these (otherwise hidden, to avoid a
// broken redirect). 2026-08-05: added targets for new notification kinds — a
// path missing from this list silently hides the "Go" button, i.e. a missing
// entry makes a notification unclickable.
const KNOWN = ["/sharing", "/clients", "/brief", "/designer", "/videographer",
  "/special-days", "/canvas", "/tools", "/notifications", "/planlama", "/reklam",
  "/videograf-deposu", "/musteri-takip", "/posta", "/marka-rehberi", "/server", "/"]

function resolveLink(link: string | null): string | null {
  if (!link) return null
  const path = link.replace(/^\/panel/, "") || "/"
  const base = path.split("?")[0]
  return KNOWN.some((k) => k !== "/" && base.startsWith(k)) ? path : null
}

function fullTime(iso: string, lang: "tr" | "en") {
  const d = new Date(iso)
  return d.toLocaleString(lang === "tr" ? "tr-TR" : "en-US", { dateStyle: "medium", timeStyle: "short" })
}

export function NotificationsPage() {
  const { t, lang } = useI18n()
  const [items, setItems] = useState<Notification[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [filtre, setFiltre] = useState<"hepsi" | Severity>("hepsi")
  const [announceOpen, setAnnounceOpen] = useState(false)
  const { isManagement } = useAuth()
  const navigate = useNavigate()
  const SEVERITY_STIL = severityStil(t)
  const FILTRELER = filtreler(t)
  const KIND_LABEL = kindLabel(t)

  async function loadFirst() {
    setLoading(true)
    try {
      const d = await fetchNotifications({ offset: 0, limit: PAGE })
      setItems(d.notifications)
      setHasMore(Boolean(d.has_more))
    } catch {
      toast.error(t("pages.notifications.loadFailed"))
    } finally {
      setLoading(false)
    }
  }

  async function loadMore() {
    setLoadingMore(true)
    try {
      const d = await fetchNotifications({ offset: items.length, limit: PAGE })
      setItems((prev) => [...prev, ...d.notifications])
      setHasMore(Boolean(d.has_more))
    } catch {
      toast.error(t("pages.notifications.loadMoreFailed"))
    } finally {
      setLoadingMore(false)
    }
  }

  useEffect(() => {
    loadFirst()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const unreadCount = useMemo(() => items.filter((n) => !n.read_at).length, [items])
  // Filtering is CLIENT-side: pagination offset lives server-side, and adding
  // a severity filter there would confuse "load more"'s page boundaries. The
  // list is already 25 rows per page.
  const gorunen = useMemo(
    () => (filtre === "hepsi" ? items : items.filter((n) => n.severity === filtre)),
    [items, filtre])

  async function onOpen(n: Notification) {
    if (!n.read_at) {
      try {
        await markNotificationRead(n.id)
        setItems((prev) => prev.map((it) => (it.id === n.id
          ? { ...it, read_at: new Date().toISOString() } : it)))
      } catch { /* ignore */ }
    }
    const path = resolveLink(n.link)
    if (path) navigate(path)
  }

  async function onReadAll() {
    try {
      await markAllNotificationsRead()
      setItems((prev) => prev.map((it) => ({ ...it, read_at: it.read_at || new Date().toISOString() })))
      toast.success(t("pages.notifications.allMarkedRead"))
    } catch {
      toast.error(t("pages.notifications.actionFailed"))
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t("pages.notifications.title")}</h1>
          <p className="text-sm text-muted-foreground">
            {unreadCount > 0
              ? t("pages.notifications.subtitleUnread", { count: unreadCount })
              : t("pages.notifications.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isManagement && (
            <Button size="sm" onClick={() => setAnnounceOpen(true)}>
              <Megaphone className="mr-1 h-4 w-4" /> {t("pages.notifications.sendAnnouncement")}
            </Button>
          )}
          {unreadCount > 0 && (
            <Button variant="outline" size="sm" onClick={onReadAll}>{t("pages.notifications.markAllRead")}</Button>
          )}
        </div>
      </div>

      {isManagement && (
        <AnnounceDialog open={announceOpen} onOpenChange={(o) => {
          setAnnounceOpen(o)
          if (!o) loadFirst()        // sent announcement should appear in the list right away
        }} />
      )}

      <NotificationSettings />

      <div className="flex flex-wrap items-center gap-1">
        {FILTRELER.map((f) => (
          <Button key={f.value} size="sm"
            variant={filtre === f.value ? "default" : "ghost"}
            onClick={() => setFiltre(f.value)}>
            {f.label}
          </Button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
        </div>
      ) : gorunen.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {items.length === 0 ? t("pages.notifications.empty") : t("pages.notifications.emptyForSeverity")}
        </p>
      ) : (
        <ul className="space-y-2">
          {gorunen.map((n) => {
            const target = resolveLink(n.link)
            return (
              <li
                key={n.id}
                className={cn(
                  "rounded-lg border p-3 transition-colors",
                  !n.read_at && "border-primary/30 bg-accent/40",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {!n.read_at && <span className="h-2 w-2 shrink-0 rounded-full bg-primary" />}
                      <span className="font-medium">{n.title}</span>
                      <span className={cn("rounded-md px-1.5 py-0.5 text-[10px] font-semibold",
                        (SEVERITY_STIL[n.severity] ?? SEVERITY_STIL.normal).cls)}>
                        {(SEVERITY_STIL[n.severity] ?? SEVERITY_STIL.normal).label}
                      </span>
                      <Badge variant="secondary" className="text-[10px]">
                        {KIND_LABEL[n.kind] || n.kind}
                      </Badge>
                    </div>
                    {n.body && (
                      <p className="whitespace-pre-wrap text-sm text-muted-foreground">{n.body}</p>
                    )}
                    <p className="text-[11px] text-muted-foreground/70">{fullTime(n.created_at, lang)}</p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    {!n.read_at && (
                      <Button variant="ghost" size="sm" onClick={() => onOpen(n)}>{t("pages.notifications.markRead")}</Button>
                    )}
                    {target && (
                      <Button variant="outline" size="sm" onClick={() => onOpen(n)}>{t("pages.notifications.go")}</Button>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {hasMore && !loading && (
        <div className="flex justify-center pt-2">
          <Button variant="outline" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? t("pages.notifications.loading") : t("pages.notifications.loadMore")}
          </Button>
        </div>
      )}
    </div>
  )
}
