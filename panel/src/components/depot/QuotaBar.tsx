// Videographer Depot quota bar — shared 5GB.
import { Progress } from "@/components/ui/progress"
import { fmtBytes, type DepotQuota } from "@/lib/depot"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

export function QuotaBar({ quota }: { quota: DepotQuota }) {
  const { t, lang } = useI18n()
  const tone = quota.over_quota || quota.pct > 95
    ? "text-rose-600 dark:text-rose-400"
    : quota.pct > 85
      ? "text-amber-600 dark:text-amber-400"
      : "text-muted-foreground"
  return (
    <div className="space-y-1.5 rounded-lg border p-3">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">
          {t("components.depot.quotaBar.used", { used: fmtBytes(quota.used, lang), limit: fmtBytes(quota.limit, lang) })}
        </span>
        <span className={cn("text-xs", tone)}>
          {quota.over_quota
            ? t("components.depot.quotaBar.overQuota")
            : t("components.depot.quotaBar.remaining", { remaining: fmtBytes(quota.remaining, lang) })}
        </span>
      </div>
      <Progress value={Math.min(100, quota.pct)} />
      <p className="text-xs text-muted-foreground">
        {t("components.depot.quotaBar.perFileLimit", { limit: fmtBytes(quota.file_limit, lang) })}
      </p>
    </div>
  )
}
