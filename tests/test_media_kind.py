"""Media type is determined FROM CONTENT — `Share.kind` is not trusted.

`Share.kind` is the publication type (post/story/reel/linkedin); NOT the file type.
A video can be shared as a "post" and that's normal. media_worker used to hand the
file to PIL whenever it saw `kind != 'video'` → a .mp4 with `kind='post'` crashed
with "cannot identify image file" on all 3 attempts (job 233 / share 671,
2026-07-27), and the caption chain for that share never progressed.
"""
import media

# Real file headers (signature portion only — the full file isn't needed).
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
    """ISO-BMFF (`ftyp`) is used by both MP4 and HEIC/AVIF — without checking the
    brand, an iPhone photo would be mistaken for video and sent to ffmpeg."""
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
    """If the name is wrong, content wins — an mp4 renamed to .jpg is still a video."""
    assert media.resolve_kind(MP4, 'yanlis-ad.jpg', fallback='image') == 'video'
    assert media.resolve_kind(PNG, 'yanlis-ad.mp4', fallback='video') == 'image'


def test_resolve_icerik_taninmazsa_uzantiya_duser():
    assert media.resolve_kind(b'bilinmeyen', 'klip.mp4', fallback='image') == 'video'


def test_resolve_ikisi_de_taninmazsa_fallback():
    assert media.resolve_kind(b'bilinmeyen', 'dosya.bin', fallback='video') == 'video'
    assert media.resolve_kind(b'bilinmeyen', 'dosya.bin', fallback='image') == 'image'
    # if no fallback is given, defaults to image (preserves old behavior)
    assert media.resolve_kind(b'bilinmeyen', None) == 'image'


def test_share_671_senaryosu_post_olarak_paylasilan_video():
    """REGRESSION: `kind='post'` + `.mp4` → the video path. Old code went to PIL
    here and crashed with 'cannot identify image file' on all three attempts."""
    hint = 'image'  # this is what media_worker produces for `kind != 'video'`
    assert media.resolve_kind(MP4, 'rizonr0727.mp4', fallback=hint) == 'video'
