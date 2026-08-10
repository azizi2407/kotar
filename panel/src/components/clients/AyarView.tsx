// Müşteri "Ayar"ının SALT-OKU görünümü (düz DB şeması — `brand_profile` alanları).
//
// İKİ sayfa paylaşır: `/clients/:id` Vault Ayar sekmesi (management, düzenleme
// düğmesiyle birlikte) ve `/marka-rehberi` (üretim rolleri, yalnız okuma).
// Bu yüzden ClientDetailPage'den buraya taşındı — iki yerde ayrı kopya tutmak
// alan eklendiğinde birinin geride kalması demekti.
import { Badge } from "@/components/ui/badge"
import type { VaultAyar } from "@/lib/clients"

export function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm">{value || "—"}</div>
    </div>
  )
}

// Serbest metin bloğu (Marka Rehberi / İçerik Sütunları — whitespace korunur).
export function TextBlock({ label, value }: { label: string; value?: string | null }) {
  if (!value || !value.trim()) return null
  return (
    <div className="space-y-1">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="whitespace-pre-wrap rounded-lg border bg-muted/20 p-3 text-sm">{value}</div>
    </div>
  )
}

export function AyarView({ ayar }: { ayar: VaultAyar }) {
  const forbidden = ayar.forbidden ?? []
  const palette = ayar.color_palette ?? []
  const mixEntries = Object.entries(ayar.content_mix ?? {})
  const hashtagEntries = Object.entries(ayar.hashtags ?? {})
  const days = ayar.posting_days ?? []
  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Marka Sesi" value={ayar.brand_voice} />
        <Field label="Hedef Kitle" value={ayar.target_audience} />
        <Field label="Birincil CTA" value={ayar.cta} />
        <Field label="Haftalık Fikir Sayısı" value={ayar.ideas_per_week} />
      </div>

      {days.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">Paylaşım Günleri</div>
          <div className="flex flex-wrap gap-1.5">
            {days.map((d) => <Badge key={d} variant="outline">{d}</Badge>)}
          </div>
        </div>
      )}

      {forbidden.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">Yasaklar</div>
          <ul className="list-disc space-y-0.5 pl-5 text-sm">
            {forbidden.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </div>
      )}

      {palette.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">Renk Paleti</div>
          <div className="flex flex-wrap gap-2">
            {palette.map((hex, i) => (
              <span key={i} className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
                <span className="h-4 w-4 rounded-sm border" style={{ backgroundColor: hex }} />
                {hex}
              </span>
            ))}
          </div>
        </div>
      )}

      {mixEntries.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs text-muted-foreground">İçerik Dağılımı</div>
          <div className="flex flex-wrap gap-1.5">
            {mixEntries.map(([k, v]) => (
              <Badge key={k} variant="secondary">{k}: {String(v)}</Badge>
            ))}
          </div>
        </div>
      )}

      {hashtagEntries.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">Hashtag Setleri</div>
          {hashtagEntries.map(([group, tags]) => (
            <div key={group} className="space-y-1">
              <div className="text-[11px] font-medium text-muted-foreground">{group}</div>
              <div className="flex flex-wrap gap-1">
                {(tags ?? []).map((t, i) => (
                  <span key={i} className="rounded bg-muted px-1.5 py-0.5 text-xs">
                    {t.startsWith("#") ? t : `#${t}`}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <TextBlock label="Marka Rehberi (guide_md)" value={ayar.guide_md} />
      <TextBlock label="İçerik Sütunları (content_pillars)" value={ayar.content_pillars} />
    </div>
  )
}
