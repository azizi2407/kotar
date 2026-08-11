// Client list: search + status filter + table. Write access is management only.
import { useState } from "react"
import { Link } from "react-router-dom"
import { Plus, Search } from "lucide-react"

import { useAuth } from "@/lib/auth"
import { useClients } from "@/lib/clients"
import { useI18n } from "@/lib/i18n"
import type { ClientListItem } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Tabs, TabsList, TabsTrigger,
} from "@/components/ui/tabs"
import { ClientFormDialog } from "@/components/clients/ClientFormDialog"

function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n()
  return status === "active"
    ? <Badge variant="secondary">{t("pages.clients.status.active")}</Badge>
    : <Badge variant="outline" className="text-muted-foreground">{t("pages.clients.status.deleted")}</Badge>
}

export function ClientsPage() {
  const { isManagement } = useAuth()
  const { t } = useI18n()
  const [status, setStatus] = useState("active")
  const [q, setQ] = useState("")
  const [adding, setAdding] = useState(false)
  const { data, isLoading, isError } = useClients({ status, q })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("pages.clients.title")}</h1>
          <p className="text-muted-foreground">{t("pages.clients.subtitle")}</p>
        </div>
        {isManagement && (
          <Button onClick={() => setAdding(true)}>
            <Plus className="mr-1 h-4 w-4" /> {t("pages.clients.newClient")}
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder={t("pages.clients.searchPlaceholder")}
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Tabs value={status} onValueChange={(v) => v && setStatus(v)}>
          <TabsList>
            <TabsTrigger value="active">{t("pages.clients.status.active")}</TabsTrigger>
            <TabsTrigger value="deleted">{t("pages.clients.status.deleted")}</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("pages.clients.table.client")}</TableHead>
              <TableHead className="hidden sm:table-cell">{t("pages.clients.table.sector")}</TableHead>
              <TableHead className="hidden md:table-cell">{t("pages.clients.table.email")}</TableHead>
              <TableHead>{t("pages.clients.table.status")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              [...Array(4)].map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={4}><Skeleton className="h-5 w-full" /></TableCell>
                </TableRow>
              ))
            )}
            {isError && (
              <TableRow>
                <TableCell colSpan={4} className="py-8 text-center text-destructive">
                  {t("pages.clients.loadError")}
                </TableCell>
              </TableRow>
            )}
            {data && data.length === 0 && (
              <TableRow>
                <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">
                  {q ? t("pages.clients.noMatch") : t("pages.clients.empty")}
                </TableCell>
              </TableRow>
            )}
            {data?.map((c: ClientListItem) => (
              <TableRow key={c.id} className="cursor-pointer">
                <TableCell className="font-medium">
                  <Link to={`/clients/${c.id}`} className="hover:underline">{c.name}</Link>
                </TableCell>
                <TableCell className="hidden sm:table-cell text-muted-foreground">
                  {c.sector || "—"}
                </TableCell>
                <TableCell className="hidden md:table-cell text-muted-foreground">
                  {c.client_email || "—"}
                </TableCell>
                <TableCell><StatusBadge status={c.status} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {isManagement && (
        <ClientFormDialog open={adding} onOpenChange={setAdding} client={null} />
      )}
    </div>
  )
}
