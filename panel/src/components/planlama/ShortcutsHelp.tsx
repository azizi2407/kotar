// Shortcuts help — opened with `?`.
//
// WHY THIS EXISTS: the canvas has a dozen shortcuts like Ctrl+C/V/D/G/A,
// Space+drag, Delete, V/H, and NONE of them were shown on screen. Users could
// only discover them by accident; in practice they were never used.
import { Keyboard, X } from "lucide-react"

import { useI18n } from "@/lib/i18n"

const KISAYOL_ANAHTARLARI = [
  "doubleClick", "rightClick", "dragSelect", "spacePan", "middleButton", "modeToggle",
  "shiftClick", "selectAll", "copyPaste", "duplicate", "groupSel", "undo", "redo",
  "deleteSel", "arrowMove", "dragHandle", "dragTip", "pasteImage", "toggleHelp",
] as const

export function ShortcutsHelp({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const kisayollar = KISAYOL_ANAHTARLARI.map((k) => ({
    tus: t(`components.planlama.shortcutsHelp.${k}.key`),
    ne: t(`components.planlama.shortcutsHelp.${k}.desc`),
  }))
  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div className="max-h-full w-full max-w-md overflow-y-auto rounded-lg border bg-popover p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Keyboard className="h-4 w-4" /> {t("components.planlama.shortcutsHelp.title")}
          </h2>
          <button type="button" onClick={onClose} title={t("components.planlama.shortcutsHelp.close")}
            className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
          {kisayollar.map((k, i) => (
            <div key={KISAYOL_ANAHTARLARI[i]} className="contents">
              <dt className="text-right">
                <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">{k.tus}</kbd>
              </dt>
              <dd className="text-muted-foreground">{k.ne}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
