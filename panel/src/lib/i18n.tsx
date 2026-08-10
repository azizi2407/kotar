// i18n altyapısı — tr/en sözlük + basit `t()` fonksiyonu. Dil tercihi localStorage'da
// kalıcıdır (varsayılan: tr). KAPSAM NOTU: şu an yalnız ortak "chrome" (layout, nav,
// giriş ekranı, hata sayfaları, ortak diyaloglar) çevrilidir — 27 sayfanın kendi içeriği
// hâlâ Türkçe metin (bkz. CONTRIBUTING.md "Known gaps"). Yeni bir sayfayı çevirirken bu
// dosyaya `pages.<sayfa>.*` altında anahtar eklemek yeterli.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

export type Lang = "tr" | "en"

const STORAGE_KEY = "panel.lang"

type Dict = Record<string, string>

const tr: Dict = {
  "brand.name": "Kotar",
  "nav.dashboard": "Panel",
  "nav.clients": "Müşteriler",
  "nav.sharing": "Sharing Board",
  "nav.designer": "Tasarım",
  "nav.designerAssignments": "Tasarımcı Atamaları",
  "nav.brief": "Brief",
  "nav.brandGuide": "Marka Rehberi",
  "nav.fonts": "Fontlar",
  "nav.monthlyReport": "Aylık Rapor",
  "nav.voiceNote": "Sesli Not",
  "nav.shootPlan": "Çekim Planı",
  "nav.videoUpload": "Video Yükleme",
  "nav.photos": "Fotoğraflar",
  "nav.videographerDepot": "Videograf Deposu",
  "nav.ideas": "Öneriler",
  "nav.specialDays": "Özel Günler",
  "nav.imageSplitter": "Görsel Bölücü",
  "nav.imageBucket": "Resim Deposu",
  "nav.aiImage": "AI Görsel Üretimi",
  "nav.codexImage": "Codex Görsel",
  "nav.planning": "Planlama Panosu",
  "nav.ads": "Reklam Takibi",
  "nav.clientTracking": "Müşteri Takip",
  "nav.mail": "Posta",
  "nav.users": "Kullanıcılar",
  "navGroup.general": "Genel",
  "navGroup.content": "İçerik",
  "navGroup.video": "Video & Çekim",
  "navGroup.brandTools": "Marka & Araçlar",
  "navGroup.reports": "Rapor & Takip",
  "navGroup.system": "Sistem",
  "navGroup.other": "Diğer",
  "user.changePassword": "Parolamı değiştir",
  "user.logout": "Çıkış Yap",
  "login.title": "Kotar Paneli",
  "login.subtitle": "E-posta ve parolanla giriş yap",
  "login.email": "E-posta",
  "login.password": "Parola",
  "login.submit": "Giriş yap",
  "login.submitting": "Giriş yapılıyor…",
  "login.failed": "Giriş başarısız",
  "login.networkError": "Ağ hatası — tekrar deneyin",
  "noPanelAccess.title": "Panel erişimin yok",
  "noPanelAccess.body": "Bu hesabın panele erişim yetkisi yok; birazdan yönlendirileceksin. Yanlış hesapla mı girdin? Çıkış yapıp doğru hesapla dene.",
  "noPanelAccess.continue": "Devam et",
  "noPanelAccess.switchAccount": "Çıkış yap / farklı hesap",
  "noPanelAccess.backToSelf": "Kendine dön",
  "noAccess.title": "Bu sayfaya erişiminiz yok",
  "noAccess.body": "Bu bölüm mevcut rolünüze kapalı. Soldaki menüden erişebileceğiniz sayfalara geçebilirsiniz.",
  "errorBoundary.title": "Bir şeyler ters gitti",
  "errorBoundary.body": "Sayfa beklenmedik bir hatayla karşılaştı. Yenilemeyi deneyin.",
  "errorBoundary.reload": "Sayfayı yenile",
  "lang.toggle": "EN",
  "changePassword.title": "Parolamı değiştir",
  "changePassword.current": "Mevcut parola",
  "changePassword.new": "Yeni parola (en az 8 karakter)",
  "changePassword.save": "Kaydet",
  "changePassword.saving": "Kaydediliyor…",
  "changePassword.success": "Parola güncellendi",
  "changePassword.failed": "Parola değiştirilemedi",
}

const en: Dict = {
  "brand.name": "Kotar",
  "nav.dashboard": "Dashboard",
  "nav.clients": "Clients",
  "nav.sharing": "Sharing Board",
  "nav.designer": "Design",
  "nav.designerAssignments": "Designer Assignments",
  "nav.brief": "Brief",
  "nav.brandGuide": "Brand Guide",
  "nav.fonts": "Fonts",
  "nav.monthlyReport": "Monthly Report",
  "nav.voiceNote": "Voice Note",
  "nav.shootPlan": "Shoot Plan",
  "nav.videoUpload": "Video Upload",
  "nav.photos": "Photos",
  "nav.videographerDepot": "Videographer Depot",
  "nav.ideas": "Ideas",
  "nav.specialDays": "Special Days",
  "nav.imageSplitter": "Image Splitter",
  "nav.imageBucket": "Image Bucket",
  "nav.aiImage": "AI Image Generation",
  "nav.codexImage": "Codex Image",
  "nav.planning": "Planning Board",
  "nav.ads": "Ad Tracking",
  "nav.clientTracking": "Client Tracking",
  "nav.mail": "Mail",
  "nav.users": "Users",
  "navGroup.general": "General",
  "navGroup.content": "Content",
  "navGroup.video": "Video & Shoots",
  "navGroup.brandTools": "Brand & Tools",
  "navGroup.reports": "Reports & Tracking",
  "navGroup.system": "System",
  "navGroup.other": "Other",
  "user.changePassword": "Change password",
  "user.logout": "Log out",
  "login.title": "Kotar Panel",
  "login.subtitle": "Sign in with your email and password",
  "login.email": "Email",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.submitting": "Signing in…",
  "login.failed": "Sign-in failed",
  "login.networkError": "Network error — try again",
  "noPanelAccess.title": "You don't have panel access",
  "noPanelAccess.body": "This account isn't authorized for the panel; you'll be redirected shortly. Signed in with the wrong account? Sign out and try again.",
  "noPanelAccess.continue": "Continue",
  "noPanelAccess.switchAccount": "Sign out / switch account",
  "noPanelAccess.backToSelf": "Back to my account",
  "noAccess.title": "You don't have access to this page",
  "noAccess.body": "This section isn't available for your current role. Use the sidebar to navigate to pages you can access.",
  "errorBoundary.title": "Something went wrong",
  "errorBoundary.body": "The page hit an unexpected error. Try reloading.",
  "errorBoundary.reload": "Reload page",
  "lang.toggle": "TR",
  "changePassword.title": "Change password",
  "changePassword.current": "Current password",
  "changePassword.new": "New password (min. 8 characters)",
  "changePassword.save": "Save",
  "changePassword.saving": "Saving…",
  "changePassword.success": "Password updated",
  "changePassword.failed": "Couldn't change password",
}

const dictionaries: Record<Lang, Dict> = { tr, en }

function detectInitialLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === "tr" || stored === "en") return stored
  } catch { /* localStorage kapalı olabilir (gizli sekme vb.) */ }
  return "tr"
}

interface I18nState {
  lang: Lang
  setLang: (l: Lang) => void
  t: (key: string) => string
}

const Ctx = createContext<I18nState | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectInitialLang)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, lang)
    } catch { /* no-op */ }
  }, [lang])

  function setLang(l: Lang) {
    setLangState(l)
  }

  function t(key: string): string {
    return dictionaries[lang][key] ?? dictionaries.tr[key] ?? key
  }

  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>
}

export function useI18n() {
  const c = useContext(Ctx)
  if (!c) throw new Error("useI18n I18nProvider içinde kullanılmalı")
  return c
}
