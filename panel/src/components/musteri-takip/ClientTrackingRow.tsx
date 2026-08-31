// Client Tracking table row + expand panel.
//
// EXPAND PATTERN: a second <TableRow colSpan> — shadcn `collapsible` is
// DELIBERATELY NOT used. base-ui Collapsible renders a <div>; a <div> can't sit
// between <tbody> and <tr> (the browser foster-parents it out, breaking layout).
// The trigger uses the chevron pattern already proven on the panel (SharingBoardPage).
import { ChevronDown, ExternalLink, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { TableCell, TableRow } from "@/components/ui/table"
import { useI18n } from "@/lib/i18n"
import {
  ENTRY_STATUS_DOT, ENTRY_STATUS_TONE,
  fmtDay, itemIcon, stalenessTone, useAddActivityNote,
  useClientTrackingDetail, useDeleteActivityNote, useEntryStatusLabels, useRoleSlotLabels,
  type TrackingItem, type TrackingRow,
  itemDisplayName,
} from "@/lib/musteri-takip"
import { cn } from "@/lib/utils"

export const TRACKING_COLS = 8

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

/** Opportunity badge: how many items are still sellable. This number is the page's business value. */
function OpportunityBadge({ count }: { count: number }) {
  const tone = count === 0
    ? "bg-muted text-muted-foreground"
    : count <= 3
      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
      : "bg-primary/10 text-primary"
  return (
    <span className={cn("inline-flex min-w-6 justify-center rounded-full px-2 py-0.5 text-xs font-semibold", tone)}>
      {count}
    </span>
  )
}

/** Coverage strip — this page's signature element: all of a client's items at a glance. */
function CoverageStrip({ items, row, statusLabels }: {
  items: TrackingItem[]
  row: TrackingRow
  statusLabels: Record<string, string>
}) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-center gap-1">
      {items.filter((i) => i.active).map((item) => {
        const status = row.entries[String(item.id)]?.status ?? "yok"
        return (
          <span
            key={item.id}
            className={cn("h-2.5 w-2.5 rounded-full", ENTRY_STATUS_DOT[status])}
            title={`${itemDisplayName(item, t)} · ${statusLabels[status]}`}
          />
        )
      })}
    </div>
  )
}

interface Props {
  row: TrackingRow
  items: TrackingItem[]
  expanded: boolean
  onToggle: () => void
  onEditEntry: (item: TrackingItem) => void
}

export function ClientTrackingRow({ row, items, expanded, onToggle, onEditEntry }: Props) {
  const { t, lang } = useI18n()
  const ENTRY_STATUS_LABELS = useEntryStatusLabels()
  const ROLE_SLOT_LABELS = useRoleSlotLabels()
  const detail = useClientTrackingDetail(expanded ? row.client_id : null)
  const addNote = useAddActivityNote()
  const delNote = useDeleteActivityNote()
  const [noteText, setNoteText] = useState("")
  const [noteDate, setNoteDate] = useState(todayStr())

  const { signals } = row
  const panelId = `takip-${row.client_id}`

  async function submitNote() {
    if (!noteText.trim()) { toast.error(t("components.clientTracking.clientTrackingRow.noteRequired")); return }
    try {
      await addNote.mutateAsync({ clientId: row.client_id, text: noteText.trim(), happened_on: noteDate })
      setNoteText("")
      toast.success(t("components.clientTracking.clientTrackingRow.added"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.clientTracking.clientTrackingRow.addFailed"))
    }
  }

  async function removeNote(id: number) {
    try {
      await delNote.mutateAsync(id)
      toast.success(t("components.clientTracking.clientTrackingRow.deleted"))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("components.clientTracking.clientTrackingRow.deleteFailed"))
    }
  }

  return (
    <>
      <TableRow className="cursor-pointer hover:bg-muted/40" onClick={onToggle}>
        <TableCell className="font-medium">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={panelId}
            onClick={(e) => { e.stopPropagation(); onToggle() }}
            className="mr-2 inline-flex align-middle text-muted-foreground hover:text-foreground"
          >
            <ChevronDown className={cn("h-4 w-4 transition-transform", !expanded && "-rotate-90")} />
          </button>
          {row.client_name}
        </TableCell>
        <TableCell className="hidden text-muted-foreground sm:table-cell">{row.sector || "—"}</TableCell>
        <TableCell><CoverageStrip items={items} row={row} statusLabels={ENTRY_STATUS_LABELS} /></TableCell>
        <TableCell className={cn("whitespace-nowrap text-sm", stalenessTone(signals.last_ad_date))}>
          {signals.last_ad_date ? fmtDay(signals.last_ad_date, lang) : t("components.clientTracking.clientTrackingRow.never")}
          {signals.ad_active && (
            <span className="ml-1 rounded bg-emerald-100 px-1 py-0.5 text-[10px] font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">
              {t("components.clientTracking.clientTrackingRow.active")}
            </span>
          )}
        </TableCell>
        <TableCell className="hidden whitespace-nowrap text-sm text-muted-foreground md:table-cell">
          {fmtDay(signals.last_shoot_date, lang)}
        </TableCell>
        <TableCell className="hidden lg:table-cell">
          <div className="flex gap-1">
            {signals.team.slice(0, 3).map((member) => (
              <span
                key={member.role_slot}
                title={`${ROLE_SLOT_LABELS[member.role_slot] ?? member.role_slot}: ${member.name ?? member.user_id}`}
                className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-muted text-[10px] font-medium"
              >
                {(member.name ?? "?").slice(0, 2).toLocaleUpperCase("tr")}
              </span>
            ))}
            {signals.team.length === 0 && <span className="text-sm text-muted-foreground">—</span>}
          </div>
        </TableCell>
        <TableCell><OpportunityBadge count={row.summary.opportunity} /></TableCell>
        <TableCell className="hidden max-w-56 xl:table-cell">
          {row.last_note ? (
            <div className={cn("truncate text-sm", stalenessTone(row.last_note.happened_on))}
              title={row.last_note.text}>
              <span className="text-xs text-muted-foreground">{fmtDay(row.last_note.happened_on, lang)} · </span>
              {row.last_note.text}
            </div>
          ) : (
            <span className="text-sm text-muted-foreground">—</span>
          )}
        </TableCell>
      </TableRow>

      {expanded && (
        <TableRow id={panelId} className="bg-muted/20 hover:bg-muted/20">
          <TableCell colSpan={TRACKING_COLS} className="p-4">
            <div className="space-y-4">
              {/* Item grid */}
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {items.filter((i) => i.active || row.entries[String(i.id)]).map((item) => {
                  const entry = row.entries[String(item.id)]
                  const status = entry?.status ?? "yok"
                  const Icon = itemIcon(item.icon)
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => onEditEntry(item)}
                      className="rounded-lg border bg-background p-2.5 text-left transition-colors hover:border-primary/50"
                    >
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="flex-1 truncate text-sm font-medium">{itemDisplayName(item, t)}</span>
                        <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", ENTRY_STATUS_TONE[status])}>
                          {ENTRY_STATUS_LABELS[status]}
                        </span>
                      </div>
                      {(entry?.status_date || entry?.note || entry?.url) && (
                        <div className="mt-1.5 space-y-0.5 pl-6">
                          {entry.status_date && (
                            <p className="text-xs text-muted-foreground">{fmtDay(entry.status_date, lang)}</p>
                          )}
                          {entry.note && <p className="text-xs text-muted-foreground">{entry.note}</p>}
                          {entry.url && (
                            <span className="inline-flex items-center gap-1 text-xs text-primary">
                              <ExternalLink className="h-3 w-3" /> {entry.url.replace(/^https?:\/\//, "")}
                            </span>
                          )}
                        </div>
                      )}
                      {!item.active && (
                        <p className="mt-1 pl-6 text-[11px] text-muted-foreground">{t("components.clientTracking.clientTrackingRow.archivedItem")}</p>
                      )}
                    </button>
                  )
                })}
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                {/* Automatic signals */}
                <div className="rounded-lg border bg-background p-3">
                  <h4 className="text-sm font-semibold">
                    {t("components.clientTracking.clientTrackingRow.autoInfo")}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">{t("components.clientTracking.clientTrackingRow.autoInfoHint")}</span>
                  </h4>
                  <dl className="mt-2 space-y-1.5 text-sm">
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">{t("components.clientTracking.clientTrackingRow.lastAd")}</dt>
                      <dd className={stalenessTone(signals.last_ad_date)}>
                        {signals.last_ad_date ? fmtDay(signals.last_ad_date, lang) : t("components.clientTracking.clientTrackingRow.neverAdvertised")}
                        {signals.ad_count > 0 && (
                          <span className="ml-1 text-xs text-muted-foreground">{t("components.clientTracking.clientTrackingRow.campaignCount", { count: signals.ad_count })}</span>
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">{t("components.clientTracking.clientTrackingRow.lastShoot")}</dt>
                      <dd>{fmtDay(signals.last_shoot_date, lang)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">{t("components.clientTracking.clientTrackingRow.nextShoot")}</dt>
                      <dd>{fmtDay(signals.next_shoot_date, lang)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-muted-foreground">{t("components.clientTracking.clientTrackingRow.team")}</dt>
                      <dd className="text-right">
                        {signals.team.length === 0
                          ? "—"
                          : signals.team.map((member) =>
                              `${ROLE_SLOT_LABELS[member.role_slot] ?? member.role_slot}: ${member.name ?? member.user_id}`).join(" · ")}
                      </dd>
                    </div>
                  </dl>
                </div>

                {/* Activity log */}
                <div className="rounded-lg border bg-background p-3">
                  <h4 className="text-sm font-semibold">{t("components.clientTracking.clientTrackingRow.activityLog")}</h4>
                  <div className="mt-2 flex gap-2">
                    <Input type="date" className="w-36" value={noteDate}
                      onChange={(e) => setNoteDate(e.target.value)} />
                    <Input placeholder={t("components.clientTracking.clientTrackingRow.notePlaceholder")} value={noteText}
                      onChange={(e) => setNoteText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") submitNote() }} />
                    <Button size="icon" onClick={submitNote} disabled={addNote.isPending} aria-label={t("components.clientTracking.clientTrackingRow.addNoteAria")}>
                      <Plus className="h-4 w-4" />
                    </Button>
                  </div>
                  <div className="mt-2 max-h-56 space-y-1 overflow-y-auto">
                    {detail.isLoading && <Skeleton className="h-5 w-full" />}
                    {detail.data?.notes.length === 0 && (
                      <p className="py-2 text-sm text-muted-foreground">{t("components.clientTracking.clientTrackingRow.noEntriesYet")}</p>
                    )}
                    {detail.data?.notes.map((n) => (
                      <div key={n.id} className="flex items-start gap-2 rounded-md px-1 py-1 hover:bg-muted/50">
                        <span className="w-20 shrink-0 text-xs text-muted-foreground">{fmtDay(n.happened_on, lang)}</span>
                        <span className="flex-1 text-sm">{n.text}</span>
                        {n.author_name && (
                          <span className="hidden text-xs text-muted-foreground sm:inline">{n.author_name}</span>
                        )}
                        <button type="button" onClick={() => removeNote(n.id)}
                          className="text-muted-foreground hover:text-destructive" aria-label={t("components.clientTracking.clientTrackingRow.deleteNoteAria")}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}
