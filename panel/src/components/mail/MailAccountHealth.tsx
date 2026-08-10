// Posta hesabının bağlantı durumu + "Bağlantıyı test et".
//
// Neden var: `testAccount` uçları ve `last_error` kolonu 2026-07-23'ten beri
// mevcuttu ama **panelde hiçbir yerde gösterilmiyordu** — `lib/mail.ts`'teki
// `testAccount` hiçbir bileşen tarafından çağrılmıyordu. Sonuç: gönderim
// patladığında kullanıcının tek gördüğü "Gönderilemedi: …" toast'uydu; hesabın
// IMAP'i mi SMTP'si mi bozuk, parola mı ağ mı olduğunu göremiyor ve panelden
// onaramıyordu. SMTP'nin aylarca "kapalı" görünmesinin bir nedeni de bu
// görünmezlikti: durum kaydı vardı, kimse bakamıyordu.
import { useState } from "react"
import { AlertTriangle, CheckCircle2, KeyRound, PlugZap, ServerCrash } from "lucide-react"
import { toast } from "sonner"

import { testAccount, type MailAccount, type TestResult } from "@/lib/mail"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function MailAccountHealth({ account, onFixPassword }: {
  account: MailAccount
  onFixPassword?: () => void
}) {
  const [result, setResult] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    try {
      const r = await testAccount(account.id)
      setResult(r)
      if (r.imap_ok && r.smtp_ok) toast.success("Okuma ve gönderim çalışıyor")
      else if (r.imap_ok) toast.warning("Okuma çalışıyor, gönderim çalışmıyor")
      else toast.error("Bağlantı kurulamadı")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test edilemedi")
    } finally {
      setBusy(false)
    }
  }

  // Test edilmediyse kayıttaki son duruma bakılır (`last_error`); test edildiyse
  // taze sonuç kazanır.
  const stored = account.last_error
  const nothingWrong = result ? result.imap_ok && result.smtp_ok : !stored
  // Parola sorunu mu? Taze sonuçta sınıf var; kayıtta yalnız metin var.
  // `giriş başarısız` da aranıyor (2026-07-31): gateway'in ham `MailAuthError`
  // metni bu ve "parola/kimlik" kelimelerini içermiyor — yalnız iki kelimeye
  // bakan süzgeç, sınıflandırmayı atlayan bir yol eklendiğinde "Şifreyi güncelle"
  // düğmesini sessizce görünmez yapardı.
  const authIssue = result
    ? result.imap_error_kind === "auth" || result.smtp_error_kind === "auth"
    : !!stored && /parola|kimli|giriş başarısız/i.test(stored)

  return (
    <div className={cn(
      "flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs",
      nothingWrong ? "border-emerald-500/40 bg-emerald-500/10"
                   : "border-amber-500/40 bg-amber-500/10")}>
      {nothingWrong ? (
        <span className="flex items-center gap-1.5 font-medium">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          {result ? "Okuma ve gönderim çalışıyor" : "Bağlantı sorunu bildirilmedi"}
        </span>
      ) : (
        <span className="flex min-w-0 items-start gap-1.5">
          {authIssue ? <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                     : <ServerCrash className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />}
          <span className="min-w-0">
            <strong>{authIssue ? "Parola sorunu" : "Bağlantı sorunu"}:</strong>{" "}
            {result ? (result.imap_error || result.smtp_error) : stored}
            {authIssue && (
              <span className="ml-1 text-muted-foreground">
                — hesabı yeniden bağlayıp şifreyi güncellemek çözer.
              </span>
            )}
          </span>
        </span>
      )}

      {result && !nothingWrong && (
        <span className="flex items-center gap-2">
          <Channel label="Okuma (IMAP)" ok={result.imap_ok} />
          <Channel label="Gönderim (SMTP)" ok={result.smtp_ok} />
        </span>
      )}

      <span className="ml-auto flex items-center gap-1.5">
        {authIssue && onFixPassword && (
          <Button size="sm" variant="outline" onClick={onFixPassword}>
            Şifreyi güncelle
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={run} disabled={busy}>
          <PlugZap className="mr-1 h-3.5 w-3.5" />
          {busy ? "Test ediliyor…" : "Bağlantıyı test et"}
        </Button>
      </span>
    </div>
  )
}

function Channel({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5",
      ok ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
         : "bg-red-500/15 text-red-600 dark:text-red-400")}>
      {ok ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {label}
    </span>
  )
}
