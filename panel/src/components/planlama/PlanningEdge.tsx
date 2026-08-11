// Custom edge (arrow) for the planning canvas.
//
// INTERACTION PARITY (2026-08-09) — this was the arrow's gap compared to
// cards/notes: cards show buttons on hover, have a right-click menu, and edit
// on double-click; the arrow had NONE of that. The only way to delete it was
// the Delete key or a tiny icon that only appeared WHILE SELECTED. Now:
//   * hovering shows the toolbar (no need to select first)
//   * double-click → edit the label
//   * right-click → menu (label, reverse direction, delete) — opened by `PlanningFlow`
//   * color changes when selected, not just a thicker line
//
// `nodrag nopan`: without these two classes, clicking the strip would start panning the canvas.
import { memo } from "react"
import {
  BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps,
} from "@xyflow/react"
import { ArrowLeftRight, Trash2 } from "lucide-react"

import { InlineText } from "@/components/planlama/InlineText"
import { useI18n } from "@/lib/i18n"
import type { PlanningItem } from "@/lib/planlama"
import { cn } from "@/lib/utils"

export interface EdgeData extends Record<string, unknown> {
  item: PlanningItem
  readOnly: boolean
  hovered: boolean
  /** Turns ON label editing — triggered by double-click, single-use. */
  autoEdit: boolean
  onPatch: (key: string, patch: Partial<PlanningItem>) => void
  onRemove: (key: string) => void
  onReverse: (key: string) => void
  onAutoEditDone: () => void
}

/** Color of the selected/hovered arrow — the base style comes from
 *  `DEFAULT_EDGE_OPTIONS`, only the highlight is overridden here. */
const VURGU = "#0ea5e9"

export const PlanningEdge = memo(function PlanningEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  markerEnd, style, data, selected,
}: EdgeProps) {
  const { t } = useI18n()
  const d = data as EdgeData | undefined
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })
  const vurgulu = !!selected || !!d?.hovered
  const etiket = d?.item.label
  // The strip: always shown for an arrow with a label, only on hover/selection
  // for others. Otherwise, dozens of "label…" placeholders would be visual noise on a busy board.
  const goster = !!etiket || (vurgulu && !d?.readOnly)

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
        style={{
          ...style,
          stroke: vurgulu ? VURGU : (style?.stroke ?? "#64748b"),
          strokeWidth: vurgulu ? 3 : (style?.strokeWidth ?? 2),
        }} />
      {goster && d && (
        <EdgeLabelRenderer>
          <div
            className={cn(
              "nodrag nopan absolute flex items-center gap-0.5 rounded border bg-popover/95 px-1 py-0.5 shadow-sm backdrop-blur transition-colors",
              vurgulu && "border-primary/60")}
            style={{
              // EdgeLabelRenderer draws its content OUTSIDE the canvas transform;
              // we have to position it ourselves.
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "all",
            }}
          >
            <InlineText
              value={d.item.label}
              placeholder={t("components.planlama.planningEdge.labelPlaceholder")}
              disabled={d.readOnly}
              className="min-w-[3rem] text-[10px] text-slate-700"
              onCommit={(v) => d.onPatch(d.item.item_key, { label: v })}
              autoEdit={d.autoEdit}
              onAutoEditDone={d.onAutoEditDone}
            />
            {!d.readOnly && vurgulu && (
              <>
                <button type="button" title={t("components.planlama.planningEdge.reverseDirection")}
                  aria-label={t("components.planlama.planningEdge.reverseDirection")}
                  onClick={(e) => { e.stopPropagation(); d.onReverse(d.item.item_key) }}
                  className="text-slate-500 hover:text-primary">
                  <ArrowLeftRight className="h-3 w-3" />
                </button>
                <button type="button" title={t("components.planlama.planningEdge.deleteArrow")}
                  aria-label={t("components.planlama.planningEdge.deleteArrow")}
                  onClick={(e) => { e.stopPropagation(); d.onRemove(d.item.item_key) }}
                  className="text-slate-500 hover:text-destructive">
                  <Trash2 className="h-3 w-3" />
                </button>
              </>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
})
