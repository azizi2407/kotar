"""Görsel araçları — image_splitter (eski monolitten). Pure PIL, DB/sır yok.

Ajans Instagram akışı: 3120×1350 geniş görsel → yan yana 3 dikey post (1080×1350).
Parça x konumları 0/1020/2040 (60px örtüşme, eski araçla birebir).

reels-cover alt modu: orta kareyi 1080×1920 Instagram Reels kapağına çevirir
(play overlay'li). Reels grid preview'da orta karenin komşularıyla hizalı
görünmesi için orta içerik 1440'a warp + yatay kompanzasyon (0.965) uygulanır,
sonra 1920 tuvale ortalanır (eski araçla birebir).
"""
import io
import os

from PIL import Image

WIDE_SIZE = (3120, 1350)
TILE_W, TILE_H = 1080, 1350
X_POSITIONS = (0, 1020, 2040)

# reels-cover sabitleri (eski monolitle birebir)
REELS_H = 1920
CENTER_WARP_H = 1440
GRID_SCALE_X = 0.965
_PLAY_OVERLAY_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'play_overlay.png')
_play_cache = None


class ImageToolError(Exception):
    """Geçersiz girdi (boyut vb.)."""


def split_wide(img):
    """3120×1350 görseli üç 1080×1350 parçaya böl. Parça listesi döndürür."""
    if img.size != WIDE_SIZE:
        raise ImageToolError(
            f"Görsel {img.size[0]}×{img.size[1]}, {WIDE_SIZE[0]}×{WIDE_SIZE[1]} olmalı.")
    return [img.crop((x, 0, x + TILE_W, TILE_H)) for x in X_POSITIONS]


def _load_play_overlay():
    """Play overlay PNG'ini (1080×1350 RGBA, ortada üçgen) yükle — cache'li."""
    global _play_cache
    if _play_cache is None:
        _play_cache = Image.open(_PLAY_OVERLAY_PATH).convert('RGBA')
        _play_cache.load()
    return _play_cache


def _compensate_reels_grid(tile, scale_x=GRID_SCALE_X):
    """Reels grid preview'un yatay crop/zoom farkını telafi et: içeriği yatayda
    biraz daraltıp ortala, boşlukları kenar pikselini uzatarak doldur (seamless)."""
    tile = tile.convert('RGBA')
    w, h = tile.size
    new_w = int(w * scale_x)
    resized = tile.resize((new_w, h), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    x0 = (w - new_w) // 2
    canvas.paste(resized, (x0, 0), resized)
    if x0 > 0:
        left = resized.crop((0, 0, 1, h)).resize((x0, h), Image.Resampling.NEAREST)
        canvas.paste(left, (0, 0))
    right_w = w - (x0 + new_w)
    if right_w > 0:
        right = resized.crop((new_w - 1, 0, new_w, h)).resize((right_w, h), Image.Resampling.NEAREST)
        canvas.paste(right, (x0 + new_w, 0))
    return canvas


def _apply_play_overlay(cover):
    """Play overlay'i kapağın ortasına, en-boy koruyarak sığdır ve bindir."""
    play = _load_play_overlay()
    cw, ch = cover.size
    pw, ph = play.size
    scale = min(cw / pw, ch / ph)
    play = play.resize((int(pw * scale), int(ph * scale)), Image.Resampling.LANCZOS)
    result = cover.convert('RGBA')
    x = (cw - play.size[0]) // 2
    y = (ch - play.size[1]) // 2
    result.paste(play, (x, y), play)
    return result


def make_reels_cover(img):
    """3120×1350 → [sol(1080×1350), orta reels kapağı(1080×1920, play overlay'li),
    sağ(1080×1350)]. Sol/sağ orijinalden birebir; orta kare warp+kompanze edilir."""
    if img.size != WIDE_SIZE:
        raise ImageToolError(
            f"Görsel {img.size[0]}×{img.size[1]}, {WIDE_SIZE[0]}×{WIDE_SIZE[1]} olmalı.")
    left = img.crop((0, 0, TILE_W, TILE_H))
    right = img.crop((2040, 0, 2040 + TILE_W, TILE_H))
    center = img.crop((1020, 0, 1020 + TILE_W, TILE_H)).resize(
        (TILE_W, CENTER_WARP_H), Image.Resampling.LANCZOS)
    center = _compensate_reels_grid(center)
    cover = Image.new('RGBA', (TILE_W, REELS_H), (0, 0, 0, 0))
    cover.paste(center, (0, (REELS_H - CENTER_WARP_H) // 2), center)
    cover = _apply_play_overlay(cover)
    return [left, cover, right]


def encode(img, ext):
    """PIL görseli (bytes, mime) olarak kodla. ext: jpg|jpeg|png."""
    ext = (ext or 'jpg').lower()
    buf = io.BytesIO()
    if ext in ('jpg', 'jpeg'):
        if img.mode == 'RGBA':
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.save(buf, 'JPEG', quality=95)
        return buf.getvalue(), 'image/jpeg'
    img.save(buf, 'PNG')
    return buf.getvalue(), 'image/png'


def split_wide_bytes(data, ext, reels=False):
    """Bytes girdi → [(dosya_adı, bytes, mime), ...]. UI için hazır.

    reels=True: orta kare 1080×1920 video kapağına çevrilir (play overlay'li),
    sol/sağ orijinal parçalar; orta dosya 'instagram_video_kapagi.<ext>' olur."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ImageToolError(f'Görsel açılamadı: {e}')
    out_ext = 'png' if (ext or '').lower() == 'png' else 'jpg'
    parts = make_reels_cover(img) if reels else split_wide(img)
    result = []
    for i, part in enumerate(parts, 1):
        name = f'instagram_video_kapagi.{out_ext}' if (reels and i == 2) else f'bolunmus_gorsel_{i}.{out_ext}'
        b, mime = encode(part, out_ext)
        result.append((name, b, mime))
    return result
