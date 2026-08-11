// Text preview for a single font row (2026-08-05).
//
// The font file is loaded WHEN IT BECOMES VISIBLE (IntersectionObserver): the
// pool can have 30+ files, so we shouldn't download all of them on page load.
// Until loading finishes, the text is shown faded in the system font — a
// readable in-between state instead of an empty box.
import { useEffect, useRef, useState } from "react"

import { cssFamily, loadFontFace, type FontItem } from "@/lib/fonts"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

export function FontPreview({ font, text, size, dark, className }: {
  font: FontItem
  text: string
  size: number
  dark?: boolean
  className?: string
}) {
  const { t } = useI18n()
  const ref = useRef<HTMLDivElement>(null)
  const [durum, setDurum] = useState<"bekliyor" | "yuklendi" | "hata">("bekliyor")

  useEffect(() => {
    const el = ref.current
    if (!el || durum !== "bekliyor") return
    const gozlemci = new IntersectionObserver((girisler) => {
      if (!girisler.some((g) => g.isIntersecting)) return
      gozlemci.disconnect()
      loadFontFace(font).then(() => setDurum("yuklendi")).catch(() => setDurum("hata"))
    }, { rootMargin: "200px" })     // start slightly before it enters the viewport
    gozlemci.observe(el)
    return () => gozlemci.disconnect()
  }, [font, durum])

  return (
    <div
      ref={ref}
      className={cn("overflow-x-auto rounded-md border px-3 py-2 transition-colors",
        dark ? "border-white/10 bg-neutral-900 text-white" : "bg-white text-neutral-900",
        className)}
    >
      <div
        className={cn("whitespace-pre-wrap break-words leading-tight",
          durum === "bekliyor" && "opacity-40",
          durum === "hata" && "text-destructive")}
        style={{
          fontFamily: durum === "yuklendi" ? `"${cssFamily(font)}", sans-serif` : undefined,
          fontSize: `${size}px`,
        }}
      >
        {durum === "hata" ? t("components.fonts.fontPreview.fontLoadFailed") : (text || " ")}
      </div>
    </div>
  )
}
