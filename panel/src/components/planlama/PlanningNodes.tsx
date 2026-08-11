// React Flow custom nodes: card · note · region · image.
//
// The `nodrag` CLASS IS CRITICAL: RF won't start a drag on an element carrying
// this class. It's the exact equivalent of the old canvas's `data-noload` marker —
// buttons, checkboxes, the palette, and inline-edit fields must all carry this
// class, otherwise clicking them starts dragging the card instead.
import { memo } from "react"
import { Handle, NodeResizer, Position, type NodeProps } from "@xyflow/react"
import {
  Building2, Camera, Check, ExternalLink, ImageOff, Loader2, Lock, Megaphone, Palette,
  SlidersHorizontal, Trash2, User,
} from "lucide-react"

import { InlineText } from "@/components/planlama/InlineText"
import { useI18n } from "@/lib/i18n"
import { COLORS, dueTone, fmtDay, imageOf, planningImageUrl, type PlanningItem } from "@/lib/planlama"
import { cn } from "@/lib/utils"

/** Node.data — the item + behaviors passed down from the canvas component. */
export interface NodeData extends Record<string, unknown> {
  item: PlanningItem
  dim: boolean                                   // filter didn't match → dimmed
  locked: boolean                                // extra.locked → read-only
  readOnly: boolean
  onPatch: (key: string, patch: Partial<PlanningItem>) => void
  onRemove: (key: string) => void
  onOpenDetail: (item: PlanningItem) => void
  paletteFor: string | null
  setPaletteFor: (key: string | null) => void
  boardKey: string                               // image URL is per board
  autoEdit?: boolean                             // newly added → open the title immediately
  onAutoEditDone?: () => void
}

/** Position/size are EXCLUDED from comparison: React Flow applies moving/resizing
 *  through its own transform, and the node's CONTENT is unaffected by it. If we
 *  didn't exclude these, the whole card would re-render on every frame of a drag. */
const GEOMETRI = new Set(["x", "y", "width", "height"])

function ayniOge(a: PlanningItem, b: PlanningItem): boolean {
  if (a === b) return true
  const ak = Object.keys(a)
  if (ak.length !== Object.keys(b).length) return false
  for (const k of ak) {
    if (GEOMETRI.has(k)) continue
    if ((a as unknown as Record<string, unknown>)[k]
        !== (b as unknown as Record<string, unknown>)[k]) return false
  }
  return true
}

/** `memo` comparator — the shared rule for all nodes.
 *
 *  Callbacks (onPatch/onRemove/…) are DELIBERATELY NOT compared: their behavior
 *  is stable, but their identity changes every time `nodes` is rebuilt. If we
 *  compared them, memo would never hold. If a new behavior field is added and
 *  it affects rendering, it must be added HERE too. */
function nodeDataEsit(prev: NodeProps, next: NodeProps): boolean {
  if (prev.selected !== next.selected) return false
  const a = prev.data as NodeData
  const b = next.data as NodeData
  if (a.dim !== b.dim || a.locked !== b.locked || a.readOnly !== b.readOnly) return false
  if (a.boardKey !== b.boardKey) return false
  if (!!a.autoEdit !== !!b.autoEdit) return false
  // The palette only matters if it's open for THIS node; another node's palette
  // shouldn't cause us to re-render.
  if ((a.paletteFor === a.item.item_key) !== (b.paletteFor === b.item.item_key)) return false
  return ayniOge(a.item, b.item)
}

function ColorRow({ d }: { d: NodeData }) {
  if (d.paletteFor !== d.item.item_key) return null
  return (
    <div className="nodrag absolute right-0 bottom-7 z-10 flex flex-wrap gap-1 rounded-md border bg-popover p-1 shadow-md"
      style={{ width: 118 }}>
      {COLORS.map((c) => (
        <button key={c} type="button" title={c}
          onClick={(e) => { e.stopPropagation(); d.onPatch(d.item.item_key, { color: c }); d.setPaletteFor(null) }}
          className="h-5 w-5 rounded border" style={{ background: c }} />
      ))}
    </div>
  )
}

function IconBtn({ title, onClick, children, danger }: {
  title: string; onClick: (e: React.MouseEvent) => void
  children: React.ReactNode; danger?: boolean
}) {
  return (
    <button type="button" title={title} onClick={(e) => { e.stopPropagation(); onClick(e) }}
      className={cn("nodrag text-slate-500", danger ? "hover:text-destructive" : "hover:text-slate-900")}>
      {children}
    </button>
  )
}

/** Connection handles — on all four edges.
 *
 *  2026-07-27 FIX, the four reasons arrows weren't working:
 *   1. All handles are now `type="source"` with the canvas in `ConnectionMode.Loose`
 *      — previously left/top were targets and right/bottom were sources, and the
 *      default Strict mode only accepted source→target; every other combination
 *      failed SILENTLY.
 *   2. `opacity-0` + 8px = an invisible, unhittable target. Now it's a persistent
 *      faint ring that becomes prominent on node hover.
 *   3. The click area is LARGER than the visible circle (20px via `::after`) — RF
 *      listens on the handle's own box, so grabbing it stays easy even if the visual stays small.
 *   4. `RegionNode` had NO handle at all → regions couldn't be connected.
 */
function Handles({ locked }: { locked?: boolean }) {
  if (locked) return null            // locked item can't be connected
  const base = "!h-3 !w-3 !border-2 !border-slate-400 !bg-background !opacity-40 "
             + "transition-all group-hover:!opacity-100 group-hover:!border-primary "
             + "hover:!scale-150 hover:!bg-primary"
  return (
    <>
      <Handle type="source" id="l" position={Position.Left} className={base} />
      <Handle type="source" id="r" position={Position.Right} className={base} />
      <Handle type="source" id="t" position={Position.Top} className={base} />
      <Handle type="source" id="b" position={Position.Bottom} className={base} />
    </>
  )
}

/** Lock badge — top-left on a locked item. */
function LockBadge() {
  const { t } = useI18n()
  return (
    <span title={t("components.planlama.planningNodes.lockedHint")}
      className="absolute top-1 left-1 z-10 rounded bg-slate-700/80 p-0.5 text-white">
      <Lock className="h-2.5 w-2.5" />
    </span>
  )
}

/** Domain link badge — on the card face, links to the related panel page. */
function LinkBadge({ icon, text, href }: { icon: React.ReactNode; text: string; href?: string }) {
  const inner = (
    <span className="inline-flex max-w-full items-center gap-0.5 truncate rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] text-slate-700">
      {icon}<span className="truncate">{text}</span>
    </span>
  )
  if (!href) return inner
  return (
    <a href={href} className="nodrag max-w-full hover:underline"
      onClick={(e) => e.stopPropagation()}>{inner}</a>
  )
}

export const CardNode = memo(function CardNode({ data, selected }: NodeProps) {
  const { t } = useI18n()
  const d = data as NodeData
  const it = d.item
  const done = it.status === "done"
  return (
    <div className={cn("group relative h-full w-full overflow-hidden rounded-lg border border-slate-300/70 shadow-sm transition-opacity",
                       selected && "ring-2 ring-primary", done && "opacity-60", d.dim && "opacity-25")}
      style={{ background: it.color || "#e0e7ff" }}>
      <NodeResizer minWidth={120} minHeight={72} isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}

      <div className="flex items-start gap-1 px-2 pt-1.5">
        <button type="button" role="checkbox" aria-checked={done}
          title={done ? t("components.planlama.planningNodes.markOpen") : t("components.planlama.planningNodes.markDone")}
          onClick={(e) => { e.stopPropagation(); d.onPatch(it.item_key, { status: done ? "open" : "done" }) }}
          className={cn("nodrag mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                        done ? "border-emerald-600 bg-emerald-600 text-white" : "border-slate-400 bg-white/70")}>
          {done && <Check className="h-3 w-3" />}
        </button>
        <InlineText value={it.title} placeholder={t("components.planlama.planningNodes.untitled")} disabled={d.readOnly}
          className={cn("nodrag flex-1 text-[13px] leading-snug font-medium text-slate-800", done && "line-through")}
          onCommit={(v) => d.onPatch(it.item_key, { title: v })} autoEdit={d.autoEdit} onAutoEditDone={d.onAutoEditDone} />
      </div>

      {(it.label || it.due_date || it.assignee_name || it.client_name
        || it.shoot_title || it.campaign_title) && (
        <div className="flex flex-wrap items-center gap-1 px-2 pt-1">
          {it.label && (
            <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] font-medium text-slate-700">{it.label}</span>
          )}
          {it.due_date && (
            <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", dueTone(it.due_date, it.status))}>
              {fmtDay(it.due_date)}
            </span>
          )}
          {it.assignee_name && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] text-slate-700">
              <User className="h-2.5 w-2.5" />{it.assignee_name}
            </span>
          )}
          {it.client_name && (
            <LinkBadge icon={<Building2 className="h-2.5 w-2.5 shrink-0" />} text={it.client_name}
              href={it.client_id ? `/panel/musteriler?id=${it.client_id}` : undefined} />
          )}
          {it.shoot_title && (
            <LinkBadge icon={<Camera className="h-2.5 w-2.5 shrink-0" />} text={it.shoot_title} href="/panel/cekim-plani" />
          )}
          {it.campaign_title && (
            <LinkBadge icon={<Megaphone className="h-2.5 w-2.5 shrink-0" />} text={it.campaign_title} href="/panel/reklam" />
          )}
        </div>
      )}

      {it.text && <div className="line-clamp-2 px-2 pt-1 text-[11px] text-slate-600">{it.text}</div>}

      {!d.readOnly && (
        <div className="absolute right-1 bottom-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {it.link && (
            <a className="nodrag text-slate-500 hover:text-primary" href={it.link} target="_blank"
              rel="noreferrer" title={it.link} onClick={(e) => e.stopPropagation()}>
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          <IconBtn title={t("components.planlama.planningNodes.details")} onClick={() => d.onOpenDetail(it)}>
            <SlidersHorizontal className="h-3.5 w-3.5" />
          </IconBtn>
          <IconBtn title={t("components.planlama.planningNodes.color")} onClick={() => d.setPaletteFor(d.paletteFor === it.item_key ? null : it.item_key)}>
            <Palette className="h-3.5 w-3.5" />
          </IconBtn>
          <IconBtn title={t("components.planlama.planningNodes.delete")} danger onClick={() => d.onRemove(it.item_key)}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconBtn>
        </div>
      )}
      <ColorRow d={d} />
    </div>
  )
}, nodeDataEsit)

export const NoteNode = memo(function NoteNode({ data, selected }: NodeProps) {
  const { t } = useI18n()
  const d = data as NodeData
  const it = d.item
  return (
    <div className={cn("group relative h-full w-full rounded-md p-2 shadow-sm transition-opacity",
                       selected && "ring-2 ring-primary", d.dim && "opacity-25")}
      style={{ background: it.color || "#fef9c3" }}>
      <NodeResizer minWidth={120} minHeight={80} isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}
      <InlineText value={it.text} placeholder={t("components.planlama.planningNodes.notePlaceholder")} multiline disabled={d.readOnly}
        className="nodrag h-[calc(100%-1.5rem)] overflow-hidden text-[13px] text-slate-700"
        onCommit={(v) => d.onPatch(it.item_key, { text: v })} autoEdit={d.autoEdit} onAutoEditDone={d.onAutoEditDone} />
      {!d.readOnly && (
        <div className="absolute right-1 bottom-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <IconBtn title={t("components.planlama.planningNodes.color")} onClick={() => d.setPaletteFor(d.paletteFor === it.item_key ? null : it.item_key)}>
            <Palette className="h-3.5 w-3.5" />
          </IconBtn>
          <IconBtn title={t("components.planlama.planningNodes.delete")} danger onClick={() => d.onRemove(it.item_key)}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconBtn>
        </div>
      )}
      <ColorRow d={d} />
    </div>
  )
}, nodeDataEsit)

export const RegionNode = memo(function RegionNode({ data, selected }: NodeProps) {
  const { t } = useI18n()
  const d = data as NodeData
  const it = d.item
  return (
    <div className={cn("group relative h-full w-full rounded-xl border-2 border-dashed transition-opacity",
                       selected && "ring-2 ring-primary", d.dim && "opacity-25")}
      style={{ background: (it.color || "#f1f5f9") + "88", borderColor: "rgba(100,116,139,.35)" }}>
      <NodeResizer minWidth={200} minHeight={160} isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}
      <div className="flex items-center gap-1 px-2 py-1">
        <InlineText value={it.title} placeholder={t("components.planlama.planningNodes.regionPlaceholder")} disabled={d.readOnly}
          className="nodrag flex-1 text-xs font-semibold text-slate-600"
          onCommit={(v) => d.onPatch(it.item_key, { title: v })} autoEdit={d.autoEdit} onAutoEditDone={d.onAutoEditDone} />
        {!d.readOnly && (
          <>
            {/* The color button existed on cards and notes but NOT on regions — the
                only way to change a region's color used to be the multi-select bar. */}
            <IconBtn title={t("components.planlama.planningNodes.color")}
              onClick={() => d.setPaletteFor(d.paletteFor === it.item_key ? null : it.item_key)}>
              <Palette className="h-3.5 w-3.5" />
            </IconBtn>
            <IconBtn title={t("components.planlama.planningNodes.deleteRegion")} danger onClick={() => d.onRemove(it.item_key)}>
              <Trash2 className="h-3.5 w-3.5" />
            </IconBtn>
          </>
        )}
      </div>
      <ColorRow d={d} />
    </div>
  )
}, nodeDataEsit)

/** Image pasted/dragged onto the board.
 *
 *  The file lives on the server (`planning_images`); the item points to it via
 *  `extra.image`. If `extra.image` doesn't exist yet, the upload is still in
 *  progress — the item is rendered immediately so the user can see where it landed,
 *  and the image takes its place once it arrives (optimistic placement).
 *  Resizing uses `keepAspectRatio`: we don't want to distort the photo's proportions. */
export const ImageNode = memo(function ImageNode({ data, selected }: NodeProps) {
  const { t } = useI18n()
  const d = data as NodeData
  const it = d.item
  const img = imageOf(it)
  const failed = it.extra?.image_error === true
  return (
    <div className={cn("group relative h-full w-full overflow-hidden rounded-lg border bg-background shadow-sm transition-opacity",
                       selected && "ring-2 ring-primary", d.dim && "opacity-25")}>
      <NodeResizer minWidth={60} minHeight={60} keepAspectRatio
        isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}

      {img ? (
        <img src={planningImageUrl(d.boardKey, img.name)} alt={it.title || t("components.planlama.planningNodes.boardImageAlt")}
          draggable={false}
          className="h-full w-full object-contain select-none" />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 bg-muted/40 text-muted-foreground">
          {failed ? <ImageOff className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
          <span className="text-[11px]">{failed ? t("components.planlama.planningNodes.uploadFailed") : t("components.planlama.planningNodes.uploading")}</span>
        </div>
      )}

      {/* The title only takes up space when filled or on hover — it shouldn't obscure the image. */}
      {(it.title || !d.readOnly) && (
        <div className={cn("absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent px-1.5 pt-4 pb-1",
                           !it.title && "opacity-0 transition-opacity group-hover:opacity-100")}>
          <InlineText value={it.title} placeholder={t("components.planlama.planningNodes.titlePlaceholder")} disabled={d.readOnly}
            className="nodrag text-[11px] font-medium text-white drop-shadow"
            onCommit={(v) => d.onPatch(it.item_key, { title: v })} />
        </div>
      )}

      {!d.readOnly && (
        <div className="absolute top-1 right-1 flex items-center gap-1 rounded bg-background/80 px-1 opacity-0 transition-opacity group-hover:opacity-100">
          {img && (
            <IconBtn title={t("components.planlama.planningNodes.openInNewTab")}
              onClick={() => window.open(planningImageUrl(d.boardKey, img.name), "_blank", "noopener")}>
              <ExternalLink className="h-3.5 w-3.5" />
            </IconBtn>
          )}
          <IconBtn title={t("components.planlama.planningNodes.delete")} danger onClick={() => d.onRemove(it.item_key)}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconBtn>
        </div>
      )}
    </div>
  )
}, nodeDataEsit)
