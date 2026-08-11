// In-panel notifications (bell) + (2026-08-05) a per-user ntfy channel.
// Both channels are fed from a single source: every notification lands in the panel,
// and if `severity` matches the user's preference, it's also sent to the phone via ntfy.
import { apiGet, apiJson } from "./api"

export type Severity = "kritik" | "normal" | "bilgi"

export interface Notification {
  id: number
  kind: string
  severity: Severity
  title: string
  body: string | null
  link: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationPrefs {
  ntfy_topic: string
  ntfy_enabled: boolean
  min_severity: Severity
  quiet_start: number | null
  quiet_end: number | null
}

export interface PrefsResponse {
  prefs: NotificationPrefs
  severities: Severity[]
  channel_ready: boolean
  // The ntfy app wants the server and topic in SEPARATE fields for subscribing; the
  // combined `subscribe_url` is for opening/sharing in the browser.
  server_url: string
  subscribe_url: string
}

export function fetchNotificationPrefs(): Promise<PrefsResponse> {
  return apiGet("/notification-prefs")
}

export function saveNotificationPrefs(
  body: Partial<NotificationPrefs> & { rotate_topic?: boolean },
): Promise<{ prefs: NotificationPrefs; subscribe_url: string }> {
  return apiJson("/notification-prefs", body, "PUT")
}

export function sendTestNotification(): Promise<{ ok: boolean }> {
  return apiJson("/notification-prefs/test", {})
}

export interface NotificationsResponse {
  notifications: Notification[]
  unread_count: number
  has_more?: boolean
}

export function fetchNotifications(params?: { offset?: number; limit?: number }): Promise<NotificationsResponse> {
  const q = new URLSearchParams()
  if (params?.offset != null) q.set("offset", String(params.offset))
  if (params?.limit != null) q.set("limit", String(params.limit))
  const qs = q.toString()
  return apiGet(`/notifications${qs ? `?${qs}` : ""}`)
}

export function markNotificationRead(id: number): Promise<{ notification: Notification }> {
  return apiJson(`/notifications/${id}/read`, {})
}

export function markAllNotificationsRead(): Promise<{ ok: boolean; count: number }> {
  return apiJson("/notifications/read-all", {})
}

// --- announcement (2026-08-05) — a manager sends a notification manually --------
// Difference from automatic types: severity is decided by the SENDER, not the DATA.

export interface AnnounceUser {
  sub: string
  name: string
  role: string
}

export function fetchAnnounceRecipients(): Promise<{ users: AnnounceUser[]; severities: Severity[] }> {
  return apiGet("/announce/recipients")
}

export function sendAnnouncement(body: {
  title: string
  body?: string
  severity: Severity
  subs?: string[]
  roles?: string[]
  link?: string
}): Promise<{ ok: boolean; sent: number }> {
  return apiJson("/announce", body)
}
