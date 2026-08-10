// Sesli not gövdesi (2026-08-09): özet, maddeler, görev önerileri, transkript.
//
// ONAY KAPISI: ajanın ürettiği görevler panoya KENDİLİĞİNDEN gitmez. Kullanıcı
// seçer, isterse müşteri/kişi/tarih düzeltir, sonra "Panoya ekle" der. Repo
// invaryantı (AI üretimi taslak doğar) ve kullanıcının yerleşik tercihi.
import { useMemo, useState } from "react"
import { ChevronDown, Loader2, Send, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { apiGet, apiJson } from "@/lib/api"
import { useClients, useUsers } from "@/lib/clients"
import { useAuth } from "@/lib/auth"
import { DEFAULT_SIZE, type PlanningItem } from "@/lib/planlama"
import {
  DURUM_METNI, sureMetni, useDeleteVoiceNote, usePatchVoiceNote, useVoiceNote,
  voiceNoteAudioUrl, type Gorev,
} from "@/lib/voice-notes"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** Görev anahtarı görevin İÇERİĞİNDEN türetilir, dizi indeksinden DEĞİL.
 *
 *  Önceki sürüm `vn-<noteId>-<i>` kullanıyordu: `normalize_structured` boş
 *  `metin`li görevi listeden DÜŞÜRÜYOR, yani indeksler kayabiliyor. Somut
 *  hata: not 5'te A(0)/B(1)/C(2) var, C panoya itilir (`vn-5-2`), sonra A
 *  silinip kaydedilince liste [B,C] olur, C artık indeks 1 → anahtarı
 *  `vn-5-1` → `pushed_item_keys`'te yok → onay kutusu yeniden açılır → AYNI
 *  görev ikinci kez panoya düşer (ters yönde: yanlış öğe "panoda ✓" görünüp
 *  hiç eklenemez). İçerik-türetilmiş anahtar liste kaysa da SABİT kalır.
 *
 *  Hash basit djb2 + base36 — Türkçe karakter/boşluk/+/- üretmez, pano
 *  `item_key` deseniyle ([A-Za-z0-9_-]{1,64}) uyumludur. Aynı not içinde iki
 *  görev BİREBİR aynı metne sahipse anahtar çakışır; bu kabul edilebilir bir
 *  kenar durum (aynı metin = kullanıcı gözünde zaten "aynı görev").
 *
 *  TEK fonksiyon: hem "Panoya ekle" yazarken hem "panoda ✓" kontrolünde
 *  aynısı kullanılıyor — ikisi ayrışırsa anahtarlar birbirini tanımaz. */
function _hashMetni(s: string) {
  let h = 5381
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0
  }
  return (h >>> 0).toString(36)
}

function gorevAnahtari(noteId: number, gorev: Gorev) {
  return `vn-${noteId}-${_hashMetni(gorev.metin)}`
}

export function NoteDetail({ noteId }: { noteId: number }) {
  const { data: not, isLoading } = useVoiceNote(noteId)
  // useClients parametre İSTER (status/q) — imzası: useClients({status, q}).
  const { data: clients } = useClients({ status: "active", q: "" })
  const { data: users } = useUsers()
  const { user } = useAuth()
  const patch = usePatchVoiceNote()
  const sil = useDeleteVoiceNote()
  const [secili, setSecili] = useState<Set<number>>(new Set())
  const [duzeltme, setDuzeltme] = useState<Record<number, Gorev>>({})
  const [pano, setPano] = useState<string>("")
  const [transkriptAcik, setTranskriptAcik] = useState(false)
  const [gonderiliyor, setGonderiliyor] = useState(false)

  const panoSecenekleri = useMemo(() => [
    { key: `user:${user?.sub ?? ""}`, label: "Kendi panom" },
    { key: "management", label: "Yönetim panosu" },
  ], [user?.sub])

  if (isLoading || !not) return <Skeleton className="h-64 w-full" />

  /** Silme HER durumda erişilebilir olmalı.
   *
   *  Eskiden düğme yalnız `done` dalındaydı; `failed` bir kayıt panelde kalıcı
   *  olarak takılı kalıyordu (kullanıcı bildirdi, 2026-08-09) ve tek çare
   *  veritabanına dokunmaktı. Sunucu ucu zaten duruma BAKMIYOR (soft-delete),
   *  eksik olan yalnız arayüzdü. `queued`/`running` de dahil: worker çökerse
   *  kayıt sonsuza dek "hazırlanıyor"da kalır ve silinemezdi. Uçuştaki bir işi
   *  silmek zararsız — worker sonucu soft-silinmiş satıra yazar, liste zaten
   *  `deleted_at` süzer. */
  async function notuSil(id: number) {
    if (!confirm("Bu sesli not silinsin mi?")) return
    try {
      await sil.mutateAsync(id)
      toast.success("Not silindi")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Not silinemedi")
    }
  }

  if (not.status === "queued" || not.status === "running") {
    return (
      <div className="flex items-center justify-between gap-2 rounded-lg border p-6">
        <span className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Not hazırlanıyor… ({DURUM_METNI[not.status]})
        </span>
        <Button variant="ghost" size="sm" title="Notu sil" aria-label="Notu sil"
          onClick={() => void notuSil(not!.id)}>
          <Trash2 className="h-4 w-4 text-destructive" />
        </Button>
      </div>
    )
  }
  if (not.status === "failed") {
    return (
      <div className="space-y-2 rounded-lg border border-destructive/50 p-6">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="font-medium text-destructive">Not oluşturulamadı</p>
            <p className="text-sm text-muted-foreground">{not.error ?? "Bilinmeyen hata"}</p>
          </div>
          <Button variant="ghost" size="sm" title="Notu sil" aria-label="Notu sil"
            onClick={() => void notuSil(not!.id)}>
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
        {not.transcript && (
          <div className="rounded bg-muted/40 p-3 text-sm">{not.transcript}</div>
        )}
        {/* Ses KALIR: transkript çıkmasa da kullanıcı kaydı dinleyip ne söylediğini
            hatırlayabilsin, sonra silsin. */}
        <audio controls src={voiceNoteAudioUrl(not.id)} className="w-full" />
      </div>
    )
  }

  const gorevler = not.structured.gorevler
  const eklendi = new Set(not.pushed_item_keys)

  function gorevAl(i: number): Gorev {
    return duzeltme[i] ?? gorevler[i]
  }

  function guncelle(i: number, yama: Partial<Gorev>) {
    setDuzeltme((d) => ({ ...d, [i]: { ...gorevAl(i), ...yama } }))
  }

  async function panoyaEkle() {
    const hedef = pano || panoSecenekleri[0].key
    const secim = [...secili].filter((i) => !eklendi.has(gorevAnahtari(not!.id, gorevAl(i))))
    if (!secim.length) {
      toast.error("Eklenecek görev seçilmedi.")
      return
    }
    setGonderiliyor(true)
    try {
      // Yerleşim: sabit (40,40) taban KULLANILMAZ — art arda eklenen notların
      // kartları birbirinin tam üstüne biner (aynı köşeden başlarlardı; kullanıcı
      // kartların kaybolduğunu sanır). Bunun yerine hedef panonun MEVCUT
      // öğeleri okunur ve yeni kartlar en alttaki öğenin altına, boş bir yere
      // yerleştirilir. Pano okuma başarısız olursa (ağ/403) aktarım BLOKLANMAZ:
      // eski sabit (40,40) tabanına düşülür — kullanıcı görevini kaybetmesin.
      let tabanY = 40
      try {
        const mevcutPano = await apiGet(`/planning/boards/${encodeURIComponent(hedef)}`) as
          { items: PlanningItem[] }
        if (mevcutPano.items.length) {
          const enAltKenar = Math.max(...mevcutPano.items.map((it) => {
            // height NULL olabilir (tür varsayılanı panelde kararlaştırılır) —
            // undefined aritmetiğine düşmemek için tür bazlı varsayılana in.
            const yukseklik = it.height ?? DEFAULT_SIZE[it.type]?.height ?? 120
            return it.y + yukseklik
          }))
          tabanY = enAltKenar + 40 // kartlar arası boşluk
        }
      } catch {
        // pano okunamadı — sabit (40,40) tabanına düş, aktarımı engelleme
      }
      // Kartlar üst üste binmesin diye 200 px aralık (pano kart genişliği ~240 px).
      const upsert = secim.map((i, sira) => {
        const g = gorevAl(i)
        return {
          item_key: gorevAnahtari(not!.id, g),
          type: "card",
          title: g.metin,
          text: not!.structured.baslik ? `Sesli not: ${not!.structured.baslik}` : null,
          x: 40, y: tabanY + sira * 200,
          client_id: g.client_id, assignee_sub: g.assignee_sub, due_date: g.due_date,
        }
      })
      // Mevcut hook `usePlanningPatch(boardKey)` bir boardKey'e SABİTLENİYOR;
      // burada hedef pano çalışma anında seçildiği için doğrudan apiJson
      // kullanılıyor (aynı uç, aynı gövde). `base_version` gönderilmiyor:
      // yalnız YENİ öğe ekliyoruz, mevcut öğeye dokunmuyoruz → çakışma yok.
      await apiJson(`/planning/boards/${encodeURIComponent(hedef)}/items`,
                    { upsert }, "PATCH")
      await patch.mutateAsync({
        id: not!.id,
        pushedItemKeys: secim.map((i) => gorevAnahtari(not!.id, gorevAl(i))),
      })
      setSecili(new Set())
      toast.success(`${secim.length} görev panoya eklendi`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Panoya eklenemedi")
    } finally {
      setGonderiliyor(false)
    }
  }

  async function duzeltmeyiKaydet() {
    const yeni = { ...not!.structured, gorevler: gorevler.map((_, i) => gorevAl(i)) }
    try {
      await patch.mutateAsync({ id: not!.id, structured: yeni })
      setDuzeltme({})
      toast.success("Not güncellendi")
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Not güncellenemedi")
    }
  }

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{not.structured.baslik || "Sesli not"}</h2>
          <p className="text-xs text-muted-foreground">
            {sureMetni(not.duration_sec)} · {new Date(not.created_at ?? "").toLocaleString("tr-TR")}
          </p>
        </div>
        <Button variant="ghost" size="sm" title="Notu sil" aria-label="Notu sil"
          onClick={() => void notuSil(not!.id)}>
          <Trash2 className="h-4 w-4 text-destructive" />
        </Button>
      </div>

      {not.structured.ozet && <p className="text-sm">{not.structured.ozet}</p>}

      {not.structured.maddeler.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {not.structured.maddeler.map((m, i) => <li key={i}>{m}</li>)}
        </ul>
      )}

      {gorevler.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">Çıkan görevler</p>
          {gorevler.map((_, i) => {
            const g = gorevAl(i)
            const anahtar = gorevAnahtari(not.id, g)
            const zatenEklendi = eklendi.has(anahtar)
            return (
              <div key={i} className={cn("flex flex-wrap items-center gap-2 rounded border p-2",
                zatenEklendi && "opacity-60")}>
                <input type="checkbox" disabled={zatenEklendi}
                  checked={secili.has(i)}
                  onChange={(e) => setSecili((s) => {
                    const n = new Set(s)
                    if (e.target.checked) n.add(i)
                    else n.delete(i)
                    return n
                  })} />
                <Input value={g.metin} className="h-8 min-w-48 flex-1"
                  onChange={(e) => guncelle(i, { metin: e.target.value })} />
                <select className="h-8 rounded border bg-background px-2 text-sm"
                  value={g.client_id ?? ""}
                  onChange={(e) => guncelle(i, {
                    client_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">müşteri yok</option>
                  {(clients ?? []).map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
                <select className="h-8 rounded border bg-background px-2 text-sm"
                  value={g.assignee_sub ?? ""}
                  onChange={(e) => guncelle(i, { assignee_sub: e.target.value || null })}>
                  <option value="">kişi yok</option>
                  {(users ?? []).map((u) => (
                    <option key={u.sub} value={u.sub}>{u.name}</option>
                  ))}
                </select>
                <Input type="date" className="h-8 w-36" value={g.due_date ?? ""}
                  onChange={(e) => guncelle(i, { due_date: e.target.value || null })} />
                {zatenEklendi && <Badge variant="outline">panoda ✓</Badge>}
              </div>
            )
          })}

          <div className="flex flex-wrap items-center gap-2">
            <select className="h-9 rounded-md border bg-background px-2 text-sm"
              value={pano} onChange={(e) => setPano(e.target.value)}>
              {panoSecenekleri.map((p) => (
                <option key={p.key} value={p.key}>{p.label}</option>
              ))}
            </select>
            <Button onClick={panoyaEkle} disabled={gonderiliyor || secili.size === 0}>
              {gonderiliyor ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                : <Send className="mr-1 h-4 w-4" />}
              Panoya ekle ({secili.size})
            </Button>
            {Object.keys(duzeltme).length > 0 && (
              <Button variant="outline" onClick={duzeltmeyiKaydet} disabled={patch.isPending}>
                Düzeltmeleri kaydet
              </Button>
            )}
          </div>
        </div>
      )}

      <div>
        <button type="button" onClick={() => setTranskriptAcik((a) => !a)}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ChevronDown className={cn("h-4 w-4 transition-transform",
            !transkriptAcik && "-rotate-90")} />
          Transkript
        </button>
        {transkriptAcik && (
          <div className="mt-2 whitespace-pre-wrap rounded bg-muted/40 p-3 text-sm">
            {not.transcript}
          </div>
        )}
      </div>

      <audio controls src={voiceNoteAudioUrl(not.id)} className="w-full" />
    </div>
  )
}
