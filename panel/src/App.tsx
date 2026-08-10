import { lazy, Suspense, useEffect } from "react"
import { Routes, Route, Navigate } from "react-router-dom"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { AppLayout } from "@/components/AppLayout"
import { ErrorBoundary } from "@/components/ErrorBoundary"

// Planlama tuvali AYRI CHUNK: @xyflow/react ~200 KB ve panelin tek chunk'ı zaten
// 1 MB'ı geçiyordu. Tuvale hiç girmeyen kullanıcı bedelini ödemesin.
const PlanlamaPage = lazy(() => import("@/pages/PlanlamaPage")
  .then((m) => ({ default: m.PlanlamaPage })))
import { DashboardPage } from "@/pages/DashboardPage"
import { ClientsPage } from "@/pages/ClientsPage"
import { ClientDetailPage } from "@/pages/ClientDetailPage"
import { DesignerAssignmentsPage } from "@/pages/DesignerAssignmentsPage"
import { MarkaRehberiPage } from "@/pages/MarkaRehberiPage"
import { FontsPage } from "@/pages/FontsPage"
import { AylikRaporPage } from "@/pages/AylikRaporPage"
import { SharingBoardPage } from "@/pages/SharingBoardPage"
import { DesignerBoardPage } from "@/pages/DesignerBoardPage"
import { ClientMediaPage } from "@/pages/ClientMediaPage"
import { VideographerBoardPage } from "@/pages/VideographerBoardPage"
import { VideographerPhotosPage } from "@/pages/VideographerPhotosPage"
import { VideographerUploadPage } from "@/pages/VideographerUploadPage"
import { VideographerIdeasPage } from "@/pages/VideographerIdeasPage"
import { SpecialDaysPage } from "@/pages/SpecialDaysPage"
import { ImageSplitterPage } from "@/pages/ImageSplitterPage"
import { ImgBucketPage } from "@/pages/ImgBucketPage"
import { BriefPage } from "@/pages/BriefPage"
import { CodexImagePage } from "@/pages/CodexImagePage"
import { ImageGenPage } from "@/pages/ImageGenPage"
import { NotificationsPage } from "@/pages/NotificationsPage"
import { MailPage } from "@/pages/MailPage"
import { UsersAdminPage } from "@/pages/UsersAdminPage"
import { AdsPage } from "@/pages/AdsPage"
import { MusteriTakipPage } from "@/pages/MusteriTakipPage"
import { DepotPage } from "@/pages/DepotPage"
import { VoiceNotePage } from "@/pages/VoiceNotePage"
import { LoginPage } from "@/pages/LoginPage"

function FullscreenSpinner() {
  return (
    <div className="flex h-screen items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-foreground" />
    </div>
  )
}

// Panel erişimi olan roller. Bunun dışındaki (pending/client/bilinmeyen) kullanıcı
// giriş yapsa bile panele giremez → kurumun genel web sitesine yönlendirilir
// (VITE_PUBLIC_SITE_URL; verilmezse panel köküne düşer).
const PANEL_ROLES = ["management", "designer", "content_creator", "videographer"]
const PUBLIC_SITE = import.meta.env.VITE_PUBLIC_SITE_URL || "/panel/"

// Panel rolü olmayan kullanıcıya gösterilen ekran. PUBLIC_SITE'a otomatik yönlendirir
// AMA önce KAÇIŞ YOLU sunar (çıkış yap / kendine dön) — aksi halde yanlış/test hesabıyla
// giren kişi anında yönlendirilip panele bir daha giremezdi (kapan). Çıkış = /auth/logout.
function NoPanelAccess() {
  const { logout, impersonating, stopImpersonate } = useAuth()
  const { t } = useI18n()
  useEffect(() => {
    const timer = setTimeout(() => { window.location.href = PUBLIC_SITE }, 8000)
    return () => clearTimeout(timer)
  }, [])
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-xl font-semibold">{t("noPanelAccess.title")}</h1>
      <p className="max-w-sm text-sm text-muted-foreground">{t("noPanelAccess.body")}</p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <a href={PUBLIC_SITE}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
          {t("noPanelAccess.continue")}
        </a>
        {impersonating ? (
          <button onClick={stopImpersonate}
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted">
            {t("noPanelAccess.backToSelf")}
          </button>
        ) : (
          <button onClick={logout}
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted">
            {t("noPanelAccess.switchAccount")}
          </button>
        )}
      </div>
    </div>
  )
}

// Oturum yoksa SSO'ya (tam sayfa) yönlendirir; local login sayfası yok. Oturum var ama
// panel rolü yoksa NoPanelAccess (kaçış yollu PUBLIC_SITE yönlendirmesi) gösterilir.
function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading, login } = useAuth()
  useEffect(() => {
    if (!loading && !user) login()
  }, [loading, user, login])
  if (loading || !user) return <FullscreenSpinner />
  if (!PANEL_ROLES.includes(user.role)) return <NoPanelAccess />
  return <>{children}</>
}

// Rol'e göre ana sayfa. `Navigate` ile YÖNLENDİRİLİR (aynı yolda farklı bileşen
// render etmek yerine) → adres çubuğu gerçek sayfayı gösterir, sidebar'daki aktif
// işaret doğru yere düşer ve yenilemede aynı yerde kalınır.
function AnaSayfa() {
  const { user } = useAuth()
  return <Navigate to={user?.role === "management" ? "/sharing" : "/dashboard"} replace />
}

export default function App() {
  return (
    <ErrorBoundary>
    <Routes>
      {/* AUTH_MODE=local'de Flask /auth/login buraya yönlendirir — Protected'ın
          dışında, oturumsuz erişilebilir tek route. */}
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Protected><AppLayout /></Protected>}>
        {/* Yöneticinin ana sayfası Sharing Board (proje sahibi 2026-08-07): gün oradan
            başlıyor. Dashboard kaldırılmadı, `/dashboard`'a taşındı — diğer roller
            için ana sayfa olmaya devam ediyor ve yönetim de menüden ulaşabiliyor. */}
        <Route path="/" element={<AnaSayfa />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/clients" element={<ClientsPage />} />
        <Route path="/clients/:id" element={<ClientDetailPage />} />
        <Route path="/sharing" element={<SharingBoardPage />} />
        <Route path="/designer" element={<DesignerBoardPage />} />
        {/* Müşteri medya sayfası bilerek /designer altında: AppLayout rol kapısını
            nav tablosundan PREFIX eşleşmesiyle türetir → management + designer. */}
        <Route path="/designer/musteri/:id" element={<ClientMediaPage />} />
        <Route path="/designer-assignments" element={<DesignerAssignmentsPage />} />
        <Route path="/marka-rehberi" element={<MarkaRehberiPage />} />
        <Route path="/fontlar" element={<FontsPage />} />
        <Route path="/aylik-rapor" element={<AylikRaporPage />} />
        <Route path="/sesli-not" element={<VoiceNotePage />} />
        <Route path="/brief" element={<BriefPage />} />
        <Route path="/videographer" element={<VideographerBoardPage />} />
        <Route path="/videographer/upload" element={<VideographerUploadPage />} />
        <Route path="/videographer/photos" element={<VideographerPhotosPage />} />
        <Route path="/videographer/ideas" element={<VideographerIdeasPage />} />
        <Route path="/special-days" element={<SpecialDaysPage />} />
        <Route path="/tools/image-splitter" element={<ImageSplitterPage />} />
        <Route path="/tools/img-bucket" element={<ImgBucketPage />} />
        <Route path="/planlama" element={
          <Suspense fallback={<div className="p-8 text-muted-foreground">Tuval yükleniyor…</div>}>
            <PlanlamaPage />
          </Suspense>
        } />
        {/* eski yer imleri kırılmasın */}
        <Route path="/canvas" element={<Navigate to="/planlama" replace />} />
        <Route path="/tools/image-gen" element={<ImageGenPage />} />
        <Route path="/tools/codex-gorsel" element={<CodexImagePage />} />
        <Route path="/posta" element={<MailPage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/reklam" element={<AdsPage />} />
        <Route path="/musteri-takip" element={<MusteriTakipPage />} />
        <Route path="/videograf-deposu" element={<DepotPage />} />
        <Route path="/kullanicilar" element={<UsersAdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </ErrorBoundary>
  )
}
