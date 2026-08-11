// Card details: base fields + DOMAIN LINKS (client / shoot / campaign).
//
// Two behavior changes (2026-07-26):
//  1. Only CHANGED fields are sent. It used to send all 7 fields on every save;
//     that created an unnecessary conflict surface — two people editing the same
//     card would land in `conflicts[]` over fields neither of them touched.
//     `InlineText` already followed the "commit only if it actually changed"
//     discipline; this was brought in line with that.
//  2. Link pickers are fed from `useLinkables`. A section the user lacks permission
//     for comes back as an EMPTY array from the server → that picker is never
//     rendered. So a designer never sees the campaign picker; the role difference
//     is correct without writing `if (role===…)` on the frontend.
import { useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useI18n } from "@/lib/i18n"
import { itemStatusLabels, useLinkables, type PlanningItem, type PlanningStatus } from "@/lib/planlama"

const NONE = "__none__"

interface Props {
  item: PlanningItem
  onSave: (patch: Partial<PlanningItem>) => void
  onClose: () => void
}

/** Form state — everything is a string; converted to its proper type on save and
 *  compared against the baseline so only the differences are sent. */
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
  const { t } = useI18n()
  const statusLabels = useMemo(() => itemStatusLabels(t), [t])
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
        <DialogHeader><DialogTitle>{t("components.planlama.cardDetailDialog.title")}</DialogTitle></DialogHeader>

        <div className="space-y-3">
          <Field label={t("components.planlama.cardDetailDialog.fieldTitle")}>
            <Input value={f.title} onChange={(e) => set("title", e.target.value)} />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("components.planlama.cardDetailDialog.fieldStatus")}>
              <select className={SELECT} value={f.status}
                onChange={(e) => set("status", e.target.value as PlanningStatus)}>
                {Object.entries(statusLabels).map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
            </Field>
            <Field label={t("components.planlama.cardDetailDialog.fieldDueDate")}>
              <Input type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("components.planlama.cardDetailDialog.fieldAssignee")}>
              <select className={SELECT} value={f.assignee_sub}
                onChange={(e) => set("assignee_sub", e.target.value)}>
                <option value={NONE}>—</option>
                {(L?.users ?? []).map((u) => (
                  <option key={u.sub} value={u.sub}>{u.name}</option>
                ))}
              </select>
            </Field>
            <Field label={t("components.planlama.cardDetailDialog.fieldLabel")}>
              <Input value={f.label} placeholder={t("components.planlama.cardDetailDialog.labelPlaceholder")}
                onChange={(e) => set("label", e.target.value)} />
            </Field>
          </div>

          <div className="rounded-lg border p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-muted-foreground">{t("components.planlama.cardDetailDialog.linksHeading")}</span>
              <Input className="h-7 max-w-[11rem] text-xs" placeholder={t("components.planlama.cardDetailDialog.searchPlaceholder")}
                value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="space-y-3">
              <Field label={t("components.planlama.cardDetailDialog.fieldClient")}>
                <select className={SELECT} value={f.client_id}
                  onChange={(e) => set("client_id", e.target.value)}>
                  <option value={NONE}>—</option>
                  {(L?.clients ?? []).map((c) => (
                    <option key={c.id} value={String(c.id)}>{c.name}</option>
                  ))}
                </select>
              </Field>

              {/* If the section comes back empty (role lacks permission OR there are no records), the picker isn't rendered. */}
              {!!L?.shoot_tasks?.length && (
                <Field label={t("components.planlama.cardDetailDialog.fieldShootTask")}>
                  <select className={SELECT} value={f.shoot_task_id}
                    onChange={(e) => set("shoot_task_id", e.target.value)}>
                    <option value={NONE}>—</option>
                    {L.shoot_tasks.map((task) => (
                      <option key={task.id} value={String(task.id)}>
                        {task.title}{task.client_name ? ` · ${task.client_name}` : ""}
                        {task.scheduled_date ? ` · ${task.scheduled_date}` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              )}

              {!!L?.ad_campaigns?.length && (
                <Field label={t("components.planlama.cardDetailDialog.fieldAdCampaign")}>
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

          <Field label={t("components.planlama.cardDetailDialog.fieldLink")}>
            <Input value={f.link} placeholder="https://…"
              onChange={(e) => set("link", e.target.value)} />
          </Field>
          <Field label={t("components.planlama.cardDetailDialog.fieldDescription")}>
            <Textarea rows={3} value={f.text} onChange={(e) => set("text", e.target.value)} />
          </Field>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{t("components.planlama.cardDetailDialog.cancel")}</Button>
          <Button onClick={save}>{t("components.planlama.cardDetailDialog.save")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
