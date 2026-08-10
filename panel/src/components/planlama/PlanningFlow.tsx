// Planlama tuvali — @xyflow/react (MIT) üzerinde.
//
// KONTROLLÜ MOD — `useNodesState` BİLEREK KULLANILMIYOR.
// RF dokümanları onu önerir ama o hook ÜÇÜNCÜ bir kopya dizi tutar. Onu kaynak
// yapmak 2026-07-25'te çözülen clobber hatasını geri getirir: sunucu refetch'i
// geldiğinde diziyi yeniden kurmak gerekir ve parmağın altındaki kart uçar.
// Kaynak tek: `usePlanningStore`. Buradaki `nodes` yalnız ondan TÜRETİLİR.
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react"
import {
  Background, ConnectionMode, Controls, MiniMap, Panel, ReactFlow,
  type Connection, type Edge, type EdgeChange, type FinalConnectionState,
  type Node, type NodeChange,
  MarkerType, useOnSelectionChange, useReactFlow,
} from "@xyflow/react"
import {
  AlignHorizontalJustifyCenter, AlignStartVertical, ArrowDownToLine, ArrowUpToLine,
  ArrowLeftRight, BoxSelect, BringToFront, ChevronLeft, ChevronRight, ClipboardPaste,
  Copy, Grid3x3,
  Group, Hand,
  ImagePlus,
  Keyboard, LayoutGrid, Lock, Maximize2, MousePointer2, Palette,
  Plus, Scan, SendToBack, SlidersHorizontal, SquareDashed, StickyNote, Tag, Trash2,
  Ungroup, Unlock, X,
} from "lucide-react"

import {
  ContextMenu, MenuItem, MenuSep, menuPosition, type MenuPos,
} from "@/components/planlama/BoardContextMenu"
import { PlanningEdge, type EdgeData } from "@/components/planlama/PlanningEdge"
import {
  CardNode, ImageNode, NoteNode, RegionNode, type NodeData,
} from "@/components/planlama/PlanningNodes"
import { ShortcutsHelp } from "@/components/planlama/ShortcutsHelp"
import {
  COLORS, DEFAULT_SIZE, IMAGE_MAX_BYTES, IMAGE_TYPES, imageBoxSize, isLocked,
  MAX_ZOOM, MIN_ZOOM, newItemKey, parentKeyOf, SNAP, snap8, uploadPlanningImage,
  type PlanningItem, type PlanningItemType,
} from "@/lib/planlama"
import {
  absBoxOf, alignCenterX, alignLeft, alignTop, boundsOf, boxOf, connectedEdgeKeys,
  distributeX, distributeY, gridLayout, PASTE_OFFSET, resolveHandles, storedHandle,
  zMoves, type ZMod,
} from "@/lib/planlama-layout"
import { toast } from "sonner"

import type { ItemPatch } from "@/lib/usePlanningStore"
import { cn } from "@/lib/utils"

import "@xyflow/react/dist/style.css"

// MODÜL DÜZEYİNDE ve SABİT: her render'da yeni nesne verilirse RF bütün node'ları
// yeniden mount eder ve yazarken odak kaybolur. Aynı kural `edgeTypes` ve
// `defaultEdgeOptions` için de geçerli.
const NODE_TYPES = { card: CardNode, note: NoteNode, region: RegionNode, image: ImageNode } as const
const EDGE_TYPES = { planning: PlanningEdge } as const
const DEFAULT_EDGE_OPTIONS = {
  type: "planning",
  // Görünen çizgi 2 px; tıklanabilir şerit bundan KALIN olmalı yoksa oku
  // seçmek/sağ tıklamak piksel avına dönüşür (RF varsayılanı 20).
  interactionWidth: 26,
  markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b", width: 18, height: 18 },
  style: { stroke: "#64748b", strokeWidth: 2 },
} as const
const SNAP_GRID: [number, number] = [SNAP, SNAP]

// Bölgeler bu taban değerin üstüne kendi `z`'lerini ekler → hepsi kartların
// arkasında kalır ama kendi aralarında sıralanabilirler.
const REGION_Z = -1000

const GROUP_PAD = 32          // grup bölgesinin içeriğe bıraktığı boşluk
const GROUP_HEADER = 28       // bölge başlığı yüksekliği

/** Araç çubuğu (sayfa seviyesinde) tuvale bu arayüzden emir verir.
 *  Ekleme buradan geçmek ZORUNDA: yeni öğenin nereye düşeceği React Flow'un
 *  viewport dönüşümüne bağlı ve o bilgi yalnız provider'ın İÇİNDE var. */
export interface PlanningFlowHandle {
  addItem: (type: PlanningItemType) => void
}

interface Props {
  boardKey: string
  items: PlanningItem[]
  byKey: Map<string, PlanningItem>
  matches: (it: PlanningItem) => boolean
  filterActive: boolean
  applyLocal: (patches: ItemPatch[]) => void
  commit: (patches: ItemPatch[]) => void
  remove: (keys: string[]) => void
  onPatch: (key: string, patch: Partial<PlanningItem>) => void
  onOpenDetail: (item: PlanningItem) => void
  readOnly?: boolean
}

function sizeOf(it: PlanningItem) {
  const d = DEFAULT_SIZE[it.type] || DEFAULT_SIZE.card
  return { width: it.width || d.width, height: it.height || d.height }
}

type Menu =
  | { kind: "node"; pos: MenuPos; key: string }
  | { kind: "selection"; pos: MenuPos }
  | { kind: "edge"; pos: MenuPos; key: string }
  | { kind: "pane"; pos: MenuPos; flow: { x: number; y: number } }

export const PlanningFlow = forwardRef<PlanningFlowHandle, Props>(function PlanningFlow({
  boardKey, items, byKey, matches, filterActive, applyLocal, commit, remove,
  onPatch, onOpenDetail, readOnly,
}: Props, ref) {
  const rf = useReactFlow()
  const [paletteFor, setPaletteFor] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  // Kenar seçimi de node'lardaki mantıkla AYNI: RF `selected`'ı bizim kenar
  // nesnemizden okur, yazmazsak öğe değişiminde seçim düşer (ve okun üstündeki
  // silme düğmesi kaybolur).
  const [selectedEdges, setSelectedEdges] = useState<string[]>([])
  // Okun üstüne gelince araç şeridi çıksın — kartlarda olduğu gibi. Seçmeyi
  // zorunlu tutmak, silmeyi gereksiz yere iki adıma çıkarıyordu.
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null)
  // Okun etiketini çift tıkla düzenleme (kartlardaki `autoEdit` ile aynı desen).
  const [edgeAutoEdit, setEdgeAutoEdit] = useState<string | null>(null)
  const clearEdgeAutoEdit = useCallback(() => setEdgeAutoEdit(null), [])
  const [menu, setMenu] = useState<Menu | null>(null)
  const clipboard = useRef<PlanningItem[]>([])
  const wrapper = useRef<HTMLDivElement | null>(null)

  /** Seç / Kaydır — Figma'daki V/H. Dokunmatikte `selectionOnDrag` ile tek parmak
   *  sürüklemenin hangisini yapacağı kaynak okunarak KESİNLEŞMİYOR (d3-zoom filtresi
   *  `touchstart`'ı geçiriyor, Pane'in seçim işleyicisi de `button===0` görüyor).
   *  Tahmin etmek yerine kullanıcıya açık bir anahtar veriyoruz. */
  const [mode, setMode] = useState<"select" | "pan">("select")
  const [helpOpen, setHelpOpen] = useState(false)
  /** Yeni eklenen öğe: seçili gelsin ve başlığı düzenlemeye açılsın. */
  const [autoEditKey, setAutoEditKey] = useState<string | null>(null)
  const clearAutoEdit = useCallback(() => setAutoEditKey(null), [])

  /** SEÇİM BİZDE TUTULUR — React Flow'a `selected` alanıyla geri verilir.
   *
   *  Neden: `adoptUserNodes` içeriden `internalNode = {...userNode}` kuruyor, yani
   *  `selected` BİZİM node nesnemizden okunuyor. Biz yazmadığımız için, `nodes`
   *  dizisi her yeniden kurulduğunda (bir öğe değişince, sürüklemede her karede)
   *  seçim sessizce düşüyordu. Yalnız hiçbir şey değişmediğinde ayakta kalıyordu,
   *  çünkü RF aynı referanslı diziyi görüp (`checkEquality`) yeniden adopte etmiyor.
   *  Aynı alan "tümünü seç"in de tek uygulanabilir yolu. */
  useOnSelectionChange({
    onChange: useCallback(({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) => {
      // Aynı seçimi tekrar yazmak diziyi boşuna yeniden kurar (ve döngü riski).
      const ayni = (a: string[], b: string[]) =>
        a.length === b.length && a.every((k, i) => k === b[i])
      const nk = nodes.map((n) => n.id)
      const ek = edges.map((e) => e.id)
      setSelected((onceki) => (ayni(onceki, nk) ? onceki : nk))
      setSelectedEdges((onceki) => (ayni(onceki, ek) ? onceki : ek))
    }, []),
  })

  const selectedSet = useMemo(() => new Set(selected), [selected])
  const selectedEdgeSet = useMemo(() => new Set(selectedEdges), [selectedEdges])

  // --- model → RF -----------------------------------------------------------

  /** EBEVEYN ÖNCE: RF "parent nodes must appear before their children in the
   *  nodes array" diyor; aksi halde çocuğun konumu ilk karede yanlış hesaplanır. */
  const ordered = useMemo(() => {
    const nonEdge = items.filter((it) => it.type !== "edge")
    return [...nonEdge.filter((it) => !parentKeyOf(it)),
            ...nonEdge.filter((it) => parentKeyOf(it))]
  }, [items])

  // --- silme (öksüz bırakmadan) ---------------------------------------------

  /** TEK SİLME YOLU — öksüz bırakmadan.
   *
   *  İki tür artık bırakılabilir, ikisi de burada kapatılıyor:
   *
   *  1. **Bölgenin çocukları:** önce bağları çözülür (mutlak koordinata
   *     döndürülür), sonra bölge silinir. Aksi halde `parent_key` var olmayan bir
   *     bölgeyi gösterir ve öğe 0,0'a yapışır.
   *  2. **Bağlı oklar (2026-08-09):** kart silinince okları geride kalıyordu. RF'in
   *     KENDİ silme yolu (Delete tuşu) bağlı kenarları da kaldırır ama panelin
   *     kendi yolları (çöp kutusu, menü, toplu silme) RF'e uğramaz. Öksüz ok DB'de
   *     görünmez bir satır olarak kalır ve RF her render'da `error008` basar.
   *     Canlıda 3 böyle satır bulundu.
   *
   *  Kart üstündeki çöp kutusu da BURAYA bağlı: eskiden ham `remove`'u çağırıyordu,
   *  yani bir bölgeyi çöp kutusundan silmek tam olarak (1)'deki hatayı üretiyordu —
   *  cascade'in var oluş sebebini. */
  const removeCascade = useCallback((keys: string[]) => {
    if (!keys.length) return
    const set = new Set(keys)
    const freed: ItemPatch[] = []
    for (const it of items) {
      const pk = parentKeyOf(it)
      if (!pk || !set.has(pk) || set.has(it.item_key)) continue
      const parent = byKey.get(pk)
      if (!parent) continue
      freed.push({ item_key: it.item_key, x: snap8(parent.x + it.x),
                   y: snap8(parent.y + it.y), extra: { parent_key: null } })
    }
    if (freed.length) commit(freed)
    remove([...keys, ...connectedEdgeKeys(keys, items)])
  }, [items, byKey, commit, remove])

  /** Kilitli öğe silinemez — kilit rozeti bunu VAAT EDİYOR ("taşınamaz,
   *  boyutlandırılamaz, silinemez") ve node'un `deletable` alanı da öyle diyor;
   *  ama kart üstündeki çöp kutusu kilidi hiç kontrol etmiyordu. */
  const silinebilir = useCallback((keys: string[]) =>
    keys.filter((k) => { const it = byKey.get(k); return it && !isLocked(it) }),
    [byKey])

  const nodes = useMemo<Node[]>(() => ordered.map((it) => {
    const s = sizeOf(it)
    const locked = isLocked(it)
    // Ebeveyn silinmişse `parentId` verme — RF eksik ebeveynde patlar.
    const pk = parentKeyOf(it)
    const parentId = pk && byKey.has(pk) ? pk : undefined
    const data: NodeData = {
      item: it,
      dim: filterActive && !matches(it),
      locked,
      readOnly: !!readOnly,
      onPatch, onRemove: (k) => removeCascade(silinebilir([k])),
      onOpenDetail, paletteFor, setPaletteFor, boardKey,
      autoEdit: autoEditKey === it.item_key,
      onAutoEditDone: clearAutoEdit,
    }
    return {
      id: it.item_key,
      type: it.type,
      // Ebeveynli node'un konumu EBEVEYNE GÖRELİDİR (RF sözleşmesi) ve DB'de de
      // göreli saklanır — gruplarken/çözerken dönüştürülür.
      position: { x: it.x, y: it.y },
      width: s.width, height: s.height,
      // Bölge her zaman içeriğinin ARKASINDA kalır, ama kendi `z`'si yok
      // sayılmaz: sabit -1 iken üst üste binen iki bölge sıralanamıyordu ve
      // menüdeki "Öne getir" bölgede sessizce hiçbir şey yapmıyordu.
      zIndex: it.type === "region" ? REGION_Z + (it.z || 0) : (it.z || 0),
      draggable: !readOnly && !locked,
      deletable: !readOnly && !locked,
      connectable: !readOnly && !locked,
      selectable: true,
      selected: selectedSet.has(it.item_key),
      ...(parentId ? { parentId, extent: "parent" as const } : {}),
      data,
    }
  }), [ordered, byKey, filterActive, matches, readOnly, onPatch, removeCascade,
       silinebilir, onOpenDetail, paletteFor, boardKey, autoEditKey, clearAutoEdit,
       selectedSet])

  /** Okun yönünü ters çevir — uçları ve saklanan tutamakları birlikte takas eder.
   *  Sadece from/to takas edilseydi, saklanan tutamaklar okun eski yönüne ait
   *  kalır ve ok kartın etrafından dolanırdı. */
  const reverseEdge = useCallback((key: string) => {
    if (readOnly) return
    const it = byKey.get(key)
    if (!it || !it.from_key || !it.to_key) return
    commit([{
      item_key: key,
      from_key: it.to_key, to_key: it.from_key,
      extra: {
        from_handle: storedHandle(it.extra, "to_handle"),
        to_handle: storedHandle(it.extra, "from_handle"),
      },
    }])
  }, [readOnly, byKey, commit])

  const edges = useMemo<Edge[]>(() => items
    // ÖKSÜZ OK SÜZGECİ (savunma): uçlarından biri artık yoksa RF'e VERME — yoksa
    // her render'da `error008` basar. `removeCascade` artık bunları üretmiyor ama
    // geçmişten kalan satırlar var (canlıda 3 tane) ve el değmiş panolar olabilir.
    .filter((it) => it.type === "edge" && it.from_key && it.to_key
      && byKey.has(it.from_key) && byKey.has(it.to_key))
    .map((it) => {
      const data: EdgeData = {
        item: it,
        readOnly: !!readOnly,
        hovered: hoveredEdge === it.item_key,
        autoEdit: edgeAutoEdit === it.item_key,
        onPatch,
        onRemove: (k) => remove([k]),
        onReverse: reverseEdge,
        onAutoEditDone: clearEdgeAutoEdit,
      }
      // TUTAMAKLAR: kullanıcının seçtiği öncelikli, geometri yedek.
      // Kenara `sourceHandle`/`targetHandle` verilmezse RF node'un İLK tutamağına
      // düşer (bizde `l` = sol) → sağdaki bir karta çekilen ok bile soldan çıkıp
      // dolanıyordu. Seçim `extra`da saklanıyor; tutamak bilgisi taşımayan ESKİ
      // oklarda ve seçim hedefe ters düştüğünde `bestHandles` devreye girer.
      const s = byKey.get(it.from_key as string)
      const t = byKey.get(it.to_key as string)
      const [sh, th] = s && t
        ? resolveHandles(absBoxOf(s, byKey), absBoxOf(t, byKey), {
            from: storedHandle(it.extra, "from_handle"),
            to: storedHandle(it.extra, "to_handle"),
          })
        : (["r", "l"] as const)
      return {
        id: it.item_key,
        source: it.from_key as string,
        target: it.to_key as string,
        sourceHandle: sh,
        targetHandle: th,
        selected: selectedEdgeSet.has(it.item_key),
        // Görsel/marker `DEFAULT_EDGE_OPTIONS`tan gelir — burada tekrar etmiyoruz,
        // yoksa iki ayrı gerçek kaynağı olur.
        deletable: !readOnly,
        data,
      }
    }), [items, byKey, readOnly, onPatch, remove, selectedEdgeSet, hoveredEdge,
         edgeAutoEdit, reverseEdge, clearEdgeAutoEdit])

  // --- RF → model -----------------------------------------------------------

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    if (readOnly) return
    const live: ItemPatch[] = []
    const done: ItemPatch[] = []
    const removed: string[] = []
    for (const c of changes) {
      if (c.type === "position" && c.position) {
        // `dragging` UNDEFINED = programatik (fitView vb.) — sunucuya yazma.
        if (c.dragging === true) {
          live.push({ item_key: c.id, x: Math.round(c.position.x), y: Math.round(c.position.y) })
        } else if (c.dragging === false) {
          done.push({ item_key: c.id, x: snap8(c.position.x), y: snap8(c.position.y) })
        }
      } else if (c.type === "dimensions" && c.dimensions) {
        // `resizing` UNDEFINED = RF'in İLK ÖLÇÜMÜ, kullanıcı eylemi değil.
        if (c.resizing === true) {
          live.push({ item_key: c.id, width: Math.round(c.dimensions.width),
                      height: Math.round(c.dimensions.height) })
        } else if (c.resizing === false) {
          done.push({ item_key: c.id, width: Math.round(c.dimensions.width),
                      height: Math.round(c.dimensions.height) })
        }
      } else if (c.type === "remove") {
        removed.push(c.id)
      }
    }
    if (live.length) applyLocal(live)
    if (done.length) commit(done)
    if (removed.length) removeCascade(removed)
  }, [readOnly, applyLocal, commit, removeCascade])

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (readOnly) return
    const removed = changes.filter((c) => c.type === "remove").map((c) => c.id)
    if (removed.length) remove(removed)
  }, [readOnly, remove])

  const onConnect = useCallback((c: Connection) => {
    if (readOnly || !c.source || !c.target || c.source === c.target) return
    // Aynı çift arasında ikinci ok çizilmesin.
    if (items.some((i) => i.type === "edge" && i.from_key === c.source && i.to_key === c.target)) return
    commit([{
      item_key: newItemKey(), type: "edge",
      from_key: c.source, to_key: c.target,
      // Kullanıcının GERÇEKTEN çektiği tutamaklar. Eskiden atılıyordu; kenara
      // tutamak yazılmayınca RF ilk tutamağa (`l`) düşüyor ve ok soldan dolanıyordu.
      // Saklanan değer `resolveHandles`ta önceliklidir (ters düşerse yok sayılır).
      extra: { from_handle: c.sourceHandle || null, to_handle: c.targetHandle || null },
      x: 0, y: 0, status: "open",
    }])
  }, [readOnly, items, commit])

  /** Okun UCUNDAN tutup başka bir öğeye taşıma (2026-08-09).
   *
   *  Tutamakları React Flow'un `EdgeWrapper`'ı kendisi çiziyor — `onReconnect`
   *  verilmesi yeterli, özel kenar bileşenimize (`PlanningEdge`) dokunmak
   *  gerekmiyor. Daireler uç noktadan DIŞARI kaydırılıyor (`shiftX`), o yüzden
   *  node'un kendi `Handle`'larıyla çakışmıyorlar.
   *
   *  Boşluğa bırakılırsa RF `setReconnecting(false)` ile oku KENDİSİ geri getirir;
   *  biz sessizce silmiyoruz — sürpriz olurdu ve silmek için okun üstünde zaten
   *  çöp kutusu var.
   *
   *  NOT (kabul edilen kenar durum): geri alma `from_key`/`to_key`'i doğru geri
   *  yükler ama sunucu `extra`yı shallow-merge ettiği için, tutamak bilgisi HİÇ
   *  olmayan eski bir okta yeni yazılan tutamak geri almadan sonra da kalır.
   *  Görsel etkisi sınırlı: `resolveHandles` hedefe ters düşen tutamağı zaten
   *  yok sayıyor. */
  const onReconnect = useCallback((oldEdge: Edge, c: Connection) => {
    if (readOnly || !c.source || !c.target) return
    if (c.source === c.target) {
      toast.error("Bir öğe kendine bağlanamaz.")
      return
    }
    if (items.some((i) => i.type === "edge" && i.item_key !== oldEdge.id
        && i.from_key === c.source && i.to_key === c.target)) {
      toast.error("Bu iki öğe arasında zaten bir ok var.")
      return
    }
    commit([{
      item_key: oldEdge.id,
      from_key: c.source, to_key: c.target,
      extra: { from_handle: c.sourceHandle || null, to_handle: c.targetHandle || null },
    }])
  }, [readOnly, items, commit])

  /** Tutamaktan çekip BOŞ tuvale bırakınca: bırakılan yerde yeni kart + ok.
   *  Akış tabanlı planlamada en çok beklenen hareket; öncesinde bağlantı boşluğa
   *  bırakılınca hiçbir şey olmuyordu. */
  const onConnectEnd = useCallback((_event: MouseEvent | TouchEvent,
                                    state: FinalConnectionState) => {
    if (readOnly || state.isValid) return       // geçerli hedefe düştüyse onConnect halletti
    const from = state.fromNode?.id
    if (!from || !state.from || !state.to) return
    // TUTAMAĞA SADECE TIKLAMAK KART YARATMASIN. RF bağlantıyı pointerdown'da başlatıp
    // pointerup'ta bitirir, yani düz bir tıklama da buraya düşer ve `isValid` o
    // durumda `false` değil `null` olur — tek başına yeterli bir ayıraç değil.
    // Ayıraç mesafe: kullanıcı gerçekten çekmiş mi?
    if (Math.hypot(state.to.x - state.from.x, state.to.y - state.from.y) < 60) return
    // `from`/`to` zaten AKIŞ koordinatı — ekran dönüşümüne gerek yok.
    const pos = state.to
    const d = DEFAULT_SIZE.card
    const key = newItemKey()
    commit([
      { item_key: key, type: "card", title: "Yeni kart",
        color: COLORS[items.length % COLORS.length],
        x: snap8(pos.x - d.width / 2), y: snap8(pos.y - d.height / 2),
        width: d.width, height: d.height, z: 0, status: "open" },
      // Hedef kart bu hareketle DOĞUYOR; hedef tutamağı diye bir seçim yok,
      // o taraf geometriden hesaplanır.
      { item_key: newItemKey(), type: "edge", from_key: from, to_key: key,
        extra: { from_handle: state.fromHandle?.id || null, to_handle: null },
        x: 0, y: 0, status: "open" },
    ])
    setAutoEditKey(key)
  }, [readOnly, commit, items.length])

  // --- seçim ----------------------------------------------------------------

  const selItems = useMemo(
    () => selected.map((k) => byKey.get(k)).filter(Boolean) as PlanningItem[],
    [selected, byKey])
  const selBoxes = useMemo(() => selItems.filter((it) => it.type !== "edge"), [selItems])
  const selRegions = useMemo(() => selBoxes.filter((it) => it.type === "region"), [selBoxes])

  const bulk = useCallback((patch: Partial<PlanningItem>) => {
    if (!selBoxes.length) return
    commit(selBoxes.map((it) => ({ item_key: it.item_key, ...patch })))
  }, [selBoxes, commit])

  /** Katman sırası. Dört mod: bir adım öne/arkaya, ve en öne/en arkaya.
   *
   *  Mutlak modlarda seçimin KENDİ İÇİNDEKİ sırası korunur (mevcut z'ye göre
   *  sıralanıp ardışık değer verilir) — hepsine aynı z verilseydi üst üste binen
   *  kartların birbirine göre düzeni her "en öne" ile bozulurdu. */
  const zSirala = useCallback((liste: PlanningItem[], mod: ZMod) => {
    const moves = zMoves(liste, items.filter((i) => i.type !== "edge"), mod)
    if (moves.length) commit(moves)
  }, [items, commit])

  const layout = useCallback((fn: (b: ReturnType<typeof boxOf>[]) => { item_key: string; x: number; y: number }[]) => {
    const moves = fn(selBoxes.map(boxOf))
    if (moves.length) commit(moves)
  }, [selBoxes, commit])

  const duplicate = useCallback((source: PlanningItem[], dx = PASTE_OFFSET, dy = PASTE_OFFSET) => {
    const fresh = source.filter((it) => it.type !== "edge").map((it) => ({
      ...it, item_key: newItemKey(),
      x: snap8(it.x + dx), y: snap8(it.y + dy),
      // Kopya gruba bağlı doğmasın; sunucu türevi alanlar da taşınmasın.
      extra: { ...(it.extra || {}), parent_key: null },
      rev: undefined, updated_at: undefined, updated_by: undefined,
    }))
    if (fresh.length) commit(fresh as ItemPatch[])
  }, [commit])

  // --- gruplama -------------------------------------------------------------

  /** Seçimi bir bölgenin içine al. Çocukların konumu EBEVEYNE GÖRELİ hale
   *  çevrilir — RF `parentId` verilen node'un x/y'sini böyle yorumlar. */
  const group = useCallback(() => {
    const kids = selBoxes.filter((it) => it.type !== "region" && !parentKeyOf(it))
    if (kids.length < 2) return
    const b = boundsOf(kids.map(boxOf))
    const rx = snap8(b.x - GROUP_PAD)
    const ry = snap8(b.y - GROUP_PAD - GROUP_HEADER)
    const regionKey = newItemKey()
    commit([
      { item_key: regionKey, type: "region", title: "Grup", x: rx, y: ry,
        width: Math.round(b.width + GROUP_PAD * 2),
        height: Math.round(b.height + GROUP_PAD * 2 + GROUP_HEADER),
        z: -1, color: "#f1f5f9", status: "open" },
      ...kids.map((it) => ({
        item_key: it.item_key,
        x: Math.round(it.x - rx), y: Math.round(it.y - ry),
        extra: { parent_key: regionKey },
      })),
    ])
  }, [selBoxes, commit])

  /** Grubu çöz: çocukları mutlak koordinata döndür, bölge kalsın (silinmez). */
  const ungroup = useCallback((regionKeys: string[]) => {
    const set = new Set(regionKeys)
    const freed: ItemPatch[] = []
    for (const it of items) {
      const pk = parentKeyOf(it)
      if (!pk || !set.has(pk)) continue
      const parent = byKey.get(pk)
      if (!parent) continue
      freed.push({ item_key: it.item_key, x: snap8(parent.x + it.x),
                   y: snap8(parent.y + it.y), extra: { parent_key: null } })
    }
    if (freed.length) commit(freed)
  }, [items, byKey, commit])

  const hasChildren = useCallback(
    (key: string) => items.some((it) => parentKeyOf(it) === key), [items])

  const setLock = useCallback((list: PlanningItem[], locked: boolean) => {
    if (!list.length) return
    // `null` göndermek anahtarı SİLER (sunucu extra'yı shallow-merge ediyor).
    commit(list.map((it) => ({ item_key: it.item_key, extra: { locked: locked || null } })))
  }, [commit])

  // --- klavye ---------------------------------------------------------------

  /** Kutu (kenar olmayan) tüm öğeleri seç. Seçim bizde tutulduğu için (bkz.
   *  `useOnSelectionChange` yorumu) tek yapmamız gereken listeyi yazmak. */
  const selectAll = useCallback(() => {
    setSelected(items.filter((it) => it.type !== "edge").map((it) => it.item_key))
  }, [items])

  const zoomToSelection = useCallback(() => {
    if (!selected.length) return
    void rf.fitView({ nodes: selected.map((id) => ({ id })), padding: 0.3, duration: 300, maxZoom: 1.5 })
  }, [rf, selected])

  // --- filtre eşleşmelerinde gezinme ---------------------------------------
  // Filtre eşleşmeyeni soluklaştırıyordu ama eşleşme görünen alanın dışındaysa
  // kullanıcı boş tuvale bakıyordu. Artık sırayla üstlerine gidilebiliyor.
  const matchKeys = useMemo(
    () => (filterActive
      ? items.filter((it) => it.type !== "edge" && matches(it)).map((it) => it.item_key)
      : []),
    [items, filterActive, matches])
  const [matchIdx, setMatchIdx] = useState(0)
  useEffect(() => { setMatchIdx(0) }, [filterActive, matchKeys.length])

  const goMatch = useCallback((delta: number) => {
    if (!matchKeys.length) return
    const next = (matchIdx + delta + matchKeys.length) % matchKeys.length
    setMatchIdx(next)
    setSelected([matchKeys[next]])
    // `fitView` tek node ile: ebeveynli öğelerde x/y GÖRELİ olduğu için `setCenter`
    // yanlış yere giderdi; fitView mutlak konumu kendisi çözüyor.
    void rf.fitView({ nodes: [{ id: matchKeys[next] }], padding: 0.6, duration: 300, maxZoom: 1.2 })
  }, [matchKeys, matchIdx, rf])

  // Kısayollar. `readOnly` panoda da yardım (?) ve mod (V/H) çalışmalı — yalnız
  // DEĞİŞTİREN kısayollar kapatılır.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return

      if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === "?") { setHelpOpen((v) => !v); e.preventDefault(); return }
        if (e.key === "Escape") { setHelpOpen(false); return }
        const tek = e.key.toLowerCase()
        if (tek === "v") { setMode("select"); return }
        if (tek === "h") { setMode("pan"); return }
        return
      }
      if (!(e.ctrlKey || e.metaKey)) return
      const k = e.key.toLowerCase()
      if (k === "a") { selectAll(); e.preventDefault(); return }
      if (readOnly) return
      if (k === "c" && selBoxes.length) { clipboard.current = selBoxes; e.preventDefault() }
      else if (k === "v" && clipboard.current.length) { duplicate(clipboard.current); e.preventDefault() }
      else if (k === "d" && selBoxes.length) { duplicate(selBoxes); e.preventDefault() }
      else if (k === "g" && selBoxes.length > 1) { group(); e.preventDefault() }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [selBoxes, duplicate, group, readOnly, selectAll])

  // --- sağ tuş --------------------------------------------------------------

  const openNodeMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault()
    const r = wrapper.current?.getBoundingClientRect(); if (!r) return
    // Çoklu seçim varsa ve tıklanan da seçiliyse SEÇİM menüsü aç.
    if (selected.length > 1 && selected.includes(node.id)) {
      setMenu({ kind: "selection", pos: menuPosition(e, r) })
    } else {
      setMenu({ kind: "node", pos: menuPosition(e, r), key: node.id })
    }
  }, [selected])

  const openPaneMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault()
    const r = wrapper.current?.getBoundingClientRect(); if (!r) return
    setMenu({
      kind: "pane", pos: menuPosition(e, r),
      flow: rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
    })
  }, [rf])

  const addAt = useCallback((type: PlanningItemType, x: number, y: number) => {
    const d = DEFAULT_SIZE[type]
    const key = newItemKey()
    commit([{
      item_key: key, type,
      title: type === "region" ? "Bölge" : type === "card" ? "Yeni kart" : null,
      text: type === "note" ? "Not…" : null,
      color: type === "region" ? "#f1f5f9" : COLORS[items.length % COLORS.length],
      x: snap8(x), y: snap8(y), width: d.width, height: d.height,
      z: type === "region" ? -1 : 0, status: "open",
    }])
    // Yeni öğe düzenlemeye açık gelsin: "Yeni kart" yazısını silmek için ayrıca
    // çift tıklamak gerekmesin.
    setAutoEditKey(key)
    return key
  }, [commit, items.length])

  /** Araç çubuğundan ekleme — GÖRÜNEN alanın merkezine.
   *
   *  ESKİ DAVRANIŞ (hata): sayfa bileşeni öğeyi `maxX + 260`'a koyuyordu, yani en
   *  sağdaki öğenin sağına; kamera oynamadığı için `fitView` yapılmış bir panoda
   *  "Kart" düğmesi HİÇBİR ŞEY YAPMAMIŞ gibi görünüyordu. Viewport dönüşümü yalnız
   *  provider'ın içinde bilindiğinden hesap buraya taşındı. */
  const addItemAtCenter = useCallback((type: PlanningItemType) => {
    if (readOnly) return
    const r = wrapper.current?.getBoundingClientRect()
    const d = DEFAULT_SIZE[type]
    const merkez = r
      ? rf.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 })
      : { x: 0, y: 0 }
    addAt(type, merkez.x - d.width / 2, merkez.y - d.height / 2)
  }, [addAt, readOnly, rf])

  useImperativeHandle(ref, () => ({ addItem: addItemAtCenter }), [addItemAtCenter])

  // --- görsel ekleme (yapıştır · sürükle-bırak · menü) -----------------------
  // Öğe ÖNCE iyimser olarak eklenir (kullanıcı nereye düştüğünü görsün), dosya
  // arkasında yüklenir, dönen `{name,w,h}` `extra.image`'a yazılır. Yükleme ile
  // öğe yazımı ayrı olduğu için normal delta akışı (undo, çakışma, offline
  // kuyruk) bozulmadan çalışır.
  const fileInput = useRef<HTMLInputElement | null>(null)
  // Menü kapandıktan sonra da geçerli olsun diye ayrı ref: dosya seçici
  // asenkron açılır, o sırada `menu` state'i çoktan null olur.
  const menuFlowPos = useRef<{ x: number; y: number } | null>(null)
  // Yapıştırma olayında imleç koordinatı YOKTUR; son bilinen konumu takip
  // ediyoruz ki görsel farenin durduğu yere düşsün (ref → render tetiklemez).
  const pointer = useRef<{ x: number; y: number } | null>(null)

  const addImages = useCallback(async (files: File[], at?: { x: number; y: number }) => {
    if (readOnly) return
    const ok = files.filter((f) => IMAGE_TYPES.includes(f.type))
    if (!ok.length) {
      toast.error("Yalnız PNG, JPEG, GIF ve WEBP görseller eklenebilir")
      return
    }
    const origin = at ?? (pointer.current
      ? rf.screenToFlowPosition(pointer.current)
      : rf.screenToFlowPosition({
          x: (wrapper.current?.getBoundingClientRect().left ?? 0) + 200,
          y: (wrapper.current?.getBoundingClientRect().top ?? 0) + 160,
        }))
    for (const [i, file] of ok.entries()) {
      if (file.size > IMAGE_MAX_BYTES) {
        toast.error(`${file.name || "Görsel"} 10 MB sınırını aşıyor`)
        continue
      }
      const key = newItemKey()
      const d = DEFAULT_SIZE.image
      commit([{
        item_key: key, type: "image",
        x: snap8(origin.x + i * 24), y: snap8(origin.y + i * 24),
        width: d.width, height: d.height, z: 0, status: "open",
      }])
      try {
        const img = await uploadPlanningImage(boardKey, file)
        commit([{ item_key: key, extra: { image: img, image_error: null },
                  ...imageBoxSize(img.width, img.height) }])
      } catch (err) {
        // Öğe SİLİNMEZ: kullanıcı "yüklenemedi" kutusunu görüp kendi kaldırsın —
        // sessizce yok olan bir kutu "yapıştırma çalışmıyor" gibi okunurdu.
        commit([{ item_key: key, extra: { image_error: true } }])
        toast.error(err instanceof Error ? err.message : "Görsel yüklenemedi")
      }
    }
  }, [boardKey, commit, readOnly, rf])

  // Pano yapıştırma. Görsel YOKSA olaya dokunulmaz → mevcut Ctrl+V (öğe
  // çoğaltma) ve metin yapıştırma olduğu gibi çalışmaya devam eder.
  useEffect(() => {
    if (readOnly) return
    function onPaste(e: ClipboardEvent) {
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      const files = Array.from(e.clipboardData?.files || [])
        .filter((f) => f.type.startsWith("image/"))
      if (!files.length) return
      e.preventDefault()
      void addImages(files)
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [addImages, readOnly])

  const closeMenu = useCallback(() => setMenu(null), [])
  const act = (fn: () => void) => () => { fn(); closeMenu() }

  const multi = selBoxes.length > 1
  const mi = menu?.kind === "node" ? byKey.get(menu.key) : null

  return (
    <div ref={wrapper} className="relative h-full w-full"
      onMouseMove={(e) => { pointer.current = { x: e.clientX, y: e.clientY } }}
      // Masaüstünden dosya sürükleme. `onDragOver`da preventDefault ŞART —
      // yoksa tarayıcı dosyayı kendi açar ve panodan çıkılır.
      onDragOver={(e) => { if (!readOnly && e.dataTransfer.types.includes("Files")) e.preventDefault() }}
      onDrop={(e) => {
        const files = Array.from(e.dataTransfer.files || []).filter((f) => f.type.startsWith("image/"))
        if (!files.length) return
        e.preventDefault()
        void addImages(files, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }))
      }}>
      <input ref={fileInput} type="file" accept={IMAGE_TYPES.join(",")} multiple hidden
        onChange={(e) => {
          const files = Array.from(e.target.files || [])
          e.target.value = ""            // aynı dosya arka arkaya seçilebilsin
          if (files.length) void addImages(files, menuFlowPos.current ?? undefined)
        }} />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        // Okun ucundan tutup taşıma. `onReconnect` VERİLMEDEN tutamaklar hiç
        // çizilmez (RF: isReconnectable = typeof onReconnect !== 'undefined' && …).
        onReconnect={onReconnect}
        edgesReconnectable={!readOnly}
        // Okun ETKİLEŞİM BÜTÜNLÜĞÜ — karttakiyle eşitlendi (2026-08-09):
        // üzerine gel → araç şeridi · çift tık → etiket · sağ tık → menü.
        onEdgeMouseEnter={(_e, edge) => setHoveredEdge(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdge(null)}
        onEdgeDoubleClick={(e, edge) => { e.stopPropagation(); setEdgeAutoEdit(edge.id) }}
        onEdgeContextMenu={(e, edge) => {
          e.preventDefault()
          const r = wrapper.current?.getBoundingClientRect(); if (!r) return
          setMenu({ kind: "edge", pos: menuPosition(e, r), key: edge.id })
        }}
        // Tutamak dairesi uç noktadan bu kadar DIŞARI kaydırılır — node'un kendi
        // `Handle`'ıyla çakışmasın diye. Varsayılan 10; kavraması kolay olsun.
        reconnectRadius={14}
        onPaneClick={() => { setPaletteFor(null); closeMenu() }}
        onNodeContextMenu={openNodeMenu}
        onPaneContextMenu={openPaneMenu}
        onSelectionContextMenu={(e) => {
          e.preventDefault()
          const r = wrapper.current?.getBoundingClientRect(); if (!r) return
          setMenu({ kind: "selection", pos: menuPosition(e, r) })
        }}
        // BAĞLANTI — 2026-07-27 düzeltmesi.
        // Loose: her tutamak hem kaynak hem hedef olabilir. Varsayılan Strict'te
        // yalnız kaynak→hedef kabul ediliyordu; kullanıcı hangi kenarın hangisi
        // olduğunu bilemediği için oklar "çalışmıyor" görünüyordu.
        connectionMode={ConnectionMode.Loose}
        // İmleç tutamağın 40 px yakınına gelince yakalar — 12 px'lik daireyi
        // birebir tutturmak gerekmez.
        connectionRadius={40}
        connectionLineStyle={{ stroke: "#0ea5e9", strokeWidth: 2 }}
        // 4 PX EŞİĞİ — SİLME. 0'da RF pointerdown'da sürüklemeye başlar ve
        // `dblclick` node'un içine ULAŞMAZ; "kartlara yazı yazılamıyor" hatası buydu.
        nodeDragThreshold={4}
        // Sürüklerken de ızgarada aksın: eskiden sürükleme serbest, BIRAKMA anında
        // `snap8` uygulanıyordu → kart bırakıldığı anda görünür şekilde zıplıyordu.
        snapToGrid
        snapGrid={SNAP_GRID}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        onlyRenderVisibleElements
        // `elevateNodesOnSelect` KALDIRILDI (2026-08-09): öğelerde açık `zIndex` var
        // ("öne getir"/"arkaya gönder" kullanıcı özelliği) ve RF bu ikisinin
        // çakıştığını söylüyor. Seçim zaten `ring-2` ile görünüyor.
        //
        // Seç modu: sol sürükleme = kutu seçimi, orta tuş/Space = pan.
        // Kaydır modu: sol sürükleme = pan (dokunmatik için de belirleyici).
        // Sağ tuş BİLEREK pan'de DEĞİL — menü onu kullanıyor.
        selectionOnDrag={mode === "select" && !readOnly}
        panOnDrag={mode === "pan" ? true : [1]}
        panOnScroll
        panActivationKeyCode="Space"
        multiSelectionKeyCode={["Shift", "Meta", "Control"]}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        className="bg-muted/20"
      >
        <Background gap={16} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="!hidden md:!block"
          nodeColor={(n) => (n.data as NodeData)?.item?.color || "#e2e8f0"} />

        {/* Mod anahtarı + yardım. Dokunmatik cihazda tek parmakla ne olacağını
            belirleyen tek yer burası. */}
        <Panel position="top-left"
          className="flex items-center gap-1 rounded-lg border bg-popover/95 p-1 shadow-sm backdrop-blur">
          <ToolBtn title="Seç (V)" active={mode === "select"} onClick={() => setMode("select")}>
            <MousePointer2 className="h-4 w-4" />
          </ToolBtn>
          <ToolBtn title="Kaydır (H)" active={mode === "pan"} onClick={() => setMode("pan")}>
            <Hand className="h-4 w-4" />
          </ToolBtn>
          <Sep />
          <ToolBtn title="Seçime yakınlaş" onClick={zoomToSelection}>
            <Scan className="h-4 w-4" />
          </ToolBtn>
          <ToolBtn title="Kısayollar (?)" onClick={() => setHelpOpen(true)}>
            <Keyboard className="h-4 w-4" />
          </ToolBtn>
        </Panel>

        {filterActive && matchKeys.length > 0 && (
          <Panel position="top-right"
            className="flex items-center gap-1 rounded-lg border bg-popover/95 p-1 shadow-sm backdrop-blur">
            <span className="px-1.5 text-xs text-muted-foreground">
              {matchIdx + 1}/{matchKeys.length} eşleşme
            </span>
            <ToolBtn title="Önceki eşleşme" onClick={() => goMatch(-1)}>
              <ChevronLeft className="h-4 w-4" />
            </ToolBtn>
            <ToolBtn title="Sonraki eşleşme" onClick={() => goMatch(1)}>
              <ChevronRight className="h-4 w-4" />
            </ToolBtn>
          </Panel>
        )}

        {multi && !readOnly && (
          <Panel position="top-center"
            className="flex flex-wrap items-center gap-1 rounded-lg border bg-popover/95 p-1 shadow-lg backdrop-blur">
            <span className="px-2 text-xs font-medium text-muted-foreground">{selBoxes.length} seçili</span>
            <Sep />
            <ToolBtn title="Sola hizala" onClick={() => layout(alignLeft)}><AlignStartVertical className="h-4 w-4" /></ToolBtn>
            <ToolBtn title="Yatayda ortala" onClick={() => layout(alignCenterX)}><AlignHorizontalJustifyCenter className="h-4 w-4" /></ToolBtn>
            <ToolBtn title="Üste hizala" onClick={() => layout(alignTop)}><AlignStartVertical className="h-4 w-4 rotate-90" /></ToolBtn>
            <ToolBtn title="Dikey aralıkları eşitle" onClick={() => layout((b) => distributeY(b))}><Grid3x3 className="h-4 w-4" /></ToolBtn>
            <ToolBtn title="Yatay aralıkları eşitle" onClick={() => layout((b) => distributeX(b))}><Grid3x3 className="h-4 w-4 rotate-90" /></ToolBtn>
            <ToolBtn title="Izgaraya diz" onClick={() => layout((b) => gridLayout(b))}><LayoutGrid className="h-4 w-4" /></ToolBtn>
            <Sep />
            <ToolBtn title="Grupla (Ctrl+G)" onClick={group}><Group className="h-4 w-4" /></ToolBtn>
            {selRegions.length > 0 && (
              <ToolBtn title="Grubu çöz" onClick={() => ungroup(selRegions.map((r) => r.item_key))}>
                <Ungroup className="h-4 w-4" />
              </ToolBtn>
            )}
            <ToolBtn title="Çoğalt (Ctrl+D)" onClick={() => duplicate(selBoxes)}><Copy className="h-4 w-4" /></ToolBtn>
            <ToolBtn title="Kilitle" onClick={() => setLock(selBoxes, true)}><Lock className="h-4 w-4" /></ToolBtn>
            <ToolBtn title="Kilidi aç" onClick={() => setLock(selBoxes, false)}><Unlock className="h-4 w-4" /></ToolBtn>
            <div className="relative">
              <ToolBtn title="Renk" onClick={() => setPaletteFor(paletteFor === "__bulk__" ? null : "__bulk__")}>
                <Palette className="h-4 w-4" />
              </ToolBtn>
              {paletteFor === "__bulk__" && (
                <div className="absolute top-9 left-0 z-20 flex w-[118px] flex-wrap gap-1 rounded-md border bg-popover p-1 shadow-md">
                  {COLORS.map((c) => (
                    <button key={c} type="button" title={c} className="h-5 w-5 rounded border"
                      style={{ background: c }}
                      onClick={() => { bulk({ color: c }); setPaletteFor(null) }} />
                  ))}
                </div>
              )}
            </div>
            <ToolBtn title="Bitti işaretle" onClick={() => bulk({ status: "done" })}>✓</ToolBtn>
            <ToolBtn title="Açık yap" onClick={() => bulk({ status: "open" })}><X className="h-4 w-4" /></ToolBtn>
            <ToolBtn title="Sil" danger
              onClick={() => removeCascade(selBoxes.filter((i) => !isLocked(i)).map((i) => i.item_key))}>
              <Trash2 className="h-4 w-4" />
            </ToolBtn>
          </Panel>
        )}
      </ReactFlow>

      {menu && !readOnly && (
        <ContextMenu pos={menu.pos} onClose={closeMenu}>
          {menu.kind === "node" && mi && (
            <>
              <MenuItem icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
                onClick={act(() => onOpenDetail(mi))}>Ayrıntılar…</MenuItem>
              <MenuItem icon={<Copy className="h-3.5 w-3.5" />} hint="Ctrl+D"
                onClick={act(() => duplicate([mi]))}>Çoğalt</MenuItem>
              <MenuItem icon={<ClipboardPaste className="h-3.5 w-3.5" />} hint="Ctrl+C"
                onClick={act(() => { clipboard.current = [mi] })}>Kopyala</MenuItem>
              <MenuSep />
              <MenuItem icon={isLocked(mi) ? <Unlock className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
                onClick={act(() => setLock([mi], !isLocked(mi)))}>
                {isLocked(mi) ? "Kilidi aç" : "Kilitle"}
              </MenuItem>
              <MenuItem icon={<ArrowUpToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "one"))}>Öne getir</MenuItem>
              <MenuItem icon={<ArrowDownToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "arkaya"))}>Arkaya gönder</MenuItem>
              <MenuItem icon={<BringToFront className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "enOne"))}>En öne getir</MenuItem>
              <MenuItem icon={<SendToBack className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala([mi], "enArkaya"))}>En arkaya gönder</MenuItem>
              {mi.type === "region" && hasChildren(mi.item_key) && (
                <MenuItem icon={<Ungroup className="h-3.5 w-3.5" />}
                  onClick={act(() => ungroup([mi.item_key]))}>Grubu çöz</MenuItem>
              )}
              {parentKeyOf(mi) && (
                <MenuItem icon={<Ungroup className="h-3.5 w-3.5" />}
                  onClick={act(() => {
                    const p = byKey.get(parentKeyOf(mi) as string)
                    if (p) commit([{ item_key: mi.item_key, x: snap8(p.x + mi.x),
                                     y: snap8(p.y + mi.y), extra: { parent_key: null } }])
                  })}>Gruptan çıkar</MenuItem>
              )}
              <MenuSep />
              <MenuItem danger disabled={isLocked(mi)} icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={act(() => removeCascade([mi.item_key]))}>Sil</MenuItem>
            </>
          )}

          {menu.kind === "selection" && (
            <>
              <div className="px-2 py-1 text-[10px] text-muted-foreground">{selBoxes.length} öğe seçili</div>
              <MenuItem icon={<Group className="h-3.5 w-3.5" />} hint="Ctrl+G"
                disabled={selBoxes.filter((i) => i.type !== "region" && !parentKeyOf(i)).length < 2}
                onClick={act(group)}>Grupla</MenuItem>
              {selRegions.length > 0 && (
                <MenuItem icon={<Ungroup className="h-3.5 w-3.5" />}
                  onClick={act(() => ungroup(selRegions.map((r) => r.item_key)))}>Grubu çöz</MenuItem>
              )}
              <MenuSep />
              <MenuItem icon={<AlignStartVertical className="h-3.5 w-3.5" />}
                onClick={act(() => layout(alignLeft))}>Sola hizala</MenuItem>
              <MenuItem icon={<LayoutGrid className="h-3.5 w-3.5" />}
                onClick={act(() => layout((b) => gridLayout(b)))}>Izgaraya diz</MenuItem>
              <MenuSep />
              <MenuItem icon={<ArrowUpToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "one"))}>Öne getir</MenuItem>
              <MenuItem icon={<ArrowDownToLine className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "arkaya"))}>Arkaya gönder</MenuItem>
              <MenuItem icon={<BringToFront className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "enOne"))}>En öne getir</MenuItem>
              <MenuItem icon={<SendToBack className="h-3.5 w-3.5" />}
                onClick={act(() => zSirala(selBoxes, "enArkaya"))}>En arkaya gönder</MenuItem>
              <MenuSep />
              <MenuItem icon={<Copy className="h-3.5 w-3.5" />} hint="Ctrl+D"
                onClick={act(() => duplicate(selBoxes))}>Çoğalt</MenuItem>
              <MenuItem icon={<Lock className="h-3.5 w-3.5" />}
                onClick={act(() => setLock(selBoxes, true))}>Kilitle</MenuItem>
              <MenuItem icon={<Unlock className="h-3.5 w-3.5" />}
                onClick={act(() => setLock(selBoxes, false))}>Kilidi aç</MenuItem>
              <MenuSep />
              <MenuItem danger icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={act(() => removeCascade(selBoxes.filter((i) => !isLocked(i)).map((i) => i.item_key)))}>
                Seçimi sil
              </MenuItem>
            </>
          )}

          {menu.kind === "edge" && (
            <>
              <MenuItem icon={<Tag className="h-3.5 w-3.5" />}
                onClick={act(() => setEdgeAutoEdit(menu.key))}>Etiketi düzenle</MenuItem>
              <MenuItem icon={<ArrowLeftRight className="h-3.5 w-3.5" />}
                onClick={act(() => reverseEdge(menu.key))}>Yönü ters çevir</MenuItem>
              <MenuSep />
              <MenuItem danger icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={act(() => remove([menu.key]))}>Oku sil</MenuItem>
            </>
          )}

          {menu.kind === "pane" && (
            <>
              <MenuItem icon={<Plus className="h-3.5 w-3.5" />}
                onClick={act(() => addAt("card", menu.flow.x, menu.flow.y))}>Kart ekle</MenuItem>
              <MenuItem icon={<StickyNote className="h-3.5 w-3.5" />}
                onClick={act(() => addAt("note", menu.flow.x, menu.flow.y))}>Not ekle</MenuItem>
              <MenuItem icon={<SquareDashed className="h-3.5 w-3.5" />}
                onClick={act(() => addAt("region", menu.flow.x, menu.flow.y))}>Bölge ekle</MenuItem>
              <MenuItem icon={<ImagePlus className="h-3.5 w-3.5" />}
                onClick={act(() => { menuFlowPos.current = menu.flow; fileInput.current?.click() })}>
                Görsel ekle…
              </MenuItem>
              <MenuSep />
              <MenuItem icon={<ClipboardPaste className="h-3.5 w-3.5" />} hint="Ctrl+V"
                disabled={!clipboard.current.length}
                onClick={act(() => {
                  const b = boundsOf(clipboard.current.map(boxOf))
                  duplicate(clipboard.current, menu.flow.x - b.x, menu.flow.y - b.y)
                })}>Yapıştır</MenuItem>
              <MenuSep />
              <MenuItem icon={<BoxSelect className="h-3.5 w-3.5" />} hint="Ctrl+A"
                onClick={act(selectAll)}>Tümünü seç</MenuItem>
              <MenuItem icon={<Maximize2 className="h-3.5 w-3.5" />}
                onClick={act(() => void rf.fitView({ padding: 0.2, duration: 300 }))}>Görünüme sığdır</MenuItem>
            </>
          )}
        </ContextMenu>
      )}

      {helpOpen && <ShortcutsHelp onClose={() => setHelpOpen(false)} />}
    </div>
  )
})

function Sep() { return <div className="mx-1 h-5 w-px bg-border" /> }

function ToolBtn({ title, onClick, children, danger, active }: {
  title: string; onClick: () => void; children: React.ReactNode
  danger?: boolean; active?: boolean
}) {
  return (
    <button type="button" title={title} aria-label={title} aria-pressed={active}
      onClick={onClick}
      className={cn("flex h-7 min-w-7 items-center justify-center rounded px-1.5 text-sm hover:bg-muted",
                    active && "bg-primary text-primary-foreground hover:bg-primary/90",
                    danger && "text-destructive hover:bg-destructive/10")}>
      {children}
    </button>
  )
}
