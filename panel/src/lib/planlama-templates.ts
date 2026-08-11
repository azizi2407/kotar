// Board templates — PURE generators. Each returns `Partial<PlanningItem>[]`,
// the caller sends them all in one delta PATCH with a single `store.commit()`.
//
// Limit: the server's `MAX_BATCH = 200`. Templates are kept well under this; still,
// `chunkForPatch()` makes chunked sending possible.
import { COLORS, DEFAULT_SIZE, newItemKey, type PlanningItem } from "@/lib/planlama"

export type NewItem = Partial<PlanningItem> & { item_key: string }

/** The max items the server accepts in a single PATCH (matches planning.MAX_BATCH). */
export const MAX_BATCH = 200

export function chunkForPatch<T>(items: T[], size = MAX_BATCH): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

function region(title: string, x: number, y: number, w: number, h: number, color = "#f1f5f9"): NewItem {
  return { item_key: newItemKey(), type: "region", title, x, y, width: w, height: h,
           z: -1, color, status: "open" }
}

function card(title: string, x: number, y: number, color?: string, label?: string): NewItem {
  return { item_key: newItemKey(), type: "card", title, x, y,
           width: DEFAULT_SIZE.card.width, height: DEFAULT_SIZE.card.height,
           z: 0, color: color ?? COLORS[0], label: label ?? null, status: "open" }
}

function note(text: string, x: number, y: number): NewItem {
  return { item_key: newItemKey(), type: "note", text, x, y,
           width: DEFAULT_SIZE.note.width, height: DEFAULT_SIZE.note.height,
           z: 0, color: "#fef9c3", status: "open" }
}

export interface Template {
  id: string
  name: string
  description: string
  build: (originX: number, originY: number) => NewItem[]
}

type TFunc = (key: string, vars?: Record<string, string | number>) => string

/** Template list — the name/description and content text are translated per language,
 *  so it's a generator that takes `t()` instead of a fixed array. The caller passes in
 *  the `t` it got from `useI18n()` (see PlanlamaPage.tsx). */
export function getTemplates(t: TFunc): Template[] {
  const GUNLER = [
    t("pages.planning.templates.day.monday"), t("pages.planning.templates.day.tuesday"),
    t("pages.planning.templates.day.wednesday"), t("pages.planning.templates.day.thursday"),
    t("pages.planning.templates.day.friday"), t("pages.planning.templates.day.saturday"),
    t("pages.planning.templates.day.sunday"),
  ]

  /** Weekly plan: 7 day regions side by side, each with one starter card. */
  function haftalikPlan(ox: number, oy: number): NewItem[] {
    const W = 260, H = 520, GAP = 16
    const out: NewItem[] = []
    GUNLER.forEach((gun, i) => {
      const x = ox + i * (W + GAP)
      out.push(region(gun, x, oy, W, H, i >= 5 ? "#f8fafc" : "#f1f5f9"))
      out.push(card(t("pages.planning.templates.newTaskCard"), x + 20, oy + 44, COLORS[i % COLORS.length]))
    })
    return out
  }

  /** Campaign flow: brief → content → approval → publish, with no connections between them
   *  (let the user draw the connection — auto-placed arrows are usually wrong). */
  function kampanyaAkisi(ox: number, oy: number): NewItem[] {
    const asamalar: [string, string][] = [
      [t("pages.planning.templates.campaignStage.brief"), "#e0f2fe"],
      [t("pages.planning.templates.campaignStage.contentProduction"), "#e0e7ff"],
      [t("pages.planning.templates.campaignStage.internalApproval"), "#fef9c3"],
      [t("pages.planning.templates.campaignStage.clientApproval"), "#f3e8ff"],
      [t("pages.planning.templates.campaignStage.publish"), "#dcfce7"],
    ]
    const out: NewItem[] = [region(t("pages.planning.templates.campaign.name"), ox, oy, 5 * 236 + 24, 240, "#f8fafc")]
    asamalar.forEach(([ad, renk], i) => {
      out.push(card(ad, ox + 20 + i * 236, oy + 48, renk, t("pages.planning.templates.stageLabel", { n: i + 1 })))
    })
    out.push(note(t("pages.planning.templates.campaignNote"), ox + 20, oy + 190))
    return out
  }

  /** Shoot flow: prep → shoot → edit → delivery. */
  function cekimAkisi(ox: number, oy: number): NewItem[] {
    const asamalar: [string, string][] = [
      [t("pages.planning.templates.shootStage.plan"), "#e0f2fe"],
      [t("pages.planning.templates.shootStage.equipment"), "#f1f5f9"],
      [t("pages.planning.templates.shootStage.day"), "#fee2e2"],
      [t("pages.planning.templates.shootStage.edit"), "#e0e7ff"],
      [t("pages.planning.templates.shootStage.delivery"), "#dcfce7"],
    ]
    const out: NewItem[] = [region(t("pages.planning.templates.shoot.name"), ox, oy, 3 * 236 + 24, 460, "#f8fafc")]
    asamalar.forEach(([ad, renk], i) => {
      out.push(card(ad, ox + 20 + (i % 3) * 236, oy + 48 + Math.floor(i / 3) * 168, renk))
    })
    return out
  }

  return [
    { id: "hafta", name: t("pages.planning.templates.weekly.name"),
      description: t("pages.planning.templates.weekly.description"),
      build: haftalikPlan },
    { id: "kampanya", name: t("pages.planning.templates.campaign.name"),
      description: t("pages.planning.templates.campaign.description"),
      build: kampanyaAkisi },
    { id: "cekim", name: t("pages.planning.templates.shoot.name"),
      description: t("pages.planning.templates.shoot.description"),
      build: cekimAkisi },
  ]
}
