"""İçerik-adresli disk deposu (2026-08-09) — sha256 adıyla atomik yazma.

`design_files` (çalışma dosyaları) ve `voice_notes` (sesli notlar) bu modülü
PAYLAŞIR. Ayrı ayrı yazılsaydı drift üretirdi: 2026-08-08'de canlıda bulunan
"mkstemp 0600 üretiyor → nginx (www-data) dosyayı okuyamıyor, her indirme 403"
hatası iki yerde ayrı ayrı düzeltilmek zorunda kalırdı.

Yerleşim `<store_dir>/<sha[:2]>/<sha>.<ext>`: iki harfli ön ek dizini, tek
dizinde on binlerce dosya birikmesin.
"""
import hashlib
import os
import tempfile

# Okuma bloğu: 8 MB — 1 GB'lık dosyada 128 tur, RAM'de tek blok kalır.
CHUNK = 8 * 1024 * 1024
# 0644: sahibi yazar, herkes okur. nginx X-Accel ile dosyayı www-data okuyor;
# mkstemp varsayılanı 0600 ve `os.replace` bunu KORUR.
FILE_MODE = 0o644


def uzanti(file_name):
    """Son uzantı: küçük harf, noktasız, yalnız alfanümerik, ≤12. Uzantısız
    dosya 'bin' olur — disk adı her zaman `<sha>.<ext>` biçiminde kalsın."""
    ext = os.path.splitext(file_name or '')[1].lstrip('.').lower()
    return ''.join(ch for ch in ext if ch.isalnum())[:12] or 'bin'


def yol(store_dir, sha256, file_name):
    """Bu içeriğin kanonik disk yolu."""
    return os.path.join(store_dir, sha256[:2], f'{sha256}.{uzanti(file_name)}')


def yaz(stream, store_dir, file_name, on_hashed=None):
    """Akışı diske yaz, sha256'yı AYNI geçişte hesapla; `(sha256, boyut)` döner.

    Önce geçici dosyaya yazılır, sonra `os.replace` ile kanonik yola taşınır:
    yarım dosya asla kanonik yolda görünmez (aynı hash'i bekleyen bir indirme
    yarım içerik okumasın). Hedef zaten varsa (dedup) geçici dosya silinir —
    ama modu yine de onarılır: yedekten dönmüş 0600'lük bir dosya aksi halde
    kalıcı olarak okunamaz kalırdı ve yeni yükleme onu düzeltmezdi.

    `on_hashed`: sha256 hesaplandıktan HEMEN SONRA, hedefin var olup olmadığına
    bakılmadan ÖNCE çağrılan isteğe bağlı geri çağırma (`on_hashed(sha)`).
    Hash-sonrası ama yazmadan-önce bir senkronizasyon noktasına ihtiyaç duyan
    çağıranlar için (örn. `design_files._sha_kilidi` — yükleme ile purge
    arasındaki TOCTOU yarışını kapatan Postgres advisory lock; bu modüle
    devredilmeden önceki sıralama BİREBİR korunsun diye kanca eklendi)."""
    os.makedirs(store_dir, exist_ok=True)
    h = hashlib.sha256()
    boyut = 0
    fd, gecici = tempfile.mkstemp(dir=store_dir, suffix='.part')
    try:
        with os.fdopen(fd, 'wb') as out:
            while True:
                parca = stream.read(CHUNK)
                if not parca:
                    break
                h.update(parca)
                boyut += len(parca)
                out.write(parca)
        sha = h.hexdigest()
        if on_hashed is not None:
            on_hashed(sha)
        hedef = yol(store_dir, sha, file_name)
        os.makedirs(os.path.dirname(hedef), exist_ok=True)
        if os.path.exists(hedef):
            os.unlink(gecici)
        else:
            os.replace(gecici, hedef)
        os.chmod(hedef, FILE_MODE)
        return sha, boyut
    except Exception:
        if os.path.exists(gecici):
            os.unlink(gecici)
        raise
