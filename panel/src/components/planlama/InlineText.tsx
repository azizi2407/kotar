// In-place text editing — on the old canvas every title/note opened a `window.prompt`.
// Double-click (or Enter) to open · Esc to cancel · blur or Ctrl+Enter to save.
import { useEffect, useRef, useState } from "react"

import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

interface Props {
  value: string | null
  placeholder?: string
  className?: string
  multiline?: boolean
  /** Only called if the value ACTUALLY changed (let's not fire a needless PATCH). */
  onCommit: (value: string | null) => void
  disabled?: boolean
  /** AUTOMATICALLY open editing on a newly created item — so the user can say "Card" and
   *  start typing right away, without needing to double-click first. */
  autoEdit?: boolean
  /** The flag is single-use: we report it consumed as soon as it opens, otherwise
   *  editing would reopen over and over every time it's closed. */
  onAutoEditDone?: () => void
}

export function InlineText({ value, placeholder = "…", className, multiline,
                            onCommit, disabled, autoEdit, onAutoEditDone }: Props) {
  const { t } = useI18n()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(value ?? "")
  const ref = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (!editing) setText(value ?? "")
  }, [value, editing])

  useEffect(() => {
    if (autoEdit && !disabled) {
      setEditing(true)
      onAutoEditDone?.()
    }
    // `editing` is deliberately not in the deps: if the flag is still true after the
    // user closes it, we don't want it reopening (we already consumed the flag).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoEdit, disabled])

  useEffect(() => {
    if (editing && ref.current) {
      ref.current.focus()
      ref.current.select()
    }
  }, [editing])

  function finish(save: boolean) {
    setEditing(false)
    if (!save) { setText(value ?? ""); return }
    const next = text.trim() || null
    if (next !== (value ?? null)) onCommit(next)
  }

  if (editing) {
    return (
      <textarea
        ref={ref}
        rows={multiline ? 4 : 1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => finish(true)}
        onPointerDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === "Escape") { e.preventDefault(); finish(false) }
          if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
            e.preventDefault(); finish(true)
          }
        }}
        className={cn("w-full resize-none rounded border border-primary/60 bg-background px-1 py-0.5 text-sm outline-none", className)}
      />
    )
  }

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      title={disabled ? undefined : t("components.planlama.inlineText.editHint")}
      onDoubleClick={(e) => { if (!disabled) { e.stopPropagation(); setEditing(true) } }}
      onKeyDown={(e) => {
        if (disabled) return
        if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); setEditing(true) }
      }}
      className={cn("cursor-text break-words whitespace-pre-wrap outline-none focus-visible:ring-1 focus-visible:ring-primary/60",
                    !value && "text-muted-foreground italic", className)}
    >
      {value || placeholder}
    </div>
  )
}
