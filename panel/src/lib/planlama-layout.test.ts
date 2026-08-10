// Ok uçlarının hangi kenardan çıkacağı — geometri.
//
// Yaşanan hata (2026-08-09, ekran görüntüsüyle bildirildi): kullanıcı bir notun
// SAĞ tutamağından çekip sağ üstteki kartlara bağladı, oklar SOLDAN çıkıp
// dolandı. Sebep: kenar nesnesine tutamak yazılmıyordu, RF de listedeki ilk
// tutamağa (`l`) düşüyordu.
import { describe, expect, it } from "vitest"

import {
  absBoxOf, bestHandles, boxOf, connectedEdgeKeys, resolveHandles, storedHandle, zMoves,
  type Box,
} from "@/lib/planlama-layout"
import type { PlanningItem } from "@/lib/planlama"

function kutu(x: number, y: number, width = 200, height = 120, item_key = "k"): Box {
  return { item_key, x, y, width, height }
}

function oge(p: Partial<PlanningItem>): PlanningItem {
  return {
    item_key: "a", type: "card", x: 0, y: 0, width: 200, height: 120,
    extra: {}, ...p,
  } as unknown as PlanningItem
}

describe("bestHandles", () => {
  it("hedef SAĞDAYSA sağdan çıkar, sola girer", () => {
    expect(bestHandles(kutu(0, 0), kutu(600, 0))).toEqual(["r", "l"])
  })

  it("hedef SOLDAYSA soldan çıkar, sağa girer", () => {
    expect(bestHandles(kutu(600, 0), kutu(0, 0))).toEqual(["l", "r"])
  })

  it("hedef AŞAĞIDAYSA alttan çıkar, üste girer", () => {
    expect(bestHandles(kutu(0, 0), kutu(0, 600))).toEqual(["b", "t"])
  })

  it("hedef YUKARIDAYSA üstten çıkar, alta girer", () => {
    expect(bestHandles(kutu(0, 600), kutu(0, 0))).toEqual(["t", "b"])
  })

  it("bildirilen durum: hedef sağ ÜSTTE ve yatay fark daha büyük → SAĞDAN çıkar", () => {
    // Ekran görüntüsündeki yerleşime yakın: kaynak sol altta, hedef sağ üstte.
    const kaynak = kutu(170, 650)
    const hedef = kutu(965, 130)
    expect(bestHandles(kaynak, hedef)).toEqual(["r", "l"])
  })

  it("dikey fark daha büyükse köşegende bile DİKEY seçer", () => {
    expect(bestHandles(kutu(0, 0), kutu(100, 900))).toEqual(["b", "t"])
  })

  it("|dx| == |dy| eşitliğinde yatayı tercih eder", () => {
    expect(bestHandles(kutu(0, 0, 0, 0), kutu(400, 400, 0, 0))).toEqual(["r", "l"])
  })

  it("üst üste binen kutularda da bir yön üretir (NaN/undefined yok)", () => {
    const [s, t] = bestHandles(kutu(0, 0), kutu(0, 0))
    expect(["l", "r", "t", "b"]).toContain(s)
    expect(["l", "r", "t", "b"]).toContain(t)
  })
})

describe("connectedEdgeKeys", () => {
  const items: PlanningItem[] = [
    oge({ item_key: "a" }),
    oge({ item_key: "b" }),
    oge({ item_key: "c" }),
    oge({ item_key: "ok1", type: "edge", from_key: "a", to_key: "b" }),
    oge({ item_key: "ok2", type: "edge", from_key: "b", to_key: "c" }),
    oge({ item_key: "ok3", type: "edge", from_key: "c", to_key: "a" }),
  ]

  it("silinen kartın HEM giden HEM gelen oklarını toplar", () => {
    expect(connectedEdgeKeys(["b"], items).sort()).toEqual(["ok1", "ok2"])
  })

  it("çoklu silmede birleşik küme döner, tekrar etmez", () => {
    expect(connectedEdgeKeys(["a", "b"], items).sort()).toEqual(["ok1", "ok2", "ok3"])
  })

  it("hiçbir oka dokunmayan silmede boş döner", () => {
    expect(connectedEdgeKeys(["yok"], items)).toEqual([])
  })

  it("zaten silinmekte olan oku TEKRAR döndürmez", () => {
    // Kullanıcı hem kartı hem okunu seçip sildiyse ok iki kez gitmemeli.
    expect(connectedEdgeKeys(["a", "ok1"], items).sort()).toEqual(["ok3"])
  })

  it("okun kendisi silinirken uçlarındaki kartlara dokunmaz", () => {
    expect(connectedEdgeKeys(["ok1"], items)).toEqual([])
  })
})

describe("storedHandle", () => {
  it("geçerli yönü okur", () => {
    expect(storedHandle({ from_handle: "t" }, "from_handle")).toBe("t")
    expect(storedHandle({ to_handle: "b" }, "to_handle")).toBe("b")
  })

  it("alan yoksa / extra boşsa null döner", () => {
    expect(storedHandle({}, "from_handle")).toBeNull()
    expect(storedHandle(null, "from_handle")).toBeNull()
    expect(storedHandle(undefined, "to_handle")).toBeNull()
  })

  it("tanımadığı değeri YOK sayar (elle düzenlenmiş pano / eski kayıt)", () => {
    expect(storedHandle({ from_handle: "sol" }, "from_handle")).toBeNull()
    expect(storedHandle({ from_handle: 3 }, "from_handle")).toBeNull()
  })
})

describe("resolveHandles", () => {
  const sol = kutu(0, 0)
  const sag = kutu(600, 0)

  it("saklanan tutamak yoksa geometriye düşer", () => {
    expect(resolveHandles(sol, sag, { from: null, to: null })).toEqual(["r", "l"])
  })

  it("KULLANICININ SEÇİMİ önceliklidir — geometri yatay derken üstten çıkarabilir", () => {
    expect(resolveHandles(sol, sag, { from: "t", to: null })).toEqual(["t", "l"])
  })

  it("kaynak ve hedef AYRI değerlendirilir", () => {
    expect(resolveHandles(sol, sag, { from: null, to: "b" })).toEqual(["r", "b"])
  })

  it("saklanan yön hedefe TERS düşerse hesaplanana düşer (dolanmayı önler)", () => {
    // Hedef sağda ama ok sol tutamağa sabitlenmiş → kartın etrafından dolanırdı.
    expect(resolveHandles(sol, sag, { from: "l", to: null })).toEqual(["r", "l"])
  })

  it("hedef tarafı TERS vektörle değerlendirilir", () => {
    // Hedef sağda; hedefin 'r' tutamağı okun geldiği yöne sırtını döner → düşer.
    expect(resolveHandles(sol, sag, { from: null, to: "r" })).toEqual(["r", "l"])
    // 'l' ise doğru taraf, korunur.
    expect(resolveHandles(sol, sag, { from: null, to: "l" })).toEqual(["r", "l"])
  })

  it("kart geri taşınınca saklanan seçim YİNE devreye girer", () => {
    const saklanan = { from: "l" as const, to: null }
    // Hedef sağdayken yok sayılır…
    expect(resolveHandles(sol, sag, saklanan)[0]).toBe("r")
    // …hedef sola geçince tekrar geçerli (değer silinmiyor, o an yok sayılıyordu).
    expect(resolveHandles(sag, sol, saklanan)[0]).toBe("l")
  })

  it("dikey yerleşimde üst/alt seçimi korunur", () => {
    const ust = kutu(0, 0)
    const alt = kutu(0, 600)
    expect(resolveHandles(ust, alt, { from: "b", to: "t" })).toEqual(["b", "t"])
    expect(resolveHandles(ust, alt, { from: "t", to: null })).toEqual(["b", "t"])
  })
})

describe("absBoxOf", () => {
  it("ebeveynsiz öğede boxOf ile aynıdır", () => {
    const it = oge({ item_key: "a", x: 40, y: 50 })
    expect(absBoxOf(it, new Map())).toEqual(boxOf(it))
  })

  it("ebeveynli öğede GÖRELİ konumu mutlaka çevirir", () => {
    const bolge = oge({ item_key: "b", type: "region", x: 1000, y: 500 })
    const cocuk = oge({ item_key: "c", x: 30, y: 20, extra: { parent_key: "b" } })
    const m = new Map([["b", bolge]])
    expect(absBoxOf(cocuk, m)).toMatchObject({ x: 1030, y: 520 })
  })

  it("ebeveyn kayıpsa göreli konumu OLDUĞU GİBİ bırakır (patlamaz)", () => {
    const cocuk = oge({ item_key: "c", x: 30, y: 20, extra: { parent_key: "yok" } })
    expect(absBoxOf(cocuk, new Map())).toMatchObject({ x: 30, y: 20 })
  })
})

describe("zMoves", () => {
  const kutular = [
    oge({ item_key: "a", z: 0 }),
    oge({ item_key: "b", z: 3 }),
    oge({ item_key: "c", z: -2 }),
  ]

  it("bir adım öne/arkaya her öğeyi KENDİ z'sinden hesaplar", () => {
    expect(zMoves([kutular[0]], kutular, "one")).toEqual([{ item_key: "a", z: 1 }])
    expect(zMoves([kutular[1]], kutular, "arkaya")).toEqual([{ item_key: "b", z: 2 }])
  })

  it("en öne: panodaki en üstün ÜSTÜNE çıkarır", () => {
    // en üst z = 3 → 4
    expect(zMoves([kutular[0]], kutular, "enOne")).toEqual([{ item_key: "a", z: 4 }])
  })

  it("en arkaya: panodaki en altın ALTINA indirir", () => {
    // en alt z = -2 → -3
    expect(zMoves([kutular[1]], kutular, "enArkaya")).toEqual([{ item_key: "b", z: -3 }])
  })

  it("mutlak modlarda seçimin KENDİ İÇİNDEKİ sırası korunur", () => {
    // c(-2) < a(0) < b(3) → aynı sırayla ardışık z alırlar
    expect(zMoves(kutular, kutular, "enOne")).toEqual([
      { item_key: "c", z: 4 },
      { item_key: "a", z: 5 },
      { item_key: "b", z: 6 },
    ])
  })

  it("en arkaya çoklu seçimde de sıra korunur ve hepsi en altın altına iner", () => {
    const moves = zMoves(kutular, kutular, "enArkaya")
    expect(moves.map((m) => m.item_key)).toEqual(["c", "a", "b"])
    expect(moves.every((m) => m.z < -2)).toBe(true)
    // ardışık: aralarındaki göreli düzen bozulmadı
    expect(moves[0].z).toBeLessThan(moves[1].z)
    expect(moves[1].z).toBeLessThan(moves[2].z)
  })

  it("boş seçimde hiç yama üretmez", () => {
    expect(zMoves([], kutular, "enOne")).toEqual([])
  })

  it("z alanı NULL/undefined olan eski kayıtları 0 sayar", () => {
    const eski = oge({ item_key: "x" })   // z yok
    expect(zMoves([eski], [eski], "one")).toEqual([{ item_key: "x", z: 1 }])
  })
})
