// Bildirimler sayfası — tüm bildirimler (okunmuş + okunmamış) detaylı + geçmiş.
// Bell (AppLayout) sadece son birkaçını gösterir; burası tam liste + sayfalama.
import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Megaphone } from "lucide-react"
import { toast } from "sonner"

import { useAuth } from "@/lib/auth"

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

// Önem rozeti (2026-08-05). Renk kodu yalnız kritik ve normal için anlamlı —
// "bilgi" sessiz kalsın diye nötr, listeyi gürültüye boğmasın.
const SEVERITY_STIL: Record<Severity, { label: string; cls: string }> = {
  kritik: { label: "Kritik", cls: "bg-red-500/15 text-red-700 dark:text-red-400" },
  normal: { label: "Normal", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  bilgi: { label: "Bilgi", cls: "bg-muted text-muted-foreground" },
}

const FILTRELER: { value: "hepsi" | Severity; label: string }[] = [
  { value: "hepsi", label: "Hepsi" },
  { value: "kritik", label: "Kritik" },
  { value: "normal", label: "Normal" },
  { value: "bilgi", label: "Bilgi" },
]

// Bildirim türü → Türkçe etiket (rozet).
const KIND_LABEL: Record<string, string> = {
  revision_requested: "Revizyon talebi",
  revision_resolved: "Revizyon çözüldü",
  client_review: "Müşteri incelemesi",
  ops_digest_report: "Ops Digest raporu",
  job_failed: "İş başarısız",
  job_stuck: "İş takıldı",
  provision_failed: "Klasör kurulamadı",
  pre_approval: "Ön onay",
  old_video_removed: "Eski video silindi",
  old_video_kept: "Eski video korundu",
  similarity_report: "Benzerlik raporu",
  mail: "Yeni e-posta",
  // 2026-08-05 ile gelenler
  announcement: "Anons",
  priority_marked: "Öncelikli müşteri",
  planning_changed: "Planlama panosu",
  photos_uploaded: "Çekim fotoğrafı",
  video_uploaded: "Video yüklendi",
  content_uploaded: "İçerik yüklendi",
  special_day_soon: "Yarının özel günü",
  ad_ending: "Reklam bitiyor",
  depot_quota: "Depo kotası",
}

// Panel içinde gerçekten var olan route'lar — bildirim linki bunlardan birine
// çözülüyorsa "Git" düğmesi gösterilir (yoksa gizli; kırık yönlendirme olmasın).
// 2026-08-05: yeni bildirim türlerinin hedefleri eklendi — listede olmayan bir yol
// "Git" düğmesini sessizce gizler, yani eksik bir kayıt bildirimi tıklanamaz yapar.
const KNOWN = ["/sharing", "/clients", "/brief", "/designer", "/videographer",
  "/special-days", "/canvas", "/tools", "/notifications", "/planlama", "/reklam",
  "/videograf-deposu", "/musteri-takip", "/posta", "/marka-rehberi", "/server", "/"]

function resolveLink(link: string | null): string | null {
  if (!link) return null
  const path = link.replace(/^\/panel/, "") || "/"
  const base = path.split("?")[0]
  return KNOWN.some((k) => k !== "/" && base.startsWith(k)) ? path : null
}

function fullTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" })
}

export function NotificationsPage() {
  const [items, setItems] = useState<Notification[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [filtre, setFiltre] = useState<"hepsi" | Severity>("hepsi")
  const [announceOpen, setAnnounceOpen] = useState(false)
  const { isManagement } = useAuth()
  const navigate = useNavigate()

  async function loadFirst() {
    setLoading(true)
    try {
      const d = await fetchNotifications({ offset: 0, limit: PAGE })
      setItems(d.notifications)
      setHasMore(Boolean(d.has_more))
    } catch {
      toast.error("Bildirimler yüklenemedi")
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
      toast.error("Daha fazla yüklenemedi")
    } finally {
      setLoadingMore(false)
    }
  }

  useEffect(() => {
    loadFirst()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const unreadCount = useMemo(() => items.filter((n) => !n.read_at).length, [items])
  // Filtre CLIENT tarafında: sayfalama offset'i sunucuda, oraya severity süzgeci
  // eklemek "daha fazla yükle"nin sayfa sınırlarını karıştırırdı. Liste zaten
  // sayfa başına 25 satır.
  const gorunen = useMemo(
    () => (filtre === "hepsi" ? items : items.filter((n) => n.severity === filtre)),
    [items, filtre])

  async function onOpen(n: Notification) {
    if (!n.read_at) {
      try {
        await markNotificationRead(n.id)
        setItems((prev) => prev.map((it) => (it.id === n.id
          ? { ...it, read_at: new Date().toISOString() } : it)))
      } catch { /* yoksay */ }
    }
    const path = resolveLink(n.link)
    if (path) navigate(path)
  }

  async function onReadAll() {
    try {
      await markAllNotificationsRead()
      setItems((prev) => prev.map((it) => ({ ...it, read_at: it.read_at || new Date().toISOString() })))
      toast.success("Tümü okundu işaretlendi")
    } catch {
      toast.error("İşlem başarısız")
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Bildirimler</h1>
          <p className="text-sm text-muted-foreground">
            Tüm bildirim geçmişin{unreadCount > 0 ? ` · ${unreadCount} okunmamış` : ""}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isManagement && (
            <Button size="sm" onClick={() => setAnnounceOpen(true)}>
              <Megaphone className="mr-1 h-4 w-4" /> Anons gönder
            </Button>
          )}
          {unreadCount > 0 && (
            <Button variant="outline" size="sm" onClick={onReadAll}>Tümünü okundu işaretle</Button>
          )}
        </div>
      </div>

      {isManagement && (
        <AnnounceDialog open={announceOpen} onOpenChange={(o) => {
          setAnnounceOpen(o)
          if (!o) loadFirst()        // gönderilen anons listede hemen görünsün
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
          {items.length === 0 ? "Henüz bildirim yok." : "Bu önem derecesinde bildirim yok."}
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
                    <p className="text-[11px] text-muted-foreground/70">{fullTime(n.created_at)}</p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    {!n.read_at && (
                      <Button variant="ghost" size="sm" onClick={() => onOpen(n)}>Okundu</Button>
                    )}
                    {target && (
                      <Button variant="outline" size="sm" onClick={() => onOpen(n)}>Git</Button>
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
            {loadingMore ? "Yükleniyor…" : "Daha fazla yükle"}
          </Button>
        </div>
      )}
    </div>
  )
}
