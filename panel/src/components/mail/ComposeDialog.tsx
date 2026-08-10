// Yeni mail / yanıt yazma. Düz metin gövde (MVP). Yanıtta kaynak mesaj id'si
// geçilir → backend In-Reply-To/References kurar, kaynağa \Answered ekler.
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { send, type MailMessage } from "@/lib/mail"

export interface ComposeState {
  to: string
  cc: string
  subject: string
  body: string
  replyToId?: number
}

export function composeReply(m: MailMessage): ComposeState {
  const quoted = (m.body_text || m.snippet || "")
    .split("\n")
    .map((l) => "> " + l)
    .join("\n")
  return {
    to: m.from_addr,
    cc: "",
    subject: m.subject.startsWith("Re:") ? m.subject : `Re: ${m.subject}`,
    body: `\n\n----- ${m.from_name || m.from_addr} yazdı -----\n${quoted}`,
    replyToId: m.id,
  }
}

export function ComposeDialog({
  accountId,
  initial,
  onClose,
  onSent,
}: {
  accountId: number
  initial: ComposeState
  onClose: () => void
  onSent: () => void
}) {
  const [st, setSt] = useState<ComposeState>(initial)
  const [busy, setBusy] = useState(false)

  async function submit() {
    const to = st.to.split(",").map((s) => s.trim()).filter(Boolean)
    if (!to.length) {
      toast.error("En az bir alıcı gerekli")
      return
    }
    setBusy(true)
    try {
      await send(accountId, {
        to,
        cc: st.cc.split(",").map((s) => s.trim()).filter(Boolean),
        subject: st.subject,
        body_text: st.body,
        reply_to_message_id: st.replyToId,
      })
      toast.success("Gönderildi")
      onSent()
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Gönderilemedi")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{st.replyToId ? "Yanıtla" : "Yeni e-posta"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>Kime (virgülle)</Label>
            <Input value={st.to} onChange={(e) => setSt({ ...st, to: e.target.value })} />
          </div>
          <div className="space-y-1">
            <Label>Cc</Label>
            <Input value={st.cc} onChange={(e) => setSt({ ...st, cc: e.target.value })} />
          </div>
          <div className="space-y-1">
            <Label>Konu</Label>
            <Input value={st.subject} onChange={(e) => setSt({ ...st, subject: e.target.value })} />
          </div>
          <div className="space-y-1">
            <Label>Mesaj</Label>
            <Textarea
              rows={12}
              value={st.body}
              onChange={(e) => setSt({ ...st, body: e.target.value })}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              İptal
            </Button>
            <Button onClick={submit} disabled={busy}>
              {busy ? "Gönderiliyor…" : "Gönder"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
