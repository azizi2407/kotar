// Designer Assignments — management bulk-assigns clients to designers. Each row has
// a designer picker (change one at a time) + multi-select → "assign selected to this
// designer". Only touches the 'designer' slot (other team assignments are preserved).
import { useMemo, useState } from "react"
import { Search, X } from "lucide-react"
import { toast } from "sonner"

import { useAssignDesigner, useClients, useUsers } from "@/lib/clients"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

const NONE = "none"

export function DesignerAssignmentsPage() {
  const { t } = useI18n()
  const { data: clients, isLoading } = useClients({ status: "active", q: "" })
  const { data: users } = useUsers()
  const assign = useAssignDesigner()
  const { isManagement } = useAuth()

  const [q, setQ] = useState("")
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDesigner, setBulkDesigner] = useState<string>(NONE)
  // Designer filter: sub | NONE (unassigned) | null (no filter). Click the badge.
  const [filterDesigner, setFilterDesigner] = useState<string | null>(null)

  const designers = useMemo(() => (users ?? []).filter((u) => u.role === "designer"), [users])
  const userName = useMemo(() => {
    const m = new Map<string, string>()
    ;(users ?? []).forEach((u) => m.set(u.sub, u.name || u.email))
    return m
  }, [users])
  // base-ui Select.Value was showing the raw value (sub=id); passing an `items` map
  // makes it show the label (name) even when closed.
  const itemsMap = useMemo<Record<string, string>>(() => {
    const m: Record<string, string> = { [NONE]: t("pages.designerAssignments.unassigned") }
    ;(users ?? []).forEach((u) => { m[u.sub] = u.name || u.email })
    return m
  }, [users, t])
  const bulkItems = useMemo<Record<string, string>>(
    () => ({ ...itemsMap, [NONE]: t("pages.designerAssignments.unassignedRemove") }), [itemsMap, t])

  const rows = useMemo(() => {
    const needle = trFold(q.trim())
    return (clients ?? []).filter((c) => {
      if (needle && !trFold(c.name).includes(needle)) return false
      if (filterDesigner === NONE) return !c.team_assignments?.designer
      if (filterDesigner != null) return c.team_assignments?.designer === filterDesigner
      return true
    })
  }, [clients, q, filterDesigner])

  // Client count per designer (all active clients; independent of search).
  const counts = useMemo(() => {
    const m = new Map<string, number>()
    ;(clients ?? []).forEach((c) => {
      const d = c.team_assignments?.designer
      if (d) m.set(d, (m.get(d) ?? 0) + 1)
    })
    return m
  }, [clients])
  const unassignedCount = (clients ?? []).filter((c) => !c.team_assignments?.designer).length

  function apply(assignments: { client_id: number; user_id: string | null }[]) {
    assign.mutate(assignments, {
      onSuccess: (r) => toast.success(t("pages.designerAssignments.updated", { count: r.updated })),
      onError: (e) => toast.error(e.message),
    })
  }

  function assignBulk() {
    const ids = [...selected]
    if (!ids.length) return
    const uid = bulkDesigner === NONE ? null : bulkDesigner
    apply(ids.map((id) => ({ client_id: id, user_id: uid })))
    setSelected(new Set())
  }

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id))
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)))
  }
  function toggle(id: number) {
    setSelected((p) => {
      const n = new Set(p)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("pages.designerAssignments.title")}</h1>
        <p className="text-muted-foreground">
          {isManagement
            ? t("pages.designerAssignments.subtitleManagement")
            : t("pages.designerAssignments.subtitleReadonly")}
        </p>
      </div>

      {/* Per-designer summary — click to filter that designer's clients (click again to clear) */}
      <div className="flex flex-wrap gap-2">
        {designers.map((d) => {
          const active = filterDesigner === d.sub
          return (
            <button key={d.sub} type="button"
              onClick={() => setFilterDesigner(active ? null : d.sub)}
              aria-pressed={active}
              className={cn(
                "rounded-full border px-3 py-1 text-sm transition-colors",
                active
                  ? "border-primary bg-primary text-primary-foreground"
                  : "hover:border-primary/50 hover:bg-muted")}>
              {d.name || d.email} · <span className="font-semibold">{counts.get(d.sub) ?? 0}</span>
            </button>
          )
        })}
        <button type="button"
          onClick={() => setFilterDesigner(filterDesigner === NONE ? null : NONE)}
          aria-pressed={filterDesigner === NONE}
          className={cn(
            "rounded-full border border-dashed px-3 py-1 text-sm transition-colors",
            filterDesigner === NONE
              ? "border-primary bg-primary text-primary-foreground"
              : "text-muted-foreground hover:border-primary/50 hover:bg-muted")}>
          {t("pages.designerAssignments.unassignedFilter", { count: unassignedCount })}
        </button>
        {filterDesigner != null && (
          <button type="button" onClick={() => setFilterDesigner(null)}
            className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-sm text-muted-foreground hover:text-foreground">
            <X className="h-3.5 w-3.5" /> {t("pages.designerAssignments.clearFilter")}
          </button>
        )}
      </div>

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder={t("pages.designerAssignments.searchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      {/* Bulk assignment bar — management only */}
      {isManagement && selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 p-2">
          <span className="text-sm font-medium">{t("pages.designerAssignments.selectedCount", { count: selected.size })}</span>
          <span className="text-muted-foreground">→</span>
          <Select value={bulkDesigner} items={bulkItems} onValueChange={(v) => v && setBulkDesigner(v)}>
            <SelectTrigger size="sm" className="w-52"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{t("pages.designerAssignments.unassignedRemove")}</SelectItem>
              {designers.map((d) => <SelectItem key={d.sub} value={d.sub}>{d.name || d.email}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button size="sm" onClick={assignBulk} disabled={assign.isPending}>{t("pages.designerAssignments.assignBtn")}</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">{[...Array(8)].map((_, i) => <Skeleton key={i} className="h-11 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">
          {q || filterDesigner != null
            ? t("pages.designerAssignments.noMatch")
            : t("pages.designerAssignments.noClients")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                {isManagement && (
                  <TableHead className="w-10">
                    <input type="checkbox" checked={allSelected} onChange={toggleAll}
                      className="h-4 w-4 cursor-pointer accent-primary" aria-label={t("pages.designerAssignments.selectAllAria")} />
                  </TableHead>
                )}
                <TableHead>{t("pages.designerAssignments.clientHeader")}</TableHead>
                <TableHead className="hidden sm:table-cell">{t("pages.designerAssignments.sectorHeader")}</TableHead>
                <TableHead className="w-56">{t("pages.designerAssignments.designerHeader")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => {
                const cur = c.team_assignments?.designer ?? NONE
                const orphan = cur !== NONE && !designers.some((d) => d.sub === cur)
                return (
                  <TableRow key={c.id} className={cn(selected.has(c.id) && "bg-muted/40")}>
                    {isManagement && (
                      <TableCell>
                        <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)}
                          className="h-4 w-4 cursor-pointer accent-primary"
                          aria-label={t("pages.designerAssignments.selectClientAria", { name: c.name })} />
                      </TableCell>
                    )}
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell className="hidden text-muted-foreground sm:table-cell">{c.sector || "—"}</TableCell>
                    <TableCell>
                      {isManagement ? (
                        <Select value={cur} items={itemsMap}
                          onValueChange={(v) => v && apply([{ client_id: c.id, user_id: v === NONE ? null : v }])}>
                          <SelectTrigger size="sm" className="w-52"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value={NONE}>{t("pages.designerAssignments.unassigned")}</SelectItem>
                            {orphan && (
                              <SelectItem value={cur}>
                                {t("pages.designerAssignments.notDesignerSuffix", { name: userName.get(cur) || cur })}
                              </SelectItem>
                            )}
                            {designers.map((d) => <SelectItem key={d.sub} value={d.sub}>{d.name || d.email}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      ) : (
                        <span className={cn("text-sm", cur === NONE && "text-muted-foreground")}>
                          {cur === NONE ? t("pages.designerAssignments.unassigned") : (userName.get(cur) || cur)}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
