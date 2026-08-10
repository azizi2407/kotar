"""Revize video eşleşmesi — "yeni yükleme hangi eski sürümü geçersiz kılıyor?"

NEDEN VAR: videograf düzeltilmiş videoyu yüklerken dosya adına küçük bir ek
koyuyor (`camsaş0728.mp4` → `camsaşr0728.mp4`); eski sürüm panelde, sunucuda ve
Drive'da öylece kalıyordu. Bu modül ADI okuyup eski sürümü tespit eder.

SAF TUTULDU — DB'ye, diske, Drive'a dokunmaz; adayları çağıran verir. Kuralın
en çok değişecek parça olması bekleniyor, tek başına test edilebilir olmalı.
Silme kararı ve güvenlik kapıları (paylaşılmış mı, onaya girmiş mi) çağıranda:
`sharing._supersede_previous_videos`.

YÖNTEM — "ek çıkarma": yeni addan bir sürüm eki çıkarıldığında eski ada
eşitleniyorsa, o eski sürümdür. Gerçek üretim verisinde (165 video, 2026-08-01)
**35 eşleşme, 0 yanlış pozitif** verdi.

    r/R   camsaşr0728 → camsaş0728 · camsaşinşaat0801r → camsaşinşaat0801
    rev   albarev0731 → alba0731
    - N   MUTLU DAHİLER - 23 - 2 → MUTLU DAHİLER - 23
    .N    camsaş0728.2 → camsaş0728

Kural KAÇIRMAYA meyilli tasarlandı: `bdkrev` (tabanı `bdk`) ya da `ATLASRENKREV`
insan gözüyle revize olsa da ad bunu kanıtlamadığı için dokunulmaz. Sağlam bir
videoyu silmektense bir revizeyi kaçırmak yeğdir.
"""
import os
import re
import unicodedata

# Sayılı sürüm eki. `- N` YALNIZ ad zaten bir `- <sayı>` taşıyorsa sürümdür:
# `MÜŞTERİ - 28` HAFTA videosudur (bağımsız), `MÜŞTERİ - 28 - 2` o haftanın 2.
# sürümü. Bu ayrım olmadan `- 27` ile `- 28` kardeş sanılıyor ve farklı
# haftaların sağlam videoları siliniyordu (2026-08-01 kuru koşusu yakaladı).
_DASH_N = re.compile(r'^(.*\s-\s*\d+)\s*-\s*(\d+)$')
_DOT_N = re.compile(r'^(.+?)\.(\d+)$')


def _tr_fold(s):
    """Türkçe-duyarlı harf katlama.

    ÖNCE Unicode NFC: üretimdeki adlar **NFD** geliyor (`ş` = `s` + birleşen
    çengel; `camsaşinşaat0801.mp4` 22 kod noktası, NFC'de 20). macOS/iPhone NFD
    üretir, Windows/Android NFC — videograf cihaz değiştirdiğinde iki sürüm
    farklı formda kaydolur ve eşleşme SESSİZCE kaçardı (2026-08-01'de canlı
    veride yakalandı).

    Sonra Türkçe: Python'un düz `casefold()`'u `İ`'yi `i̇` (i + birleşen nokta)
    yapar → `İDEAL0725R` ile `ideal0725` eşleşmezdi. Noktalı/noktasız i ayrımını
    da siliyoruz: dosya adlarında `I`/`ı`/`i` tutarsız kullanılıyor."""
    s = unicodedata.normalize('NFC', s)
    return (s.replace('İ', 'i').replace('I', 'ı').replace('ı', 'i')
             .casefold().replace('i̇', 'i'))


def normalize(name):
    """Karşılaştırma kökü: uzantısız, boşluk-teklenmiş, Türkçe-katlanmış."""
    if not name:
        return ''
    root = os.path.splitext(name)[0]
    return _tr_fold(re.sub(r'\s+', ' ', root).strip())


def version_key(name):
    """`(taban, sürüm_no)` — sayılı sürüm eki varsa ayrıştırır, yoksa no=0.

        MÜŞTERİ - 28        → ('müşteri - 28', 0)   ← hafta videosu, sürüm DEĞİL
        MÜŞTERİ - 28 - 2    → ('müşteri - 28', 2)
        camsaş0728.2        → ('camsaş0728', 2)

    Sürüm zincirini sayı karşılaştırmasıyla çözer: aynı tabanın daha küçük
    numaralı üyeleri eskidir. Taban eşitliği şart olduğu için `- 27` ile `- 28`
    (iki ayrı hafta) asla kardeş sayılmaz."""
    n = normalize(name)
    if not n:
        return '', 0
    for rx in (_DASH_N, _DOT_N):
        m = rx.match(n)
        if m:
            return m.group(1).strip(), int(m.group(2))
    return n, 0


def base_candidates(name):
    """`name`'den HARF eki (r / rev) çıkarılarak elde edilebilecek taban adlar.

    Sayılı ekler burada DEĞİL — onlar `version_key`'in işi (sıra karşılaştırması
    gerekiyor). Küme döner; kendisi asla içinde değildir."""
    n = normalize(name)
    if not n:
        return set()
    out = set()

    # 'r' eki: konumu sabit değil — tarihten önce de sonra da gelebiliyor.
    # Tahmin etmek yerine her `r`'yi tek tek düşürüp aday üretiyoruz. Fazladan
    # adaylar (`rizonr0727` → `izonr0727`) zararsız: gerçek bir ada eşleşmezler.
    for i, ch in enumerate(n):
        if ch == 'r':
            out.add(n[:i] + n[i + 1:])

    # 'rev' hecesi (albarev0731 → alba0731)
    for m in re.finditer('rev', n):
        out.add(n[:m.start()] + n[m.end():])

    out.discard(n)
    return {c.strip() for c in out if c.strip()}


def find_superseded(new_name, older):
    """`older` içinden `new_name`'in geçersiz kıldığı kayıtları döndür.

    `older`: `file_name` niteliği olan, YENİDEN ESKİ oldukları çağıran tarafından
    garanti edilmiş kayıtlar (zaman süzgeci burada değil — bu modül saf).

    İki bağımsız yol:
      1. **Sayılı zincir** — aynı taban, daha küçük sürüm no. `- 28 - 4` gelince
         `- 28`, `- 28 - 2`, `- 28 - 3` birlikte yakalanır.
      2. **Harf eki** — eski kaydın tam adı, yeni addan `r`/`rev` çıkarılarak
         elde edilen adaylardan biriyse."""
    cands = base_candidates(new_name)
    yeni_taban, yeni_no = version_key(new_name)
    hits = []
    for rec in older:
        ad = getattr(rec, 'file_name', None)
        n = normalize(ad)
        if not n:
            continue
        eski_taban, eski_no = version_key(ad)
        if yeni_no > 0 and eski_taban == yeni_taban and eski_no < yeni_no:
            hits.append(rec)
        elif n in cands:
            hits.append(rec)
    return hits
