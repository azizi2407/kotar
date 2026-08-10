// Posta kutusu bağlama — self-servis. Kullanıcı yalnız e-posta + şifre girer;
// host/port varsayılan mail sunucusuyla ön-dolu ("Gelişmiş" altında düzenlenebilir).
//
// İKİ MOD (2026-07-31): `account` verilirse **şifre güncelleme**, verilmezse yeni
// hesap bağlama. Öncesinde tek mod vardı ve sağlık şeridindeki "Şifreyi güncelle"
// düğmesi de bu diyaloğu açıyordu → var olan hesap için `createAccount` (POST)
// çağrılıyor, `uq_mail_owner_email` ihlaliyle **500** dönüyordu. Yani düğme
// yapısal olarak çalışamıyordu; doğru yol (`PATCH /accounts/:id` + `password`)
// backend'de ve `lib/mail.ts`'te vardı ama hiçbir yerden çağrılmıyordu.
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { createAccount, updateAccount, type MailAccount } from "@/lib/mail"

const DEFAULTS = {
  imap_host: "mail.example.com",
  imap_port: 993,
  smtp_host: "mail.example.com",
  smtp_port: 465,
}

export function ConnectAccountDialog({
  open,
  onClose,
  onConnected,
  isSuperadmin,
  account,
}: {
  open: boolean
  onClose: () => void
  onConnected: () => void
  isSuperadmin: boolean
  /** Verilirse diyalog "şifre güncelle" modunda açılır (yeni hesap OLUŞTURMAZ). */
  account?: MailAccount | null
}) {
  const editing = !!account
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [advanced, setAdvanced] = useState(false)
  const [cfg, setCfg] = useState(DEFAULTS)
  const [isShared, setIsShared] = useState(false)
  const [pollEnabled, setPollEnabled] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (editing) {
      if (!password) {
        toast.error("Yeni şifreyi girin")
        return
      }
      setBusy(true)
      try {
        // Backend şifreyi kaydetmeden ÖNCE IMAP ile doğruluyor → buraya düşen
        // hata gerçekten yanlış şifre/erişim demek, sessiz bozuk kayıt oluşmaz.
        await updateAccount(account!.id, { password })
        toast.success("Şifre güncellendi — bağlantı doğrulandı")
        setPassword("")
        onConnected()
        onClose()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Şifre güncellenemedi")
      } finally {
        setBusy(false)
      }
      return
    }

    if (!email || !password) {
      toast.error("E-posta ve şifre zorunlu")
      return
    }
    setBusy(true)
    try {
      await createAccount({
        email,
        password,
        imap_host: cfg.imap_host,
        imap_port: cfg.imap_port,
        smtp_host: cfg.smtp_host,
        smtp_port: cfg.smtp_port,
        smtp_security: "ssl",
        is_shared: isSuperadmin ? isShared : false,
        poll_enabled: isSuperadmin ? pollEnabled : false,
      })
      toast.success("Posta kutusu bağlandı")
      onConnected()
      onClose()
    } catch (e) {
      // Backend "zaten bağlı" gibi anlaşılır hatalar döndürüyor; kendi tahminimizi
      // onun üstüne yazmıyoruz (eski metin parola doğruyken bile şüpheyi
      // parolaya yönlendiriyordu).
      toast.error(e instanceof Error ? e.message : "Bağlanamadı (e-posta/şifre?)")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{editing ? "Şifreyi güncelle" : "Posta kutunu bağla"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>E-posta</Label>
            {/* Güncelleme modunda hesap sabit — e-postayı değiştirmek "başka bir
                hesap" demek olurdu, o da bağlama akışının işi. */}
            <Input
              type="email"
              placeholder="ad@sirket.com"
              value={editing ? account!.email : email}
              disabled={editing}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{editing ? "Yeni şifre" : "Şifre"}</Label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {editing && (
            <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
              Posta sunucusunda şifreni değiştirdiysen buraya yenisini gir.
              Kaydetmeden önce sunucuya bağlanıp doğrulanır.
            </p>
          )}

          {!editing && isSuperadmin && (
            <div className="space-y-2 rounded-md border p-3">
              <div className="flex items-center justify-between">
                <Label className="text-sm">Ortak kutu (info@ gibi)</Label>
                <Switch checked={isShared} onCheckedChange={setIsShared} />
              </div>
              <div className="flex items-center justify-between">
                <Label className="text-sm">Arka plan senkron (poller)</Label>
                <Switch checked={pollEnabled} onCheckedChange={setPollEnabled} />
              </div>
            </div>
          )}

          {!editing && (
            <button
              type="button"
              className="text-xs text-muted-foreground underline"
              onClick={() => setAdvanced((v) => !v)}
            >
              {advanced ? "Gelişmiş ayarları gizle" : "Gelişmiş (sunucu/port)"}
            </button>
          )}
          {!editing && advanced && (
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">IMAP sunucu</Label>
                <Input
                  value={cfg.imap_host}
                  onChange={(e) => setCfg({ ...cfg, imap_host: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">IMAP port</Label>
                <Input
                  type="number"
                  value={cfg.imap_port}
                  onChange={(e) => setCfg({ ...cfg, imap_port: Number(e.target.value) })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">SMTP sunucu</Label>
                <Input
                  value={cfg.smtp_host}
                  onChange={(e) => setCfg({ ...cfg, smtp_host: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">SMTP port</Label>
                <Input
                  type="number"
                  value={cfg.smtp_port}
                  onChange={(e) => setCfg({ ...cfg, smtp_port: Number(e.target.value) })}
                />
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              İptal
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? (editing ? "Doğrulanıyor…" : "Bağlanıyor…")
                    : (editing ? "Kaydet ve doğrula" : "Bağlan")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
