"""Seamless procedural textures for game assets, as Pillow images.

Every texture **tiles**: the noise is periodic over the image, and patterns
(bricks, tiles, planks) repeat an exact whole number of times. Mesh UVs are in
world units (see `mesh`), so one texture repeat covers `1 / uv_scale` world
units — keep a material's `repeat` and the mesh's `uv_scale` in step and every
block that uses it has the same texel density.

Sizes are powers of two (default 256) so GPUs can mipmap them.

    from utils import texture
    img = texture.brick(256, rows=8, cols=4, color="#9a4a32")
    glass, glow = texture.windows(256, cols=4, rows=6)
"""

from __future__ import annotations

import io
from typing import Sequence

import numpy as np
from PIL import Image

Color = str | Sequence[float]


def rgb(color: Color) -> np.ndarray:
    """A colour as float RGB in [0, 1], from '#rrggbb' or a 0-1 / 0-255 tuple."""
    if isinstance(color, str):
        c = color.lstrip("#")
        if len(c) == 3:  # '#rgb' shorthand
            c = "".join(ch * 2 for ch in c)
        return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64) / 255
    a = np.asarray(color, dtype=np.float64)[:3]
    return a / 255 if a.max() > 1 else a


def to_image(arr: np.ndarray) -> Image.Image:
    """Float array (H, W, 3|4) in [0, 1], or (H, W) grey, to a Pillow image."""
    a = np.clip(np.asarray(arr), 0, 1)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    mode = "RGBA" if a.shape[2] == 4 else "RGB"
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8), mode)


def to_png(img: Image.Image) -> bytes:
    """Encode an image as PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------ noise


def noise(size: int = 256, cells: int = 8, *, octaves: int = 4, seed: int = 0,
          persistence: float = 0.5) -> np.ndarray:
    """Tileable fractal value noise in [0, 1], shape (size, size)."""
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        n = cells * 2 ** o
        grid = rng.random((n, n))
        t = np.arange(size) * n / size
        i0 = np.floor(t).astype(int) % n
        i1 = (i0 + 1) % n
        f = t - np.floor(t)
        f = f * f * (3 - 2 * f)
        a = grid[i0][:, i0] * (1 - f)[None, :] + grid[i0][:, i1] * f[None, :]
        b = grid[i1][:, i0] * (1 - f)[None, :] + grid[i1][:, i1] * f[None, :]
        out += amp * (a * (1 - f)[:, None] + b * f[:, None])
        total += amp
        amp *= persistence
    return out / total


def colorize(value: np.ndarray, stops: Sequence[tuple[float, Color]]) -> np.ndarray:
    """Map a [0, 1] field through colour stops [(position, colour), ...] to RGB."""
    pos = np.array([p for p, _ in stops])
    cols = np.array([rgb(c) for _, c in stops])
    return np.stack([np.interp(value, pos, cols[:, k]) for k in range(3)], axis=-1)


def tint(base: Color, value: np.ndarray, amount: float = 0.25) -> np.ndarray:
    """A flat colour modulated by a [0, 1] field: (1 - amount .. 1 + amount)."""
    return rgb(base)[None, None, :] * (1 + amount * (value[..., None] * 2 - 1))


def solid(color: Color, size: int = 64, *, grain: float = 0.06, seed: int = 0) -> Image.Image:
    """A flat colour with a faint grain so large faces do not look like plastic."""
    return to_image(tint(color, noise(size, 4, seed=seed), grain))


# ------------------------------------------------------------------ materials


def concrete(size: int = 256, color: Color = "#9a9a96", *, seed: int = 0) -> Image.Image:
    """Mottled concrete or plaster."""
    n = noise(size, 6, octaves=5, seed=seed)
    return to_image(tint(color, n, 0.18))


def grass(size: int = 256, color: Color = "#5d8f3a", *, seed: int = 0) -> Image.Image:
    """Lawn or meadow ground."""
    n = noise(size, 8, octaves=5, seed=seed)
    blades = noise(size, 64, octaves=1, seed=seed + 1)
    return to_image(tint(color, 0.6 * n + 0.4 * blades, 0.3))


def snow(size: int = 256, *, seed: int = 0) -> Image.Image:
    """Snow cover with a cool blue shadow tone."""
    n = noise(size, 6, octaves=5, seed=seed)
    return to_image(colorize(n, [(0, "#c9d6e8"), (0.55, "#eef3fa"), (1, "#ffffff")]))


def dirt(size: int = 256, color: Color = "#7a5a3a", *, seed: int = 0) -> Image.Image:
    """Bare earth with pebbles."""
    n = noise(size, 8, octaves=5, seed=seed)
    pebbles = (noise(size, 32, octaves=1, seed=seed + 7) > 0.78) * 0.25
    return to_image(tint(color, np.clip(n + pebbles, 0, 1), 0.35))


def rock(size: int = 256, color: Color = "#7c7f84", *, seed: int = 0) -> Image.Image:
    """Granite-like stone."""
    n = noise(size, 5, octaves=6, seed=seed, persistence=0.6)
    return to_image(tint(color, n, 0.4))


def water(size: int = 256, color: Color = "#3d7fb8", *, seed: int = 0) -> Image.Image:
    """Rippled water."""
    n = noise(size, 6, octaves=4, seed=seed)
    ripples = np.sin(np.linspace(0, 2 * np.pi * 12, size, endpoint=False))[:, None] * 0.5 + 0.5
    return to_image(tint(color, 0.7 * n + 0.3 * ripples, 0.25))


def asphalt(size: int = 256, *, seed: int = 0) -> Image.Image:
    """Road surface."""
    n = noise(size, 32, octaves=3, seed=seed)
    return to_image(tint("#3b3e46", n, 0.25))


def wood(size: int = 256, color: Color = "#8a5a32", *, seed: int = 0) -> Image.Image:
    """Wood grain running vertically (along V)."""
    n = noise(size, 4, octaves=3, seed=seed)
    x = np.arange(size)[None, :] / size
    rings = (np.sin((x * 24 + n * 3) * 2 * np.pi) * 0.5 + 0.5)
    return to_image(tint(color, 0.6 * rings + 0.4 * n, 0.3))


def bark(size: int = 256, color: Color = "#5a3f2a", *, seed: int = 0) -> Image.Image:
    """Tree bark: vertical furrows."""
    n = noise(size, 4, octaves=4, seed=seed)
    x = np.arange(size)[None, :] / size
    furrow = np.abs(np.sin((x * 10 + n * 0.8) * np.pi))
    return to_image(tint(color, furrow * 0.7 + n * 0.3, 0.45))


def foliage(size: int = 256, color: Color = "#2f6b3a", *, seed: int = 0) -> Image.Image:
    """Leaf or needle clusters."""
    n = noise(size, 16, octaves=4, seed=seed)
    return to_image(tint(color, n, 0.45))


def metal(size: int = 256, color: Color = "#9aa3ad", *, panels: int = 4, seed: int = 0) -> Image.Image:
    """Brushed metal panels with seams."""
    n = noise(size, 4, octaves=3, seed=seed)
    brushed = noise(size, 64, octaves=1, seed=seed + 3)
    base = tint(color, 0.5 * n + 0.5 * brushed[:, ::-1], 0.12)
    step = size // panels
    seam = np.zeros((size, size), bool)
    seam[::step, :] = True
    seam[:, ::step] = True
    base[seam] *= 0.6
    return to_image(base)


def fabric(size: int = 128, color: Color = "#3f6fb0", *, seed: int = 0) -> Image.Image:
    """Woven cloth for clothing."""
    y, x = np.mgrid[0:size, 0:size]
    weave = ((x // 2 + y // 2) % 2) * 0.5 + noise(size, 16, octaves=2, seed=seed) * 0.5
    return to_image(tint(color, weave, 0.12))


def skin(size: int = 64, color: Color = "#e0b48c", *, seed: int = 0) -> Image.Image:
    """Faintly mottled skin tone."""
    return to_image(tint(color, noise(size, 4, octaves=3, seed=seed), 0.06))


def stripes(size: int = 256, count: int = 8, *, a: Color = "#f4f1ea", b: Color = "#3b3e46",
            vertical: bool = False, duty: float = 0.5) -> Image.Image:
    """Even stripes — zebra crossings, hazard tape, awnings."""
    t = (np.arange(size) * count / size) % 1.0 < duty
    mask = np.broadcast_to(t[None, :] if vertical else t[:, None], (size, size))
    out = np.where(mask[..., None], rgb(a), rgb(b))
    return to_image(out)


# ------------------------------------------------------------------ patterns


def brick(size: int = 256, *, rows: int = 8, cols: int = 4, color: Color = "#9a4a32",
          mortar: Color = "#c9c2b4", gap: float = 0.08, seed: int = 0) -> Image.Image:
    """Running-bond brick, `rows` courses and `cols` bricks per course per repeat."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    r = np.floor(y * rows).astype(int)
    xs = x * cols + (r % 2) * 0.5
    c = np.floor(xs).astype(int) % cols
    fy, fx = (y * rows) % 1, xs % 1
    gy, gx = gap, gap * cols / rows
    is_mortar = (fy < gy) | (fx < gx)
    shade = rng.uniform(0.82, 1.12, (rows, cols))[r % rows, c]
    n = noise(size, 16, octaves=3, seed=seed)
    out = rgb(color)[None, None] * (shade * (0.9 + 0.2 * n))[..., None]
    out[is_mortar] = rgb(mortar) * (0.9 + 0.2 * n[is_mortar, None])
    return to_image(out)


def tiles(size: int = 256, *, count: int = 8, color: Color = "#d8d2c4", grout: Color = "#8c867a",
          gap: float = 0.06, vary: float = 0.08, seed: int = 0) -> Image.Image:
    """Square floor or wall tiles, `count` per side per repeat."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    ix, iy = np.floor(x * count).astype(int), np.floor(y * count).astype(int)
    fx, fy = (x * count) % 1, (y * count) % 1
    shade = rng.uniform(1 - vary, 1 + vary, (count, count))[iy, ix]
    out = rgb(color)[None, None] * shade[..., None]
    out[(fx < gap) | (fy < gap)] = rgb(grout)
    return to_image(out)


def planks(size: int = 256, *, count: int = 6, color: Color = "#9a6a3a", seed: int = 0) -> Image.Image:
    """Floor boards or wooden siding: `count` planks across, staggered joints."""
    rng = np.random.default_rng(seed)
    grain = np.asarray(wood(size, color, seed=seed), dtype=np.float64) / 255
    y, x = np.mgrid[0:size, 0:size] / size
    i = np.floor(x * count).astype(int)
    off = rng.random(count)[i]
    joint = ((y + off) % 1.0) < 0.012
    edge = (x * count) % 1 < 0.04
    shade = rng.uniform(0.85, 1.1, count)[i]
    out = grain * shade[..., None]
    out[joint | edge] *= 0.55
    return to_image(out)


def shingles(size: int = 256, *, rows: int = 8, cols: int = 8, color: Color = "#3d4a5a",
             seed: int = 0) -> Image.Image:
    """Overlapping roof tiles (kawara-style rounded courses); rows run along U."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    r = np.floor(y * rows).astype(int)
    fy = (y * rows) % 1
    xs = x * cols + (r % 2) * 0.5
    fx = xs % 1
    c = np.floor(xs).astype(int) % cols
    curve = np.sqrt(np.clip(1 - (2 * fx - 1) ** 2, 0, 1))
    light = 0.55 + 0.45 * curve * (1 - 0.6 * fy)
    shade = rng.uniform(0.9, 1.08, (rows, cols))[r % rows, c]
    out = rgb(color)[None, None] * (light * shade)[..., None]
    out[fy > 0.9] *= 0.5
    return to_image(out)


def windows(size: int = 256, *, cols: int = 4, rows: int = 4, wall: Color = "#c3c8d0",
            glass: Color = "#2c4660", frame: float = 0.18, lit: float = 0.35,
            light: Color = "#ffd98a", seed: int = 0) -> tuple[Image.Image, Image.Image]:
    """A facade of `cols` x `rows` windows per repeat: (colour, emissive) images.

    The emissive image is black except for the windows that are lit, so the
    same material glows at night when the game raises its emissive strength.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    fx, fy = (x * cols) % 1, (y * rows) % 1
    ix, iy = np.floor(x * cols).astype(int), np.floor(y * rows).astype(int)
    pane = (fx > frame / 2) & (fx < 1 - frame / 2) & (fy > frame) & (fy < 1 - frame * 0.6)
    n = noise(size, 8, octaves=3, seed=seed)
    color = tint(wall, n, 0.08)
    sky = (1 - fy)[..., None] * 0.25
    color[pane] = rgb(glass) + sky[pane]
    on = rng.random((rows, cols)) < lit
    glow = np.zeros((size, size, 3))
    mask = pane & on[iy, ix]
    glow[mask] = rgb(light)
    color[mask] = color[mask] * 0.5 + rgb(light) * 0.5
    return to_image(color), to_image(glow)


def lattice(size: int = 256, *, cells: int = 4, color: Color = "#ec5b2a", bar: float = 0.12,
            background: Color | None = None) -> Image.Image:
    """Diagonal truss bracing; transparent between bars unless `background` is given."""
    y, x = np.mgrid[0:size, 0:size] / size
    u, v = (x * cells) % 1, (y * cells) % 1
    on = (np.abs(u - v) < bar / 2) | (np.abs(u + v - 1) < bar / 2) | (u < bar / 2) | (u > 1 - bar / 2) \
        | (v < bar / 2) | (v > 1 - bar / 2)
    out = np.zeros((size, size, 4))
    out[..., :3] = rgb(color)
    out[..., 3] = on
    if background is not None:
        out[~on, :3] = rgb(background)
        out[..., 3] = 1
    return to_image(out)


def gradient(size: int = 256, top: Color = "#9cc8ff", bottom: Color = "#f7e3c0") -> Image.Image:
    """A vertical gradient, top colour at V=0 — sky domes and backdrops."""
    t = np.linspace(0, 1, size)[:, None, None]
    out = rgb(top) * (1 - t) + rgb(bottom) * t
    return to_image(np.broadcast_to(out, (size, size, 3)))


def atlas(images: Sequence[Image.Image], *, cell: int = 128) -> tuple[Image.Image, list[tuple[float, float, float, float]]]:
    """Pack images into one square atlas: (image, [(u0, v0, u1, v1) per input])."""
    n = max(1, int(np.ceil(np.sqrt(len(images)))))
    side = 1
    while side < n * cell:
        side *= 2
    sheet = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    rects = []
    for k, img in enumerate(images):
        cx, cy = (k % n) * cell, (k // n) * cell
        sheet.paste(img.convert("RGBA").resize((cell, cell)), (cx, cy))
        rects.append((cx / side, cy / side, (cx + cell) / side, (cy + cell) / side))
    return sheet, rects
