// Mail module API client — /api/mail/*. Access is restricted by owner_sub on the backend.
import { apiGet, apiJson } from "@/lib/api"

export interface MailAccount {
  id: number
  owner_sub: string
  email: string
  display_name: string | null
  imap_host: string
  imap_port: number
  imap_ssl: boolean
  smtp_host: string
  smtp_port: number
  smtp_security: string
  is_shared: boolean
  poll_enabled: boolean
  active: boolean
  has_secret: boolean
  last_ok_at: string | null
  last_error: string | null
}

export interface MailFolder {
  id: number
  name: string
  path: string
  special_use: string | null
  last_sync_at: string | null
}

export interface MailAttachment {
  id: number
  filename: string
  content_type: string
  size: number
  part_id: string
}

export interface MailMessage {
  id: number
  uid: number
  folder_id: number
  message_id: string | null
  thread_key: string | null
  from_addr: string
  from_name: string
  to_addrs: string
  cc_addrs: string
  subject: string
  date: string | null
  snippet: string
  seen: boolean
  flagged: boolean
  answered: boolean
  has_attachments: boolean
  // full mode
  body_text?: string
  body_html?: string
  in_reply_to?: string
  references?: string
  attachments?: MailAttachment[]
}

export interface SendPayload {
  to: string[]
  cc?: string[]
  subject: string
  body_text: string
  body_html?: string
  in_reply_to?: string
  reply_to_message_id?: number
}

export const listAccounts = () =>
  apiGet("/mail/accounts").then((r) => r.accounts as MailAccount[])

export const createAccount = (body: Record<string, unknown>) =>
  apiJson("/mail/accounts", body).then((r) => r.account as MailAccount)

export const updateAccount = (id: number, body: Record<string, unknown>) =>
  apiJson(`/mail/accounts/${id}`, body, "PATCH").then((r) => r.account as MailAccount)

export const deleteAccount = (id: number) =>
  apiJson(`/mail/accounts/${id}`, {}, "DELETE")

export interface TestResult {
  imap_ok: boolean
  smtp_ok: boolean
  imap_error: string | null
  smtp_error: string | null
  // The error's CLASS (2026-07-31): 'auth' = wrong password (user can fix it),
  // 'connect' = server unreachable (network/server-side). These are two different
  // problems; looking at a single message alone couldn't distinguish them, and a
  // fixable password issue could be mistaken for a "server block" for months.
  imap_error_kind: "auth" | "connect" | null
  smtp_error_kind: "auth" | "connect" | null
}

export const testAccount = (id: number) =>
  apiJson(`/mail/accounts/${id}/test`, {}).then((r) => r as TestResult)

export const listFolders = (acc: number) =>
  apiGet(`/mail/${acc}/folders`).then((r) => r.folders as MailFolder[])

export const listMessages = (acc: number, folder: number, page = 1, q = "") =>
  apiGet(`/mail/${acc}/folders/${folder}/messages?page=${page}${q ? `&q=${encodeURIComponent(q)}` : ""}`).then(
    (r) => r as { messages: MailMessage[]; total: number; page: number }
  )

export const syncFolder = (acc: number, folder: number) =>
  apiJson(`/mail/${acc}/folders/${folder}/sync`, {}).then((r) => r.new as number)

export const getMessage = (acc: number, mid: number) =>
  apiGet(`/mail/${acc}/messages/${mid}`).then((r) => r.message as MailMessage)

export const setFlags = (acc: number, mid: number, flags: { seen?: boolean; flagged?: boolean }) =>
  apiJson(`/mail/${acc}/messages/${mid}/flags`, flags).then((r) => r.message as MailMessage)

export const attachmentUrl = (acc: number, mid: number, part: string) =>
  `/api/mail/${acc}/messages/${mid}/attachments/${part}`

export const send = (acc: number, payload: SendPayload) =>
  apiJson(`/mail/${acc}/send`, payload).then((r) => r.sent)
