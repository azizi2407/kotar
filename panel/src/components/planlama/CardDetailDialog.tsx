// Kart ayrıntıları: temel alanlar + DOMAIN BAĞLARI (müşteri / çekim / kampanya).
//
// İki davranış değişikliği (2026-07-26):
//  1. Yalnız DEĞİŞEN alanlar gönderilir. Eskiden her kaydetmede 7 alan birden
//     gidiyordu; bu gereksiz çakışma yüzeyi üretiyordu — aynı kartı düzenleyen
//     iki kişi hiç dokunmadıkları alanlar yüzünden `conflicts[]`'a düşüyordu.
//     `InlineText` zaten "gerçekten değiştiyse commit" disiplinini uyguluyordu;
//     burası ona hizalandı.
//  2. Bağ seçicileri `useLinkables`'tan beslenir. Yetkisi olmayan bölüm sunucudan
//     BOŞ dizi gelir → o seçici hiç render edilmez. Yani tasarımcı kampanya
//     seçicisini görmez; rol farkı frontend'de `if (role===…)` yazmadan doğru olur.
import { useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ITEM_STATUS_LABELS, useLinkables, type PlanningItem, type PlanningStatus } from "@/lib/planlama"

const NONE = "__none__"

interface Props {
  item: PlanningItem
  onSave: (patch: Partial<PlanningItem>) => void
  onClose: () => void
}

/** Form durumu — hepsi string; kaydederken tipine çevrilir ve taban ile
 *  karşılaştırılıp yalnız farklar gönderilir. */
function snapshot(it: PlanningItem) {
  return {
    title: it.title ?? "",
    status: it.status as PlanningStatus,
    due_date: it.due_date ?? "",
    assignee_sub: it.assignee_sub ?? NONE,
    label: it.label ?? "",
    link: it.link ?? "",
    text: it.text ?? "",
    client_id: it.client_id ? String(it.client_id) : NONE,
    shoot_task_id: it.shoot_task_id ? String(it.shoot_task_id) : NONE,
    ad_campaign_id: it.ad_campaign_id ? String(it.ad_campaign_id) : NONE,
  }
}

const SELECT = "h-9 w-full rounded-md border bg-background px-2 text-sm"

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

export function CardDetailDialog({ item, onSave, onClose }: Props) {
  const base = useMemo(() => snapshot(item), [item])
  const [f, setF] = useState(base)
  const [q, setQ] = useState("")
  const L = useLinkables(q).data

  function set<K extends keyof typeof f>(k: K, v: (typeof f)[K]) {
    setF((prev) => ({ ...prev, [k]: v }))
  }

  function save() {
    const patch: Partial<PlanningItem> = {}
    const txt = (v: string) => (v.trim() ? v.trim() : null)
    const num = (v: string) => (v === NONE ? null : Number(v))

    if (f.title !== base.title) patch.title = txt(f.title)
    if (f.status !== base.status) patch.status = f.status
    if (f.due_date !== base.due_date) patch.due_date = txt(f.due_date)
    if (f.label !== base.label) patch.label = txt(f.label)
    if (f.link !== base.link) patch.link = txt(f.link)
    if (f.text !== base.text) patch.text = txt(f.text)
    if (f.assignee_sub !== base.assignee_sub) {
      patch.assignee_sub = f.assignee_sub === NONE ? null : f.assignee_sub
    }
    if (f.client_id !== base.client_id) patch.client_id = num(f.client_id)
    if (f.shoot_task_id !== base.shoot_task_id) patch.shoot_task_id = num(f.shoot_task_id)
    if (f.ad_campaign_id !== base.ad_campaign_id) patch.ad_campaign_id = num(f.ad_campaign_id)

    if (Object.keys(patch).length) onSave(patch)
    onClose()
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader><DialogTitle>Kart ayrıntıları</DialogTitle></DialogHeader>

        <div className="space-y-3">
          <Field label="Başlık">
            <Input value={f.title} onChange={(e) => set("title", e.target.value)} />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Durum">
              <select className={SELECT} value={f.status}
                onChange={(e) => set("status", e.target.value as PlanningStatus)}>
                {Object.entries(ITEM_STATUS_LABELS).map(([v, t]) => (
                  <option key={v} value={v}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Son tarih">
              <Input type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Sorumlu">
              <select className={SELECT} value={f.assignee_sub}
                onChange={(e) => set("assignee_sub", e.target.value)}>
                <option value={NONE}>—</option>
                {(L?.users ?? []).map((u) => (
                  <option key={u.sub} value={u.sub}>{u.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Etiket">
              <Input value={f.label} placeholder="örn. Reels"
                onChange={(e) => set("label", e.target.value)} />
            </Field>
          </div>

          <div className="rounded-lg border p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-muted-foreground">Bağlantılar</span>
              <Input className="h-7 max-w-[11rem] text-xs" placeholder="Listede ara…"
                value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="space-y-3">
              <Field label="Müşteri">
                <select className={SELECT} value={f.client_id}
                  onChange={(e) => set("client_id", e.target.value)}>
                  <option value={NONE}>—</option>
                  {(L?.clients ?? []).map((c) => (
                    <option key={c.id} value={String(c.id)}>{c.name}</option>
                  ))}
                </select>
              </Field>

              {/* Bölüm boş gelirse (rol yetmiyor VEYA kayıt yok) seçici render edilmez. */}
              {!!L?.shoot_tasks?.length && (
                <Field label="Çekim görevi">
                  <select className={SELECT} value={f.shoot_task_id}
                    onChange={(e) => set("shoot_task_id", e.target.value)}>
                    <option value={NONE}>—</option>
                    {L.shoot_tasks.map((t) => (
                      <option key={t.id} value={String(t.id)}>
                        {t.title}{t.client_name ? ` · ${t.client_name}` : ""}
                        {t.scheduled_date ? ` · ${t.scheduled_date}` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              )}

              {!!L?.ad_campaigns?.length && (
                <Field label="Reklam kampanyası">
                  <select className={SELECT} value={f.ad_campaign_id}
                    onChange={(e) => set("ad_campaign_id", e.target.value)}>
                    <option value={NONE}>—</option>
                    {L.ad_campaigns.map((c) => (
                      <option key={c.id} value={String(c.id)}>
                        {c.title}{c.client_name ? ` · ${c.client_name}` : ""} ({c.platform})
                      </option>
                    ))}
                  </select>
                </Field>
              )}
            </div>
          </div>

          <Field label="Bağlantı (link)">
            <Input value={f.link} placeholder="https://…"
              onChange={(e) => set("link", e.target.value)} />
          </Field>
          <Field label="Açıklama">
            <Textarea rows={3} value={f.text} onChange={(e) => set("text", e.target.value)} />
          </Field>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Vazgeç</Button>
          <Button onClick={save}>Kaydet</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
