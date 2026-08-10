// Yerinde metin düzenleme — eski canvas'ta her başlık/not `window.prompt` açıyordu.
// Çift tıkla (veya Enter) aç · Esc iptal · blur ya da Ctrl+Enter kaydet.
import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"

interface Props {
  value: string | null
  placeholder?: string
  className?: string
  multiline?: boolean
  /** Yalnız değer GERÇEKTEN değiştiyse çağrılır (boşuna PATCH atmayalım). */
  onCommit: (value: string | null) => void
  disabled?: boolean
  /** Yeni yaratılan öğede düzenlemeyi KENDİLİĞİNDEN aç — kullanıcı "Kart" deyip
   *  hemen yazmaya başlayabilsin, ayrıca çift tıklaması gerekmesin. */
  autoEdit?: boolean
  /** Bayrak tek kullanımlık: açılır açılmaz tüketildiğini bildiririz, yoksa
   *  düzenlemeyi kapatınca tekrar tekrar açılırdı. */
  onAutoEditDone?: () => void
}

export function InlineText({ value, placeholder = "…", className, multiline,
                            onCommit, disabled, autoEdit, onAutoEditDone }: Props) {
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
    // `editing` bilerek deps'te değil: kullanıcı kapattıktan sonra bayrak hâlâ
    // true'ysa yeniden açılmasını istemiyoruz (bayrağı zaten tükettik).
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
      title={disabled ? undefined : "Düzenlemek için çift tıkla"}
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
