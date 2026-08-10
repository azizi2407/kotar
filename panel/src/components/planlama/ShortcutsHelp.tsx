// Kısayol yardımı — `?` ile açılır.
//
// NEDEN VAR: tuvalde Ctrl+C/V/D/G/A, Space+sürükle, Delete, V/H gibi bir düzine
// kısayol var ve HİÇBİRİ ekranda yazmıyordu. Kullanıcı bunları ancak tesadüfen
// keşfedebilirdi; pratikte hiç kullanılmıyorlardı.
import { Keyboard, X } from "lucide-react"

const KISAYOLLAR: { tus: string; ne: string }[] = [
  { tus: "Çift tık", ne: "Kart/not metnini düzenle" },
  { tus: "Sağ tık", ne: "Ekleme ve öğe menüsü" },
  { tus: "Sürükle", ne: "Kutu ile çoklu seçim (Seç modu)" },
  { tus: "Space + sürükle", ne: "Tuvali kaydır" },
  { tus: "Orta tuş", ne: "Tuvali kaydır" },
  { tus: "V / H", ne: "Seç modu / Kaydır modu" },
  { tus: "Shift + tık", ne: "Seçime ekle" },
  { tus: "Ctrl + A", ne: "Tümünü seç" },
  { tus: "Ctrl + C / V", ne: "Kopyala / yapıştır" },
  { tus: "Ctrl + D", ne: "Çoğalt" },
  { tus: "Ctrl + G", ne: "Seçimi grupla" },
  { tus: "Ctrl + Z", ne: "Geri al" },
  { tus: "Ctrl + Shift + Z", ne: "İleri al" },
  { tus: "Delete", ne: "Seçimi sil" },
  { tus: "Ok tuşları", ne: "Seçili öğeyi kaydır" },
  { tus: "Tutamaktan boşluğa çek", ne: "Bağlı yeni kart oluştur" },
  { tus: "Okun ucundan çek", ne: "Oku başka bir öğeye bağla" },
  { tus: "Ctrl/Cmd + V (görsel)", ne: "Panodaki görseli yapıştır" },
  { tus: "?", ne: "Bu listeyi aç/kapat" },
]

export function ShortcutsHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}>
      <div className="max-h-full w-full max-w-md overflow-y-auto rounded-lg border bg-popover p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Keyboard className="h-4 w-4" /> Klavye ve fare kısayolları
          </h2>
          <button type="button" onClick={onClose} title="Kapat"
            className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
          {KISAYOLLAR.map((k) => (
            <div key={k.tus} className="contents">
              <dt className="text-right">
                <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">{k.tus}</kbd>
              </dt>
              <dd className="text-muted-foreground">{k.ne}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
