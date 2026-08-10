import { Link } from "react-router-dom"

import { useAuth } from "@/lib/auth"
import {
  Card, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card"

// roles: undefined = herkes; aksi halde yalnız listedeki roller. Nav tablosuyla tutarlı
// tutulur — kullanıcı yalnız erişebileceği modül kartlarını görür.
const modules = [
  { title: "Müşteriler", desc: "Müşteri kartları ve anlaşmalar", to: "/clients", roles: ["management"] },
  { title: "Sharing Board", desc: "Haftalık paylaşım takvimi", to: "/sharing", roles: ["management"] },
  { title: "Tasarım", desc: "Tasarımcı board'u", to: "/designer", roles: ["management", "designer"] },
  { title: "Çekim Planı", desc: "Videografçı çekim planı", to: "/videographer", roles: ["management", "videographer"] },
  { title: "Fotoğraflar", desc: "Çekim fotoğrafları kütüphanesi", to: "/videographer/photos", roles: ["management", "videographer"] },
  { title: "Öneriler", desc: "AI trend-öneri kartları", to: "/videographer/ideas", roles: ["management", "videographer"] },
  { title: "Brief", desc: "Haftalık içerik brief'leri", to: "/brief",
    roles: ["management", "designer", "content_creator", "videographer"] },
  { title: "Tasarımcı Atamaları", desc: "Müşteri-tasarımcı atamaları", to: "/designer-assignments",
    roles: ["management", "designer"] },
  { title: "Özel Günler", desc: "Aylık özel gün takvimi", to: "/special-days",
    roles: ["management", "designer"] },
  { title: "Müşteri Takip", desc: "İş kalemleri ve satış fırsatları", to: "/musteri-takip",
    roles: ["management"] },
]

export function DashboardPage() {
  const { user } = useAuth()
  const role = user?.role || ""
  const visible = modules.filter((m) => !m.roles || m.roles.includes(role))
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Panel</h1>
        <p className="text-muted-foreground">
          Hoş geldin, {user?.name || user?.email}.
        </p>
      </div>

      {user?.role === "pending" && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardHeader>
            <CardTitle className="text-base">Rolün henüz atanmadı</CardTitle>
            <CardDescription>
              Bir yönetici sana rol atayana kadar modüllere erişemezsin.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((m) => {
          const card = (
            <Card className={m.to ? "transition-colors hover:border-primary/50" : "opacity-70"}>
              <CardHeader>
                <CardTitle className="text-base">{m.title}</CardTitle>
                <CardDescription>{m.desc}</CardDescription>
              </CardHeader>
            </Card>
          )
          return m.to
            ? <Link key={m.title} to={m.to}>{card}</Link>
            : <div key={m.title}>{card}</div>
        })}
      </div>
    </div>
  )
}
