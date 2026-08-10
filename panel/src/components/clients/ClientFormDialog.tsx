// Müşteri ekle/düzenle diyaloğu. Yeni kayıtta tüm alanlar; düzenlemede yalnız
// değişen alanlar PATCH'lenir (form'un tümü gönderilir, backend partial uygular).
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import type { ClientDetail } from "@/lib/types"
import {
  ROLE_SLOTS, useCreateClient, useUpdateClient, useUsers, type ClientForm,
} from "@/lib/clients"
import { ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"

const UNASSIGNED = "__none__"

function numOrNull(v: string): number | null {
  const n = parseInt(v, 10)
  return Number.isFinite(n) ? n : null
}

function toForm(c: ClientDetail | null): ClientForm {
  return {
    name: c?.name ?? "",
    sector: c?.sector ?? "",
    client_email: c?.client_email ?? "",
    instagram_url: c?.instagram_url ?? "",
    google_drive_url: c?.google_drive_url ?? "",
    notes: c?.notes ?? "",
    contract: {
      weekly_content_count: c?.contract?.weekly_content_count ?? null,
      post_count: c?.contract?.post_count ?? null,
      story_count: c?.contract?.story_count ?? null,
      description: c?.contract?.description ?? "",
      vat_rate: c?.contract?.vat_rate ?? null,
      video_shooting_enabled: c?.contract?.video_shooting_enabled ?? false,
      weekly_video_count: c?.contract?.weekly_video_count ?? null,
      photo_shooting_enabled: c?.contract?.photo_shooting_enabled ?? false,
      weekly_photo_count: c?.contract?.weekly_photo_count ?? null,
      drone_usage: c?.contract?.drone_usage ?? false,
    },
    team_assignments: { ...(c?.team_assignments ?? {}) },
  }
}

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  client: ClientDetail | null // null => yeni
}

export function ClientFormDialog({ open, onOpenChange, client }: Props) {
  const editing = client != null
  const [form, setForm] = useState<ClientForm>(() => toForm(client))
  const { data: users } = useUsers()
  const create = useCreateClient()
  const update = useUpdateClient(client?.id ?? 0)
  const busy = create.isPending || update.isPending

  // Diyalog her açıldığında/farklı müşteriye geçildiğinde formu tazele.
  useEffect(() => {
    if (open) setForm(toForm(client))
  }, [open, client])

  const roleOptions = useMemo(
    () => (users ?? []).map((u) => ({ value: u.sub, label: u.name || u.email })),
    [users],
  )

  function setField<K extends keyof ClientForm>(k: K, v: ClientForm[K]) {
    setForm((f) => ({ ...f, [k]: v }))
  }
  function setContract(patch: Partial<NonNullable<ClientForm["contract"]>>) {
    setForm((f) => ({ ...f, contract: { ...f.contract, ...patch } }))
  }
  function setTeam(slot: string, userId: string) {
    setForm((f) => {
      const t = { ...(f.team_assignments ?? {}) }
      if (userId === UNASSIGNED) delete t[slot]
      else t[slot] = userId
      return { ...f, team_assignments: t }
    })
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name.trim()) {
      toast.error("Müşteri adı zorunlu")
      return
    }
    try {
      if (editing) await update.mutateAsync(form)
      else await create.mutateAsync(form)
      toast.success(editing ? "Müşteri güncellendi" : "Müşteri eklendi")
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "İşlem başarısız")
    }
  }

  const c = form.contract ?? {}

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? "Müşteriyi Düzenle" : "Yeni Müşteri"}</DialogTitle>
          <DialogDescription>
            Temel bilgiler, anlaşma ve ekip ataması. Değişiklikler kaydedilince yansır.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-5">
          {/* Genel */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="name">Müşteri Adı *</Label>
              <Input id="name" value={form.name}
                onChange={(e) => setField("name", e.target.value)} autoFocus />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sector">Sektör</Label>
              <Input id="sector" value={form.sector ?? ""}
                onChange={(e) => setField("sector", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="email">E-posta</Label>
              <Input id="email" type="email" value={form.client_email ?? ""}
                onChange={(e) => setField("client_email", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ig">Instagram</Label>
              <Input id="ig" value={form.instagram_url ?? ""}
                onChange={(e) => setField("instagram_url", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="drive">Drive Klasörü</Label>
              <Input id="drive" value={form.google_drive_url ?? ""}
                onChange={(e) => setField("google_drive_url", e.target.value)} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="notes">Notlar</Label>
              <Textarea id="notes" rows={2} value={form.notes ?? ""}
                onChange={(e) => setField("notes", e.target.value)} />
            </div>
          </div>

          {/* Anlaşma */}
          <div className="space-y-3 rounded-lg border p-3">
            <div className="text-sm font-medium">Anlaşma & Çekim</div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Haftalık İçerik</Label>
                <Input type="number" value={c.weekly_content_count ?? ""}
                  onChange={(e) => setContract({ weekly_content_count: numOrNull(e.target.value) })} />
              </div>
              <div className="space-y-1.5">
                <Label>Post</Label>
                <Input type="number" value={c.post_count ?? ""}
                  onChange={(e) => setContract({ post_count: numOrNull(e.target.value) })} />
              </div>
              <div className="space-y-1.5">
                <Label>Story</Label>
                <Input type="number" value={c.story_count ?? ""}
                  onChange={(e) => setContract({ story_count: numOrNull(e.target.value) })} />
              </div>
              <div className="space-y-1.5">
                <Label>KDV %</Label>
                <Input type="number" value={c.vat_rate ?? ""}
                  onChange={(e) => setContract({ vat_rate: numOrNull(e.target.value) })} />
              </div>
            </div>
            <div className="flex flex-wrap gap-6 pt-1">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={!!c.video_shooting_enabled}
                  onCheckedChange={(v) => setContract({ video_shooting_enabled: v })} />
                Video çekimi
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={!!c.photo_shooting_enabled}
                  onCheckedChange={(v) => setContract({ photo_shooting_enabled: v })} />
                Foto çekimi
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={!!c.drone_usage}
                  onCheckedChange={(v) => setContract({ drone_usage: v })} />
                Drone
              </label>
            </div>
          </div>

          {/* Ekip */}
          <div className="space-y-3 rounded-lg border p-3">
            <div className="text-sm font-medium">Ekip Ataması</div>
            <div className="grid gap-3 sm:grid-cols-2">
              {ROLE_SLOTS.map((slot) => (
                <div key={slot.key} className="space-y-1.5">
                  <Label>{slot.label}</Label>
                  <Select
                    value={form.team_assignments?.[slot.key] ?? UNASSIGNED}
                    onValueChange={(v) => v && setTeam(slot.key, v)}
                  >
                    <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={UNASSIGNED}>— Atanmadı —</SelectItem>
                      {roleOptions.map((o) => (
                        <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              İptal
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? "Kaydediliyor…" : editing ? "Kaydet" : "Ekle"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
