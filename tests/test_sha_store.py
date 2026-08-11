"""Content-addressed disk store (2026-08-09) — sha_store.

`design_files` and `voice_notes` SHARE this module. The tests here lock down
atomicity, dedup, and the file mode: mode 0644 is critical because
`design_files` has nginx (www-data) do the downloading, and `mkstemp`'s
default is 0600 (a bug found in production on 2026-08-08).
"""
import io
import os
import stat

import sha_store


def test_uzanti_kucuk_harf_noktasiz(tmp_path):
    assert sha_store.uzanti("Ana Şablon.PSD") == "psd"
    assert sha_store.uzanti("arsiv.tar.gz") == "gz"


def test_uzanti_yoksa_bin(tmp_path):
    assert sha_store.uzanti("uzantisiz") == "bin"
    assert sha_store.uzanti("") == "bin"


def test_uzanti_alfanumerik_disini_atar():
    assert sha_store.uzanti("dosya.p s d!") == "psd"


def test_yol_iki_harfli_on_ek_dizini(tmp_path):
    sha = "ab" + "c" * 62
    y = sha_store.yol(str(tmp_path), sha, "x.psd")
    assert y == os.path.join(str(tmp_path), "ab", f"{sha}.psd")


def test_yaz_dosyayi_ve_hash_i_uretir(tmp_path):
    veri = b"8BPS" + b"govde" * 100
    sha, boyut = sha_store.yaz(io.BytesIO(veri), str(tmp_path), "a.psd")
    import hashlib
    assert sha == hashlib.sha256(veri).hexdigest()
    assert boyut == len(veri)
    yol = tmp_path / sha[:2] / f"{sha}.psd"
    assert yol.read_bytes() == veri


def test_yaz_modu_0644(tmp_path):
    """mkstemp gives 0600 and os.replace preserves it — nginx (www-data) can't read it."""
    sha, _ = sha_store.yaz(io.BytesIO(b"veri"), str(tmp_path), "a.bin")
    yol = tmp_path / sha[:2] / f"{sha}.bin"
    assert stat.S_IMODE(os.stat(yol).st_mode) == 0o644


def test_yaz_dedup_ikinci_kopya_yazmaz(tmp_path):
    veri = b"ayni icerik"
    sha, _ = sha_store.yaz(io.BytesIO(veri), str(tmp_path), "a.bin")
    yol = tmp_path / sha[:2] / f"{sha}.bin"
    onceki = os.stat(yol).st_mtime_ns
    sha2, _ = sha_store.yaz(io.BytesIO(veri), str(tmp_path), "a.bin")
    assert sha2 == sha
    assert os.stat(yol).st_mtime_ns == onceki      # file was not touched


def test_yaz_dedup_dalinda_da_modu_onarir(tmp_path):
    """A file left at 0600 (may have come back from a backup) must be repaired on re-upload."""
    veri = b"ayni icerik"
    sha, _ = sha_store.yaz(io.BytesIO(veri), str(tmp_path), "a.bin")
    yol = tmp_path / sha[:2] / f"{sha}.bin"
    os.chmod(yol, 0o600)
    sha_store.yaz(io.BytesIO(veri), str(tmp_path), "a.bin")
    assert stat.S_IMODE(os.stat(yol).st_mode) == 0o644


def test_yaz_gecici_dosya_birakmaz(tmp_path):
    sha_store.yaz(io.BytesIO(b"veri"), str(tmp_path), "a.bin")
    assert not [p for p in tmp_path.rglob("*.part")]


def test_yaz_hata_yolunda_gecici_dosya_temizlenir(tmp_path):
    class _Patlayan:
        def read(self, n):
            raise OSError("akış koptu")
    try:
        sha_store.yaz(_Patlayan(), str(tmp_path), "a.bin")
    except OSError:
        pass
    assert not [p for p in tmp_path.rglob("*.part")]


def test_yaz_on_hashed_kancasi_hedef_kontrolunden_once_cagrilir(tmp_path):
    """TOCTOU locks like `design_files._sha_kilidi` hook onto this callback:
    it must be called right after the sha is computed, BEFORE checking whether
    the target exists (otherwise a race opens up between upload and purge)."""
    cagrilar = []

    def kanca(sha):
        cagrilar.append(sha)
        # At the moment the hook is called, the target must not exist yet.
        assert not os.path.exists(sha_store.yol(str(tmp_path), sha, "a.bin"))

    veri = b"veri"
    import hashlib
    beklenen_sha = hashlib.sha256(veri).hexdigest()
    sha, _ = sha_store.yaz(io.BytesIO(veri), str(tmp_path), "a.bin", on_hashed=kanca)
    assert cagrilar == [beklenen_sha] == [sha]
