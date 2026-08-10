// Tasarımcı Atamaları — yönetim müşterileri tasarımcılara toplu atar. Her satırda
// tasarımcı seçici (tek tek değiştir) + çoklu seçim → "seçilenleri şu tasarımcıya ata".
// Yalnız 'designer' slot'una dokunur (diğer ekip atamaları korunur).
import { useMemo, useState } from "react"
import { Search, X } from "lucide-react"
import { toast } from "sonner"

import { useAssignDesigner, useClients, useUsers } from "@/lib/clients"
import { useAuth } from "@/lib/auth"
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
  const { data: clients, isLoading } = useClients({ status: "active", q: "" })
  const { data: users } = useUsers()
  const assign = useAssignDesigner()
  const { isManagement } = useAuth()

  const [q, setQ] = useState("")
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkDesigner, setBulkDesigner] = useState<string>(NONE)
  // Tasarımcı filtresi: sub | NONE (atanmamışlar) | null (filtre yok). Rozete tıkla.
  const [filterDesigner, setFilterDesigner] = useState<string | null>(null)

  const designers = useMemo(() => (users ?? []).filter((u) => u.role === "designer"), [users])
  const userName = useMemo(() => {
    const m = new Map<string, string>()
    ;(users ?? []).forEach((u) => m.set(u.sub, u.name || u.email))
    return m
  }, [users])
  // base-ui Select.Value ham değeri (sub=id) gösteriyordu; `items` map'i verilince
  // kapalı durumda da etiketi (isim) gösterir.
  const itemsMap = useMemo<Record<string, string>>(() => {
    const m: Record<string, string> = { [NONE]: "Atanmamış" }
    ;(users ?? []).forEach((u) => { m[u.sub] = u.name || u.email })
    return m
  }, [users])
  const bulkItems = useMemo<Record<string, string>>(
    () => ({ ...itemsMap, [NONE]: "Atanmamış (kaldır)" }), [itemsMap])

  const rows = useMemo(() => {
    const needle = trFold(q.trim())
    return (clients ?? []).filter((c) => {
      if (needle && !trFold(c.name).includes(needle)) return false
      if (filterDesigner === NONE) return !c.team_assignments?.designer
      if (filterDesigner != null) return c.team_assignments?.designer === filterDesigner
      return true
    })
  }, [clients, q, filterDesigner])

  // Tasarımcı başına müşteri sayısı (tüm aktifler; aramadan bağımsız).
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
      onSuccess: (r) => toast.success(`${r.updated} müşteri güncellendi`),
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
        <h1 className="text-2xl font-semibold tracking-tight">Tasarımcı Atamaları</h1>
        <p className="text-muted-foreground">
          {isManagement
            ? "Müşterileri tasarımcılara ata; tek tek ya da toplu değiştir."
            : "Müşteri-tasarımcı atamaları (salt-okunur)."}
        </p>
      </div>

      {/* Tasarımcı başına özet — tıkla, o tasarımcının müşterilerini süz (tekrar tıkla = kaldır) */}
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
          Atanmamış · <span className="font-semibold">{unassignedCount}</span>
        </button>
        {filterDesigner != null && (
          <button type="button" onClick={() => setFilterDesigner(null)}
            className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-sm text-muted-foreground hover:text-foreground">
            <X className="h-3.5 w-3.5" /> Filtreyi temizle
          </button>
        )}
      </div>

      <div className="relative w-full sm:max-w-xs">
        <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-8" placeholder="Müşteri ara…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      {/* Toplu atama şeridi — yalnız yönetim */}
      {isManagement && selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 p-2">
          <span className="text-sm font-medium">{selected.size} seçili</span>
          <span className="text-muted-foreground">→</span>
          <Select value={bulkDesigner} items={bulkItems} onValueChange={(v) => v && setBulkDesigner(v)}>
            <SelectTrigger size="sm" className="w-52"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Atanmamış (kaldır)</SelectItem>
              {designers.map((d) => <SelectItem key={d.sub} value={d.sub}>{d.name || d.email}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button size="sm" onClick={assignBulk} disabled={assign.isPending}>Ata</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">{[...Array(8)].map((_, i) => <Skeleton key={i} className="h-11 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-muted-foreground">
          {q || filterDesigner != null ? "Eşleşen müşteri yok." : "Müşteri yok."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                {isManagement && (
                  <TableHead className="w-10">
                    <input type="checkbox" checked={allSelected} onChange={toggleAll}
                      className="h-4 w-4 cursor-pointer accent-primary" aria-label="Tümünü seç" />
                  </TableHead>
                )}
                <TableHead>Müşteri</TableHead>
                <TableHead className="hidden sm:table-cell">Sektör</TableHead>
                <TableHead className="w-56">Tasarımcı</TableHead>
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
                          className="h-4 w-4 cursor-pointer accent-primary" aria-label={`${c.name} seç`} />
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
                            <SelectItem value={NONE}>Atanmamış</SelectItem>
                            {orphan && (
                              <SelectItem value={cur}>{userName.get(cur) || cur} (tasarımcı değil)</SelectItem>
                            )}
                            {designers.map((d) => <SelectItem key={d.sub} value={d.sub}>{d.name || d.email}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      ) : (
                        <span className={cn("text-sm", cur === NONE && "text-muted-foreground")}>
                          {cur === NONE ? "Atanmamış" : (userName.get(cur) || cur)}
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
