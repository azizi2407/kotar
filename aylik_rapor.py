"""Aylık Instagram performans raporu — CSV işleme, HTML ve PDF üretimi.

**Kaynak:** proje sahibi'in kendi bilgisayarında çalıştırdığı masaüstü aracın (tkinter)
veri katmanı, 2026-08-07'de panele taşındı. Hesaplama mantığı BİLEREK satır satır
korundu — aylardır gerçek Meta CSV'leriyle sınanmış sezgisel kuralları (sütun
seçimi, Türkçe sayı biçimi, dosya adı eşleştirmesi) yeniden yazmak, kanıtlanmış
davranışı kaybetme riskiydi. Değişenler yalnız ortama bağlı olanlar:

- tkinter/webbrowser bağımlılıkları atıldı (bu modül arayüz bilmez).
- Şablon ve logo repo içinde (`templates/`), kullanıcının indirme klasöründe değil.
- PDF motoru macOS Chrome yerine Linux `chromium` (`CHROMIUM_BIN` ile override).

Web akışı dosya YOLU üzerinden ilerler (klasör → CSV eşleştirme → rapor): yüklenen
dosyalar geçici bir dizine müşteri klasörleriyle yazılır, sonra buradaki fonksiyonlar
masaüstündeki gibi çağrılır. Böylece `auto_match_csv_files` / `preflight_bulk` tek
satır değişmeden toplu üretimi de karşılar.
"""
import csv
import json
import os
import re
import shutil
import tempfile
import unicodedata
from io import StringIO
from datetime import datetime

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# SABITLER
# ─────────────────────────────────────────────────────────────────────────────

_TR_MAP = str.maketrans(
    "ıİğĞüÜşŞöÖçÇ",
    "iIgGuUsSoOcC"
)

CSV_KEYS = [
    ("Görüntüleme CSV",          False),
    ("Erişim CSV",               True),
    ("Etkileşim CSV",            False),
    ("Bağlantı Tıklamaları CSV", False),
    ("Profil Ziyaretleri CSV",   False),
    ("Takipler CSV",             False),
    ("Gönderiler CSV",           False),
    ("Hikayeler CSV",            False),
    ("Meta Reklam Raporu CSV",   True),
]

REQUIRED_KEYS = [k for k, optional in CSV_KEYS if not optional]

_MATCH_RULES: dict[str, list[str]] = {
    "Görüntüleme CSV":          ["goruntuleme", "goruntulemeler", "views", "view"],
    "Erişim CSV":               ["erisim", "reach", "goruntuleyenler", "viewers"],
    "Etkileşim CSV":            ["etkilesim", "etkilesimler", "interaction", "interactions"],
    "Bağlantı Tıklamaları CSV": ["baglanti", "tiklama", "link"],
    "Profil Ziyaretleri CSV":   ["ziyaret", "ziyaretler", "visit", "visits", "profile"],
    "Takipler CSV":             ["takip", "takipler", "follower", "followers", "follow", "follows"],
    "Gönderiler CSV":           ["gonderi", "gonderiler", "post", "posts"],
    "Hikayeler CSV":            ["hikaye", "hikayeler", "story", "stories"],
    "Meta Reklam Raporu CSV":   ["rapor", "reklam", "aylik", "report", "ads"],
}

_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "templates", "rapor_sablonu.html")
_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "templates", "rapor_logo.png")


# ─────────────────────────────────────────────────────────────────────────────
# CSV HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def read_csv_robust(path: str) -> pd.DataFrame:
    """UTF-8/16 BOM, virgül/noktalı virgül/tab ayraçlarıyla CSV okur."""
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Dosya bulunamadı: {path}")

    lines = None
    for enc in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc, errors="replace") as f:
                lines = f.readlines()
            if lines:
                break
        except Exception:
            continue

    if not lines:
        raise ValueError(f"Dosya okunamadı: {path}")

    # "sep=," gibi meta satırları atla
    start = 0
    for i, line in enumerate(lines[:5]):
        if line.strip().lower().lstrip('"').startswith("sep="):
            start = i + 1
            break

    content = "".join(lines[start:])
    sample = content[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        delim = dialect.delimiter
    except Exception:
        delim = ";" if sample.count(";") > sample.count(",") else ","

    # Yeni Meta formatı: tek alanlı metrik başlığı + '"Tarih","Primary"' header'ı.
    # Başlık satırını atla; değer sütununu metrik adıyla yeniden adlandır.
    first_rows = list(csv.reader(lines[start:start + 2], delimiter=delim))
    title = None
    if (len(first_rows) >= 2 and len(first_rows[0]) == 1
            and len(first_rows[1]) >= 2):
        title = first_rows[0][0].strip()
        content = "".join(lines[start + 1:])

    df = pd.read_csv(StringIO(content), sep=delim, engine="python")
    df.columns = [str(c).strip() for c in df.columns]
    if title and "Primary" in df.columns:
        df = df.rename(columns={"Primary": title})
    return df


def coerce_numeric_series(s: pd.Series) -> pd.Series:
    """Türkçe binlik/ondalık ayraçlarını düzelterek sayıya çevirir."""
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")

    def _norm(x) -> str:
        if not isinstance(x, str):
            x = "" if (x is None or (isinstance(x, float) and pd.isna(x))) else str(x)
        x = x.strip().replace("\u00a0", "").replace(" ", "")
        if not x or x.lower() in ("nan", "none"):
            return ""
        x = re.sub(r"[^0-9\-,\.]", "", x)
        if x.count(",") > 0 and x.count(".") > 0:
            x = x.replace(".", "").replace(",", ".")   # TR: 1.234,56 → 1234.56
        elif x.count(",") > 0:
            x = x.replace(",", ".")                    # 1234,56 → 1234.56
        return x

    return pd.to_numeric(s.astype(str).map(_norm), errors="coerce")


def best_value_column(df: pd.DataFrame) -> str | None:
    """Toplanacak en uygun sayısal sütunu seçer."""
    if df is None or df.empty:
        return None

    preferred = [
        "Değer", "Value", "Toplam", "Total",
        "Erişim", "Reach", "Görüntülemeler", "Views",
        "Gösterimler", "Impressions", "Etkileşimler", "Interactions",
        "Tıklamalar", "Clicks", "Ziyaretler", "Visits", "Takipler", "Followers",
    ]
    cols_lower = {c.lower(): c for c in df.columns}
    for p in preferred:
        if p.lower() in cols_lower:
            return cols_lower[p.lower()]

    skip = {"tarih", "date", "saat", "time", "ad", "name",
            "başlık", "baslik", "caption", "url", "permalink"}
    best, best_score = None, -1
    for c in df.columns:
        if any(k in c.lower() for k in skip):
            continue
        score = coerce_numeric_series(df[c]).notna().sum()
        if score > best_score:
            best_score, best = score, c

    return best if best_score > 0 else None


def sum_metric_from_csv(path: str) -> float:
    """CSV'deki en uygun sayısal sütunu toplar."""
    df = read_csv_robust(path)
    col = best_value_column(df)
    if col is None:
        raise ValueError(f"Toplanacak sayısal sütun bulunamadı: {os.path.basename(path)}")
    return float(coerce_numeric_series(df[col]).sum(skipna=True))


def sum_column_from_df(df: pd.DataFrame, candidates: list[str]) -> float:
    """DataFrame'de aday sütun listesinden ilk bulunanın toplamını döndürür."""
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return float(coerce_numeric_series(df[cols_lower[cand.lower()]]).sum(skipna=True))
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ANALİZ
# ─────────────────────────────────────────────────────────────────────────────

def analyze_follower_changes(path: str) -> dict:
    """Kazanılan, kaybedilen ve net takipçi değişimini hesaplar."""
    if not path or not os.path.exists(path):
        return {}
    try:
        df = read_csv_robust(path)
        col = best_value_column(df)
        if col is None:
            return {}
        vals = coerce_numeric_series(df[col])
        return {
            "toplam_kazanilan": float(vals[vals > 0].sum(skipna=True)),
            "toplam_kaybedilen": abs(float(vals[vals < 0].sum(skipna=True))),
            "net_degisim": float(vals.sum(skipna=True)),
        }
    except Exception:
        return {}


def process_meta_ads_csv(path: str) -> dict:
    """Meta reklam CSV'sini işler: harcama, erişim, CTR, CPM ve detay analizleri."""
    if not path or not os.path.exists(path):
        return {}
    try:
        df = read_csv_robust(path)
        metrics: dict = {}
        details: dict = {}

        cols_lower = {c.lower(): c for c in df.columns}

        def _col(candidates: list[str]):
            for cand in candidates:
                if cand.lower() in cols_lower:
                    col = cols_lower[cand.lower()]
                    return coerce_numeric_series(df[col]), col
            return None, None

        harcama_ser, harcama_col = _col(["Harcanan Tutar (TRY)"])
        if harcama_ser is not None:
            metrics["Toplam Harcama (TRY)"] = float(harcama_ser.sum(skipna=True))

        erisim_ser, _ = _col(["Erişim"])
        if erisim_ser is not None:
            metrics["Reklam Erişimi"] = float(erisim_ser.sum(skipna=True))

        gosterim_ser, _ = _col(["Gösterim"])
        if gosterim_ser is not None:
            metrics["Reklam Gösterimi"] = float(gosterim_ser.sum(skipna=True))

        tiklama_ser, _ = _col(["Tıklamalar (Tümü)", "Bağlantı Tıklamaları",
                                "Tıklamalar", "Clicks"])
        if tiklama_ser is not None:
            metrics["Reklam Tıklamaları"] = float(tiklama_ser.sum(skipna=True))

        etkilesim = 0.0
        for col_name in ["Sayfa Etkileşimleri", "Gönderi etkileşimleri"]:
            ser, _ = _col([col_name])
            if ser is not None:
                etkilesim += float(ser.sum(skipna=True))
        if etkilesim > 0:
            metrics["Reklam Etkileşimleri"] = etkilesim

        takipci_ser, _ = _col([
            "Instagram takipleri", "Takipler", "Takipçiler", "Followers",
            "Takip", "Follower", "Yeni Takipçiler", "New Followers",
        ])
        if takipci_ser is not None:
            toplam = float(takipci_ser.sum(skipna=True))
            if toplam > 0:
                metrics["Reklamdan Gelen Takipçiler"] = toplam

        # CTR
        if gosterim_ser is not None and tiklama_ser is not None:
            g = float(gosterim_ser.sum(skipna=True))
            t = float(tiklama_ser.sum(skipna=True))
            if g > 0:
                metrics["CTR (%)"] = (t / g) * 100

        # Ağırlıklı CPM
        cpm_col = next((c for c in df.columns if "CPM" in c), None)
        if cpm_col and harcama_col:
            df_tmp = df[[cpm_col, harcama_col]].copy()
            df_tmp.columns = ["cpm", "harcama"]
            df_tmp["cpm"] = coerce_numeric_series(df_tmp["cpm"])
            df_tmp["harcama"] = coerce_numeric_series(df_tmp["harcama"])
            df_tmp = df_tmp.dropna()
            total_h = df_tmp["harcama"].sum()
            if total_h > 0:
                metrics["Ortalama CPM (TRY)"] = float(
                    (df_tmp["cpm"] * df_tmp["harcama"]).sum() / total_h
                )

        # Yaş gruplarına göre dağılım
        if "Yaş" in df.columns and harcama_ser is not None and erisim_ser is not None:
            yas_analiz = {}
            for yas in df["Yaş"].unique():
                if pd.isna(yas):
                    continue
                mask = df["Yaş"] == yas
                h = float(harcama_ser[mask].sum(skipna=True))
                e = float(erisim_ser[mask].sum(skipna=True))
                if h > 0 or e > 0:
                    yas_analiz[str(yas)] = {"harcama": h, "erisim": e}
            if yas_analiz:
                details["yas_gruplari"] = yas_analiz

        # Cinsiyete göre dağılım
        if "Cinsiyet" in df.columns and harcama_ser is not None and erisim_ser is not None:
            cinsiyet_analiz = {}
            for cinsiyet in df["Cinsiyet"].unique():
                if pd.isna(cinsiyet) or str(cinsiyet).lower() == "unknown":
                    continue
                mask = df["Cinsiyet"] == cinsiyet
                h = float(harcama_ser[mask].sum(skipna=True))
                e = float(erisim_ser[mask].sum(skipna=True))
                if h > 0 or e > 0:
                    label = "Kadın" if str(cinsiyet).lower() == "female" else "Erkek"
                    cinsiyet_analiz[label] = {"harcama": h, "erisim": e}
            if cinsiyet_analiz:
                details["cinsiyet"] = cinsiyet_analiz

        # En çok harcama yapılan reklamlar
        if "Reklamlar" in df.columns and harcama_ser is not None:
            df_tmp = df.copy()
            df_tmp["_h"] = harcama_ser
            top = (df_tmp.dropna(subset=["_h"])
                   .groupby("Reklamlar")["_h"].sum()
                   .sort_values(ascending=False).head(5))
            if not top.empty:
                details["top_reklamlar"] = [(str(k), float(v)) for k, v in top.items()]

        # En iyi günler (erişim)
        if "Gün" in df.columns and erisim_ser is not None:
            df_tmp = df.copy()
            df_tmp["_e"] = erisim_ser
            top = (df_tmp.dropna(subset=["_e"])
                   .groupby("Gün")["_e"].sum()
                   .sort_values(ascending=False).head(5))
            if not top.empty:
                details["top_gunler"] = [(str(k), float(v)) for k, v in top.items()]

        return {"metrics": metrics, "details": details}

    except Exception:
        return {}


def detect_rank_column(df: pd.DataFrame, kind: str) -> str | None:
    """Gönderi/hikaye sıralaması için en uygun metrik sütununu seçer."""
    priority = {
        "post": ["Erişim", "Reach", "Gösterimler", "Impressions",
                 "Görüntülemeler", "Views", "Etkileşimler", "Interactions"],
        "story": ["Görüntülemeler", "Views", "Erişim", "Reach",
                  "Gösterimler", "Impressions"],
    }.get(kind, [])

    cols_lower = {c.lower(): c for c in df.columns}
    for p in priority:
        if p.lower() in cols_lower:
            return cols_lower[p.lower()]
    return best_value_column(df)


def detect_title_column(df: pd.DataFrame) -> str | None:
    """Gönderi/hikaye için okunabilir bir kimlik sütunu bulur."""
    candidates = [
        "Başlık", "Title", "Açıklama", "Description", "Metin", "Text",
        "Caption", "İçerik", "Content", "URL", "Permalink", "ID",
    ]
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]

    # Fallback: en dolgun metin sütunu
    best, best_score = None, -1.0
    for c in df.columns:
        if coerce_numeric_series(df[c]).notna().mean() > 0.5:
            continue
        score = df[c].astype(str).str.strip().ne("").ne("nan").mean()
        if score > best_score:
            best_score, best = score, c
    return best


_AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
          "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _publish_date_tr(val) -> str | None:
    """'07/15/2026 01:13' veya ISO tarih string'ini '15 Temmuz' biçimine çevirir."""
    val = str(val).strip()
    if not val or val.lower() in ("nan", "none"):
        return None
    date_part = val.split()[0].split("T")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(date_part, fmt)
            return f"{dt.day} {_AYLAR[dt.month - 1]}"
        except ValueError:
            continue
    return None


def _publish_column(df_cols: list[str]) -> str | None:
    """Yayınlanma tarihi sütununu bulur."""
    for col_name in df_cols:
        if "yayınlanma" in col_name.lower() or "publish" in col_name.lower():
            return col_name
    return None


def _post_title(row, title_col: str | None, df_cols: list[str]) -> str:
    """Bir gönderi satırı için en anlamlı başlığı üretir."""
    title = ""
    if title_col and title_col in df_cols:
        title = " ".join(str(row[title_col]).split()).strip()

    if not title or title.lower() in ("nan", "none"):
        pub_col = _publish_column(df_cols)
        if pub_col:
            val = str(row.get(pub_col, "")).strip()
            if val and val.lower() not in ("nan", "none"):
                title = _publish_date_tr(val) or val.split()[0]

    return title or "—"


def top_story_publish_date(path: str) -> str | None:
    """En iyi hikayenin yayınlanma tarihini '15 Temmuz' biçiminde döndürür."""
    df = read_csv_robust(path)
    rank_col = detect_rank_column(df, "story")
    pub_col = _publish_column(df.columns.tolist())
    if rank_col is None or pub_col is None:
        return None
    df = df.copy()
    df["_rank"] = coerce_numeric_series(df[rank_col])
    top = df.dropna(subset=["_rank"]).nlargest(1, "_rank")
    if top.empty:
        return None
    return _publish_date_tr(top.iloc[0][pub_col])


def top5_items_from_csv(path: str, kind: str) -> tuple[list[tuple[str, float]], str]:
    """CSV'den en iyi 5 gönderi/hikayeyi döndürür: [(başlık, değer), ...], sıralama_sütunu"""
    df = read_csv_robust(path)
    rank_col = detect_rank_column(df, kind)
    if rank_col is None:
        raise ValueError(f"Sıralama metriği bulunamadı: {os.path.basename(path)}")

    title_col = detect_title_column(df)
    df = df.copy()
    df["_rank"] = coerce_numeric_series(df[rank_col])
    top = df.dropna(subset=["_rank"]).nlargest(5, "_rank")

    max_len = 100 if kind == "post" else 60
    results = []
    for _, row in top.iterrows():
        title = _post_title(row, title_col, df.columns.tolist())
        if len(title) > max_len:
            title = title[:max_len - 3] + "..."
        results.append((title, float(row["_rank"])))
    return results, rank_col


# ─────────────────────────────────────────────────────────────────────────────
# OTOMATİK DOSYA EŞLEŞTİRME
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Dosya ismini eşleştirme için normalleştirir (küçük harf, Türkçe → ASCII)."""
    text = unicodedata.normalize("NFC", text.lower()).translate(_TR_MAP)
    text = re.sub(r"\.csv$", "", text)
    text = re.sub(r"[-_][a-z0-9]+$", "", text)   # -mahir, -user gibi ekleri kaldır
    return re.sub(r"[-_]+", "-", text).strip("-")


def auto_match_csv_files(folder_path: str) -> dict[str, str]:
    """Klasördeki CSV dosyalarını isimlerine göre otomatik eşleştirir."""
    if not os.path.isdir(folder_path):
        return {}

    csv_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".csv")]
    matched: dict[str, str] = {}
    used: set[str] = set()

    for csv_type, keywords in _MATCH_RULES.items():
        best_file, best_score = None, 0
        for fname in csv_files:
            if fname in used:
                continue
            norm = _normalize(fname)
            score = sum(
                (len(keywords) - i) * 10
                for i, kw in enumerate(keywords)
                if kw in norm
            )
            if score > best_score:
                best_score, best_file = score, fname

        if best_file and best_score >= 10:
            matched[csv_type] = os.path.join(folder_path, best_file)
            used.add(best_file)

    return matched


def preflight_bulk(parent: str) -> list[dict]:
    """Toplu üretim öncesi tüm müşteri klasörlerini analiz eder (hiçbir dosya yazmaz)."""
    results: list[dict] = []
    for item in sorted(os.listdir(parent)):
        p = os.path.join(parent, item)
        if not os.path.isdir(p) or item.startswith("."):
            continue
        if not any(f.lower().endswith(".csv") for f in os.listdir(p)):
            continue

        matched = auto_match_csv_files(p)
        entry = {
            "name": item, "path": p, "matched": matched,
            "missing": [k for k, _ in CSV_KEYS if k not in matched],
            "warnings": [], "data": None, "skip": None,
        }

        content_missing = [k for k in ("Gönderiler CSV", "Hikayeler CSV") if k not in matched]
        if content_missing:
            entry["skip"] = "Eksik dosya: " + ", ".join(content_missing)
            results.append(entry)
            continue

        try:
            data = generate_report_data(matched, item)
            entry["data"] = data
            entry["warnings"] = list(data.get("warnings", []))
            for k in ("Toplam Görüntüleme", "Toplam Erişim", "Toplam Etkileşim"):
                if data["totals"].get(k, 0) == 0:
                    entry["warnings"].append(f"{k} = 0 — veri eksik olabilir")
        except Exception as e:
            entry["skip"] = f"Hata: {type(e).__name__}: {str(e)[:80]}"
        results.append(entry)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# RAPOR VERİSİ OLUŞTURMA
# ─────────────────────────────────────────────────────────────────────────────

def _sum_from_posts_and_stories(matched: dict, candidates: list[str]) -> float:
    """Gönderi ve hikaye CSV'lerinden belirtilen sütunları toplar (fallback)."""
    total = 0.0
    for key in ("Gönderiler CSV", "Hikayeler CSV"):
        if key not in matched:
            continue
        try:
            df = read_csv_robust(matched[key])
            total += sum_column_from_df(df, candidates)
        except Exception:
            pass
    return total


def _safe_metric_sum(matched: dict, key: str, warnings: list[str]) -> float:
    """CSV toplamını okur; hata durumunda 0 döner ve uyarı kaydeder."""
    try:
        return sum_metric_from_csv(matched[key])
    except Exception as e:
        fname = os.path.basename(matched.get(key, "?"))
        warnings.append(f"{key}: '{fname}' okunamadı ({type(e).__name__}) — 0 varsayıldı")
        return 0.0


def generate_report_data(matched: dict, client_name: str) -> dict:
    """Eşleştirilmiş CSV dosyalarından tek bir rapor veri sözlüğü üretir."""
    client_name = unicodedata.normalize("NFC", client_name)
    totals: dict[str, float] = {}
    warnings: list[str] = []

    # Görüntüleme
    if "Görüntüleme CSV" in matched:
        totals["Toplam Görüntüleme"] = _safe_metric_sum(matched, "Görüntüleme CSV", warnings)
    else:
        totals["Toplam Görüntüleme"] = _sum_from_posts_and_stories(
            matched, ["Görüntülemeler", "Görüntüleme", "Gösterimler", "Gösterim", "Views", "Impressions"]
        )
        warnings.append("Görüntüleme CSV yok — gönderi+hikaye toplamından hesaplandı")

    # Erişim
    if "Erişim CSV" in matched:
        totals["Toplam Erişim"] = _safe_metric_sum(matched, "Erişim CSV", warnings)
    else:
        totals["Toplam Erişim"] = _sum_from_posts_and_stories(matched, ["Erişim", "Reach"])
        warnings.append("Erişim CSV yok — gönderi+hikaye toplamı kullanıldı (şişirme riski)")

    # Etkileşim
    if "Etkileşim CSV" in matched:
        totals["Toplam Etkileşim"] = _safe_metric_sum(matched, "Etkileşim CSV", warnings)
    else:
        totals["Toplam Etkileşim"] = _sum_from_posts_and_stories(
            matched, ["Etkileşimler", "Etkileşim", "Interactions", "Engagement", "Beğeniler", "Likes"]
        )
        warnings.append("Etkileşim CSV yok — gönderi+hikaye toplamından hesaplandı")

    totals["Bağlantı Tıklamaları"] = (
        _safe_metric_sum(matched, "Bağlantı Tıklamaları CSV", warnings)
        if "Bağlantı Tıklamaları CSV" in matched else 0.0
    )
    totals["Profil Ziyaretleri"] = (
        _safe_metric_sum(matched, "Profil Ziyaretleri CSV", warnings)
        if "Profil Ziyaretleri CSV" in matched else 0.0
    )
    totals["Net Takipçi Değişimi"] = (
        _safe_metric_sum(matched, "Takipler CSV", warnings)
        if "Takipler CSV" in matched else 0.0
    )

    # Reklam
    ads_data = (
        process_meta_ads_csv(matched["Meta Reklam Raporu CSV"])
        if "Meta Reklam Raporu CSV" in matched else {}
    )

    # En iyi içerikler
    top_posts, post_rank_col = [], ""
    if "Gönderiler CSV" in matched:
        try:
            top_posts, post_rank_col = top5_items_from_csv(matched["Gönderiler CSV"], "post")
        except Exception:
            pass

    top_stories, story_rank_col = [], ""
    if "Hikayeler CSV" in matched:
        try:
            top_stories, story_rank_col = top5_items_from_csv(matched["Hikayeler CSV"], "story")
        except Exception:
            pass

    story_date = ""
    if "Hikayeler CSV" in matched and top_stories:
        try:
            story_date = top_story_publish_date(matched["Hikayeler CSV"]) or ""
        except Exception:
            pass

    # Takipçi analizi
    toplam_takipci = totals["Net Takipçi Değişimi"]
    reklam_takipci = ads_data.get("metrics", {}).get("Reklamdan Gelen Takipçiler", 0.0)
    organik_takipci = max(0.0, toplam_takipci - reklam_takipci)

    takipci_analiz = analyze_follower_changes(matched.get("Takipler CSV", ""))
    kazanilan = takipci_analiz.get("toplam_kazanilan", 0.0)
    kaybedilen = takipci_analiz.get("toplam_kaybedilen", 0.0)
    if kaybedilen == 0 and kazanilan > toplam_takipci > 0:
        kaybedilen = kazanilan - toplam_takipci

    return {
        "client_name": client_name,
        "totals": totals,
        "top_posts": top_posts,
        "top_stories": top_stories,
        "ads_data": ads_data,
        "takipci_analiz": takipci_analiz,
        "toplam_takipci": toplam_takipci,
        "reklam_takipci": reklam_takipci,
        "organik_takipci": organik_takipci,
        "kazanilan_takipci": kazanilan,
        "kaybedilen_takipci": kaybedilen,
        "post_rank_col": post_rank_col,
        "story_rank_col": story_rank_col,
        "story_date": story_date,
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT & METİN
# ─────────────────────────────────────────────────────────────────────────────

def fmt_int(n: float) -> str:
    try:
        return f"{int(round(n)):,}".replace(",", ".")
    except Exception:
        return str(n)


def fmt_currency(n: float) -> str:
    try:
        return f"{n:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return str(n)


def build_report_text(data: dict) -> str:
    """Rapor verisinden kopyalanabilir metin özet üretir."""
    totals = data["totals"]
    ads_data = data.get("ads_data", {})
    ads_metrics = ads_data.get("metrics", {})
    ads_details = ads_data.get("details", {})
    top_posts = data["top_posts"]
    top_stories = data["top_stories"]

    toplam_takipci = data["toplam_takipci"]
    kazanilan = data["kazanilan_takipci"]
    kaybedilen = data["kaybedilen_takipci"]
    reklam_takipci = data["reklam_takipci"]
    organik_takipci = data["organik_takipci"]

    lines = ["INSTAGRAM AYLIK ÖZET", ""]

    for key in ["Toplam Görüntüleme", "Toplam Erişim", "Toplam Etkileşim",
                "Bağlantı Tıklamaları", "Profil Ziyaretleri"]:
        lines.append(f"{key}: {fmt_int(totals.get(key, 0))}")

    lines.append(f"Net Takipçi Değişimi: {fmt_int(toplam_takipci)}")
    if kazanilan > 0 or kaybedilen > 0:
        lines.append(f"  • Kazanılan Takipçi: {fmt_int(kazanilan)}")
        if kaybedilen > 0:
            lines.append(f"  • Takibi Bırakan: {fmt_int(kaybedilen)}")
            if kazanilan > 0:
                lines.append(f"  • Kayıp Oranı: {kaybedilen / kazanilan * 100:.1f}%")
    if reklam_takipci > 0:
        lines.append(f"  • Organik: {fmt_int(organik_takipci)}  |  Reklam: {fmt_int(reklam_takipci)}")
    else:
        lines.append(f"  • Organik: {fmt_int(organik_takipci)}")

    # Meta Reklam
    if ads_metrics:
        lines += ["", "META REKLAM METRİKLERİ", "─" * 40]

        if "Toplam Harcama (TRY)" in ads_metrics:
            lines.append(f"Toplam Harcama: {fmt_currency(ads_metrics['Toplam Harcama (TRY)'])} TRY")
        for label, key in [
            ("Reklam Erişimi",            "Reklam Erişimi"),
            ("Reklam Gösterimi",          "Reklam Gösterimi"),
            ("Reklam Tıklamaları",        "Reklam Tıklamaları"),
            ("Reklam Etkileşimleri",      "Reklam Etkileşimleri"),
            ("Reklamdan Gelen Takipçiler","Reklamdan Gelen Takipçiler"),
        ]:
            if key in ads_metrics:
                lines.append(f"{label}: {fmt_int(ads_metrics[key])}")
        if "CTR (%)" in ads_metrics:
            lines.append(f"CTR (Tıklama Oranı): {ads_metrics['CTR (%)']:.2f}%")
        if "Ortalama CPM (TRY)" in ads_metrics:
            lines.append(f"Ortalama CPM: {fmt_currency(ads_metrics['Ortalama CPM (TRY)'])} TRY")
        if "Reklamdan Gelen Takipçiler" in ads_metrics and "Toplam Harcama (TRY)" in ads_metrics:
            tak = ads_metrics["Reklamdan Gelen Takipçiler"]
            if tak > 0:
                maliyet = ads_metrics["Toplam Harcama (TRY)"] / tak
                lines.append(f"Takipçi Başına Maliyet: {fmt_currency(maliyet)} TRY")

        if "yas_gruplari" in ads_details:
            lines.append("\nYaş Gruplarına Göre Dağılım:")
            toplam_h = ads_metrics.get("Toplam Harcama (TRY)", 1)
            for yas, d in sorted(ads_details["yas_gruplari"].items(),
                                  key=lambda x: x[1]["harcama"], reverse=True):
                pct = d["harcama"] / toplam_h * 100 if toplam_h > 0 else 0
                lines.append(f"  • {yas}: {fmt_currency(d['harcama'])} TRY ({pct:.1f}%) | Erişim: {fmt_int(d['erisim'])}")

        if "cinsiyet" in ads_details:
            lines.append("\nCinsiyete Göre Dağılım:")
            toplam_h = ads_metrics.get("Toplam Harcama (TRY)", 1)
            for cinsiyet, d in ads_details["cinsiyet"].items():
                pct = d["harcama"] / toplam_h * 100 if toplam_h > 0 else 0
                lines.append(f"  • {cinsiyet}: {fmt_currency(d['harcama'])} TRY ({pct:.1f}%) | Erişim: {fmt_int(d['erisim'])}")

        if "top_reklamlar" in ads_details:
            lines.append("\nEn Çok Harcama Yapılan Reklamlar:")
            for i, (reklam, h) in enumerate(ads_details["top_reklamlar"], 1):
                kisa = reklam[:60] + "..." if len(reklam) > 60 else reklam
                lines.append(f"  {i}. {kisa} — {fmt_currency(h)} TRY")

        if "top_gunler" in ads_details:
            lines.append("\nEn İyi Performans Gösteren Günler:")
            for i, (gun, erisim) in enumerate(ads_details["top_gunler"], 1):
                try:
                    dt = datetime.strptime(str(gun), "%Y-%m-%d")
                    gun_fmt = f"{dt.day} {_AYLAR[dt.month - 1]}"
                except Exception:
                    gun_fmt = str(gun)
                lines.append(f"  {i}. {gun_fmt} — {fmt_int(erisim)} erişim")

    # En iyi içerikler
    if top_posts:
        lines += ["", f"En İyi 5 Gönderi (Sıralama: {data['post_rank_col']})"]
        for i, (title, val) in enumerate(top_posts, 1):
            lines.append(f"  {i}. {title} — {fmt_int(val)}")

    if top_stories:
        lines += ["", f"En İyi 5 Hikaye (Sıralama: {data['story_rank_col']})"]
        for i, (title, val) in enumerate(top_stories, 1):
            lines.append(f"  {i}. {title} — {fmt_int(val)}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# HTML EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def _safe_json(data) -> str:
    """JSON dump yapar, </script> injection'ını engeller."""
    return json.dumps(data, ensure_ascii=False, indent=2).replace("</script>", "<\\/script>")


def export_to_html(data: dict, output_path: str) -> None:
    """Rapor verisini HTML şablonuna enjekte ederek kaydeder."""
    if not os.path.exists(_TEMPLATE_PATH):
        raise FileNotFoundError("report_generator.html şablonu bulunamadı!")

    with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Tuple listelerini JSON'a uygun dict'e çevir
    export_data = {**data,
                   "top_posts":   [{"title": t, "value": v} for t, v in data["top_posts"]],
                   "top_stories": [{"title": t, "value": v} for t, v in data["top_stories"]]}

    js_block = f"\n<script>\nconst reportData = {_safe_json(export_data)};\n</script>\n"
    html = html.replace("</head>", js_block + "</head>")

    client = data.get("client_name", "")
    html = html.replace("<title>Casaba Mahir - Aylık Rapor</title>",
                        f"<title>{client} - Aylık Rapor</title>")
    html = html.replace('value="Casaba Mahir"', f'value="{client}"')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def create_index_page(reports_folder: str, clients: list[tuple[str, str]]) -> None:
    """Tüm müşteri raporlarına bağlantı veren index.html oluşturur."""
    cards = "\n".join(
        f'<a href="{fname}" class="client-card">'
        f'<div class="icon">📊</div><h3>{name}</h3>'
        f'<p>Instagram performans raporu</p>'
        f'<span class="view-btn">Raporu Görüntüle</span></a>'
        for name, fname in clients
    )
    html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>Kotar - Müşteri Raporları</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:#f5f5f5;min-height:100vh;padding:40px 20px}}
.container{{max-width:900px;margin:0 auto}}
.header{{text-align:center;margin-bottom:40px}}
.header h1{{color:#1a365d;font-size:36px;margin-bottom:5px}}
.subtitle{{color:#dc2626;font-size:18px}}
.clients-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px}}
.client-card{{background:white;border-radius:12px;padding:25px;box-shadow:0 4px 15px rgba(0,0,0,.08);transition:transform .2s,box-shadow .2s;text-decoration:none;display:block}}
.client-card:hover{{transform:translateY(-5px);box-shadow:0 8px 25px rgba(0,0,0,.12)}}
.client-card h3{{color:#1a365d;font-size:20px;margin-bottom:10px}}
.icon{{font-size:48px;margin-bottom:15px}}
.client-card p{{color:#666;font-size:14px}}
.view-btn{{display:inline-block;background:#dc2626;color:white;padding:8px 20px;border-radius:6px;margin-top:15px;font-weight:500}}
.footer{{text-align:center;margin-top:50px;color:#999;font-size:14px}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Kotar</h1>
    <div class="subtitle">Dijital Medya Ajansı</div>
  </div>
  <div class="clients-grid">{cards}</div>
  <div class="footer"><p>{len(clients)} müşteri raporu</p></div>
</div>
</body>
</html>"""
    with open(os.path.join(reports_folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ─────────────────────────────────────────────────────────────────────────────
# PDF EXPORT
# ─────────────────────────────────────────────────────────────────────────────

# macOS Chrome yolu yerine sunucudaki Chromium. Şablon veriyi JS ile bastığı için
# PDF gerçek bir tarayıcı motoru İSTER — weasyprint/wkhtmltopdf boş sayfa üretirdi.
_CHROME = os.environ.get("CHROMIUM_BIN") or "/usr/bin/chromium"
_PDF_CACHE_DIR = os.path.join(tempfile.gettempdir(), "kotar_pdf_font_cache")


def export_to_pdf(html_path: str, pdf_path: str) -> None:
    """Chrome headless kullanarak HTML dosyasını PDF'e dönüştürür."""
    import subprocess

    # Relative logo.png → absolute path + JS ile @page yüksekliğini içeriğe göre ayarla
    html_dir = os.path.dirname(os.path.abspath(html_path))
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace('src="logo.png"', f'src="file://{html_dir}/logo.png"')

    _measure_script = """<script>
window.addEventListener('load', function() {
    function measure() {
        var rc = document.querySelector('.report-container');
        if (!rc) return;
        var h = Math.ceil(rc.scrollHeight) + 4;
        var st = document.createElement('style');
        st.textContent = '@media print { @page { size: 820px ' + h + 'px !important; margin: 0; } }';
        document.head.appendChild(st);
    }
    // Web fontları (Google Fonts) tam yüklenmeden ölçüm/yazdırma yapılırsa
    // Chrome fallback fonta düşer ve Türkçe karakterler (ş, ğ, ç) bozulur.
    // Fontlar hazır olunca ölç.
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(measure);
    } else {
        measure();
    }
});
</script>"""
    html = html.replace("</head>", _measure_script + "</head>", 1)

    tmp = html_path + "._pdf_tmp.html"
    # Sunucuda servis `svc-agency` (nologin) olarak koşuyor: HOME'u yazılabilir
    # değil ve Chromium varsayılan profil dizinini açamayınca "Failed to create
    # headless user data directory container" ile ÇIKIYOR — PDF hiç üretilmiyordu.
    # Çözüm HTML'in yanına tek kullanımlık bir profil açmak; HOME da oraya
    # bakmalı, yoksa crashpad yine ev dizinine uzanıyor. Masaüstünde de zararsız:
    # profil zaten geçici ve istek sonunda siliniyor.
    profil = os.path.join(os.path.dirname(os.path.abspath(html_path)), "_chrome_profil")
    os.makedirs(profil, exist_ok=True)
    env = {**os.environ, "HOME": profil}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        result = subprocess.run(
            [
                _CHROME,
                "--headless=new", "--disable-gpu", "--no-sandbox",
                "--no-pdf-header-footer",
                f"--user-data-dir={profil}",
                # Crashpad yardımcısı bu ortamda başlatılamıyor ve hata akışını
                # kirletiyor; PDF için gereği yok.
                "--disable-crash-reporter", "--no-first-run",
                "--window-size=1200,800",
                # Web fontları inene kadar (max ~20sn) sanal zamanı ilerlet;
                # yazdırmadan önce fontların yüklenmesini garantiler.
                "--virtual-time-budget=20000",
                # Paylaşılan disk cache: ilk PDF fontları indirir, sonrakiler
                # cache'ten alır → toplu üretimde fallback fonta düşme önlenir
                # (Türkçe karakterlerin ş/ğ/ç bozulmasının asıl nedeni).
                f"--disk-cache-dir={_PDF_CACHE_DIR}",
                f"--print-to-pdf={pdf_path}",
                f"file://{tmp}",
            ],
            capture_output=True,
            timeout=60,
            env=env,
        )
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        shutil.rmtree(profil, ignore_errors=True)

    if not os.path.exists(pdf_path):
        raise RuntimeError(result.stderr.decode(errors="replace")[-300:])
