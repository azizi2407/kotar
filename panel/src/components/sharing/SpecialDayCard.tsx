// Board kart şeridinde bir özel gün bilgi kartı (müşterinin o haftaya düşen seçili özel
// günü). İçerik: tarih + özel gün adı. Amber/altın tema — içerik kartlarından ayrışsın.
import { CalendarDays } from "lucide-react"

import type { SpecialDayInWeek } from "@/lib/sharing"
import { cn } from "@/lib/utils"

export function SpecialDayCard({ sd, className }: { sd: SpecialDayInWeek; className?: string }) {
  return (
    <div
      title={sd.day_name ?? ""}
      className={cn(
        "flex shrink-0 flex-col justify-between gap-1 rounded-lg border border-amber-400/60 bg-amber-50 p-2 dark:border-amber-500/40 dark:bg-amber-950/30",
        className,
      )}>
      <div className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
        <CalendarDays className="h-3.5 w-3.5 shrink-0" />
        <span className="text-[9px] font-semibold uppercase tracking-wide">
          {sd.type === "week" ? "Özel Hafta" : "Özel Gün"}
        </span>
      </div>
      <div className="min-w-0">
        <div className="text-xs font-bold text-amber-900 dark:text-amber-200">{sd.date_label}</div>
        <div className="line-clamp-2 text-[11px] leading-tight text-amber-800 dark:text-amber-300">
          {sd.day_name || "Özel gün"}
        </div>
      </div>
    </div>
  )
}
