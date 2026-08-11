#!/usr/bin/env python3
"""Import fonts from Google Fonts into the pool (2026-08-06).

**Single source: `github.com/google/fonts`** — the official repo, all
OFL/Apache licensed (open for commercial use) and scanned by Google.
Random "free font" sites are DELIBERATELY not used: they carry both a
licensing risk and a malicious-file risk.

Every file passes THREE gates before entering the pool:
  1. **Font signature** (`fonts._format_of`) — the extension isn't trusted.
  2. **Turkish glyphs** — whether the font's own `cmap` table has Ğ ğ İ ı Ş ş.
     We don't trust Google's `latin-ext` tag: the tag is a subset name, not a
     glyph guarantee. A font without Turkish is useless to the agency.
  3. **Size** — `fonts.MAX_FONT_BYTES`.

Usage:
    python scripts/font_import.py                 # dry run (downloads, validates, doesn't write)
    python scripts/font_import.py --apply         # add to the pool
    python scripts/font_import.py --apply --only inter
"""
import argparse
import os
import struct
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
import fonts as F  # noqa: E402
from extensions import db  # noqa: E402

RAW = 'https://raw.githubusercontent.com/google/fonts/main'
UA = {'User-Agent': 'agency-font-import'}

# The distinguishing letters of Turkish for font selection. "İ" (U+0130) and
# "ı" (U+0131) are usually the first to be missing in an incomplete font —
# they're the real point of this check.
TURKCE = {0x011E: 'Ğ', 0x011F: 'ğ', 0x0130: 'İ', 0x0131: 'ı', 0x015E: 'Ş', 0x015F: 'ş'}

# (dir, file, family, style). License directory is `ofl` (SIL Open Font License).
# For variable fonts the file name is `Family[wght].ttf` — a single file
# carries all weights; the style is tagged "Variable" so it's clear in the
# panel what it is.
KATALOG = [
    # --- Sans ---
    ('montserrat', 'Montserrat[wght].ttf', 'Montserrat', 'Variable'),
    ('montserrat', 'Montserrat-Italic[wght].ttf', 'Montserrat', 'Variable Italic'),
    ('inter', 'Inter[opsz,wght].ttf', 'Inter', 'Variable'),
    ('raleway', 'Raleway[wght].ttf', 'Raleway', 'Variable'),
    ('worksans', 'WorkSans[wght].ttf', 'Work Sans', 'Variable'),
    ('dmsans', 'DMSans[opsz,wght].ttf', 'DM Sans', 'Variable'),
    ('manrope', 'Manrope[wght].ttf', 'Manrope', 'Variable'),
    ('figtree', 'Figtree[wght].ttf', 'Figtree', 'Variable'),
    # Poppins is distributed as static (no variable version) — four useful cuts.
    ('poppins', 'Poppins-Regular.ttf', 'Poppins', 'Regular'),
    ('poppins', 'Poppins-Italic.ttf', 'Poppins', 'Italic'),
    ('poppins', 'Poppins-SemiBold.ttf', 'Poppins', 'SemiBold'),
    ('poppins', 'Poppins-Bold.ttf', 'Poppins', 'Bold'),
    # --- Serif ---
    ('playfairdisplay', 'PlayfairDisplay[wght].ttf', 'Playfair Display', 'Variable'),
    ('lora', 'Lora[wght].ttf', 'Lora', 'Variable'),
    ('merriweather', 'Merriweather[opsz,wdth,wght].ttf', 'Merriweather', 'Variable'),
    ('ebgaramond', 'EBGaramond[wght].ttf', 'EB Garamond', 'Variable'),
    # --- Headline / display ---
    ('oswald', 'Oswald[wght].ttf', 'Oswald', 'Variable'),
    ('bebasneue', 'BebasNeue-Regular.ttf', 'Bebas Neue', 'Regular'),
    ('archivoblack', 'ArchivoBlack-Regular.ttf', 'Archivo Black', 'Regular'),
    # --- Handwriting ---
    ('caveat', 'Caveat[wght].ttf', 'Caveat', 'Variable'),
    ('dancingscript', 'DancingScript[wght].ttf', 'Dancing Script', 'Variable'),
    # --- Mono ---
    ('jetbrainsmono', 'JetBrainsMono[wght].ttf', 'JetBrains Mono', 'Variable'),
]


# --- bulk discovery (2026-08-06) ----------------------------------------------
# The family list is manually curated (families relevant to agency work), the
# FILE NAMES are automatic: each family's `METADATA.pb` gives both file names
# and subsets. `raw.githubusercontent.com` is used instead of the GitHub API —
# the API is limited to 60 anonymous requests/hour, which would run out
# immediately when scanning 100+ families; the raw CDN has no such limit.

KURATORLU_AILELER = """
roboto opensans lato notosans sourcesans3 nunito nunitosans rubik karla mulish
publicsans plusjakartasans outfit sora urbanist spacegrotesk barlow cabin catamaran
exo2 firasans heebo ibmplexsans josefinsans jost kanit lexend librefranklin mavenpro
mukta overpass quicksand redhatdisplay robotocondensed ubuntu asap assistant chivo
commissioner epilogue hind lexenddeca oxygen prompt questrial saira sarabun signika
teko titilliumweb varelaround yantramanav archivo baijamjuree hankengrotesk
notoserif ptserif robotoslab sourceserif4 crimsontext cormorantgaramond
librebaskerville spectral bitter arvo domine faustina ibmplexserif literata
newsreader vollkorn zillaslab alegreya cardo gelasio neuton rokkitt tinos
anton righteous alfaslabone fjallaone staatliches russoone lilitaone changa
comfortaa concertone fredoka luckiestguy orbitron pacifico permanentmarker
titanone ultra unicaone bowlbyone bungee
greatvibes satisfy sacramento kalam indieflower shadowsintolight amaticsc
courgette cookie allura yellowtail patrickhand gloriahallelujah architectsdaughter
handlee calligraffitti
robotomono sourcecodepro spacemono ibmplexmono firacode inconsolata cousine
ptmono sharetechmono overpassmono cutivemono anonymouspro azeretmono redhatmono
dmmono martianmono
""".split()

LISANS_DIZINLERI = ('ofl', 'apache', 'ufl')


def _metadata(aile):
    """(license_dir, text) or (None, None) — three license directories are tried."""
    for lis in LISANS_DIZINLERI:
        try:
            req = urllib.request.Request(
                f'{RAW}/{lis}/{aile}/METADATA.pb', headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return lis, r.read().decode('utf-8', 'replace')
        except Exception:  # noqa: BLE001 — 404 is normal, move to the next directory
            continue
    return None, None


def _metadata_coz(metin):
    """From METADATA.pb: (family_name, latin_ext, [(file, style, weight)...])."""
    import re as _re
    aile = (_re.search(r'^name:\s*"([^"]+)"', metin, _re.M) or [None, aile_yok := ''])[1]
    latin_ext = 'subsets: "latin-ext"' in metin
    kayitlar = []
    for blok in _re.findall(r'fonts\s*\{(.*?)\n\}', metin, _re.S):
        d = _re.search(r'filename:\s*"([^"]+)"', blok)
        s = _re.search(r'style:\s*"([^"]+)"', blok)
        w = _re.search(r'weight:\s*(\d+)', blok)
        if d:
            kayitlar.append((d.group(1), s.group(1) if s else 'normal',
                             int(w.group(1)) if w else 400))
    return aile, latin_ext, kayitlar


def _dosya_sec(kayitlar):
    """Which files should be taken from the family? `[(file, style_label)...]`

    If a Variable exists, take only that (one file carries all weights ->
    the pool doesn't bloat); otherwise the Regular (400) and Bold (700)
    cuts. Goal is 1-2 files per family: taking 9 weights from every family
    while adding 100+ fonts would make the pool unusable."""
    variable = [(d, s) for d, s, _w in kayitlar if '[' in d]
    if variable:
        return [(d, 'Variable' if s == 'normal' else 'Variable Italic')
                for d, s in variable][:2]
    secim = []
    for hedef, etiket in ((400, 'Regular'), (700, 'Bold')):
        eslesen = [d for d, s, w in kayitlar if w == hedef and s == 'normal']
        if eslesen:
            secim.append((eslesen[0], etiket))
    if not secim and kayitlar:                 # single-weight families
        secim = [(kayitlar[0][0], 'Regular')]
    return secim


def kesfet(aileler):
    """Scan family directories and produce rows in `KATALOG` shape."""
    satirlar, elenen = [], []
    for aile in aileler:
        lis, metin = _metadata(aile)
        if metin is None:
            elenen.append(f'{aile}: bulunamadı')
            continue
        ad, latin_ext, kayitlar = _metadata_coz(metin)
        if not latin_ext:
            elenen.append(f'{aile}: latin-ext yok (Türkçe desteklemiyor)')
            continue
        for dosya, etiket in _dosya_sec(kayitlar):
            satirlar.append((lis, aile, dosya, ad or aile, etiket))
    return satirlar, elenen


def indir(dizin, dosya, lisans='ofl'):
    url = f'{RAW}/{lisans}/{dizin}/{urllib.parse.quote(dosya)}'
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _cmap_kodlari(data):
    """Unicode code points in the TTF/OTF `cmap` table (formats 4 and 12).

    fontTools DEPENDENCY WAS NOT ADDED: not worth growing a production
    dependency for a one-off validation, all we need is "does this code point exist".
    """
    # On corrupt/short data this should return an EMPTY set instead of
    # raising `struct.error`: the caller treats an empty set as "no Turkish
    # letters at all" and rejects the font — the safe side. (A crash here
    # would take down the whole import; seen while testing the validator on
    # 2026-08-06.)
    try:
        if len(data) < 12:
            return set()
        tablo_sayisi = struct.unpack('>H', data[4:6])[0]
        cmap_off = None
        for i in range(tablo_sayisi):
            p = 12 + i * 16
            if p + 16 > len(data):
                return set()
            etiket, _, off, _ = struct.unpack('>4sIII', data[p:p + 16])
            if etiket == b'cmap':
                cmap_off = off
                break
        if cmap_off is None or cmap_off + 4 > len(data):
            return set()
        return _alt_tablolar(data, cmap_off)
    except (struct.error, ValueError, IndexError):
        return set()


def _alt_tablolar(data, cmap_off):

    alt_sayisi = struct.unpack('>H', data[cmap_off + 2:cmap_off + 4])[0]
    kodlar = set()
    for i in range(alt_sayisi):
        p = cmap_off + 4 + i * 8
        _plat, _enc, off = struct.unpack('>HHI', data[p:p + 8])
        t = cmap_off + off
        if t + 4 > len(data):
            continue
        bicim = struct.unpack('>H', data[t:t + 2])[0]
        if bicim == 4:
            seg_x2 = struct.unpack('>H', data[t + 6:t + 8])[0]
            seg = seg_x2 // 2
            son = struct.unpack(f'>{seg}H', data[t + 14:t + 14 + seg_x2])
            bas_off = t + 16 + seg_x2
            bas = struct.unpack(f'>{seg}H', data[bas_off:bas_off + seg_x2])
            for b, s in zip(bas, son):
                if s == 0xFFFF and b == 0xFFFF:
                    continue
                if s - b > 0x1000:          # excessively wide segment: scan cost
                    continue
                kodlar.update(range(b, s + 1))
        elif bicim == 12:
            grup = struct.unpack('>I', data[t + 12:t + 16])[0]
            for g in range(min(grup, 10000)):
                gp = t + 16 + g * 12
                bas, son, _gid = struct.unpack('>III', data[gp:gp + 12])
                if son - bas > 0x1000:
                    continue
                kodlar.update(range(bas, son + 1))
    return kodlar


def turkce_eksikleri(data):
    """Turkish letters missing from the font (empty set = all good)."""
    kodlar = _cmap_kodlari(data)
    if not kodlar:
        return set(TURKCE.values())          # cmap couldn't be read -> stay on the safe side
    return {ad for kod, ad in TURKCE.items() if kod not in kodlar}


def main():
    ap = argparse.ArgumentParser(description='Google Fonts → havuz')
    ap.add_argument('--apply', action='store_true', help='gerçekten ekle')
    ap.add_argument('--only', help='yalnız bu dizin (ör. inter)')
    ap.add_argument('--discover', action='store_true',
                    help='küratörlü aile listesini tara (dosya adları METADATA.pb\'den)')
    args = ap.parse_args()

    if args.discover:
        print(f'{len(KURATORLU_AILELER)} aile taranıyor…')
        bulunan, elenen = kesfet(KURATORLU_AILELER)
        for e in elenen:
            print(f'  ELE    {e}')
        print(f'{len(bulunan)} dosya adayı, {len(elenen)} aile elendi\n')
        satirlar = [(dizin, dosya, ad, etiket, lis)
                    for lis, dizin, dosya, ad, etiket in bulunan]
    else:
        satirlar = [(d, f, a, s2, 'ofl') for d, f, a, s2 in KATALOG]
    satirlar = [s2 for s2 in satirlar if not args.only or s2[0] == args.only]
    eklenen = atlanan = hatali = 0

    with app.app_context():
        for dizin, dosya, aile, stil, lisans in satirlar:
            etiket = f'{aile} {stil}'
            try:
                data = indir(dizin, dosya, lisans)
            except Exception as e:  # noqa: BLE001 — let a network error drop only this one row
                print(f'  HATA   {etiket}: indirilemedi ({e})')
                hatali += 1
                continue

            fmt = F._format_of(data)
            if fmt is None:
                print(f'  HATA   {etiket}: font imzası tanınmadı')
                hatali += 1
                continue
            eksik = turkce_eksikleri(data)
            if eksik:
                print(f'  ATLA   {etiket}: Türkçe harfler eksik ({" ".join(sorted(eksik))})')
                atlanan += 1
                continue
            if len(data) > F.MAX_FONT_BYTES:
                print(f'  ATLA   {etiket}: {len(data)//1024} KB — boyut sınırı üstü')
                atlanan += 1
                continue

            if not args.apply:
                print(f'  EKLE   {etiket:32} {fmt} {len(data)//1024:>5} KB  ✓ Türkçe')
                eklenen += 1
                continue

            font, hata = F._kaydet(data, dosya, 'font_import', aile, stil)
            if hata:
                print(f'  ATLA   {etiket}: {hata}')
                atlanan += 1
                continue
            db.session.commit()
            print(f'  EKLE   {etiket:32} {fmt} {len(data)//1024:>5} KB  ✓ Türkçe (id={font.id})')
            eklenen += 1

        print(f'\nBitti — eklenen {eklenen}, atlanan {atlanan}, hatalı {hatali}'
              f'{"" if args.apply else "  (KURU KOŞU)"}')
    return 1 if hatali else 0


if __name__ == '__main__':
    sys.exit(main())
