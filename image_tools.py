"""Image tools — image_splitter (ported from the old monolith). Pure PIL, no DB/secrets.

Agency Instagram flow: a 3120x1350 wide image -> 3 side-by-side vertical
posts (1080x1350). Tile x positions are 0/1020/2040 (60px overlap, exact
match with the old tool).

reels-cover submode: converts ALL THREE tiles (in a triple post the video can
be any of the tiles — previously only the center tile was converted) into a
1080x1920 Instagram Reels cover (with a play overlay). For a tile to appear
aligned with its neighbors in the Reels grid preview, the content is warped
to 1440 + a horizontal compensation (0.965) is applied, then centered onto a
1920 canvas (exact match with the old tool; the top/bottom margins stay
unfilled — deliberate, the existing output must not change).
"""
import io
import os

from PIL import Image

WIDE_SIZE = (3120, 1350)
TILE_W, TILE_H = 1080, 1350
X_POSITIONS = (0, 1020, 2040)

# reels-cover constants (exact match with the old monolith)
REELS_H = 1920
CENTER_WARP_H = 1440
GRID_SCALE_X = 0.965
_PLAY_OVERLAY_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'play_overlay.png')
_play_cache = None

# Play button opacity: the circle drops to 40% opacity, the triangle stays FULLY
# opaque — the play icon stays readable while the design underneath shows through
# the circle. `assets/play_overlay.png` is NOT modified (the source stays
# canonical in one place); the transform is applied to the alpha channel at
# generation time. In the source PNG the triangle has alpha=255 and the circle
# alpha≈133 (~52%; edge antialiasing also produces in-between values like
# 130/198 — scaling by ratio carries those to the right proportion too).
# Above the threshold = "triangle", untouched.
PLAY_CIRCLE_OPACITY = 0.40
_PLAY_CIRCLE_SRC_OPACITY = 133 / 255
_PLAY_SOLID_THRESHOLD = 200


class ImageToolError(Exception):
    """Invalid input (size etc.)."""


def split_wide(img):
    """Split a 3120x1350 image into three 1080x1350 tiles. Returns the tile list."""
    if img.size != WIDE_SIZE:
        raise ImageToolError(
            f"Image is {img.size[0]}×{img.size[1]}; it must be "
            f"{WIDE_SIZE[0]}×{WIDE_SIZE[1]}.")
    return [img.crop((x, 0, x + TILE_W, TILE_H)) for x in X_POSITIONS]


def _load_play_overlay():
    """Load the play overlay PNG (1080x1350 RGBA, triangle in the center) — cached."""
    global _play_cache
    if _play_cache is None:
        _play_cache = Image.open(_PLAY_OVERLAY_PATH).convert('RGBA')
        _play_cache.load()
    return _play_cache


def _compensate_reels_grid(tile, scale_x=GRID_SCALE_X):
    """Compensate for the Reels grid preview's horizontal crop/zoom
    difference: narrow the content horizontally a bit and center it, fill
    the gaps by stretching the edge pixel (seamless)."""
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


def _scale_circle_alpha(v):
    """Play overlay alpha transform: above the threshold (the triangle) is
    UNTOUCHED, below it is scaled proportionally to `PLAY_CIRCLE_OPACITY`
    (see the constants at the top of the module)."""
    if v > _PLAY_SOLID_THRESHOLD:
        return v
    return int(v * PLAY_CIRCLE_OPACITY / _PLAY_CIRCLE_SRC_OPACITY)


def _apply_play_overlay(cover):
    """Fit the play overlay to the cover's center, preserving aspect ratio, and composite it on top.

    The circle's alpha is scaled to `PLAY_CIRCLE_OPACITY`; the triangle stays fully opaque."""
    play = _load_play_overlay()
    cw, ch = cover.size
    pw, ph = play.size
    scale = min(cw / pw, ch / ph)
    play = play.resize((int(pw * scale), int(ph * scale)), Image.Resampling.LANCZOS)
    play.putalpha(play.split()[3].point(_scale_circle_alpha))
    result = cover.convert('RGBA')
    x = (cw - play.size[0]) // 2
    y = (ch - play.size[1]) // 2
    result.paste(play, (x, y), play)
    return result


def _to_cover(tile):
    """Convert a 1080x1350 tile into a 1080x1920 reels cover: vertical warp to
    1440 + horizontal compensation (see `_compensate_reels_grid`) + centering
    onto a 1920 canvas + the play overlay. The remaining top/bottom space is
    transparent (falls to white when encoded as JPEG)."""
    center = tile.resize((TILE_W, CENTER_WARP_H), Image.Resampling.LANCZOS)
    center = _compensate_reels_grid(center)
    cover = Image.new('RGBA', (TILE_W, REELS_H), (0, 0, 0, 0))
    cover.paste(center, (0, (REELS_H - CENTER_WARP_H) // 2), center)
    return _apply_play_overlay(cover)


def make_reels_cover(img):
    """3120x1350 -> THREE 1080x1920 reels covers (with a play overlay).

    In triple posts the video tile's position (left/center/right) can vary;
    previously only the center tile was converted to a cover (left/right
    stayed original posts). Now all three go through the SAME transform — the
    user takes whichever one they need from the ZIP."""
    if img.size != WIDE_SIZE:
        raise ImageToolError(
            f"Image is {img.size[0]}×{img.size[1]}; it must be "
            f"{WIDE_SIZE[0]}×{WIDE_SIZE[1]}.")
    tiles = [img.crop((x, 0, x + TILE_W, TILE_H)) for x in X_POSITIONS]
    return [_to_cover(t) for t in tiles]


def encode(img, ext):
    """Encode a PIL image as (bytes, mime). ext: jpg|jpeg|png."""
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
    """Bytes input -> [(file_name, bytes, mime), ...]. Ready for the UI.

    reels=True: ALL THREE tiles are converted to 1080x1920 video covers (with
    a play overlay); the files are named 'instagram_video_cover_1..3.<ext>'
    (previously only the center tile was a cover)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ImageToolError(f'Could not open image: {e}')
    out_ext = 'png' if (ext or '').lower() == 'png' else 'jpg'
    parts = make_reels_cover(img) if reels else split_wide(img)
    result = []
    for i, part in enumerate(parts, 1):
        name = (f'instagram_video_cover_{i}.{out_ext}' if reels
                else f'split_image_{i}.{out_ext}')
        b, mime = encode(part, out_ext)
        result.append((name, b, mime))
    return result
