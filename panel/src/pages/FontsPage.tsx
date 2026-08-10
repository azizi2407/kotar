// Font havuzu (2026-08-05, proje sahibi isteği) — `/fontlar`.
//
// İki işi var: fontları tek yerde toplamak (indirilebilir) ve **yazılan metnin o
// fontlarla nasıl göründüğünü** göstermek. Fontlar aileye göre gruplanır; aynı
// ailenin Regular/Bold/Italic dosyaları alt alta gelir ki ağırlıklar karşılaştırılsın.
//
// Rol kapısı ROUTE'tan gelir (AppLayout nav tablosu: dört üretim rolü). Yazma
// aksiyonları (yükle/ata/adlandır) ayrıca burada `isManagement || designer` ile gizlenir —
// backend zaten zorluyor, bu yalnız arayüz gürültüsünü azaltıyor. SİLME ise ayrı:
// bayrak font başına backend'ten gelir (`can_delete`) — tasarımcı yalnız kendi
// yüklediğini kaldırabilir (2026-08-06).
import { useMemo, useRef, useState } from "react"
import {
  Check, Columns2, Download, Loader2, Pencil, Search, Trash2, Type, Upload, Users,
} from "lucide-react"
import { toast } from "sonner"

import {
  downloadFont, formatSize, sonucOzeti, useAssignFont, useDeleteFont, useFonts,
  useRenameFont, useUploadFont, type FontItem,
} from "@/lib/fonts"
import { useClients } from "@/lib/clients"
import { FontPreview } from "@/components/fonts/FontPreview"
import { useAuth } from "@/lib/auth"
import { trFold } from "@/lib/week"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const VARSAYILAN_METIN = "Pijamalı hasta yağız şoföre çabucak güvendi"
const ILK_GORUNEN = 10
const SAYFA = 20

export function FontsPage() {
  const { user, isManagement } = useAuth()
  const yazabilir = isManagement || user?.role === "designer"

  const { data: fonts, isLoading } = useFonts()
  const { data: clients } = useClients({ status: "active", q: "" })
  const yukle = useUploadFont()
  const sil = useDeleteFont()
  const ata = useAssignFont()
  const adlandir = useRenameFont()

  const [metin, setMetin] = useState(VARSAYILAN_METIN)
  const [punto, setPunto] = useState(32)
  const [koyu, setKoyu] = useState(false)
  const [ara, setAra] = useState("")
  // Havuz 200 fontu aşabiliyor; ilk açılışta hepsini çizmek hem gözü hem tarayıcıyı
  // yorar (her satır ayrı font dosyası indiriyor). Varsayılan 10, kullanıcı açar.
  const [limit, setLimit] = useState(ILK_GORUNEN)
  // Karşılaştırma: seçilen fontlar tek ekranda, AYNI metin ve puntoyla yan yana
  // görünsün diye ayrı bir kip — aksi halde uzun listede kaydırıp durmak gerekir.
  const [secili, setSecili] = useState<Set<number>>(new Set())
  const [karsilastir, setKarsilastir] = useState(false)
  const dosyaRef = useRef<HTMLInputElement>(null)

  function secimDegistir(id: number) {
    setSecili((s) => {
      const yeni = new Set(s)
      if (yeni.has(id)) yeni.delete(id)
      else yeni.add(id)
      return yeni
    })
  }

  // İki ayrı kip: NORMAL'de arama + limit uygulanır; KARŞILAŞTIRMADA ikisi de
  // uygulanmaz — kullanıcı zaten seçtiğini görmek istiyor.
  const suzulen = useMemo(() => {
    // Karşılaştırma kipinde arama HİÇ uygulanmaz: kip "seçtiğim her şeyi göster"
    // demek, bir süzgecin seçimlerin bir kısmını gizlemesi bu sözü bozar. Kutu da
    // o sırada kapalı (aşağıda `disabled`), yani sessiz bir yok sayma değil.
    if (karsilastir) return (fonts ?? []).filter((f) => secili.has(f.id))
    return (fonts ?? []).filter((f) => {
      if (!ara.trim()) return true
      const needle = trFold(ara)
      return trFold(`${f.family} ${f.style}`).includes(needle)
        || f.clients.some((c) => trFold(c.name).includes(needle))
    })
  }, [fonts, ara, karsilastir, secili])

  const gosterilecek = karsilastir ? suzulen : suzulen.slice(0, limit)
  const kalan = suzulen.length - gosterilecek.length

  // Aileye göre grupla; grup içinde stiller alfabetik değil, backend sırasında
  // (family, style) geldiği için doğal sırada kalır.
  const gruplar = useMemo(() => {
    const map = new Map<string, FontItem[]>()
    gosterilecek.forEach((f) => map.set(f.family, [...(map.get(f.family) ?? []), f]))
    return [...map.entries()]
  }, [gosterilecek])

  async function dosyaSec(files: File[]) {
    for (const file of files) {
      try {
        const d = await yukle.mutateAsync({ file })
        toast.success(sonucOzeti(d))
      } catch (e) {
        // 409 = aynı dosya zaten havuzda; bu bir hata değil, bilgi.
        const msg = e instanceof Error ? e.message : "Yüklenemedi"
        toast[msg.includes("zaten havuzda") ? "info" : "error"](`${file.name}: ${msg}`)
      }
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Fontlar</h1>
          <p className="text-muted-foreground">
            Ortak font havuzu — yazdığın metnin her fontla nasıl göründüğünü gör, indir. Font sitesinden inen ZIP'i olduğu gibi yükleyebilirsin; içindeki fontlar alınır, lisans/görsel dosyaları atlanır.
          </p>
        </div>
        {yazabilir && (
          <>
            <Button size="sm" disabled={yukle.isPending}
              onClick={() => dosyaRef.current?.click()}>
              {yukle.isPending
                ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                : <Upload className="mr-1 h-4 w-4" />}
              Font veya ZIP yükle
            </Button>
            <input ref={dosyaRef} type="file" multiple hidden
              accept=".ttf,.otf,.woff,.woff2,.zip,font/*,application/zip"
              onChange={(e) => {
                dosyaSec(Array.from(e.target.files ?? []))
                e.target.value = ""
              }} />
          </>
        )}
      </div>

      {/* önizleme kontrolleri — sayfa kaydırılırken de erişilebilir kalsın */}
      <div className="sticky top-2 z-20 space-y-2 rounded-lg border bg-background/95 p-3 shadow-sm backdrop-blur">
        <div className="flex items-center gap-2">
          <Type className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input value={metin} onChange={(e) => setMetin(e.target.value)}
            placeholder="Önizleme metni yaz…" className="flex-1" />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            Punto
            <input type="range" min={12} max={96} value={punto} className="accent-primary"
              onChange={(e) => setPunto(Number(e.target.value))} />
            <span className="w-10 tabular-nums text-foreground">{punto}px</span>
          </label>
          <Button variant="outline" size="sm" onClick={() => setKoyu((k) => !k)}>
            {koyu ? "Açık zemin" : "Koyu zemin"}
          </Button>
          <div className="relative ml-auto w-52">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={ara} onChange={(e) => setAra(e.target.value)}
              disabled={karsilastir}
              placeholder={karsilastir ? "Karşılaştırmada arama kapalı" : "Font veya müşteri ara"}
              className="pl-7" />
          </div>
        </div>
      </div>

      {/* seçim şeridi — yalnız bir şey seçiliyken görünür, boşuna yer kaplamasın */}
      {secili.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-accent/40 p-2">
          <Check className="h-4 w-4 text-primary" />
          <span className="text-sm font-medium">{secili.size} font seçili</span>
          <Button size="sm" variant={karsilastir ? "default" : "outline"}
            onClick={() => {
              // Karşılaştırmaya geçerken ARAMA TEMİZLENİR: kullanıcı fontları
              // aramayla bulup seçiyor, ama seçtikleri farklı aramalardan gelmiş
              // olabilir — açık bir arama, seçtiği fontların bir kısmını gizlerdi.
              // "Karşılaştır" demek "seçtiğim her şeyi göster" demektir.
              if (!karsilastir) setAra("")
              setKarsilastir((k) => !k)
            }}>
            <Columns2 className="mr-1 h-3.5 w-3.5" />
            {karsilastir ? "Tüm havuza dön" : "Seçilenleri karşılaştır"}
          </Button>
          <Button size="sm" variant="ghost"
            onClick={() => { setSecili(new Set()); setKarsilastir(false) }}>
            Seçimi temizle
          </Button>
          {karsilastir && (
            <span className="text-xs text-muted-foreground">
              Aynı metin ve puntoyla yan yana — punto ve zemin üstteki şeritten değişir.
            </span>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
        </div>
      ) : gruplar.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          {(fonts ?? []).length === 0
            ? "Havuzda henüz font yok."
            : karsilastir ? "Seçili font kalmadı." : "Aramaya uyan font yok."}
        </p>
      ) : (
        <div className="space-y-4">
          {gruplar.map(([aile, liste]) => (
            <div key={aile} className="space-y-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{aile}</span>
                <Badge variant="outline">{liste.length} stil</Badge>
              </div>
              {liste.map((f) => (
                <FontRow key={f.id} font={f} metin={metin} punto={punto} koyu={koyu}
                  yazabilir={yazabilir} clients={clients ?? []}
                  secili={secili.has(f.id)} onSecim={() => secimDegistir(f.id)}
                  onSil={() => {
                    if (!confirm(`"${f.family} ${f.style}" havuzdan kaldırılsın mı?`)) return
                    sil.mutate(f.id, {
                      onSuccess: () => toast.success("Font kaldırıldı"),
                      onError: () => toast.error("Kaldırılamadı"),
                    })
                  }}
                  onAta={(clientId, assigned) => ata.mutate({ id: f.id, clientId, assigned })}
                  onAdlandir={(family, style) => adlandir.mutate({ id: f.id, family, style }, {
                    onSuccess: () => toast.success("Ad güncellendi"),
                  })} />
              ))}
            </div>
          ))}

          {/* Sayfalama: karşılaştırma kipinde anlamsız (kullanıcı zaten seçti). */}
          {!karsilastir && kalan > 0 && (
            <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
              <span className="text-sm text-muted-foreground">
                {gosterilecek.length} / {suzulen.length} font gösteriliyor
              </span>
              <Button variant="outline" size="sm" onClick={() => setLimit((l) => l + SAYFA)}>
                {kalan < SAYFA ? `Kalan ${kalan} fontu göster` : `Daha fazla göster (+${SAYFA})`}
              </Button>
              {kalan > SAYFA && (
                <Button variant="ghost" size="sm" onClick={() => setLimit(suzulen.length)}>
                  Hepsini göster ({suzulen.length})
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FontRow({ font, metin, punto, koyu, yazabilir, clients, secili, onSecim,
                  onSil, onAta, onAdlandir }: {
  font: FontItem
  metin: string
  punto: number
  koyu: boolean
  yazabilir: boolean
  clients: { id: number; name: string }[]
  secili: boolean
  onSecim: () => void
  onSil: () => void
  onAta: (clientId: number, assigned: boolean) => void
  onAdlandir: (family: string, style: string) => void
}) {
  const [duzenle, setDuzenle] = useState(false)
  const [family, setFamily] = useState(font.family)
  const [style, setStyle] = useState(font.style)
  const atanmis = new Set(font.clients.map((c) => c.id))

  return (
    <div className={cn("space-y-1.5 rounded-md transition-colors",
      secili && "bg-accent/40 p-2 ring-1 ring-primary/30")}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {/* Karşılaştırma seçimi — herkeste var (okuma yetkisi yeter), yazma
            yetkisiyle ilgisi yok. */}
        <input type="checkbox" checked={secili} onChange={onSecim}
          title="Karşılaştırmak için seç"
          className="h-4 w-4 shrink-0 cursor-pointer accent-primary" />
        {duzenle ? (
          <>
            <Input value={family} onChange={(e) => setFamily(e.target.value)}
              className="h-8 w-40" placeholder="Aile" />
            <Input value={style} onChange={(e) => setStyle(e.target.value)}
              className="h-8 w-32" placeholder="Stil" />
            <Button size="sm" onClick={() => { onAdlandir(family, style); setDuzenle(false) }}>
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDuzenle(false)}>Vazgeç</Button>
          </>
        ) : (
          <>
            <span className="font-medium">{font.style}</span>
            <Badge variant="secondary" className="text-[10px] uppercase">{font.format}</Badge>
            <span className="text-xs text-muted-foreground">{formatSize(font.file_size)}</span>
            {font.clients.map((c) => (
              <Badge key={c.id} variant="outline" className="text-[10px]">{c.name}</Badge>
            ))}
          </>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => downloadFont(font)} title="İndir">
            <Download className="h-3.5 w-3.5" />
          </Button>
          {yazabilir && !duzenle && (
            <>
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="sm" title="Müşteriye ata" />}>
                  <Users className="h-3.5 w-3.5" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
                  {clients.map((c) => (
                    <DropdownMenuItem key={c.id}
                      onClick={() => onAta(c.id, !atanmis.has(c.id))}>
                      <span className={cn("mr-2 w-3.5", atanmis.has(c.id) && "text-primary")}>
                        {atanmis.has(c.id) ? "✓" : ""}
                      </span>
                      <span className="truncate">{c.name}</span>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="ghost" size="sm" onClick={() => setDuzenle(true)} title="Adı düzelt">
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              {/* Silme kuralını panel YENİDEN KURMAZ: bayrak backend'ten gelir
                  (yönetim ayrımsız / tasarımcı yalnız kendi yüklediği, 2026-08-06).
                  Yetki yoksa düğme hiç render edilmez — tıklanıp 403 almak,
                  düğmenin baştan olmamasından daha kötü bir deneyim. */}
              {font.can_delete && (
                <Button variant="ghost" size="sm" onClick={onSil} title="Havuzdan kaldır">
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              )}
            </>
          )}
        </div>
      </div>
      <FontPreview font={font} text={metin} size={punto} dark={koyu} />
    </div>
  )
}
