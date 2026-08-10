// Planlama tuvalinin özel kenarı (ok).
//
// ETKİLEŞİM BÜTÜNLÜĞÜ (2026-08-09) — okun kart/nota göre eksik kalan tarafı buydu:
// karta hover'da düğmeler çıkıyor, sağ tık menüsü var, çift tıkla düzenleniyor;
// okta bunların HİÇBİRİ yoktu. Silmenin tek yolu Delete tuşu ya da yalnız
// SEÇİLİYKEN beliren küçük bir ikondu. Artık:
//   * üzerine gelince araç şeridi çıkar (seçmeye gerek yok)
//   * çift tık → etiketi düzenle
//   * sağ tık → menü (etiket, yön çevir, sil) — `PlanningFlow` açar
//   * seçiliyken renk değişir, sadece kalınlaşmakla kalmaz
//
// `nodrag nopan`: bu iki sınıf olmadan şeride tıklamak tuvali kaydırmaya başlar.
import { memo } from "react"
import {
  BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps,
} from "@xyflow/react"
import { ArrowLeftRight, Trash2 } from "lucide-react"

import { InlineText } from "@/components/planlama/InlineText"
import type { PlanningItem } from "@/lib/planlama"
import { cn } from "@/lib/utils"

export interface EdgeData extends Record<string, unknown> {
  item: PlanningItem
  readOnly: boolean
  hovered: boolean
  /** Etiketi düzenlemeye AÇ — çift tıkla tetiklenir, tek kullanımlık. */
  autoEdit: boolean
  onPatch: (key: string, patch: Partial<PlanningItem>) => void
  onRemove: (key: string) => void
  onReverse: (key: string) => void
  onAutoEditDone: () => void
}

/** Seçili/hover okun rengi — gövde stili `DEFAULT_EDGE_OPTIONS`tan gelir, burada
 *  yalnız vurgulama ezilir. */
const VURGU = "#0ea5e9"

export const PlanningEdge = memo(function PlanningEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  markerEnd, style, data, selected,
}: EdgeProps) {
  const d = data as EdgeData | undefined
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })
  const vurgulu = !!selected || !!d?.hovered
  const etiket = d?.item.label
  // Şerit: etiketi olan okta hep, diğerlerinde yalnız hover/seçimde. Aksi halde
  // kalabalık bir panoda onlarca "etiket…" yer tutucusu görsel gürültü olurdu.
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
              // EdgeLabelRenderer içeriği tuval dönüşümünün DIŞINDA çizer; konumu
              // kendimiz taşımak zorundayız.
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "all",
            }}
          >
            <InlineText
              value={d.item.label}
              placeholder="etiket…"
              disabled={d.readOnly}
              className="min-w-[3rem] text-[10px] text-slate-700"
              onCommit={(v) => d.onPatch(d.item.item_key, { label: v })}
              autoEdit={d.autoEdit}
              onAutoEditDone={d.onAutoEditDone}
            />
            {!d.readOnly && vurgulu && (
              <>
                <button type="button" title="Yönü ters çevir" aria-label="Yönü ters çevir"
                  onClick={(e) => { e.stopPropagation(); d.onReverse(d.item.item_key) }}
                  className="text-slate-500 hover:text-primary">
                  <ArrowLeftRight className="h-3 w-3" />
                </button>
                <button type="button" title="Oku sil" aria-label="Oku sil"
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
