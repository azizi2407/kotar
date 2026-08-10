// Tuval dışı görünümler: Liste · Takvim · "Bana atanan" şeridi.
//
// Neden tuvalin yanında liste/takvim: 600 kartlık bir panoda "geciken işler
// hangileri" sorusu tuvalde gözle taranamıyor. Aynı veri, üç okuma biçimi.
import { useMemo } from "react"
import { Link } from "react-router-dom"
import { CalendarDays, Building2, Camera, Megaphone, User } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  dueTone, fmtDay, ITEM_STATUS_LABELS, type AssignedItem, type PlanningItem,
} from "@/lib/planlama"
import { cn } from "@/lib/utils"

// --- Liste ----------------------------------------------------------------

export function ListView({ items, onOpen }: {
  items: PlanningItem[]
  onOpen: (it: PlanningItem) => void
}) {
  // Sıralama: geciken önce, sonra son tarihe göre, tarihsizler en sonda.
  const rows = useMemo(() => [...items]
    .filter((it) => it.type === "card" || it.type === "note")
    .sort((a, b) => {
      if (!a.due_date && !b.due_date) return (a.title || "").localeCompare(b.title || "", "tr")
      if (!a.due_date) return 1
      if (!b.due_date) return -1
      return a.due_date.localeCompare(b.due_date)
    }), [items])

  if (!rows.length) {
    return <p className="py-12 text-center text-muted-foreground">Gösterilecek kart yok.</p>
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Başlık</TableHead>
            <TableHead className="w-28">Durum</TableHead>
            <TableHead className="w-32">Son tarih</TableHead>
            <TableHead className="w-40">Sorumlu</TableHead>
            <TableHead className="w-56">Bağlantılar</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((it) => (
            <TableRow key={it.item_key} className="cursor-pointer" onClick={() => onOpen(it)}>
              <TableCell className={cn("font-medium", it.status === "done" && "text-muted-foreground line-through")}>
                {it.title || it.text || <span className="text-muted-foreground italic">Başlıksız</span>}
                {it.label && <Badge variant="outline" className="ml-2 text-[10px]">{it.label}</Badge>}
              </TableCell>
              <TableCell>
                <Badge variant={it.status === "done" ? "secondary" : "outline"} className="text-[10px]">
                  {ITEM_STATUS_LABELS[it.status]}
                </Badge>
              </TableCell>
              <TableCell>
                {it.due_date
                  ? <span className={cn("rounded-full px-1.5 py-0.5 text-xs", dueTone(it.due_date, it.status))}>
                      {fmtDay(it.due_date)}
                    </span>
                  : <span className="text-muted-foreground">—</span>}
              </TableCell>
              <TableCell className="text-sm">{it.assignee_name || "—"}</TableCell>
              <TableCell><LinkChips it={it} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function LinkChips({ it }: { it: PlanningItem }) {
  const chips: [React.ReactNode, string][] = []
  if (it.client_name) chips.push([<Building2 key="c" className="h-3 w-3" />, it.client_name])
  if (it.shoot_title) chips.push([<Camera key="s" className="h-3 w-3" />, it.shoot_title])
  if (it.campaign_title) chips.push([<Megaphone key="a" className="h-3 w-3" />, it.campaign_title])
  if (!chips.length) return <span className="text-muted-foreground">—</span>
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map(([icon, text], i) => (
        <span key={i} className="inline-flex max-w-[10rem] items-center gap-0.5 truncate rounded-full bg-muted px-1.5 py-0.5 text-[10px]">
          {icon}<span className="truncate">{text}</span>
        </span>
      ))}
    </div>
  )
}

// --- Takvim ---------------------------------------------------------------

/** Son tarihi olan kartları aya göre gruplar. Tam bir takvim ızgarası değil:
 *  ajansın sorusu "hangi gün ne var" değil "yaklaşan işler neler" — gün gün
 *  boş kutu çizmek 600 kartlık panoda gürültü üretiyordu. */
export function CalendarView({ items, onOpen }: {
  items: PlanningItem[]
  onOpen: (it: PlanningItem) => void
}) {
  const groups = useMemo(() => {
    const withDue = items.filter((it) => it.due_date && it.type !== "edge" && it.type !== "region")
    const map = new Map<string, PlanningItem[]>()
    for (const it of withDue.sort((a, b) => (a.due_date || "").localeCompare(b.due_date || ""))) {
      const key = (it.due_date as string).slice(0, 7)
      const arr = map.get(key)
      if (arr) arr.push(it); else map.set(key, [it])
    }
    return [...map.entries()]
  }, [items])

  if (!groups.length) {
    return (
      <p className="py-12 text-center text-muted-foreground">
        Son tarihi olan kart yok — bir kartın ayrıntılarından tarih ver, burada belirsin.
      </p>
    )
  }

  return (
    <div className="space-y-6">
      {groups.map(([month, list]) => (
        <section key={month} className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-muted-foreground">
            <CalendarDays className="h-4 w-4" />
            {new Date(`${month}-01`).toLocaleDateString("tr-TR", { month: "long", year: "numeric" })}
            <Badge variant="outline" className="text-[10px]">{list.length}</Badge>
          </h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {list.map((it) => (
              <button key={it.item_key} type="button" onClick={() => onOpen(it)}
                className="rounded-lg border bg-card p-3 text-left transition-colors hover:bg-muted/50">
                <div className="flex items-start justify-between gap-2">
                  <span className={cn("text-sm font-medium", it.status === "done" && "line-through opacity-60")}>
                    {it.title || "Başlıksız"}
                  </span>
                  <span className={cn("shrink-0 rounded-full px-1.5 py-0.5 text-[10px]", dueTone(it.due_date, it.status))}>
                    {fmtDay(it.due_date)}
                  </span>
                </div>
                {it.assignee_name && (
                  <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                    <User className="h-3 w-3" />{it.assignee_name}
                  </div>
                )}
                <div className="mt-1"><LinkChips it={it} /></div>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

// --- "Bana atanan" şeridi --------------------------------------------------

/** Pano sınırını AŞAN atama listesi — SALT-OKUNUR.
 *
 *  Bilinçli yetki gediği (2026-07-26 kararı): yönetim panosundaki bir kart bu
 *  kişiye atanmışsa burada görünür, oysa kişi o panoyu açamaz. Aksi halde
 *  yöneticinin atama yapması çalışan açısından tamamen görünmez kalırdı.
 *  Sızan alan kümesi dar: gövde metni, renk, konum ve `extra` DÖNMEZ. */
export function AssignedStrip({ items, currentBoard }: {
  items: AssignedItem[]
  currentBoard: string
}) {
  const other = items.filter((i) => i.board_key !== currentBoard)
  if (!other.length) return null
  return (
    <section className="rounded-lg border border-dashed bg-muted/30 p-3">
      <h3 className="mb-2 text-xs font-semibold text-muted-foreground">
        Bana atanan · başka panolardan {other.length}
        <span className="ml-2 font-normal">— salt-okunur, düzenlemek için kartın kendi panosuna git</span>
      </h3>
      <div className="flex flex-wrap gap-2">
        {other.map((it) => (
          <Link key={`${it.board_key}:${it.item_key}`}
            to={`/planlama?pano=${encodeURIComponent(it.board_key)}`}
            className="flex max-w-xs items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-xs hover:bg-muted">
            <span className={cn("truncate font-medium", it.status === "done" && "line-through opacity-60")}>
              {it.title || "Başlıksız"}
            </span>
            {it.due_date && (
              <span className={cn("shrink-0 rounded-full px-1.5 py-0.5 text-[10px]", dueTone(it.due_date, it.status))}>
                {fmtDay(it.due_date)}
              </span>
            )}
            <span className="shrink-0 text-muted-foreground">{it.board_title}</span>
          </Link>
        ))}
      </div>
    </section>
  )
}
