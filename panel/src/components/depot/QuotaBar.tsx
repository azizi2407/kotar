// Videograf Deposu kota çubuğu — ortak 5 GB.
import { Progress } from "@/components/ui/progress"
import { fmtBytes, type DepotQuota } from "@/lib/depot"
import { cn } from "@/lib/utils"

export function QuotaBar({ quota }: { quota: DepotQuota }) {
  const tone = quota.over_quota || quota.pct > 95
    ? "text-rose-600 dark:text-rose-400"
    : quota.pct > 85
      ? "text-amber-600 dark:text-amber-400"
      : "text-muted-foreground"
  return (
    <div className="space-y-1.5 rounded-lg border p-3">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">
          {fmtBytes(quota.used)} / {fmtBytes(quota.limit)} kullanıldı
        </span>
        <span className={cn("text-xs", tone)}>
          {quota.over_quota
            ? "Depo sınırı aşıldı — yer açın"
            : `${fmtBytes(quota.remaining)} boş`}
        </span>
      </div>
      <Progress value={Math.min(100, quota.pct)} />
      <p className="text-xs text-muted-foreground">
        Dosya başına en fazla {fmtBytes(quota.file_limit)}.
      </p>
    </div>
  )
}
