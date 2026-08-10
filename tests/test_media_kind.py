"""Medya türü İÇERİKTEN belirlenir — `Share.kind`'a güvenilmez.

`Share.kind` yayın türüdür (post/story/reel/linkedin); dosya türü DEĞİL. Bir
video "post" olarak paylaşılabilir ve bu olağandır. media_worker eskiden
`kind != 'video'` görünce dosyayı PIL'e veriyordu → `kind='post'` olan bir .mp4
"cannot identify image file" ile 3 denemede de çöktü (job 233 / share 671,
2026-07-27) ve o paylaşımda caption zinciri hiç ilerlemedi.
"""
import media

# Gerçek dosya başlangıçları (yalnız imza kısmı — tam dosya gerekmiyor).
MP4 = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2'
MOV = b'\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  '
HEIC = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic'
AVIF = b'\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1'
PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01'
GIF = b'GIF89a\x01\x00\x01\x00'
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 '
AVI = b'RIFF\x24\x00\x00\x00AVI LIST'
MKV = b'\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1f'
FLV = b'FLV\x01\x05\x00\x00\x00\x09'
BMP = b'BM\x36\x00\x00\x00\x00\x00\x00\x00'
TIFF = b'II*\x00\x08\x00\x00\x00\x10\x00'


def test_sniff_video_imzalari():
    for raw in (MP4, MOV, AVI, MKV, FLV):
        assert media.sniff_kind(raw) == 'video', raw[:12]


def test_sniff_gorsel_imzalari():
    for raw in (PNG, JPEG, GIF, WEBP, BMP, TIFF):
        assert media.sniff_kind(raw) == 'image', raw[:12]


def test_sniff_heic_avif_gorsel_sayilir():
    """ISO-BMFF (`ftyp`) hem MP4 hem HEIC/AVIF tarafından kullanılır — brand'a
    bakılmazsa iPhone fotoğrafı video sanılır ve ffmpeg'e giderdi."""
    assert media.sniff_kind(HEIC) == 'image'
    assert media.sniff_kind(AVIF) == 'image'


def test_sniff_taninmayan_none():
    assert media.sniff_kind(b'hicbir seye benzemeyen icerik') is None
    assert media.sniff_kind(b'') is None


def test_uzanti_yedegi():
    assert media.kind_from_name('rizonr0727.mp4') == 'video'
    assert media.kind_from_name('KAPAK.MOV') == 'video'
    assert media.kind_from_name('camsas-1.jpg') == 'image'
    assert media.kind_from_name('dosya.pdf') is None
    assert media.kind_from_name(None) is None


def test_resolve_oncelik_icerik_uzantinin_onunde():
    """Ad yanlışsa içerik kazanır — .jpg'ye çevrilmiş bir mp4 hâlâ videodur."""
    assert media.resolve_kind(MP4, 'yanlis-ad.jpg', fallback='image') == 'video'
    assert media.resolve_kind(PNG, 'yanlis-ad.mp4', fallback='video') == 'image'


def test_resolve_icerik_taninmazsa_uzantiya_duser():
    assert media.resolve_kind(b'bilinmeyen', 'klip.mp4', fallback='image') == 'video'


def test_resolve_ikisi_de_taninmazsa_fallback():
    assert media.resolve_kind(b'bilinmeyen', 'dosya.bin', fallback='video') == 'video'
    assert media.resolve_kind(b'bilinmeyen', 'dosya.bin', fallback='image') == 'image'
    # fallback verilmezse görsel yolu (eski davranış korunur)
    assert media.resolve_kind(b'bilinmeyen', None) == 'image'


def test_share_671_senaryosu_post_olarak_paylasilan_video():
    """REGRESYON: `kind='post'` + `.mp4` → video kolu. Eski kod burada PIL'e
    gidip 'cannot identify image file' ile üç denemede de çöküyordu."""
    hint = 'image'  # media_worker `kind != 'video'` için bunu üretir
    assert media.resolve_kind(MP4, 'rizonr0727.mp4', fallback=hint) == 'video'
