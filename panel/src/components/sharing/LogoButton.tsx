// "Logo" button on board tiles (2026-08-04). Lets the designer/videographer download
// a client's brand assets from the panel — previously the only way was opening the
// shared Drive folder and finding the right one among 75 files.
//
// The asset query runs ONLY on the first click of the button: the board has 30 tiles,
// and firing a request for each on load would needlessly slow the board down.
import { useState } from "react"
import { Download, Loader2, Shapes } from "lucide-react"

import { downloadClientAsset, useClientAssets } from "@/lib/sharing"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function LogoButton({ clientId }: { clientId: number }) {
  const { t } = useI18n()
  const [armed, setArmed] = useState(false)
  const { data: assets, isLoading } = useClientAssets(armed ? clientId : null)

  // Before the query has run, the button "arms" itself; after that, a dropdown.
  if (!armed) {
    return (
      <Button variant="ghost" size="sm" onClick={() => setArmed(true)} title={t("components.sharing.logoButton.brandAssets")}>
        <Shapes className="mr-1 h-3.5 w-3.5" /> {t("components.sharing.logoButton.logo")}
      </Button>
    )
  }

  if (isLoading) {
    return (
      <Button variant="ghost" size="sm" disabled>
        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> {t("components.sharing.logoButton.logo")}
      </Button>
    )
  }

  const list = assets ?? []
  if (list.length === 0) {
    return (
      <Button variant="ghost" size="sm" disabled title={t("components.sharing.logoButton.noAssets")}>
        <Shapes className="mr-1 h-3.5 w-3.5" /> {t("components.sharing.logoButton.noLogo")}
      </Button>
    )
  }

  if (list.length === 1) {
    return (
      <Button variant="ghost" size="sm"
        onClick={() => downloadClientAsset(clientId, list[0].id)}
        title={list[0].file_name ?? t("components.sharing.logoButton.downloadLogo")}>
        <Download className="mr-1 h-3.5 w-3.5" /> {t("components.sharing.logoButton.logo")}
      </Button>
    )
  }

  return (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger render={<Button variant="ghost" size="sm" />}>
        <Shapes className="mr-1 h-3.5 w-3.5" /> {t("components.sharing.logoButton.logo")} ({list.length})
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-w-72">
        {list.map((a) => (
          <DropdownMenuItem key={a.id} onClick={() => downloadClientAsset(clientId, a.id)}>
            <Download className="mr-2 h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {a.label ?? (a.kind === "logo" ? t("components.sharing.logoButton.logo") : a.file_name)
                ?? t("components.sharing.logoButton.imageN", { id: a.id })}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
