// Posta — panele gömülü webmail. Sol: klasörler · Orta: mesaj listesi (arama) ·
// Sağ: okuma paneli. Erişim backend'de owner_sub ile sınırlı. HTML gövde DOMPurify
// ile sanitize edilir (XSS). react-query ile veri.
import { useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import DOMPurify from "dompurify"
import { Inbox, Mail, Paperclip, RefreshCw, Reply, Send, Star, Plus } from "lucide-react"
import { toast } from "sonner"

import { ComposeDialog, composeReply, type ComposeState } from "@/components/mail/ComposeDialog"
import { ConnectAccountDialog } from "@/components/mail/ConnectAccountDialog"
import { MailAccountHealth } from "@/components/mail/MailAccountHealth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/lib/auth"
import {
  attachmentUrl,
  getMessage,
  listAccounts,
  listFolders,
  listMessages,
  setFlags,
  syncFolder,
  type MailMessage,
} from "@/lib/mail"

function fmtDate(s: string | null) {
  if (!s) return ""
  const d = new Date(s)
  return d.toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
}

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function MailPage() {
  const { canImpersonate } = useAuth()
  const qc = useQueryClient()
  const [accId, setAccId] = useState<number | null>(null)
  const [folderId, setFolderId] = useState<number | null>(null)
  const [msgId, setMsgId] = useState<number | null>(null)
  const [q, setQ] = useState("")
  const [connectOpen, setConnectOpen] = useState(false)
  // Diyalog "şifre güncelle" modunda mı? (2026-07-31) Öncesinde tek bayrak vardı
  // ve "Şifreyi güncelle" düğmesi de yeni-hesap akışını açıyordu → var olan hesap
  // için POST /accounts çağrılıp unique kısıtla 500 alınıyordu.
  const [pwdMode, setPwdMode] = useState(false)
  const [compose, setCompose] = useState<ComposeState | null>(null)

  const isSuperadmin = canImpersonate

  const accountsQ = useQuery({ queryKey: ["mail-accounts"], queryFn: listAccounts })
  const accounts = accountsQ.data ?? []
  const activeAcc = accId ?? accounts[0]?.id ?? null
  const activeAccObj = accounts.find((a) => a.id === activeAcc) ?? null

  const foldersQ = useQuery({
    queryKey: ["mail-folders", activeAcc],
    queryFn: () => listFolders(activeAcc!),
    enabled: activeAcc != null,
  })
  const folders = foldersQ.data ?? []
  const activeFolder =
    folderId ?? folders.find((f) => f.special_use === "inbox")?.id ?? folders[0]?.id ?? null

  const messagesQ = useQuery({
    queryKey: ["mail-messages", activeAcc, activeFolder, q],
    queryFn: () => listMessages(activeAcc!, activeFolder!, 1, q),
    enabled: activeAcc != null && activeFolder != null,
  })
  const messages = messagesQ.data?.messages ?? []

  const messageQ = useQuery({
    queryKey: ["mail-message", activeAcc, msgId],
    queryFn: () => getMessage(activeAcc!, msgId!),
    enabled: activeAcc != null && msgId != null,
  })

  const safeHtml = useMemo(() => {
    const html = messageQ.data?.body_html
    return html ? DOMPurify.sanitize(html) : null
  }, [messageQ.data?.body_html])

  async function doSync() {
    if (activeAcc == null || activeFolder == null) return
    try {
      const n = await syncFolder(activeAcc, activeFolder)
      toast.success(n ? `${n} yeni e-posta` : "Güncel")
      qc.invalidateQueries({ queryKey: ["mail-messages", activeAcc, activeFolder] })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Senkron başarısız")
    }
  }

  async function toggleFlag(m: MailMessage, key: "seen" | "flagged", val: boolean) {
    if (activeAcc == null) return
    try {
      await setFlags(activeAcc, m.id, { [key]: val })
      qc.invalidateQueries({ queryKey: ["mail-messages", activeAcc, activeFolder] })
      qc.invalidateQueries({ queryKey: ["mail-message", activeAcc, m.id] })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "İşaret güncellenemedi")
    }
  }

  // Hiç hesap yok → bağlama çağrısı
  if (accountsQ.isSuccess && accounts.length === 0) {
    return (
      <div className="flex h-[70vh] flex-col items-center justify-center gap-4 text-center">
        <Mail className="h-12 w-12 text-muted-foreground" />
        <div>
          <h2 className="text-lg font-semibold">Posta kutun bağlı değil</h2>
          <p className="text-sm text-muted-foreground">
            E-posta ve şifrenle bağlan; postalarını buradan oku ve gönder.
          </p>
        </div>
        <Button onClick={() => setConnectOpen(true)}>
          <Plus className="mr-1 h-4 w-4" /> Posta kutunu bağla
        </Button>
        <ConnectAccountDialog
          open={connectOpen}
          onClose={() => setConnectOpen(false)}
          onConnected={() => accountsQ.refetch()}
          isSuperadmin={isSuperadmin}
        />
      </div>
    )
  }

  const active = messageQ.data

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col gap-3">
      {/* Üst bar: hesap seçici + aksiyonlar */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="h-9 rounded-md border bg-background px-2 text-sm"
          value={activeAcc ?? ""}
          onChange={(e) => {
            setAccId(Number(e.target.value))
            setFolderId(null)
            setMsgId(null)
          }}
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.email} {a.is_shared ? "· ortak" : ""}
            </option>
          ))}
        </select>
        <Button size="sm" variant="outline" onClick={doSync}>
          <RefreshCw className="mr-1 h-4 w-4" /> Senkron
        </Button>
        <Button
          size="sm"
          onClick={() => setCompose({ to: "", cc: "", subject: "", body: "" })}
          disabled={activeAcc == null}
        >
          <Send className="mr-1 h-4 w-4" /> Yeni
        </Button>
        <Button size="sm" variant="ghost"
          onClick={() => { setPwdMode(false); setConnectOpen(true) }}>
          <Plus className="mr-1 h-4 w-4" /> Hesap ekle
        </Button>
      </div>

      {/* Hesabın bağlantı durumu — okuma/gönderim ayrı, parola ile ağ sorunu ayrı.
          Önceden `last_error` hiçbir ekranda görünmüyordu; gönderim bozulduğunda
          tek sinyal "Gönderilemedi" toast'uydu. */}
      {activeAccObj && (
        <MailAccountHealth
          account={activeAccObj}
          onFixPassword={() => { setPwdMode(true); setConnectOpen(true) }}
        />
      )}

      <div className="grid flex-1 grid-cols-1 gap-3 overflow-hidden md:grid-cols-[180px_320px_1fr]">
        {/* Klasörler */}
        <div className="hidden overflow-y-auto rounded-lg border p-2 md:block">
          {foldersQ.isLoading && <Skeleton className="h-6 w-full" />}
          {folders.map((f) => (
            <button
              key={f.id}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                activeFolder === f.id ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted"
              }`}
              onClick={() => {
                setFolderId(f.id)
                setMsgId(null)
              }}
            >
              {f.special_use === "inbox" ? <Inbox className="h-4 w-4" /> : <Mail className="h-4 w-4" />}
              <span className="truncate">{f.name}</span>
            </button>
          ))}
        </div>

        {/* Mesaj listesi */}
        <div className="flex flex-col overflow-hidden rounded-lg border">
          <div className="border-b p-2">
            <Input
              placeholder="Ara (konu/gönderen)…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="h-8"
            />
          </div>
          <div className="flex-1 overflow-y-auto">
            {messagesQ.isLoading && <Skeleton className="m-2 h-16" />}
            {messagesQ.isSuccess && messages.length === 0 && (
              <p className="p-4 text-center text-sm text-muted-foreground">Mesaj yok</p>
            )}
            {messages.map((m) => (
              <button
                key={m.id}
                onClick={() => setMsgId(m.id)}
                className={`flex w-full flex-col gap-0.5 border-b px-3 py-2 text-left ${
                  msgId === m.id ? "bg-primary/5" : "hover:bg-muted"
                } ${m.seen ? "" : "font-semibold"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm">{m.from_name || m.from_addr}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{fmtDate(m.date)}</span>
                </div>
                <div className="flex items-center gap-1 truncate text-sm">
                  {m.flagged && <Star className="h-3 w-3 fill-amber-400 text-amber-400" />}
                  {m.has_attachments && <Paperclip className="h-3 w-3 text-muted-foreground" />}
                  <span className="truncate">{m.subject || "(konu yok)"}</span>
                </div>
                <span className="truncate text-xs text-muted-foreground">{m.snippet}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Okuma paneli */}
        <div className="flex flex-col overflow-hidden rounded-lg border">
          {!active && (
            <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
              {messageQ.isLoading ? "Yükleniyor…" : "Okumak için bir mesaj seç"}
            </div>
          )}
          {active && (
            <>
              <div className="border-b p-3">
                <div className="flex items-start justify-between gap-2">
                  <h2 className="text-base font-semibold">{active.subject || "(konu yok)"}</h2>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      size="icon"
                      variant="ghost"
                      title="Yıldızla"
                      onClick={() => toggleFlag(active, "flagged", !active.flagged)}
                    >
                      <Star className={`h-4 w-4 ${active.flagged ? "fill-amber-400 text-amber-400" : ""}`} />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      title="Yanıtla"
                      onClick={() => setCompose(composeReply(active))}
                    >
                      <Reply className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  <div>
                    <span className="font-medium">{active.from_name || active.from_addr}</span>{" "}
                    &lt;{active.from_addr}&gt;
                  </div>
                  <div>Kime: {active.to_addrs}</div>
                  <div>{fmtDate(active.date)}</div>
                </div>
                {active.attachments && active.attachments.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {active.attachments.map((a) => (
                      <a
                        key={a.id}
                        href={attachmentUrl(activeAcc!, active.id, a.part_id)}
                        className="flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted"
                      >
                        <Paperclip className="h-3 w-3" />
                        {a.filename} <span className="text-muted-foreground">({fmtSize(a.size)})</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                {safeHtml ? (
                  <div
                    className="prose prose-sm max-w-none dark:prose-invert"
                    dangerouslySetInnerHTML={{ __html: safeHtml }}
                  />
                ) : (
                  <pre className="whitespace-pre-wrap break-words font-sans text-sm">
                    {active.body_text}
                  </pre>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {compose && activeAcc != null && (
        <ComposeDialog
          accountId={activeAcc}
          initial={compose}
          onClose={() => setCompose(null)}
          onSent={() => qc.invalidateQueries({ queryKey: ["mail-messages", activeAcc, activeFolder] })}
        />
      )}
      <ConnectAccountDialog
        open={connectOpen}
        onClose={() => { setConnectOpen(false); setPwdMode(false) }}
        onConnected={() => {
          accountsQ.refetch()
          // Şifre düzeldiyse klasör/mesaj sorguları 502'den dönmüş halde duruyor
          // → elle yenilemeye gerek kalmasın diye tazelenir.
          qc.invalidateQueries({ queryKey: ["mail-folders"] })
          qc.invalidateQueries({ queryKey: ["mail-messages"] })
        }}
        isSuperadmin={isSuperadmin}
        account={pwdMode ? activeAccObj : null}
      />
    </div>
  )
}
