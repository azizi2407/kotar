import { Link } from "react-router-dom"

import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import {
  Card, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card"

// roles: undefined = everyone; otherwise only the roles in the list. Kept consistent
// with the nav table — the user only sees module cards they can actually access.
// title/desc are translation keys — the label text is produced by t() in the component.
const modules = [
  { titleKey: "pages.dashboard.module.clients.title", descKey: "pages.dashboard.module.clients.desc", to: "/clients", roles: ["management"] },
  { titleKey: "pages.dashboard.module.sharing.title", descKey: "pages.dashboard.module.sharing.desc", to: "/sharing", roles: ["management"] },
  { titleKey: "pages.dashboard.module.designer.title", descKey: "pages.dashboard.module.designer.desc", to: "/designer", roles: ["management", "designer"] },
  { titleKey: "pages.dashboard.module.shootPlan.title", descKey: "pages.dashboard.module.shootPlan.desc", to: "/videographer", roles: ["management", "videographer"] },
  { titleKey: "pages.dashboard.module.photos.title", descKey: "pages.dashboard.module.photos.desc", to: "/videographer/photos", roles: ["management", "videographer"] },
  { titleKey: "pages.dashboard.module.ideas.title", descKey: "pages.dashboard.module.ideas.desc", to: "/videographer/ideas", roles: ["management", "videographer"] },
  { titleKey: "pages.dashboard.module.brief.title", descKey: "pages.dashboard.module.brief.desc", to: "/brief",
    roles: ["management", "designer", "content_creator", "videographer"] },
  { titleKey: "pages.dashboard.module.designerAssignments.title", descKey: "pages.dashboard.module.designerAssignments.desc", to: "/designer-assignments",
    roles: ["management", "designer"] },
  { titleKey: "pages.dashboard.module.specialDays.title", descKey: "pages.dashboard.module.specialDays.desc", to: "/special-days",
    roles: ["management", "designer"] },
  { titleKey: "pages.dashboard.module.clientTracking.title", descKey: "pages.dashboard.module.clientTracking.desc", to: "/musteri-takip",
    roles: ["management"] },
]

export function DashboardPage() {
  const { t } = useI18n()
  const { user } = useAuth()
  const role = user?.role || ""
  const visible = modules.filter((m) => !m.roles || m.roles.includes(role))
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("pages.dashboard.title")}</h1>
        <p className="text-muted-foreground">
          {t("pages.dashboard.welcome", { name: user?.name || user?.email || "" })}
        </p>
      </div>

      {user?.role === "pending" && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardHeader>
            <CardTitle className="text-base">{t("pages.dashboard.pendingRoleTitle")}</CardTitle>
            <CardDescription>
              {t("pages.dashboard.pendingRoleDesc")}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((m) => {
          const card = (
            <Card className={m.to ? "transition-colors hover:border-primary/50" : "opacity-70"}>
              <CardHeader>
                <CardTitle className="text-base">{t(m.titleKey)}</CardTitle>
                <CardDescription>{t(m.descKey)}</CardDescription>
              </CardHeader>
            </Card>
          )
          return m.to
            ? <Link key={m.titleKey} to={m.to}>{card}</Link>
            : <div key={m.titleKey}>{card}</div>
        })}
      </div>
    </div>
  )
}
