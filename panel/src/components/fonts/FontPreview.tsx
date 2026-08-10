// Tek font satırının metin önizlemesi (2026-08-05).
//
// Font dosyası GÖRÜNÜR OLUNCA yüklenir (IntersectionObserver): havuzda 30+ dosya
// olabiliyor, sayfa açılışta hepsini indirmemeli. Yükleme bitene kadar metin
// sistem fontuyla soluk gösterilir — boş kutu yerine okunur bir ara durum.
import { useEffect, useRef, useState } from "react"

import { cssFamily, loadFontFace, type FontItem } from "@/lib/fonts"
import { cn } from "@/lib/utils"

export function FontPreview({ font, text, size, dark, className }: {
  font: FontItem
  text: string
  size: number
  dark?: boolean
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [durum, setDurum] = useState<"bekliyor" | "yuklendi" | "hata">("bekliyor")

  useEffect(() => {
    const el = ref.current
    if (!el || durum !== "bekliyor") return
    const gozlemci = new IntersectionObserver((girisler) => {
      if (!girisler.some((g) => g.isIntersecting)) return
      gozlemci.disconnect()
      loadFontFace(font).then(() => setDurum("yuklendi")).catch(() => setDurum("hata"))
    }, { rootMargin: "200px" })     // ekrana girmeden biraz önce başlat
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
        {durum === "hata" ? "Font yüklenemedi" : (text || " ")}
      </div>
    </div>
  )
}
