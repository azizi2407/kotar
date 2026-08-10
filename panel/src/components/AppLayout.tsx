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

// Okunmamış sayısı ve liste kaç saniyede bir tazelenir (basit polling — YAGNI).
const NOTIFICATION_POLL_MS = 30_000

// roles: undefined = herkes; aksi halde yalnız listedeki roller (management her zaman görür).
const nav = [
  // "/" rol bazlı yönlendirici (App.tsx AnaSayfa): yönetim → /sharing, diğerleri
  // → /dashboard. Menüde bu yüzden gerçek hedef yazılı, yoksa yöneticinin "Panel"e
  // tıklaması onu tekrar Sharing Board'a atardı.
  { to: "/dashboard", labelKey: "nav.dashboard", icon: LayoutDashboard, end: true },
  { to: "/clients", labelKey: "nav.clients", icon: Users, end: false, roles: ["management"] },
  { to: "/sharing", labelKey: "nav.sharing", icon: LayoutGrid, end: false, roles: ["management"] },
  { to: "/designer", labelKey: "nav.designer", icon: Palette, end: false, roles: ["management", "designer"] },
  { to: "/designer-assignments", labelKey: "nav.designerAssignments", icon: UserCog, end: false, roles: ["management", "designer"] },
  { to: "/brief", labelKey: "nav.brief", icon: FileText, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  // Marka rehberi salt-okunur; müşteri detayının ticari/iletişim yüzeyi olmadan.
  { to: "/marka-rehberi", labelKey: "nav.brandGuide", icon: BookOpen, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  // Font havuzu (2026-08-05): dört üretim rolü de görür/indirir; yükleme ve
  // silme yalnız yönetim + tasarımcıda (kapı backend'de, bkz. fonts.py).
  { to: "/fontlar", labelKey: "nav.fonts", icon: Type, end: false,
    roles: ["management", "designer", "content_creator", "videographer"] },
  // Aylık rapor (2026-08-07): Meta CSV'lerinden müşteri raporu. Ticari veri
  // (harcama, erişim) taşıdığı için YALNIZ yönetim.
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

// Sidebar grupları (2026-08-07). Menü 24 öğeye çıkınca düz liste taranamaz hale
// geldi. Gruplar `nav`'ı KOPYALAMAZ, yalnız yol listesiyle ona atıf yapar: `nav`
// hâlâ tek gerçek kaynak (rol/superadmin kapıları oradan türetiliyor), buradaki
// tablo sadece SIRA ve BAŞLIK bilgisi taşır. Yeni bir sayfa eklenip buraya
// yazılmazsa kaybolmaz — "Diğer" grubuna düşer (aşağıdaki artık-toplayıcı).
// `baslik` KARARLI bir iç anahtardır (localStorage + aktif-grup eşleşmesinde
// kullanılır) — dil değişince değişmez. Görünen metin `titleKey` üzerinden
// `t()` ile üretilir (bkz. NavItems).
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

// AÇIK gruplar saklanır (kapalı değil): varsayılan "hepsi kapalı" (proje sahibi 2026-08-07).
// Anahtar adı bilerek yeni — eski "navKapaliGruplar" değerleri ters anlamlıydı,
// aynı anahtarı yeniden kullanmak mevcut tarayıcılarda menüyü tersine çevirirdi.
const GRUP_DEPO_ANAHTARI = "panel.navAcikGruplar"

// Aktif path'in ait olduğu grup — en uzun eşleşme kazanır (/videographer/upload,
// /videographer'dan önce gelmeli). Bu grup, kullanıcı kapatmış olsa bile AÇIK
// gösterilir: "neredeyim" bilgisi kapalı bir grubun içinde kaybolmamalı.
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

// Aktif path için gereken rolleri nav tablosundan türet (tek gerçek kaynak). En uzun
// eşleşen giriş kazanır (ör. /clients/123 → /clients kaydı). Nav'da olmayan route'lar
// (ör. /notifications) kısıtsız sayılır.
function rolesForPath(pathname: string): string[] | undefined {
  const match = nav
    .filter((n) => n.roles && n.to !== "/" &&
      (pathname === n.to || pathname.startsWith(n.to + "/")))
    .sort((a, b) => b.to.length - a.to.length)[0]
  return match?.roles
}

// Rol'e kapalı bir sayfaya girilince (özellikle impersonation'da) ham API 403 yerine
// bu ekran gösterilir. Erişim mantığı nav filtresiyle aynı kaynaktan gelir.
function NoAccess() {
  const { t } = useI18n()
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <p className="text-lg font-medium">{t("noAccess.title")}</p>
      <p className="mt-2 text-sm text-muted-foreground">{t("noAccess.body")}</p>
    </div>
  )
}

// Path superadmin'e mi kapalı (nav.superadmin bayrağı) — GuardedOutlet için.
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

  // Açık gruplar kalıcı (localStorage). Varsayılan HEPSİ KAPALI (proje sahibi tercihi):
  // 24 öğelik menüde asıl kazanç kısa listeyle başlamak. Kaybolma riskini iki şey
  // kapatıyor: aktif sayfanın grubu her zaman açık gösterilir ve başlıklar
  // daima görünür kalır.
  const [acikGruplar, setAcikGruplar] = useState<string[]>(() => {
    try {
      const ham = localStorage.getItem(GRUP_DEPO_ANAHTARI)
      return ham ? (JSON.parse(ham) as string[]) : []
    } catch {
      return []          // bozuk/erişilemez depo menüyü kırmasın
    }
  })

  function grubuCevir(baslik: string) {
    setAcikGruplar((mevcut) => {
      const yeni = mevcut.includes(baslik)
        ? mevcut.filter((b) => b !== baslik)
        : [...mevcut, baslik]
      try { localStorage.setItem(GRUP_DEPO_ANAHTARI, JSON.stringify(yeni)) } catch { /* yok say */ }
      return yeni
    })
  }

  const items = nav.filter(
    (n) => (!n.roles || n.roles.includes(role)) && (!n.superadmin || canImpersonate),
  )
  const aktifGrup = aktifGrupBasligi(pathname)

  // Gruplara dağıt. Bir yol grupta yazılıysa oraya, yazılmamışsa "Diğer"e —
  // yeni sayfa eklenip gruba yazılmayı unutunca menüden DÜŞMESİN.
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
        // Aktif sayfanın grubu, kullanıcı açmamış olsa bile açık gösterilir —
        // "neredeyim" bilgisi kapalı bir grubun içinde kaybolmamalı.
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

// Göreli zaman metni (ör. "5 dk önce") — basit tutuldu, kütüphane eklenmedi.
function timeAgo(iso: string) {
  const diffMs = Date.now() - new Date(iso).getTime()
  const min = Math.round(diffMs / 60000)
  if (min < 1) return "az önce"
  if (min < 60) return `${min} dk önce`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr} sa önce`
  const day = Math.round(hr / 24)
  return `${day} gün önce`
}

function NotificationBell() {
  const [items, setItems] = useState<Notification[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const navigate = useNavigate()

  async function load() {
    try {
      const d = await fetchNotifications()
      setItems(d.notifications)
      setUnreadCount(d.unread_count)
    } catch {
      // bell kritik akış değil — sessizce geç
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
      // yoksay
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
          <DropdownMenuGroup><DropdownMenuLabel className="p-0 font-normal text-foreground">Bildirimler</DropdownMenuLabel></DropdownMenuGroup>
          {unreadCount > 0 && (
            <button
              type="button"
              onClick={handleReadAll}
              className="text-xs font-medium text-primary hover:underline"
            >
              Tümünü okundu işaretle
            </button>
          )}
        </div>
        <DropdownMenuSeparator />
        {items.length === 0 && (
          <div className="px-1.5 py-4 text-center text-sm text-muted-foreground">Bildirim yok</div>
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
            <span className="text-[10px] text-muted-foreground/70">{timeAgo(n.created_at)}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <button
          type="button"
          onClick={() => navigate("/notifications")}
          className="w-full py-1.5 text-center text-xs font-medium text-primary hover:underline"
        >
          Tüm bildirimler ve geçmiş →
        </button>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// Superadmin'e özel: bir kullanıcı seçip onun gözünden bak (impersonation başlat/değiştir).
function ViewAsSwitcher() {
  const { canImpersonate, realUser, impersonate } = useAuth()
  const { data: users } = useUsers()
  if (!canImpersonate) return null
  const others = (users || []).filter((u) => u.sub !== realUser?.sub)
  return (
    <Select value="" onValueChange={(v) => v && impersonate(v)}>
      <SelectTrigger className="h-8 w-auto gap-1 text-xs" title="Bir kullanıcının gözünden bak">
        <Eye className="h-3.5 w-3.5" />
        <SelectValue placeholder="Gözünden bak" />
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

// Üst bar Magnific kredi rozeti (yalnız management). Cache'ten gelir; tazeleme
// günde 2x timer + üretim sonrası. AI görsel üretimleri kredi tüketir (unlimited değil).
function CreditsBadge() {
  const { isManagement } = useAuth()
  const { data } = useMagnificCredits(isManagement)
  if (!isManagement || data?.credits?.available == null) return null
  const at = data.credits.at ? new Date(data.credits.at).toLocaleString("tr-TR") : null
  return (
    <span
      className="hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium text-muted-foreground sm:flex"
      title={`Magnific kalan kredi${at ? ` · güncelleme: ${at}` : ""}${data.refreshing ? " · tazeleniyor…" : ""}`}
    >
      <Coins className="h-3.5 w-3.5 text-amber-500" />
      {data.credits.available.toLocaleString("tr-TR")} kredi
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
              <strong>{user?.name || user?.email}</strong> ({user?.role}) gözünden bakıyorsun.
            </span>
            <span className="text-amber-700 dark:text-amber-400/80">Gerçek kimlik: {realUser?.email}</span>
            <Button size="sm" variant="outline" className="ml-auto h-7"
              onClick={() => stopImpersonate()}>
              Kendine dön
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
