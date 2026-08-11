// Right-click menu — three contexts: node / selection / empty pane.
//
// Positioning follows the pattern from React Flow's own example: to keep the menu
// from overflowing the viewport, it's aligned from the opposite corner when near the right/bottom edge.
import { type ReactNode } from "react"

import { cn } from "@/lib/utils"

export interface MenuPos {
  top?: number; left?: number; right?: number; bottom?: number
}

/** Produces a position from the click point that doesn't overflow the panel's edge. */
export function menuPosition(e: { clientX: number; clientY: number },
                             pane: DOMRect, w = 200, h = 260): MenuPos {
  const x = e.clientX - pane.left
  const y = e.clientY - pane.top
  return {
    top: y < pane.height - h ? y : undefined,
    left: x < pane.width - w ? x : undefined,
    right: x >= pane.width - w ? pane.width - x : undefined,
    bottom: y >= pane.height - h ? pane.height - y : undefined,
  }
}

export function MenuItem({ onClick, icon, children, danger, disabled, hint }: {
  onClick: () => void
  icon?: ReactNode
  children: ReactNode
  danger?: boolean
  disabled?: boolean
  hint?: string
}) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}
      className={cn("flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm",
                    disabled ? "cursor-not-allowed opacity-40"
                             : danger ? "text-destructive hover:bg-destructive/10"
                                      : "hover:bg-muted")}>
      <span className="flex h-4 w-4 shrink-0 items-center justify-center">{icon}</span>
      <span className="flex-1 truncate">{children}</span>
      {hint && <span className="shrink-0 text-[10px] text-muted-foreground">{hint}</span>}
    </button>
  )
}

export function MenuSep() {
  return <div className="my-1 h-px bg-border" />
}

export function ContextMenu({ pos, onClose, children }: {
  pos: MenuPos
  onClose: () => void
  children: ReactNode
}) {
  return (
    // A full-screen transparent catcher so it closes on an outside click; RF's
    // onPaneClick also fires on a click ON TOP OF the menu, so it isn't sufficient on its own.
    <div className="absolute inset-0 z-50" onClick={onClose} onContextMenu={(e) => {
      e.preventDefault(); onClose()
    }}>
      <div style={pos} className="absolute min-w-[11rem] rounded-lg border bg-popover p-1 shadow-lg"
        onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}
