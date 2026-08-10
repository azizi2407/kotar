// Board tile'larındaki "Logo" düğmesi (2026-08-04). Tasarımcı/videografın müşterinin
// marka görsellerini panelden indirmesi için — daha önce tek yol ortak Drive klasörünü
// açıp 75 dosya arasından doğru olanı bulmaktı.
//
// Asset sorgusu YALNIZ düğmeye ilk tıklandığında koşar: board'da 30 tile var, hepsi
// açılışta istek atsa boardu gereksiz yere yavaşlatırdı.
import { useState } from "react"
import { Download, Loader2, Shapes } from "lucide-react"

import { downloadClientAsset, useClientAssets } from "@/lib/sharing"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function LogoButton({ clientId }: { clientId: number }) {
  const [armed, setArmed] = useState(false)
  const { data: assets, isLoading } = useClientAssets(armed ? clientId : null)

  // Henüz sorgu koşmadıysa düğme "silah kur"ar; sonrası dropdown.
  if (!armed) {
    return (
      <Button variant="ghost" size="sm" onClick={() => setArmed(true)} title="Marka görselleri">
        <Shapes className="mr-1 h-3.5 w-3.5" /> Logo
      </Button>
    )
  }

  if (isLoading) {
    return (
      <Button variant="ghost" size="sm" disabled>
        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Logo
      </Button>
    )
  }

  const list = assets ?? []
  if (list.length === 0) {
    return (
      <Button variant="ghost" size="sm" disabled title="Bu müşteri için marka görseli yok">
        <Shapes className="mr-1 h-3.5 w-3.5" /> Logo yok
      </Button>
    )
  }

  if (list.length === 1) {
    return (
      <Button variant="ghost" size="sm"
        onClick={() => downloadClientAsset(clientId, list[0].id)}
        title={list[0].file_name ?? "Logo indir"}>
        <Download className="mr-1 h-3.5 w-3.5" /> Logo
      </Button>
    )
  }

  return (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger render={<Button variant="ghost" size="sm" />}>
        <Shapes className="mr-1 h-3.5 w-3.5" /> Logo ({list.length})
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-w-72">
        {list.map((a) => (
          <DropdownMenuItem key={a.id} onClick={() => downloadClientAsset(clientId, a.id)}>
            <Download className="mr-2 h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {a.label ?? (a.kind === "logo" ? "Logo" : a.file_name) ?? `Görsel ${a.id}`}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
