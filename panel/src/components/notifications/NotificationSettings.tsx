// Bildirim ayarları kartı (2026-08-05) — kişiye özel ntfy kanalı.
//
// Panel-içi çan HERKESTE çalışmaya devam eder; bu kart yalnız TELEFON kanalını
// yönetir ve bilinçli olarak opt-in: `ntfy_enabled` açılmadan hiçbir şey gitmez.
// Topic yalnız sahibine gösterilir (ntfy'de okuma yetkisi adın gizliliğine
// dayanıyor) — o yüzden "yenile" düğmesi var: adres sızarsa tek çare değiştirmek.
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
import { cn } from "@/lib/utils"

// Eşik etiketleri kullanıcı diliyle: "kritik" seçen YALNIZ kritik alır.
const ESIK_SECENEK: { value: Severity; label: string; hint: string }[] = [
  { value: "kritik", label: "Yalnız kritik", hint: "Revizyon talebi, müşteri revizesi, sunucu uyarısı" },
  { value: "normal", label: "Kritik + normal", hint: "Yukarıdakiler + onaylar, başarısız işler, yeni e-posta" },
  { value: "bilgi", label: "Hepsi", hint: "Arşiv niteliğindeki kayıtlar dahil" },
]

const SAATLER = Array.from({ length: 24 }, (_, i) => i)
const ss = (h: number) => `${String(h).padStart(2, "0")}:00`

// Etiketli, tek tıkla kopyalanan alan — ntfy kurulumunda sunucu ve konu ayrı ayrı
// yapıştırılıyor, kullanıcının URL'den parça sökmesi gerekmesin.
function CopyRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-20 shrink-0 text-xs text-muted-foreground">{label}</span>
      <code className={cn("min-w-0 flex-1 truncate rounded border bg-background px-2 py-1 text-xs",
        mono && "font-mono tracking-tight")}>
        {value}
      </code>
      <Button variant="ghost" size="sm" onClick={() => {
        navigator.clipboard.writeText(value)
        toast.success(`${label} kopyalandı`)
      }}>
        <Copy className="mr-1 h-3.5 w-3.5" /> Kopyala
      </Button>
    </div>
  )
}

export function NotificationSettings() {
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
      .catch(() => toast.error("Bildirim ayarları yüklenemedi"))
      .finally(() => setLoading(false))
  }, [])

  async function kaydet(patch: Partial<NotificationPrefs> & { rotate_topic?: boolean }) {
    setBusy(true)
    try {
      const d = await saveNotificationPrefs(patch)
      setPrefs(d.prefs)
      setSubscribeUrl(d.subscribe_url)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Kaydedilemedi")
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    try {
      await sendTestNotification()
      toast.success("Test bildirimi gönderildi — telefonuna bakabilirsin")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test bildirimi gönderilemedi")
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
            Telefon bildirimleri
          </div>
          <p className="text-sm text-muted-foreground">
            Seçtiğin önemdeki bildirimler telefonuna anında düşer. Panel içi bildirimler
            bu ayardan bağımsız, her zaman çalışır.
          </p>
        </div>
        <Button variant={prefs.ntfy_enabled ? "outline" : "default"} size="sm" disabled={busy}
          onClick={() => kaydet({ ntfy_enabled: !prefs.ntfy_enabled })}>
          {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          {prefs.ntfy_enabled ? "Kapat" : "Aç"}
        </Button>
      </div>

      {!channelReady && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
          Bildirim sunucusu yapılandırılmamış — ayarlar kaydedilir ama gönderim yapılmaz.
        </p>
      )}

      {prefs.ntfy_enabled && (
        <>
          {/* kurulum — ntfy uygulaması SUNUCU ve KONU'yu ayrı alanlarda ister,
              o yüzden ikisi ayrı satırda ve ayrı ayrı kopyalanabilir. */}
          <div className="space-y-2 rounded-md bg-muted/40 p-3">
            <div className="text-sm font-medium">Telefonu bağla</div>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
              <li><span className="font-medium text-foreground">ntfy</span> uygulamasını kur (App Store / Google Play).</li>
              <li>Uygulamada <span className="font-medium text-foreground">+</span> (Abone ol) → <span className="font-medium text-foreground">"Use another server"</span> / "Başka sunucu kullan" seçeneğini aç.</li>
              <li><span className="font-medium text-foreground">Sunucu</span> ve <span className="font-medium text-foreground">Konu adı</span> alanlarına aşağıdaki değerleri yapıştır.</li>
              <li><span className="font-medium text-foreground">Test bildirimi</span> düğmesiyle doğrula.</li>
            </ol>

            <div className="space-y-1.5">
              <CopyRow label="Sunucu" value={serverUrl} />
              <CopyRow label="Konu adı" value={prefs.ntfy_topic} mono />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <a href={subscribeUrl} target="_blank" rel="noreferrer"
                className="text-xs text-primary hover:underline">
                Tarayıcıda aç (tek bağlantı)
              </a>
              <Button variant="ghost" size="sm" disabled={busy} onClick={test}>
                <Send className="mr-1 h-3.5 w-3.5" /> Test bildirimi
              </Button>
              {/* Adres gizli bir anahtar gibi çalışıyor: sızarsa yenilemek tek çözüm.
                  Yenileyince eski cihazların aboneliği kesilir — bu yüzden onay soruyoruz. */}
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => {
                if (!confirm("Adres yenilenecek. Telefonundaki mevcut abonelik çalışmayı bırakır, yeniden bağlaman gerekir. Devam?")) return
                kaydet({ rotate_topic: true })
              }}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" /> Adresi yenile
              </Button>
            </div>
          </div>

          {/* eşik */}
          <div className="space-y-1.5">
            <div className="text-sm font-medium">Hangi bildirimler gelsin?</div>
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

          {/* sessiz saatler */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium">Sessiz saatler</div>
              <Button variant="ghost" size="sm" disabled={busy}
                onClick={() => kaydet(sessizAcik
                  ? { quiet_start: null, quiet_end: null }
                  : { quiet_start: 22, quiet_end: 8 })}>
                {sessizAcik ? "Kapat" : "Aç"}
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
                  Bu aralıkta yalnız <span className="font-medium">kritik</span> bildirimler telefonu çaldırır.
                </span>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Kapalı — bildirimler saat farkı gözetmeden gelir.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
