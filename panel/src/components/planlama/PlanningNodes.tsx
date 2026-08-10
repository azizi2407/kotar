// React Flow özel node'ları: kart · not · bölge · görsel.
//
// `nodrag` SINIFI KRİTİK: RF bu sınıfı taşıyan elemanın üzerinde sürükleme
// başlatmaz. Eski tuvaldeki `data-noload` işaretinin birebir karşılığı — düğme,
// checkbox, palet ve yerinde düzenleme alanı bu sınıfı taşımak zorunda, yoksa
// tıklamak kartı sürüklemeye başlar.
import { memo } from "react"
import { Handle, NodeResizer, Position, type NodeProps } from "@xyflow/react"
import {
  Building2, Camera, Check, ExternalLink, ImageOff, Loader2, Lock, Megaphone, Palette,
  SlidersHorizontal, Trash2, User,
} from "lucide-react"

import { InlineText } from "@/components/planlama/InlineText"
import { COLORS, dueTone, fmtDay, imageOf, planningImageUrl, type PlanningItem } from "@/lib/planlama"
import { cn } from "@/lib/utils"

/** Node.data — tuval bileşeninden geçen öğe + davranışlar. */
export interface NodeData extends Record<string, unknown> {
  item: PlanningItem
  dim: boolean                                   // filtre eşleşmedi → soluk
  locked: boolean                                // extra.locked → salt görüntü
  readOnly: boolean
  onPatch: (key: string, patch: Partial<PlanningItem>) => void
  onRemove: (key: string) => void
  onOpenDetail: (item: PlanningItem) => void
  paletteFor: string | null
  setPaletteFor: (key: string | null) => void
  boardKey: string                               // görsel URL'i pano başına
  autoEdit?: boolean                             // yeni eklendi → başlığı hemen aç
  onAutoEditDone?: () => void
}

/** Konum/ölçü karşılaştırmadan DIŞLANIR: taşımayı/boyutlandırmayı React Flow
 *  kendi transform'uyla uyguluyor, node'un İÇERİĞİ bundan etkilenmiyor. Dışlamazsak
 *  sürüklemenin her karesinde tüm kart yeniden çizilir. */
const GEOMETRI = new Set(["x", "y", "width", "height"])

function ayniOge(a: PlanningItem, b: PlanningItem): boolean {
  if (a === b) return true
  const ak = Object.keys(a)
  if (ak.length !== Object.keys(b).length) return false
  for (const k of ak) {
    if (GEOMETRI.has(k)) continue
    if ((a as unknown as Record<string, unknown>)[k]
        !== (b as unknown as Record<string, unknown>)[k]) return false
  }
  return true
}

/** `memo` karşılaştırıcısı — node'ların ortak kuralı.
 *
 *  Callback'ler (onPatch/onRemove/…) BİLEREK karşılaştırılmıyor: davranışları
 *  sabittir, kimlikleri ise `nodes` her kurulduğunda değişir. Karşılaştırsaydık
 *  memo hiçbir zaman tutmazdı. Yeni bir davranış alanı eklenirse ve o alan
 *  render'ı etkiliyorsa BURAYA da eklenmeli. */
function nodeDataEsit(prev: NodeProps, next: NodeProps): boolean {
  if (prev.selected !== next.selected) return false
  const a = prev.data as NodeData
  const b = next.data as NodeData
  if (a.dim !== b.dim || a.locked !== b.locked || a.readOnly !== b.readOnly) return false
  if (a.boardKey !== b.boardKey) return false
  if (!!a.autoEdit !== !!b.autoEdit) return false
  // Palet yalnız BU node için açıksa fark yaratır; başka node'un paleti bizi
  // yeniden çizmemeli.
  if ((a.paletteFor === a.item.item_key) !== (b.paletteFor === b.item.item_key)) return false
  return ayniOge(a.item, b.item)
}

function ColorRow({ d }: { d: NodeData }) {
  if (d.paletteFor !== d.item.item_key) return null
  return (
    <div className="nodrag absolute right-0 bottom-7 z-10 flex flex-wrap gap-1 rounded-md border bg-popover p-1 shadow-md"
      style={{ width: 118 }}>
      {COLORS.map((c) => (
        <button key={c} type="button" title={c}
          onClick={(e) => { e.stopPropagation(); d.onPatch(d.item.item_key, { color: c }); d.setPaletteFor(null) }}
          className="h-5 w-5 rounded border" style={{ background: c }} />
      ))}
    </div>
  )
}

function IconBtn({ title, onClick, children, danger }: {
  title: string; onClick: (e: React.MouseEvent) => void
  children: React.ReactNode; danger?: boolean
}) {
  return (
    <button type="button" title={title} onClick={(e) => { e.stopPropagation(); onClick(e) }}
      className={cn("nodrag text-slate-500", danger ? "hover:text-destructive" : "hover:text-slate-900")}>
      {children}
    </button>
  )
}

/** Bağlantı tutamakları — dört kenarda.
 *
 *  2026-07-27 DÜZELTMESİ, okların çalışmamasının dört nedeni:
 *   1. Hepsi artık `type="source"` ve tuval `ConnectionMode.Loose` — eskiden
 *      sol/üst hedef, sağ/alt kaynaktı ve varsayılan Strict modda yalnız
 *      kaynak→hedef kabul ediliyordu; başka her kombinasyon SESSİZCE başarısızdı.
 *   2. `opacity-0` + 8 px = görünmez ve isabet edilemez hedef. Artık kalıcı
 *      soluk halka, node'a gelince belirginleşiyor.
 *   3. Tıklama alanı görünen daireden BÜYÜK (`::after` ile 20 px) — RF tutamağın
 *      kendi kutusunu dinler, görsel küçük kalsa da kavraması kolay olur.
 *   4. `RegionNode`'da tutamak HİÇ yoktu → bölgeler bağlanamıyordu.
 */
function Handles({ locked }: { locked?: boolean }) {
  if (locked) return null            // kilitli öğe bağlanamaz
  const base = "!h-3 !w-3 !border-2 !border-slate-400 !bg-background !opacity-40 "
             + "transition-all group-hover:!opacity-100 group-hover:!border-primary "
             + "hover:!scale-150 hover:!bg-primary"
  return (
    <>
      <Handle type="source" id="l" position={Position.Left} className={base} />
      <Handle type="source" id="r" position={Position.Right} className={base} />
      <Handle type="source" id="t" position={Position.Top} className={base} />
      <Handle type="source" id="b" position={Position.Bottom} className={base} />
    </>
  )
}

/** Kilit rozeti — kilitli öğede sol üstte. */
function LockBadge() {
  return (
    <span title="Kilitli — taşınamaz, boyutlandırılamaz, silinemez"
      className="absolute top-1 left-1 z-10 rounded bg-slate-700/80 p-0.5 text-white">
      <Lock className="h-2.5 w-2.5" />
    </span>
  )
}

/** Domain bağı rozeti — kart yüzünde, ilgili panel sayfasına götürür. */
function LinkBadge({ icon, text, href }: { icon: React.ReactNode; text: string; href?: string }) {
  const inner = (
    <span className="inline-flex max-w-full items-center gap-0.5 truncate rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] text-slate-700">
      {icon}<span className="truncate">{text}</span>
    </span>
  )
  if (!href) return inner
  return (
    <a href={href} className="nodrag max-w-full hover:underline"
      onClick={(e) => e.stopPropagation()}>{inner}</a>
  )
}

export const CardNode = memo(function CardNode({ data, selected }: NodeProps) {
  const d = data as NodeData
  const it = d.item
  const done = it.status === "done"
  return (
    <div className={cn("group relative h-full w-full overflow-hidden rounded-lg border border-slate-300/70 shadow-sm transition-opacity",
                       selected && "ring-2 ring-primary", done && "opacity-60", d.dim && "opacity-25")}
      style={{ background: it.color || "#e0e7ff" }}>
      <NodeResizer minWidth={120} minHeight={72} isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}

      <div className="flex items-start gap-1 px-2 pt-1.5">
        <button type="button" role="checkbox" aria-checked={done}
          title={done ? "Açık yap" : "Bitti işaretle"}
          onClick={(e) => { e.stopPropagation(); d.onPatch(it.item_key, { status: done ? "open" : "done" }) }}
          className={cn("nodrag mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                        done ? "border-emerald-600 bg-emerald-600 text-white" : "border-slate-400 bg-white/70")}>
          {done && <Check className="h-3 w-3" />}
        </button>
        <InlineText value={it.title} placeholder="Başlıksız" disabled={d.readOnly}
          className={cn("nodrag flex-1 text-[13px] leading-snug font-medium text-slate-800", done && "line-through")}
          onCommit={(v) => d.onPatch(it.item_key, { title: v })} autoEdit={d.autoEdit} onAutoEditDone={d.onAutoEditDone} />
      </div>

      {(it.label || it.due_date || it.assignee_name || it.client_name
        || it.shoot_title || it.campaign_title) && (
        <div className="flex flex-wrap items-center gap-1 px-2 pt-1">
          {it.label && (
            <span className="rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] font-medium text-slate-700">{it.label}</span>
          )}
          {it.due_date && (
            <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", dueTone(it.due_date, it.status))}>
              {fmtDay(it.due_date)}
            </span>
          )}
          {it.assignee_name && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-black/10 px-1.5 py-0.5 text-[10px] text-slate-700">
              <User className="h-2.5 w-2.5" />{it.assignee_name}
            </span>
          )}
          {it.client_name && (
            <LinkBadge icon={<Building2 className="h-2.5 w-2.5 shrink-0" />} text={it.client_name}
              href={it.client_id ? `/panel/musteriler?id=${it.client_id}` : undefined} />
          )}
          {it.shoot_title && (
            <LinkBadge icon={<Camera className="h-2.5 w-2.5 shrink-0" />} text={it.shoot_title} href="/panel/cekim-plani" />
          )}
          {it.campaign_title && (
            <LinkBadge icon={<Megaphone className="h-2.5 w-2.5 shrink-0" />} text={it.campaign_title} href="/panel/reklam" />
          )}
        </div>
      )}

      {it.text && <div className="line-clamp-2 px-2 pt-1 text-[11px] text-slate-600">{it.text}</div>}

      {!d.readOnly && (
        <div className="absolute right-1 bottom-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {it.link && (
            <a className="nodrag text-slate-500 hover:text-primary" href={it.link} target="_blank"
              rel="noreferrer" title={it.link} onClick={(e) => e.stopPropagation()}>
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          <IconBtn title="Ayrıntılar" onClick={() => d.onOpenDetail(it)}>
            <SlidersHorizontal className="h-3.5 w-3.5" />
          </IconBtn>
          <IconBtn title="Renk" onClick={() => d.setPaletteFor(d.paletteFor === it.item_key ? null : it.item_key)}>
            <Palette className="h-3.5 w-3.5" />
          </IconBtn>
          <IconBtn title="Sil" danger onClick={() => d.onRemove(it.item_key)}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconBtn>
        </div>
      )}
      <ColorRow d={d} />
    </div>
  )
}, nodeDataEsit)

export const NoteNode = memo(function NoteNode({ data, selected }: NodeProps) {
  const d = data as NodeData
  const it = d.item
  return (
    <div className={cn("group relative h-full w-full rounded-md p-2 shadow-sm transition-opacity",
                       selected && "ring-2 ring-primary", d.dim && "opacity-25")}
      style={{ background: it.color || "#fef9c3" }}>
      <NodeResizer minWidth={120} minHeight={80} isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}
      <InlineText value={it.text} placeholder="Not…" multiline disabled={d.readOnly}
        className="nodrag h-[calc(100%-1.5rem)] overflow-hidden text-[13px] text-slate-700"
        onCommit={(v) => d.onPatch(it.item_key, { text: v })} autoEdit={d.autoEdit} onAutoEditDone={d.onAutoEditDone} />
      {!d.readOnly && (
        <div className="absolute right-1 bottom-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <IconBtn title="Renk" onClick={() => d.setPaletteFor(d.paletteFor === it.item_key ? null : it.item_key)}>
            <Palette className="h-3.5 w-3.5" />
          </IconBtn>
          <IconBtn title="Sil" danger onClick={() => d.onRemove(it.item_key)}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconBtn>
        </div>
      )}
      <ColorRow d={d} />
    </div>
  )
}, nodeDataEsit)

export const RegionNode = memo(function RegionNode({ data, selected }: NodeProps) {
  const d = data as NodeData
  const it = d.item
  return (
    <div className={cn("group relative h-full w-full rounded-xl border-2 border-dashed transition-opacity",
                       selected && "ring-2 ring-primary", d.dim && "opacity-25")}
      style={{ background: (it.color || "#f1f5f9") + "88", borderColor: "rgba(100,116,139,.35)" }}>
      <NodeResizer minWidth={200} minHeight={160} isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}
      <div className="flex items-center gap-1 px-2 py-1">
        <InlineText value={it.title} placeholder="Bölge" disabled={d.readOnly}
          className="nodrag flex-1 text-xs font-semibold text-slate-600"
          onCommit={(v) => d.onPatch(it.item_key, { title: v })} autoEdit={d.autoEdit} onAutoEditDone={d.onAutoEditDone} />
        {!d.readOnly && (
          <>
            {/* Renk düğmesi kart ve notta vardı, bölgede YOKTU — bölgenin rengini
                değiştirmenin tek yolu çoklu seçim çubuğuydu. */}
            <IconBtn title="Renk"
              onClick={() => d.setPaletteFor(d.paletteFor === it.item_key ? null : it.item_key)}>
              <Palette className="h-3.5 w-3.5" />
            </IconBtn>
            <IconBtn title="Bölgeyi sil" danger onClick={() => d.onRemove(it.item_key)}>
              <Trash2 className="h-3.5 w-3.5" />
            </IconBtn>
          </>
        )}
      </div>
      <ColorRow d={d} />
    </div>
  )
}, nodeDataEsit)

/** Panoya yapıştırılan/sürüklenen görsel.
 *
 *  Dosya sunucuda (`planning_images`), öğe ona `extra.image` ile işaret eder.
 *  `extra.image` henüz yoksa yükleme sürüyordur — öğe hemen çizilir ki kullanıcı
 *  nereye düştüğünü görsün, görsel gelince yerini alır (iyimser yerleştirme).
 *  Boyutlandırma `keepAspectRatio`: fotoğrafın oranını bozmak istenmiyor. */
export const ImageNode = memo(function ImageNode({ data, selected }: NodeProps) {
  const d = data as NodeData
  const it = d.item
  const img = imageOf(it)
  const failed = it.extra?.image_error === true
  return (
    <div className={cn("group relative h-full w-full overflow-hidden rounded-lg border bg-background shadow-sm transition-opacity",
                       selected && "ring-2 ring-primary", d.dim && "opacity-25")}>
      <NodeResizer minWidth={60} minHeight={60} keepAspectRatio
        isVisible={!!selected && !d.readOnly && !d.locked}
        lineClassName="!border-primary/40" handleClassName="!h-2 !w-2 !border-primary !bg-white" />
      <Handles locked={d.locked} />
      {d.locked && <LockBadge />}

      {img ? (
        <img src={planningImageUrl(d.boardKey, img.name)} alt={it.title || "Pano görseli"}
          draggable={false}
          className="h-full w-full object-contain select-none" />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 bg-muted/40 text-muted-foreground">
          {failed ? <ImageOff className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
          <span className="text-[11px]">{failed ? "Yüklenemedi" : "Yükleniyor…"}</span>
        </div>
      )}

      {/* Başlık yalnız doluysa veya hover'da yer kaplar — görselin önünü kesmesin. */}
      {(it.title || !d.readOnly) && (
        <div className={cn("absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent px-1.5 pt-4 pb-1",
                           !it.title && "opacity-0 transition-opacity group-hover:opacity-100")}>
          <InlineText value={it.title} placeholder="Başlık…" disabled={d.readOnly}
            className="nodrag text-[11px] font-medium text-white drop-shadow"
            onCommit={(v) => d.onPatch(it.item_key, { title: v })} />
        </div>
      )}

      {!d.readOnly && (
        <div className="absolute top-1 right-1 flex items-center gap-1 rounded bg-background/80 px-1 opacity-0 transition-opacity group-hover:opacity-100">
          {img && (
            <IconBtn title="Yeni sekmede aç"
              onClick={() => window.open(planningImageUrl(d.boardKey, img.name), "_blank", "noopener")}>
              <ExternalLink className="h-3.5 w-3.5" />
            </IconBtn>
          )}
          <IconBtn title="Sil" danger onClick={() => d.onRemove(it.item_key)}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconBtn>
        </div>
      )}
    </div>
  )
}, nodeDataEsit)
