import { useEffect, useState } from "react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { Bell, BookOpen, CalendarHeart, Camera, ChevronDown, Clapperboard, ClipboardList, Coins, Eye, FileBarChart, FileText, Film, HardDrive, Images, LayoutDashboard, LayoutGrid, LogOut, Mail, Megaphone, Menu as MenuIcon, Mic, Palette, PencilRuler, Scissors, ShieldCheck, Sparkles, Type, UserCog, Users, WandSparkles, X } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { useUsers } from "@/lib/clients"
import { useMagnificCredits } from "@/lib/sharing"
import { cn } from "@/lib/utils"
import {
  fetchNotifications, markAllNotificationsRead, markNotificationRead,
  type Notification,
} from "@/lib/notifications"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog"

// How often (in seconds) the unread count and list refresh (simple polling — YAGNI).
const NOTIFICATION_POLL_MS = 30_000

// roles: undefined = everyone; otherwise only the listed roles (management always sees it).
const nav = [
  // "/" is a role-based redirector (App.tsx AnaSayfa): management → /sharing, others
  // → /dashboard. That's why the actual target is written in the menu — otherwise a
  // manager clicking "Dashboard" would get sent back to the Sharing Board.
  { to: "/dashboard", labelKey: "nav.dashboard", icon: LayoutDashboard, end: true },
  { to: "/clients", labelKey: "nav.clients", icon: Users, end: false, roles: ["management"] },
  { to: "/sharing", labelKey: "nav.sharing", icon: LayoutGrid, end: false, roles: ["management"] },
  { to: "/designer", labelKey: "nav.designer", icon: Palette, end: false, roles: ["management", "designer"] },
  { to: "/designer-assignments", labelKey: "nav.designerAssignments", icon: UserCog, end: false, roles: ["management", "designer"] },
  { to: "/brief", labelKey: "nav.brief", icon: FileText, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  // Brand guide is read-only; the commercial/contact surface of the client detail page is excluded.
  { to: "/marka-rehberi", labelKey: "nav.brandGuide", icon: BookOpen, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  // Font pool (2026-08-05): visible/downloadable by all four production roles;
  // upload and delete are management + designer only (gated on the backend, see fonts.py).
  { to: "/fontlar", labelKey: "nav.fonts", icon: Type, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  // Monthly report (2026-08-07): client report from Meta CSVs. Carries commercial data
  // (spend, reach), so management ONLY.
  { to: "/aylik-rapor", labelKey: "nav.monthlyReport", icon: FileBarChart, end: false, roles: ["management"] },
  { to: "/sesli-not", labelKey: "nav.voiceNote", icon: Mic, end: false, roles: ["management"] },
  { to: "/videographer", labelKey: "nav.shootPlan", icon: Clapperboard, end: true, roles: ["management", "videographer"] },
  { to: "/videographer/upload", labelKey: "nav.videoUpload", icon: Film, end: false, roles: ["management", "videographer"] },
  { to: "/videographer/photos", labelKey: "nav.photos", icon: Camera, end: false, roles: ["management", "videographer"] },
  { to: "/videograf-deposu", labelKey: "nav.videographerDepot", icon: HardDrive, end: false, roles: ["management", "videographer"] },
  { to: "/videographer/ideas", labelKey: "nav.ideas", icon: Sparkles, end: false, roles: ["management", "videographer"] },
  { to: "/special-days", labelKey: "nav.specialDays", icon: CalendarHeart, end: false, roles: ["management", "designer"] },
  { to: "/tools/image-splitter", labelKey: "nav.imageSplitter", icon: Scissors, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  { to: "/tools/img-bucket", labelKey: "nav.imageBucket", icon: Images, end: false, roles: ["management"] },
  { to: "/tools/image-gen", labelKey: "nav.aiImage", icon: WandSparkles, end: false, roles: ["management"] },
  { to: "/tools/codex-gorsel", labelKey: "nav.codexImage", icon: Sparkles, end: false, roles: ["management"] },
  { to: "/planlama", labelKey: "nav.planning", icon: PencilRuler, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  { to: "/reklam", labelKey: "nav.ads", icon: Megaphone, end: false, roles: ["management"] },
  { to: "/musteri-takip", labelKey: "nav.clientTracking", icon: ClipboardList, end: false, roles: ["management"] },
  { to: "/posta", labelKey: "nav.mail", icon: Mail, end: false },
  { to: "/kullanicilar", labelKey: "nav.users", icon: ShieldCheck, end: false, superadmin: true },
]

// Sidebar groups (2026-08-07). Once the menu grew to 24 items, a flat list became
// hard to scan. Groups do NOT copy `nav`, they only reference it by a path list:
// `nav` is still the single source of truth (role/superadmin gates are derived from
// it) — this table only carries ORDER and TITLE info. A new page that's added but
// not listed here won't disappear — it falls into the "Other" group (the catch-all
// below). `baslik` is a STABLE internal key (used for localStorage + matching the
// active group) — it doesn't change when the language changes. The displayed text
// is produced via `titleKey` with `t()` (see NavItems).
const NAV_GRUPLARI: { baslik: string; titleKey: string; yollar: string[] }[] = [
  { baslik: "Genel", titleKey: "navGroup.general",
    yollar: ["/dashboard", "/clients", "/sharing", "/planlama", "/sesli-not"] },
  { baslik: "İçerik", titleKey: "navGroup.content",
    yollar: ["/designer", "/designer-assignments", "/brief", "/special-days"] },
  { baslik: "Video & Çekim", titleKey: "navGroup.video",
    yollar: ["/videographer", "/videographer/upload", "/videographer/photos",
             "/videograf-deposu", "/videographer/ideas"] },
  { baslik: "Marka & Araçlar", titleKey: "navGroup.brandTools",
    yollar: ["/marka-rehberi", "/fontlar", "/tools/image-splitter",
             "/tools/img-bucket", "/tools/image-gen", "/tools/codex-gorsel"] },
  { baslik: "Rapor & Takip", titleKey: "navGroup.reports",
    yollar: ["/aylik-rapor", "/reklam", "/musteri-takip"] },
  { baslik: "Sistem", titleKey: "navGroup.system", yollar: ["/posta", "/kullanicilar"] },
]

// OPEN groups are stored (not closed ones): default is "all closed" (project owner, 2026-08-07).
// The key name is deliberately new — the old "navKapaliGruplar" values had the opposite
// meaning, so reusing the same key would have flipped the menu on existing browsers.
const GRUP_DEPO_ANAHTARI = "panel.navAcikGruplar"

// The group the active path belongs to — the longest match wins (/videographer/upload
// must come before /videographer). This group is shown OPEN even if the user closed it:
// "where am I" info shouldn't get lost inside a closed group.
function aktifGrupBasligi(pathname: string): string | undefined {
  let enIyi: { baslik: string; uzunluk: number } | undefined
  for (const g of NAV_GRUPLARI) {
    for (const yol of g.yollar) {
      const eslesti = yol === "/" ? pathname === "/" : (pathname === yol || pathname.startsWith(yol + "/"))
      if (eslesti && (!enIyi || yol.length > enIyi.uzunluk)) {
        enIyi = { baslik: g.baslik, uzunluk: yol.length }
      }
    }
  }
  return enIyi?.baslik
}

// Derive the roles required for the active path from the nav table (single source of
// truth). The longest matching entry wins (e.g. /clients/123 → /clients entry). Routes
// not in nav (e.g. /notifications) are considered unrestricted.
function rolesForPath(pathname: string): string[] | undefined {
  const match = nav
    .filter((n) => n.roles && n.to !== "/" &&
      (pathname === n.to || pathname.startsWith(n.to + "/")))
    .sort((a, b) => b.to.length - a.to.length)[0]
  return match?.roles
}

// Shown instead of a raw API 403 when a page closed to the role is entered (especially
// during impersonation). The access logic comes from the same source as the nav filter.
function NoAccess() {
  const { t } = useI18n()
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <p className="text-lg font-medium">{t("noAccess.title")}</p>
      <p className="mt-2 text-sm text-muted-foreground">{t("noAccess.body")}</p>
    </div>
  )
}

// Whether the path is closed to superadmin (nav.superadmin flag) — for GuardedOutlet.
function isSuperadminPath(pathname: string): boolean {
  const match = nav
    .filter((n) => n.to !== "/" && (pathname === n.to || pathname.startsWith(n.to + "/")))
    .sort((a, b) => b.to.length - a.to.length)[0]
  return Boolean(match?.superadmin)
}

function GuardedOutlet() {
  const { user, canImpersonate } = useAuth()
  const { pathname } = useLocation()
  const roles = rolesForPath(pathname)
  if (roles && !roles.includes(user?.role || "")) return <NoAccess />
  if (isSuperadminPath(pathname) && !canImpersonate) return <NoAccess />
  return <Outlet />
}

function initials(name: string, email: string) {
  const base = (name || email || "?").trim()
  const parts = base.split(/\s+/)
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : base.slice(0, 2)).toUpperCase()
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const { user, canImpersonate } = useAuth()
  const { t } = useI18n()
  const { pathname } = useLocation()
  const role = user?.role || ""

  // Open groups persist (localStorage). Default is ALL CLOSED (project owner's
  // preference): the real win in a 24-item menu is starting with a short list. Two
  // things prevent the "getting lost" risk: the active page's group is always shown
  // open, and the titles always stay visible.
  const [acikGruplar, setAcikGruplar] = useState<string[]>(() => {
    try {
      const ham = localStorage.getItem(GRUP_DEPO_ANAHTARI)
      return ham ? (JSON.parse(ham) as string[]) : []
    } catch {
      return []          // don't let corrupted/inaccessible storage break the menu
    }
  })

  function grubuCevir(baslik: string) {
    setAcikGruplar((mevcut) => {
      const yeni = mevcut.includes(baslik)
        ? mevcut.filter((b) => b !== baslik)
        : [...mevcut, baslik]
      try { localStorage.setItem(GRUP_DEPO_ANAHTARI, JSON.stringify(yeni)) } catch { /* ignore */ }
      return yeni
    })
  }

  const items = nav.filter(
    (n) => (!n.roles || n.roles.includes(role)) && (!n.superadmin || canImpersonate),
  )
  const aktifGrup = aktifGrupBasligi(pathname)

  // Distribute into groups. If a path is listed in a group, it goes there; if not,
  // into "Other" — so a new page won't DISAPPEAR from the menu if adding it to a
  // group is forgotten.
  const gruplanmis = NAV_GRUPLARI.map((g) => ({
    baslik: g.baslik,
    titleKey: g.titleKey,
    items: g.yollar.map((y) => items.find((n) => n.to === y)).filter(Boolean) as typeof items,
  })).filter((g) => g.items.length > 0)
  const yerlesikYollar = new Set(NAV_GRUPLARI.flatMap((g) => g.yollar))
  const artik = items.filter((n) => !yerlesikYollar.has(n.to))
  if (artik.length) gruplanmis.push({ baslik: "Diğer", titleKey: "navGroup.other", items: artik })

  return (
    <nav className="flex flex-col gap-0.5 px-3">
      {gruplanmis.map((g) => {
        // The active page's group is shown open even if the user hasn't opened it —
        // "where am I" info shouldn't get lost inside a closed group.
        const acik = acikGruplar.includes(g.baslik) || g.baslik === aktifGrup
        return (
          <div key={g.baslik} className="pb-0.5">
            <button
              type="button"
              onClick={() => grubuCevir(g.baslik)}
              aria-expanded={acik}
              className="flex w-full items-center gap-1.5 rounded-md px-3 py-1.5 text-[11px]
                         font-semibold tracking-wide text-sidebar-foreground/45 uppercase
                         transition-colors hover:text-sidebar-foreground/70"
            >
              <ChevronDown className={cn("h-3 w-3 transition-transform", !acik && "-rotate-90")} />
              {t(g.titleKey)}
            </button>
            {acik && (
              <div className="flex flex-col gap-1">
                {g.items.map((n) => (
                  <NavLink
                    key={n.to}
                    to={n.to}
                    end={n.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                        isActive
                          ? "bg-sidebar-accent text-sidebar-accent-foreground"
                          : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                      )
                    }
                  >
                    <n.icon className="h-4 w-4" />
                    {t(n.labelKey)}
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </nav>
  )
}

function Brand() {
  return (
    <div className="flex h-16 items-center gap-2 px-6">
      <img src="/panel/kotar-mark.png" alt="Kotar" className="h-9 w-9 object-contain" />
      <span className="text-base font-semibold tracking-tight">Kotar</span>
    </div>
  )
}

// Relative time text (e.g. "5 min ago") — kept simple, no library added.
function timeAgo(iso: string, t: (key: string, vars?: Record<string, string | number>) => string) {
  const diffMs = Date.now() - new Date(iso).getTime()
  const min = Math.round(diffMs / 60000)
  if (min < 1) return t("timeAgo.justNow")
  if (min < 60) return t("timeAgo.minutesAgo", { min })
  const hr = Math.round(min / 60)
  if (hr < 24) return t("timeAgo.hoursAgo", { hr })
  const day = Math.round(hr / 24)
  return t("timeAgo.daysAgo", { day })
}

function NotificationBell() {
  const [items, setItems] = useState<Notification[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const navigate = useNavigate()
  const { t } = useI18n()

  async function load() {
    try {
      const d = await fetchNotifications()
      setItems(d.notifications)
      setUnreadCount(d.unread_count)
    } catch {
      // the bell isn't a critical flow — fail silently
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, NOTIFICATION_POLL_MS)
    return () => clearInterval(t)
  }, [])

  async function handleClick(n: Notification) {
    if (!n.read_at) {
      try {
        await markNotificationRead(n.id)
        setItems((prev) =>
          prev.map((it) => (it.id === n.id ? { ...it, read_at: new Date().toISOString() } : it))
        )
        setUnreadCount((c) => Math.max(0, c - 1))
      } catch {
        // yoksay
      }
    }
    if (n.link) {
      const path = n.link.replace(/^\/panel/, "") || "/"
      navigate(path)
    }
  }

  async function handleReadAll() {
    try {
      await markAllNotificationsRead()
      setItems((prev) => prev.map((it) => ({ ...it, read_at: it.read_at || new Date().toISOString() })))
      setUnreadCount(0)
    } catch {
      // ignore
    }
  }

  return (
    <DropdownMenu onOpenChange={(open) => { if (open) load() }}>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon" className="relative" />}>
        <Bell className="h-5 w-5" />
        {unreadCount > 0 && (
          <Badge
            variant="destructive"
            className="absolute -right-1 -top-1 h-4 min-w-4 justify-center px-1 text-[10px]"
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </Badge>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <div className="flex items-center justify-between gap-2 px-1.5 py-1">
          <DropdownMenuGroup><DropdownMenuLabel className="p-0 font-normal text-foreground">{t("notifications.title")}</DropdownMenuLabel></DropdownMenuGroup>
          {unreadCount > 0 && (
            <button
              type="button"
              onClick={handleReadAll}
              className="text-xs font-medium text-primary hover:underline"
            >
              {t("notifications.markAllRead")}
            </button>
          )}
        </div>
        <DropdownMenuSeparator />
        {items.length === 0 && (
          <div className="px-1.5 py-4 text-center text-sm text-muted-foreground">{t("notifications.empty")}</div>
        )}
        {items.map((n) => (
          <DropdownMenuItem
            key={n.id}
            onClick={() => handleClick(n)}
            className={cn(
              "flex-col items-start gap-0.5 whitespace-normal py-2",
              !n.read_at && "bg-accent/40"
            )}
          >
            <div className="flex w-full items-center gap-1.5">
              {!n.read_at && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />}
              <span className="font-medium">{n.title}</span>
            </div>
            {n.body && <span className="line-clamp-2 text-xs text-muted-foreground">{n.body}</span>}
            <span className="text-[10px] text-muted-foreground/70">{timeAgo(n.created_at, t)}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <button
          type="button"
          onClick={() => navigate("/notifications")}
          className="w-full py-1.5 text-center text-xs font-medium text-primary hover:underline"
        >
          {t("notifications.viewAll")}
        </button>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// Superadmin-only: pick a user and view as them (start/switch impersonation).
function ViewAsSwitcher() {
  const { canImpersonate, realUser, impersonate } = useAuth()
  const { data: users } = useUsers()
  const { t } = useI18n()
  if (!canImpersonate) return null
  const others = (users || []).filter((u) => u.sub !== realUser?.sub)
  return (
    <Select value="" onValueChange={(v) => v && impersonate(v)}>
      <SelectTrigger className="h-8 w-auto gap-1 text-xs" title={t("viewAs.title")}>
        <Eye className="h-3.5 w-3.5" />
        <SelectValue placeholder={t("viewAs.placeholder")} />
      </SelectTrigger>
      <SelectContent>
        {others.map((u) => (
          <SelectItem key={u.sub} value={u.sub}>
            {(u.name || u.email)} · {u.role}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

// Top bar Magnific credit badge (management only). Comes from cache; refreshed by
// a 2x/day timer + after generation. AI image generation consumes credits (not unlimited).
function CreditsBadge() {
  const { isManagement } = useAuth()
  const { data } = useMagnificCredits(isManagement)
  const { t, lang } = useI18n()
  if (!isManagement || data?.credits?.available == null) return null
  const locale = lang === "tr" ? "tr-TR" : "en-US"
  const at = data.credits.at ? new Date(data.credits.at).toLocaleString(locale) : null
  const title = t("credits.label")
    + (at ? t("credits.updated", { at }) : "")
    + (data.refreshing ? t("credits.refreshing") : "")
  return (
    <span
      className="hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium text-muted-foreground sm:flex"
      title={title}
    >
      <Coins className="h-3.5 w-3.5 text-amber-500" />
      {data.credits.available.toLocaleString(locale)} {t("credits.unit")}
    </span>
  )
}

export function AppLayout() {
  const { user, logout, impersonating, realUser, stopImpersonate, authMode } = useAuth()
  const { t, lang, setLang } = useI18n()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [pwdOpen, setPwdOpen] = useState(false)

  return (
    <div className="min-h-screen bg-muted/30">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
        <Brand />
        <div className="mt-2 flex-1 overflow-y-auto">
          <NavItems />
        </div>
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r bg-sidebar text-sidebar-foreground">
            <div className="flex items-center justify-between pr-3">
              <Brand />
              <Button variant="ghost" size="icon" onClick={() => setMobileOpen(false)}>
                <X className="h-5 w-5" />
              </Button>
            </div>
            <div className="mt-2 flex-1 overflow-y-auto">
              <NavItems onNavigate={() => setMobileOpen(false)} />
            </div>
          </aside>
        </div>
      )}

      <div className="md:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur md:px-8">
          <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileOpen(true)}>
            <MenuIcon className="h-5 w-5" />
          </Button>
          <div className="ml-auto flex items-center gap-2">
            <CreditsBadge />
            <ViewAsSwitcher />
            <Button
              variant="ghost"
              size="sm"
              className="px-2 text-xs font-semibold"
              onClick={() => setLang(lang === "tr" ? "en" : "tr")}
              title={lang === "tr" ? "Switch to English" : "Türkçeye geç"}
            >
              {t("lang.toggle")}
            </Button>
            <NotificationBell />
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" className="gap-2 px-2" />}>
                <Avatar className="h-8 w-8">
                  <AvatarFallback className="text-xs">
                    {initials(user?.name || "", user?.email || "")}
                  </AvatarFallback>
                </Avatar>
                <span className="hidden text-sm font-medium sm:inline">{user?.name || user?.email}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="font-normal">
                    <div className="truncate text-muted-foreground">{user?.email}</div>
                    <Badge variant="secondary" className="mt-1">{user?.role}</Badge>
                  </DropdownMenuLabel>
                </DropdownMenuGroup>
                {authMode === "local" && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setPwdOpen(true)}>
                      {t("user.changePassword")}
                    </DropdownMenuItem>
                  </>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={logout} className="text-destructive focus:text-destructive">
                  <LogOut className="mr-2 h-4 w-4" /> {t("user.logout")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            {authMode === "local" && (
              <ChangePasswordDialog open={pwdOpen} onClose={() => setPwdOpen(false)} />
            )}
          </div>
        </header>
        {impersonating && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-amber-300 bg-amber-100 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-200 md:px-8">
            <Eye className="h-4 w-4 shrink-0" />
            <span>
              {t("impersonate.viewingAs")} <strong>{user?.name || user?.email}</strong> ({user?.role})
            </span>
            <span className="text-amber-700 dark:text-amber-400/80">{t("impersonate.realIdentity")} {realUser?.email}</span>
            <Button size="sm" variant="outline" className="ml-auto h-7"
              onClick={() => stopImpersonate()}>
              {t("impersonate.backToSelf")}
            </Button>
          </div>
        )}
        <main className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">
          <GuardedOutlet />
        </main>
      </div>
    </div>
  )
}
