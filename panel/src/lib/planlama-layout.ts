// Otomatik düzen — SAF fonksiyonlar (React/DOM yok, girdi→çıktı).
//
// Neden ayrı dosya: hizalama/dağıtma/ızgara matematiği tuval bileşeninin içinde
// yaşarsa test edilemez ve her render'da yeniden okunur. Buradaki her fonksiyon
// `{item_key, x, y}` yamaları döndürür — doğrudan `store.commit()`'e verilebilir.
import { DEFAULT_SIZE, parentKeyOf, SNAP, snap8, type PlanningItem } from "@/lib/planlama"

export interface Box { item_key: string; x: number; y: number; width: number; height: number }
export type Move = { item_key: string; x: number; y: number }

/** Bir okun bağlanacağı kenar — `PlanningNodes`taki `Handle` id'leriyle AYNI. */
export type Yon = "l" | "r" | "t" | "b"

/** Öğenin gerçek ölçüsü — `width/height` NULL ise tür varsayılanı.
 *  Eski kayıtlarda NULL yaygın; normalize etmezsek hizalama NaN üretir. */
export function boxOf(it: PlanningItem): Box {
  const d = DEFAULT_SIZE[it.type] || DEFAULT_SIZE.card
  return {
    item_key: it.item_key, x: it.x, y: it.y,
    width: it.width || d.width, height: it.height || d.height,
  }
}

function sortByX(b: Box[]) { return [...b].sort((p, q) => p.x - q.x || p.y - q.y) }
function sortByY(b: Box[]) { return [...b].sort((p, q) => p.y - q.y || p.x - q.x) }

/** Sol kenarları en soldakine hizala. */
export function alignLeft(boxes: Box[]): Move[] {
  if (boxes.length < 2) return []
  const x = snap8(Math.min(...boxes.map((b) => b.x)))
  return boxes.filter((b) => b.x !== x).map((b) => ({ item_key: b.item_key, x, y: b.y }))
}

/** Üst kenarları en yukarıdakine hizala. */
export function alignTop(boxes: Box[]): Move[] {
  if (boxes.length < 2) return []
  const y = snap8(Math.min(...boxes.map((b) => b.y)))
  return boxes.filter((b) => b.y !== y).map((b) => ({ item_key: b.item_key, x: b.x, y }))
}

/** Dikey eksende ortala (ortak merkez X). */
export function alignCenterX(boxes: Box[]): Move[] {
  if (boxes.length < 2) return []
  const cx = boxes.reduce((s, b) => s + b.x + b.width / 2, 0) / boxes.length
  return boxes.map((b) => ({ item_key: b.item_key, x: snap8(cx - b.width / 2), y: b.y }))
                .filter((m, i) => m.x !== boxes[i].x)
}

/** Dikey aralıkları eşitle — uçtaki iki öğe sabit kalır, aradakiler dağıtılır. */
export function distributeY(boxes: Box[], gap = 24): Move[] {
  if (boxes.length < 3) return []
  const s = sortByY(boxes)
  const moves: Move[] = []
  let cursor = s[0].y + s[0].height + gap
  for (let i = 1; i < s.length - 1; i++) {
    const y = snap8(cursor)
    if (y !== s[i].y) moves.push({ item_key: s[i].item_key, x: s[i].x, y })
    cursor = y + s[i].height + gap
  }
  return moves
}

/** Yatay aralıkları eşitle. */
export function distributeX(boxes: Box[], gap = 24): Move[] {
  if (boxes.length < 3) return []
  const s = sortByX(boxes)
  const moves: Move[] = []
  let cursor = s[0].x + s[0].width + gap
  for (let i = 1; i < s.length - 1; i++) {
    const x = snap8(cursor)
    if (x !== s[i].x) moves.push({ item_key: s[i].item_key, x, y: s[i].y })
    cursor = x + s[i].width + gap
  }
  return moves
}

/** Seçimi ızgaraya diz — sol-üst köşe korunur, satır başına `cols` öğe.
 *  Hücre genişliği en geniş öğeye göre: farklı boyutlu kartlar çakışmasın. */
export function gridLayout(boxes: Box[], cols = 4, gap = 24): Move[] {
  if (!boxes.length) return []
  const ordered = sortByY(sortByX(boxes))
  const x0 = snap8(Math.min(...boxes.map((b) => b.x)))
  const y0 = snap8(Math.min(...boxes.map((b) => b.y)))
  const cw = Math.max(...boxes.map((b) => b.width)) + gap
  const ch = Math.max(...boxes.map((b) => b.height)) + gap
  const moves: Move[] = []
  ordered.forEach((b, i) => {
    const x = x0 + (i % cols) * cw
    const y = y0 + Math.floor(i / cols) * ch
    if (x !== b.x || y !== b.y) moves.push({ item_key: b.item_key, x, y })
  })
  return moves
}

/** Tek kolona diz (dikey liste). */
export function columnLayout(boxes: Box[], gap = 16): Move[] {
  return gridLayout(boxes, 1, gap)
}

/** Çoğaltma/yapıştırma ofseti — üst üste binmesin diye sabit kaydırma. */
export const PASTE_OFFSET = SNAP * 3

/** Öğenin MUTLAK kutusu.
 *
 *  Ebeveynli (bir bölgeye alınmış) öğede `x/y` EBEVEYNE GÖRELİDİR — React Flow'un
 *  sözleşmesi böyle ve DB'de de öyle saklanıyor. Ok uçlarını hesaplarken göreli
 *  koordinatla çalışmak, gruptaki bir kartın okunu tamamen yanlış kenardan
 *  çıkarırdı. Bölgeler iç içe geçmediği için tek seviye yeterli. */
export function absBoxOf(it: PlanningItem, byKey: Map<string, PlanningItem>): Box {
  const b = boxOf(it)
  const pk = parentKeyOf(it)
  if (!pk) return b
  const p = byKey.get(pk)
  return p ? { ...b, x: b.x + p.x, y: b.y + p.y } : b
}

/** İki kutu arasındaki okun hangi kenarlardan çıkıp gireceği.
 *
 *  NEDEN HESAPLANIYOR: `onConnect` kullanıcının çektiği tutamağı (`sourceHandle`/
 *  `targetHandle`) kaydetmiyordu ve kenar nesnesine de tutamak yazılmıyordu; React
 *  Flow tutamak verilmeyince listedeki İLKİNE düşüyor (bizde `l` = sol). Sonuç:
 *  sağdaki bir karta çekilen ok bile SOLDAN çıkıp dolanıyordu.
 *
 *  Kaydedilmiş tutamak yerine geometriye bakmayı seçtik: (1) hiçbir tutamak
 *  bilgisi taşımayan ESKİ oklar da kendiliğinden düzelir, (2) kart taşınınca ok
 *  kendini toplar — sabitlenmiş bir kenar taşımadan sonra yine dolanırdı.
 *
 *  Eşitlikte (|dx| == |dy|) yatay tercih edilir; köşegen yerleşimde yatay ok
 *  gözle daha okunur.
 */
export function bestHandles(a: Box, b: Box): [Yon, Yon] {
  const dx = (b.x + b.width / 2) - (a.x + a.width / 2)
  const dy = (b.y + b.height / 2) - (a.y + a.height / 2)
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ["r", "l"] : ["l", "r"]
  return dy >= 0 ? ["b", "t"] : ["t", "b"]
}

const YONLER: Yon[] = ["l", "r", "t", "b"]

/** `extra`ya yazılmış tutamağı OKU ve doğrula.
 *
 *  `extra` serbest JSON: eski kayıtlarda alan hiç yok, elle düzenlenmiş bir panoda
 *  saçma bir değer olabilir. Tanımadığımız her şey `null` sayılır → hesaplanana düşer. */
export function storedHandle(extra: Record<string, unknown> | null | undefined,
                             alan: "from_handle" | "to_handle"): Yon | null {
  const v = extra?.[alan]
  return typeof v === "string" && (YONLER as string[]).includes(v) ? (v as Yon) : null
}

/** Bir yön, hedef vektörüyle tutarlı mı?
 *  `r` yalnız hedef sağdayken, `t` yalnız hedef yukarıdayken makul. */
function yonMakul(y: Yon, dx: number, dy: number): boolean {
  if (y === "r") return dx >= 0
  if (y === "l") return dx <= 0
  if (y === "b") return dy >= 0
  return dy <= 0
}

/** Okun kenar çifti: KULLANICININ SEÇTİĞİ tutamak öncelikli, geometri yedek.
 *
 *  Neden düz "saklananı kullan" değil: tutamak sabitlenirse, kart okun ters
 *  tarafına taşındığında ok yine kartın etrafından dolanır — bildirilen hatanın ta
 *  kendisi. Bu yüzden saklanan yön yalnız hedef O YÖNDE durduğu sürece geçerli;
 *  ters düştüğü anda hesaplanana düşer. Kart geri taşınırsa seçim yine devreye girer
 *  (saklanan değer SİLİNMİYOR, sadece o an yok sayılıyor).
 *
 *  Kaynak ve hedef ayrı ayrı değerlendirilir: biri saklıyken diğeri boş olabilir
 *  (ör. boşluğa çekilerek yaratılan kartta hedef tutamağı yoktur). */
export function resolveHandles(a: Box, b: Box,
                               stored: { from: Yon | null; to: Yon | null }): [Yon, Yon] {
  const [gs, gt] = bestHandles(a, b)
  const dx = (b.x + b.width / 2) - (a.x + a.width / 2)
  const dy = (b.y + b.height / 2) - (a.y + a.height / 2)
  return [
    stored.from && yonMakul(stored.from, dx, dy) ? stored.from : gs,
    // Hedef kutunun bakış açısından vektör TERSTİR.
    stored.to && yonMakul(stored.to, -dx, -dy) ? stored.to : gt,
  ]
}

export type ZMod = "one" | "arkaya" | "enOne" | "enArkaya"

/** Katman sırası yamaları.
 *
 *  Dört mod: bir adım öne/arkaya (`one`/`arkaya`) ve en öne/en arkaya
 *  (`enOne`/`enArkaya`).
 *
 *  Mutlak modlarda seçimin KENDİ İÇİNDEKİ sırası korunur: mevcut `z`'ye göre
 *  sıralanıp ardışık değer verilir. Hepsine aynı `z` verilseydi üst üste binen
 *  kartların birbirine göre düzeni her "en öne"de bozulurdu.
 *
 *  `kutular` = panodaki tüm kutular (kenarlar hariç) — uç değerler oradan gelir. */
export function zMoves(liste: PlanningItem[], kutular: PlanningItem[],
                       mod: ZMod): { item_key: string; z: number }[] {
  if (!liste.length) return []
  const enUst = Math.max(0, ...kutular.map((i) => i.z || 0))
  const enAlt = Math.min(0, ...kutular.map((i) => i.z || 0))
  const sirali = [...liste].sort((a, b) => (a.z || 0) - (b.z || 0))
  return sirali.map((it, i) => ({
    item_key: it.item_key,
    z: mod === "one" ? (it.z || 0) + 1
      : mod === "arkaya" ? (it.z || 0) - 1
      : mod === "enOne" ? enUst + 1 + i
      : enAlt - sirali.length + i,
  }))
}

/** Silinen öğelere bağlı okların anahtarları.
 *
 *  NEDEN VAR: kart silindiğinde okları geride kalıyordu. React Flow'un KENDİ silme
 *  yolu (Delete tuşu → `deleteElements`) bağlı kenarları da kaldırır, ama panelin
 *  kendi silme yolları (kart üstündeki çöp kutusu, sağ tık menüsü, toplu silme) RF'e
 *  uğramadan doğrudan store'a gider ve okları öksüz bırakırdı. Öksüz ok DB'de
 *  görünmez bir satır olarak kalır ve RF her render'da `error008` basar
 *  ("Couldn't create edge for source handle id"). Canlıda 3 böyle satır bulundu
 *  (management panosu, 2026-08-09).
 *
 *  Zaten silinmekte olan okları tekrar döndürmez. */
export function connectedEdgeKeys(silinen: Iterable<string>,
                                  items: PlanningItem[]): string[] {
  const set = new Set(silinen)
  return items
    .filter((it) => it.type === "edge"
      && !set.has(it.item_key)
      && ((it.from_key && set.has(it.from_key)) || (it.to_key && set.has(it.to_key))))
    .map((it) => it.item_key)
}

/** Bir kutu kümesinin sınırlayıcı dikdörtgeni (yapıştırmada imleci hizalamak için). */
export function boundsOf(boxes: Box[]) {
  if (!boxes.length) return { x: 0, y: 0, width: 0, height: 0 }
  const x0 = Math.min(...boxes.map((b) => b.x))
  const y0 = Math.min(...boxes.map((b) => b.y))
  const x1 = Math.max(...boxes.map((b) => b.x + b.width))
  const y1 = Math.max(...boxes.map((b) => b.y + b.height))
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
}
