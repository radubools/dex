"""Ground for Many Builders: 1x1 terrain tiles, N x M chunks, seasons, and the package helpers.

Tile types follow `src/world.js` (`T = {GRASS: 0, WATER: 1, DIRT: 2, TRAIL: 3}`)
plus `forest` — forest floor under the trees, which the game marks by the
tiles its `world.trees` stand on (`tiles_from_world`).

Geometry rules — what makes tiles and chunks join with no seams:

- A tile is 1 x 1 centred on its origin, a grid of `SUBDIV` x `SUBDIV` quads.
- **Every vertex on a tile edge is at y = 0.** Relief (`RELIEF`) only moves
  interior vertices, so any two tiles, of any type, meet exactly.
- Water is the one exception, and only inside a chunk: a vertex whose every
  touching tile is water sinks with its distance to the shore, so lakes get a
  sloping bed. The depth is computed from the whole map grid, so the shared
  edge of two chunks gets the same heights from both sides.
- UVs are map coordinates / `UV_TILES` (one texture repeat per 4 x 4 tiles,
  so no per-tile pattern shows), continuous across tiles and chunks. A lone
  tile takes its map position (`tile(..., at=(x, z))`) for the same phase.

Seasons: every tile/chunk is written once per season (`summer`, `autumn`,
`winter`) with **identical geometry, UVs and material names** — only the
textures change (winter = snow; water becomes ice and stops bobbing). So the
game may either swap files at a season change or load both and crossfade the
base colour textures by `game.snowLevel()`.

    from utils import terrain
    scene = terrain.chunk(grid, "winter", origin=(8, 16))
    terrain.build_tile("grass", "out/")          # package: summer/autumn/winter
    grid, trees = terrain.world_grid(seed=1234)  # the game's own map, ported
"""

from __future__ import annotations

import functools
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw

from . import catalog, gltf, mesh, preview, texture

#: The game's map size in tiles (`src/config.js` MAP_W / MAP_H).
MAP_W = MAP_H = 52
#: Tile types this module builds, in `world.js` order plus forest floor.
TILE_TYPES = ("grass", "water", "dirt", "trail", "forest")
#: `world.js` tile codes to names.
GAME_TILES = {0: "grass", 1: "water", 2: "dirt", 3: "trail"}
SEASONS = ("summer", "autumn", "winter")
#: Quads per tile side. Every tile type uses the same grid, so edges have the same vertices.
SUBDIV = 4
#: Interior relief amplitude per tile type (units). Edges stay at 0 regardless.
RELIEF = {"grass": 0.05, "forest": 0.07, "dirt": 0.01, "trail": 0.02, "water": 0.0}
#: Water surface inside a chunk (below the y=0 banks) and on a lone tile (just above its rim).
WATER_LEVEL = -0.06
TILE_WATER_LEVEL = 0.02
#: Deepest lake bed, and the distance from shore (tiles) at which it is reached.
WATER_DEPTH = 0.32
SHORE = 0.8
#: Gentle bob of the water node, units and seconds.
BOB, BOB_PERIOD = 0.012, 4.0
#: Tiles covered by one repeat of a ground texture (UVs are map coordinates / UV_TILES).
UV_TILES = 4
#: Default chunk side in tiles: 8 x 8 x 32 triangles stays inside the environment budget.
CHUNK = 8
#: Depth of the earth sides added by `skirt=True` on chunk sides that are the map edge.
SKIRT_DEPTH = 0.8

# Colours carried over from renderer.js GROUND so the 3D map reads like the 2D one.
_GROUND = {"grass": "#4b6d3b", "forest": "#3c4a2b", "dirt": "#7e6649", "trail": "#7d705c",
           "water": "#2f6090", "lakebed": "#4d4734", "earth": "#4a3828", "ice": "#c4deec"}
_AUTUMN = {"grass": "#6f7a3e", "forest": "#5b4a2c", "water": "#2b5679"}
#: Fraction of a tile type's ground hidden by snow in winter (renderer.js mixes ~the same).
SNOW_COVER = {"grass": 0.9, "forest": 0.82, "dirt": 0.45, "trail": 0.6, "lakebed": 0.7, "earth": 0.0}


# ------------------------------------------------------------------ textures


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.float64) / 255


def _specks(size: int, density: float, cells: int, seed: int) -> np.ndarray:
    """A 0/1 field of small tileable specks covering about `density` of the image."""
    n = texture.noise(size, cells, octaves=1, seed=seed)
    return (n > np.quantile(n, 1 - density)).astype(np.float64)


def snow_cover(base: np.ndarray, coverage: float, *, seed: int = 0) -> np.ndarray:
    """Lay patchy snow over an RGB array so about `coverage` of it is white."""
    if coverage <= 0:
        return base
    size = base.shape[0]
    snow = _arr(texture.snow(size, seed=seed + 11))
    if coverage >= 0.999:
        return snow
    n = 0.8 * texture.noise(size, 3, octaves=4, seed=seed + 5) + 0.2 * texture.noise(size, 16, octaves=2, seed=seed + 6)
    thr = np.quantile(n, 1 - coverage)
    m = np.clip((n - thr + 0.02) / 0.04, 0, 1)[..., None]
    return base * (1 - m) + snow * m


@functools.lru_cache(maxsize=None)
def ground_texture(kind: str, season: str = "summer", size: int = 256) -> Image.Image:
    """The seamless ground texture of a tile type (or 'lakebed' / 'earth') for a season."""
    s = size
    col = (_AUTUMN.get(kind) if season == "autumn" else None) or _GROUND[kind]
    if kind == "grass":
        a = _arr(texture.grass(s, col, seed=3))
        if season == "summer":
            a = np.where(_specks(s, 0.012, 96, 4)[..., None] > 0, texture.rgb("#e8e2b0"), a)
        else:
            a = np.where(_specks(s, 0.02, 64, 5)[..., None] > 0, texture.rgb("#8a6a3a"), a)
    elif kind == "forest":
        n = texture.noise(s, 10, octaves=5, seed=21)
        a = texture.tint(col, 0.6 * n + 0.4 * texture.noise(s, 80, octaves=1, seed=22), 0.35)
        a = np.where(_specks(s, 0.05, 72, 23)[..., None] > 0, texture.rgb("#6b5233"), a)   # needles
        moss = texture.noise(s, 5, octaves=3, seed=24) > 0.62
        a[moss] = a[moss] * 0.6 + texture.rgb("#557a35") * 0.4
        if season == "autumn":
            for k, leaf in enumerate(("#c8642a", "#d9a43a", "#a8402a")):
                a = np.where(_specks(s, 0.025, 48, 30 + k)[..., None] > 0, texture.rgb(leaf), a)
    elif kind == "dirt":
        a = _arr(texture.dirt(s, col, seed=7))
    elif kind == "trail":
        a = _arr(texture.dirt(s, col, seed=8)) * 0.85 + 0.15 * texture.tint("#8d7a60", texture.noise(s, 48, seed=9), 0.3)
        a = np.where(_specks(s, 0.03, 128, 10)[..., None] > 0, texture.rgb("#9d927e"), a)  # gravel
    elif kind == "water":
        if season == "winter":
            n = texture.noise(s, 6, octaves=5, seed=41)
            a = texture.colorize(n, [(0, "#a9c9de"), (0.5, "#c4deec"), (1, "#e6f2f8")])
            cracks = np.abs(texture.noise(s, 4, octaves=3, seed=42) - 0.5) < 0.012
            a[cracks] = texture.rgb("#f6fbff")
            return texture.to_image(a)
        a = _arr(texture.water(s, col, seed=40))
    elif kind == "lakebed":
        a = _arr(texture.dirt(s, col, seed=50))
    elif kind == "earth":
        a = _arr(texture.dirt(s, col, seed=60))
        a *= (0.85 + 0.15 * np.sin(np.arange(s) / s * 2 * np.pi * 6))[:, None, None]   # strata
    else:
        raise KeyError(f"unknown ground kind {kind!r}; have {TILE_TYPES + ('lakebed', 'earth')}")
    if season == "winter":
        a = snow_cover(a, SNOW_COVER.get(kind, 0.0), seed=sum(map(ord, kind)))
    return texture.to_image(a)


def declare_materials(scene: gltf.Scene, season: str, kinds: Sequence[str] = TILE_TYPES + ("lakebed", "earth")) -> None:
    """Declare the ground materials (named after the tile type) on a scene for a season."""
    for k in kinds:
        if k == "water":
            if season == "winter":
                scene.material("water", texture=ground_texture("water", "winter"), roughness=0.25, season=season)
            else:
                scene.material("water", texture=ground_texture("water", season), roughness=0.08, opacity=0.82,
                               season=season)
        else:
            scene.material(k, texture=ground_texture(k, season), roughness=0.95, season=season)


# ------------------------------------------------------------------ snow on models


@functools.lru_cache(maxsize=None)
def snow_texture(size: int = 128) -> Image.Image:
    """The shared snow texture used on caps and drifts of models."""
    return texture.snow(size, seed=77)


def snow_material(scene: gltf.Scene, name: str = "snow") -> None:
    """Declare the shared `snow` material on a scene."""
    scene.material(name, texture=snow_texture(), roughness=0.9)


def snowify(meshes: Sequence[mesh.Mesh], *, depth: float = 0.04, threshold: float = 0.6,
            material: str = "snow", skip: Sequence[str] = ()) -> list[mesh.Mesh]:
    """Snow slabs on the up-facing triangles of meshes: tops lifted by `depth`, open edges walled.

    Returns only the snow meshes; add them next to the originals. Triangles
    whose normal's y is below `threshold` (walls, undersides) stay bare, as do
    meshes whose material is in `skip`.
    """
    out = []
    for m in meshes:
        if m.material in skip or not m.triangles:
            continue
        p = m.positions.astype(np.float64)
        tri = m.indices.astype(np.int64)
        a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
        fn = np.cross(b - a, c - a)
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        sel = fn[:, 1] > threshold
        if not sel.any():
            continue
        tris = np.stack([a[sel], b[sel], c[sel]], axis=1)
        up = np.array([0.0, depth, 0.0])
        key = lambda v: tuple(np.round(v, 5))  # noqa: E731
        count: dict = {}
        for t in tris:
            for k in range(3):
                e = (key(t[k]), key(t[(k + 1) % 3]))
                count[e] = count.get(e, 0) + 1
        verts, faces = [], []
        for t in tris:
            n0 = len(verts)
            verts += [t[0] + up, t[1] + up, t[2] + up]
            faces.append((n0, n0 + 1, n0 + 2))
            centre = t.mean(0)
            for k in range(3):
                pa, pb = t[k], t[(k + 1) % 3]
                if count.get((key(pb), key(pa)), 0):   # shared with another snowy triangle: no wall
                    continue
                n1 = len(verts)
                drop = np.array([0.0, -0.01, 0.0])
                verts += [pa + drop, pb + drop, pb + up, pa + up]
                quad = (n1, n1 + 1, n1 + 2, n1 + 3)
                normal = np.cross(pb - pa, up)
                if np.dot(normal, (pa + pb) / 2 - centre) < 0:
                    quad = quad[::-1]
                faces.append(quad)
        out.append(mesh.from_faces(np.asarray(verts), faces, material))
    return out


def jitter(m: mesh.Mesh, amount: float, *, seed: int = 0, keep_floor: bool = True) -> mesh.Mesh:
    """Displace vertices randomly (coincident vertices move together, so no cracks open)."""
    out = m.copy()
    key = np.round(out.positions.astype(np.float64), 5)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    rng = np.random.default_rng(seed)
    d = rng.uniform(-amount, amount, (len(uniq), 3))
    if keep_floor:
        d[uniq[:, 1] <= 1e-6, 1] = 0.0
    p = (uniq + d)[inv.reshape(-1)]
    out.positions = p.astype(np.float32)
    return _renormal(out)


def sink(m: mesh.Mesh, depth: float = 0.0) -> mesh.Mesh:
    """Rest a mesh on the ground: lift its lowest point to y=0, lower it by `depth`, flatten what is below."""
    out = m.translate(0, -float(m.positions[:, 1].min()) - depth, 0)
    out.positions[:, 1] = np.maximum(out.positions[:, 1], 0.0)
    return _renormal(out)


def _renormal(m: mesh.Mesh) -> mesh.Mesh:
    """Recompute normals after moving vertices: flat where corners are unshared, averaged where shared."""
    p = m.positions.astype(np.float64)
    tri = m.indices.astype(np.int64)
    fn = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(length > 1e-12, n / np.maximum(length, 1e-12), [0.0, 1.0, 0.0])
    m.normals = n.astype(np.float32)
    return m


# ------------------------------------------------------------------ grids


def normalize_grid(grid: Any) -> list[list[str]]:
    """A tile grid as rows (map y / world +Z) of tile-type names; accepts names or world.js codes."""
    rows = [list(r) for r in grid]
    out = []
    for r in rows:
        row = []
        for t in r:
            name = GAME_TILES[int(t)] if not isinstance(t, str) else t
            if name not in TILE_TYPES:
                raise ValueError(f"unknown tile type {t!r}; have {TILE_TYPES}")
            row.append(name)
        out.append(row)
    if len({len(r) for r in out}) > 1:
        raise ValueError("grid rows have different lengths")
    return out


def tiles_from_world(tiles: Sequence[int], width: int = MAP_W, height: int = MAP_H,
                     trees: Sequence[Mapping[str, float]] = ()) -> list[list[str]]:
    """Convert world.js's flat `tiles` array (+ `trees`, which make forest floor) into a name grid."""
    grid = [[GAME_TILES[int(tiles[y * width + x])] for x in range(width)] for y in range(height)]
    for t in trees:
        x, y = int(math.floor(t["x"])), int(math.floor(t["y"]))
        if 0 <= x < width and 0 <= y < height and grid[y][x] == "grass":
            grid[y][x] = "forest"
    return grid


def _mulberry32(seed: int) -> Callable[[], float]:
    a = [seed & 0xFFFFFFFF]

    def imul(x: int, y: int) -> int:
        return ((x & 0xFFFFFFFF) * (y & 0xFFFFFFFF)) & 0xFFFFFFFF

    def rng() -> float:
        a[0] = (a[0] + 0x6D2B79F5) & 0xFFFFFFFF
        x = a[0]
        t = imul(x ^ (x >> 15), 1 | x)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rng


# Plot rectangles from src/config.js (LANDMARKS x, y, w, h and BUNKER). Keep in step.
_PLOTS = [(21, 22, 1, 1), (29, 21, 3, 3), (19, 28, 2, 2), (31, 28, 3, 3), (22, 33, 4, 2), (14, 20, 2, 2),
          (32, 14, 4, 3), (16, 13, 3, 2), (36, 34, 2, 2)]
_BUNKER = (25, 25, 2, 2)


def world_grid(seed: int, width: int = MAP_W, height: int = MAP_H) -> tuple[list[list[str]], list[dict]]:
    """Port of world.js `createWorld` tiles and trees for a seed: (name grid, trees [{x, y, s, v}])."""
    rng = _mulberry32(seed)
    W, H = width, height
    n = W * H
    tiles = [0] * n
    reserved = [0] * n
    shade = [rng() for _ in range(n)]
    lakes = [(43, 16, 4.5, 3.6), (8, 39, 3.6, 4.6), (31, 44.5, 3.2, 2.4)]
    for y in range(H):
        for x in range(W):
            i = y * W + x
            for lx, ly, rx, ry in lakes:
                d = ((x + 0.5 - lx) / rx) ** 2 + ((y + 0.5 - ly) / ry) ** 2
                if d < 1 + (shade[i] - 0.5) * 0.25:
                    tiles[i] = 1

    def reserve(r, margin):
        rx, ry, rw, rh = r
        for y in range(ry - margin, ry + rh + margin):
            for x in range(rx - margin, rx + rw + margin):
                if 0 <= x < W and 0 <= y < H:
                    i = y * W + x
                    reserved[i] = 1
                    if tiles[i] != 1:
                        tiles[i] = 2

    for p in _PLOTS:
        reserve(p, 1)
    reserve(_BUNKER, 2)
    cx = cy = 26
    for t in range(W):
        wig = math.floor(math.sin(t * 0.3 + seed) * 1.2 + 0.5)
        for k in (-1, 0, 1):
            for x, y in ((t, cy + wig + k), (cx + wig + k, t)):
                if not (0 <= x < W and 0 <= y < H):
                    continue
                i = y * W + x
                if reserved[i] or tiles[i] == 1:
                    continue
                tiles[i] = 3
                reserved[i] = 1
    trees = []
    for y in range(H):
        for x in range(W):
            i = y * W + x
            if reserved[i] or tiles[i] == 1:
                continue
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            dens = min(1, max(0, (d - 6) / 18)) * 0.42
            cluster = 0.5 + 0.5 * math.sin(x * 0.45 + (seed % 7)) * math.cos(y * 0.38 + (seed % 5))
            if rng() < dens * (0.35 + cluster):
                tx, ty = x + 0.5 + (rng() - 0.5) * 0.3, y + 0.5 + (rng() - 0.5) * 0.3
                trees.append({"x": tx, "y": ty, "s": 0.8 + rng() * 0.5, "v": rng()})
    return tiles_from_world(tiles, W, H, trees), trees


# ------------------------------------------------------------------ heights


def _relief(kind: str, seed: int, tx: int, tz: int) -> np.ndarray:
    """(SUBDIV+1)^2 interior relief of one tile, zero on its edges, from a hash of its map position."""
    n = SUBDIV
    rng = np.random.default_rng([seed & 0xFFFF, tx & 0xFFFF, tz & 0xFFFF, TILE_TYPES.index(kind)])
    h = rng.uniform(-1, 1, (n + 1, n + 1)) * RELIEF[kind]
    if kind == "forest":
        h += RELIEF[kind] * 0.5   # forest floor is a little humped under the roots
    t = np.sin(np.pi * np.arange(n + 1) / n)
    h *= t[:, None] * t[None, :]
    h[0, :] = h[-1, :] = h[:, 0] = h[:, -1] = 0.0
    return h


def _rect_dist(px: float, pz: float, tx: int, tz: int) -> float:
    dx = max(tx - px, 0.0, px - (tx + 1))
    dz = max(tz - pz, 0.0, pz - (tz + 1))
    return math.hypot(dx, dz)


def heightfield(grid: Sequence[Sequence[str]], origin: tuple[int, int] = (0, 0),
                size: tuple[int, int] | None = None, *, seed: int = 0) -> np.ndarray:
    """Vertex heights (rows = z, cols = x) of a chunk of a name grid at SUBDIV per tile."""
    g = normalize_grid(grid)
    H, W = len(g), len(g[0])
    x0, z0 = origin
    cw, ch = size or (W - x0, H - z0)
    n = SUBDIV
    out = np.zeros((ch * n + 1, cw * n + 1))
    reach = int(math.ceil(SHORE)) + 1

    def tile(x: int, z: int) -> str | None:
        return g[z][x] if 0 <= x < W and 0 <= z < H else None

    for j in range(ch * n + 1):
        for i in range(cw * n + 1):
            vx, vz = x0 + i / n, z0 + j / n
            xs = [x0 + i // n - 1, x0 + i // n] if i % n == 0 else [x0 + i // n]
            zs = [z0 + j // n - 1, z0 + j // n] if j % n == 0 else [z0 + j // n]
            touching = [tile(x, z) for x in xs for z in zs]
            if all(t == "water" for t in touching):
                d = min((_rect_dist(vx, vz, tx, tz)
                         for tz in range(int(vz) - reach, int(vz) + reach + 1)
                         for tx in range(int(vx) - reach, int(vx) + reach + 1)
                         if tile(tx, tz) != "water"), default=SHORE)
                t = min(1.0, d / SHORE)
                out[j, i] = -WATER_DEPTH * (1 - (1 - t) ** 2)
            elif i % n and j % n:
                tx, tz = x0 + i // n, z0 + j // n
                kind = tile(tx, tz)
                out[j, i] = _relief(kind, seed, tx, tz)[j % n, i % n] if kind else 0.0
    return out


# ------------------------------------------------------------------ geometry


def _ground_meshes(g: list[list[str]], origin, size, heights: np.ndarray, centre,
                   normals: np.ndarray | None = None) -> list[mesh.Mesh]:
    """Ground triangles per material; smooth (shared, heightfield) normals when `normals` is given."""
    n = SUBDIV
    x0, z0 = origin
    cw, ch = size
    cx, cz = centre
    by_mat: dict[str, list[tuple[int, int, int]]] = {}
    pos, uvs = [], []
    for j in range(ch * n + 1):
        for i in range(cw * n + 1):
            vx, vz = x0 + i / n, z0 + j / n
            pos.append((vx - cx, heights[j, i], vz - cz))
            uvs.append((vx / UV_TILES, vz / UV_TILES))
    row = cw * n + 1
    for j in range(ch * n):
        for i in range(cw * n):
            kind = g[z0 + j // n][x0 + i // n]
            mat = "lakebed" if kind == "water" else kind
            a, b, c, d = j * row + i, j * row + i + 1, (j + 1) * row + i + 1, (j + 1) * row + i
            # a=(x0,z0) b=(x1,z0) c=(x1,z1) d=(x0,z1); CCW from above is a, d, c.
            tris = by_mat.setdefault(mat, [])
            if (i + j) % 2:
                tris += [(a, d, c), (a, c, b)]
            else:
                tris += [(a, d, b), (b, d, c)]
    P, U = np.asarray(pos), np.asarray(uvs)
    if normals is None:
        return [mesh.from_faces(P, faces, mat, uvs=U) for mat, faces in by_mat.items()]
    N = normals.reshape(-1, 3)
    out = []
    for mat, faces in by_mat.items():
        f = np.asarray(faces, dtype=np.int64)
        used, inv = np.unique(f, return_inverse=True)
        out.append(mesh.Mesh(P[used].astype(np.float32), N[used].astype(np.float32), U[used].astype(np.float32),
                             inv.reshape(-1, 3).astype(np.uint32), mat))
    return out


def ground_normals(grid: Any, origin: tuple[int, int], size: tuple[int, int], *, seed: int = 0) -> np.ndarray:
    """Smooth per-vertex normals of a chunk's heightfield, using neighbours outside it so chunks shade seamlessly."""
    n = SUBDIV
    x0, z0 = origin
    cw, ch = size
    big = heightfield(grid, (x0 - 1, z0 - 1), (cw + 2, ch + 2), seed=seed)
    step = 1.0 / n
    dx = (big[n:-n, n + 1:-n + 1] - big[n:-n, n - 1:-n - 1]) / (2 * step)
    dz = (big[n + 1:-n + 1 or None, n:-n] - big[n - 1:-n - 1, n:-n]) / (2 * step)
    nn = np.stack([-dx, np.ones_like(dx), -dz], axis=-1)
    return nn / np.linalg.norm(nn, axis=-1, keepdims=True)


def _water_mesh(g, origin, size, centre) -> mesh.Mesh | None:
    x0, z0 = origin
    cw, ch = size
    cx, cz = centre
    pos, uvs, faces = [], [], []
    for z in range(z0, z0 + ch):
        for x in range(x0, x0 + cw):
            if g[z][x] != "water":
                continue
            k = len(pos)
            for vx, vz in ((x, z), (x + 1, z), (x + 1, z + 1), (x, z + 1)):
                pos.append((vx - cx, 0.0, vz - cz))
                uvs.append((vx / UV_TILES, vz / UV_TILES))
            faces += [(k, k + 3, k + 2), (k, k + 2, k + 1)]
    if not faces:
        return None
    return mesh.from_faces(np.asarray(pos), faces, "water", uvs=np.asarray(uvs))


def _skirt_meshes(origin, size, centre, map_size) -> list[mesh.Mesh]:
    x0, z0 = origin
    cw, ch = size
    cx, cz = centre
    W, H = map_size
    lo, hi = (x0 - cx, z0 - cz), (x0 + cw - cx, z0 + ch - cz)
    d = -SKIRT_DEPTH
    sides = []
    if x0 == 0:
        sides.append(((lo[0], lo[1]), (lo[0], hi[1]), (-1, 0)))
    if x0 + cw == W:
        sides.append(((hi[0], lo[1]), (hi[0], hi[1]), (1, 0)))
    if z0 == 0:
        sides.append(((lo[0], lo[1]), (hi[0], lo[1]), (0, -1)))
    if z0 + ch == H:
        sides.append(((lo[0], hi[1]), (hi[0], hi[1]), (0, 1)))
    walls = []
    for (ax, az), (bx, bz), (ox, oz) in sides:
        quad = [(ax, 0.0, az), (bx, 0.0, bz), (bx, d, bz), (ax, d, az)]
        m = mesh.from_faces(quad, [(0, 1, 2, 3)], "earth")
        if float(m.normals[0] @ np.array([ox, 0, oz])) < 0:
            m = mesh.from_faces(quad[::-1], [(0, 1, 2, 3)], "earth")
        walls.append(m)
    return walls


def chunk(grid: Any, season: str = "summer", *, origin: tuple[int, int] = (0, 0),
          size: tuple[int, int] | None = None, seed: int = 0, skirt: bool = False,
          name: str = "chunk", water_level: float = WATER_LEVEL, smooth: bool = True) -> gltf.Scene:
    """One GLB scene of a size[0] x size[1] tile area of a map grid, merged per material.

    The origin is the centre of the area at y = 0; map x runs along +X and map
    y along +Z. Ground normals are smooth across tiles and chunks (`smooth=False`
    gives faceted low-poly shading instead). Nodes: `<name>` root, `ground`, `water` (with a `bob` clip
    except in winter, when it is ice), `skirt` (earth sides on map edges).
    """
    if season not in SEASONS:
        raise ValueError(f"season must be one of {SEASONS}")
    if name in ("ground", "water", "skirt"):
        raise ValueError(f"chunk name {name!r} would clash with its child nodes")
    g = normalize_grid(grid)
    H, W = len(g), len(g[0])
    x0, z0 = origin
    size = size or (min(CHUNK, W - x0), min(CHUNK, H - z0))
    cw, ch = size
    if x0 < 0 or z0 < 0 or x0 + cw > W or z0 + ch > H:
        raise ValueError(f"chunk {origin}+{size} is outside the {W}x{H} grid")
    centre = (x0 + cw / 2, z0 + ch / 2)
    heights = heightfield(g, origin, size, seed=seed)
    normals = ground_normals(g, origin, size, seed=seed) if smooth else None
    scene = gltf.Scene(name, extras={"tileable": True, "season": season, "origin": list(origin), "size": list(size),
                                     "subdiv": SUBDIV, "water_level": water_level})
    declare_materials(scene, season)
    root = scene.node(name)
    scene.node("ground", parent=root, meshes=_ground_meshes(g, origin, size, heights, centre, normals))
    wm = _water_mesh(g, origin, size, centre)
    if wm is not None:
        water = scene.node("water", parent=root, meshes=[wm], translation=(0, water_level, 0))
        if season != "winter":
            ts = [0, BOB_PERIOD / 4, BOB_PERIOD / 2, 3 * BOB_PERIOD / 4, BOB_PERIOD]
            ys = [water_level, water_level + BOB, water_level, water_level - BOB, water_level]
            scene.animation("bob").translate(water, ts, [(0, y, 0) for y in ys])
    if skirt:
        walls = _skirt_meshes(origin, size, centre, (W, H))
        if walls:
            scene.node("skirt", parent=root, meshes=walls)
    return scene


def tile(tile_type: str, season: str = "summer", *, seed: int = 0, at: tuple[int, int] = (0, 0)) -> gltf.Scene:
    """A lone 1x1 tile of one type at map position `at` (sets UV phase and relief); lone water floods its rim."""
    if tile_type not in TILE_TYPES:
        raise ValueError(f"unknown tile type {tile_type!r}; have {TILE_TYPES}")
    x, z = at
    grid = [[tile_type if (i, j) == (x, z) else "grass" for i in range(x + 1)] for j in range(z + 1)]
    level = TILE_WATER_LEVEL if tile_type == "water" else WATER_LEVEL
    s = chunk(grid, season, origin=(x, z), size=(1, 1), seed=seed, name=f"terrain-{tile_type}", water_level=level)
    s.extras["tile_type"] = tile_type
    return s


# ------------------------------------------------------------------ packages


def compose(parts: Sequence[tuple[gltf.Scene, Sequence[float]] | tuple[gltf.Scene, Sequence[float], float]],
            name: str = "scene") -> gltf.Scene:
    """Merge several scenes into one, each root moved by (x, y, z) and turned by an optional yaw.

    Node names get a `<k>_` prefix and clips are dropped — for dressed test
    scenes and previews, not for game packages (ship pieces separately).
    """
    out = gltf.Scene(name)
    mat_names: dict[int, str] = {}
    for k, part in enumerate(parts):
        scene, offset = part[0], part[1]
        yaw = part[2] if len(part) > 2 else 0.0
        rename: dict[str, str] = {}
        for mname, m in scene.materials.items():
            key = id(m)
            if key not in mat_names:
                new = mname if mname not in out.materials else f"{mname}_{k}"
                out.materials[new] = m
                mat_names[key] = new
            rename[mname] = mat_names[key]

        def copy(node: gltf.Node, parent: gltf.Node | None) -> None:
            meshes = [m.with_material(rename.get(m.material, m.material)) for m in node.meshes]
            if parent is None:
                q = mesh.quat_mul(mesh.quat_euler(y=yaw), node.rotation)
                r = mesh.quat_matrix(mesh.quat_euler(y=yaw))
                t = r @ np.asarray(node.translation) + np.asarray(offset, dtype=np.float64)
                nn = out.node(f"{k}_{node.name}", meshes=meshes, translation=t, rotation=q, scale=node.scale)
            else:
                nn = out.node(f"{k}_{node.name}", meshes=meshes, parent=parent, translation=node.translation,
                              rotation=node.rotation, scale=node.scale)
            for c in node.children:
                copy(c, nn)

        for r in scene.roots:
            copy(r, None)
    return out


def turntable_sheet(path: str | Path, out: str | Path, *, yaws: Sequence[float] = (45, 135, 225, 315),
                    pitch: float = 30.0, size: int = 320, floor: bool = True,
                    hidden: Callable[[float], Sequence[str]] | None = None,
                    background: tuple[int, int, int] = (238, 240, 244)) -> Image.Image:
    """`preview.turntable` with a `floor` switch and per-yaw hidden nodes (for backdrop rings)."""
    imgs = [preview.render(path, size=size, yaw=y, pitch=pitch, floor=floor, background=background,
                           hidden=hidden(y) if hidden else ()) for y in yaws]
    sheet = Image.new("RGB", (size * len(imgs), size + 18), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for k, (img, y) in enumerate(zip(imgs, yaws)):
        sheet.paste(img, (k * size, 18))
        draw.text((k * size + 4, 3), f"yaw {y:g}", fill=(40, 40, 40))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return sheet


def write_package(name: str, out_dir: str | Path, variants: Mapping[str, gltf.Scene], *, category: str,
                  seasons: Mapping[str, str] | None = None, footprint: Sequence[int] | None = None,
                  tileable: bool = False, floor: bool = True, pitch: float = 30.0, size: int = 384,
                  hidden: Callable[[float], Sequence[str]] | None = None, clip_frames: int = 6,
                  title: str = "", **fields: Any) -> dict:
    """Write an environment package: one GLB per variant, previews, manifest; return `validate_package`.

    `variants` maps a variant key (a season, or a time of day for skies) to
    its scene; the first is the main file `<name>.glb`, the others
    `<name>-<key>.glb`. `seasons` maps each season the asset is used in to a
    variant key (default: every season key present, summer standing in for
    autumn when there is no autumn variant). Previews: an iso render and a
    four-corner turntable per variant, plus a strip per clip of the main file.
    """
    pkg = Path(out_dir) / name
    (pkg / "previews").mkdir(parents=True, exist_ok=True)
    files, var_files = [], {}
    for k, (key, scene) in enumerate(variants.items()):
        fname = f"{name}.glb" if k == 0 else f"{name}-{key}.glb"
        p = scene.write(pkg / fname)
        files.append(fname)
        var_files[key] = fname
        stem = p.stem
        hid = hidden(45.0) if hidden else ()
        preview.render(p, pkg / "previews" / f"{stem}.png", size=size, pitch=pitch, floor=floor, hidden=hid)
        turntable_sheet(p, pkg / "previews" / f"{stem}_turntable.png", pitch=pitch, size=size // 2 + 64,
                        floor=floor, hidden=hidden)
        if k == 0:
            for clip in gltf.read(p).clip_names():
                preview.clip_strip(p, pkg / "previews" / f"{stem}_{clip}.png", clip, frames=clip_frames,
                                   pitch=pitch, size=200)
    if seasons is None:
        seasons = {s: s for s in SEASONS if s in variants}
        if "autumn" not in seasons and "summer" in variants:
            seasons["autumn"] = "summer"
        if not seasons:   # e.g. a sky whose variants are times of day: one set for every season
            seasons = {s: next(iter(variants)) for s in SEASONS}
    season_files = {s: var_files[v] for s, v in seasons.items()}
    extra = {"tileable": True} if tileable else {"footprint": list(footprint or [1, 1])}
    catalog.write_manifest(pkg, name=name, kind="environment", files=files, title=title, category=category,
                           season=[s for s in SEASONS if s in season_files], seasons=season_files,
                           variants=var_files, **extra, **fields)
    return catalog.validate_package(pkg)


def build_tile(tile_type: str, out_dir: str | Path, *, name: str | None = None, seed: int = 0,
               size: int = 320) -> dict:
    """Package `terrain-<type>`: a 1x1 tile in summer, autumn and winter (identical geometry)."""
    variants = {s: tile(tile_type, s, seed=seed) for s in SEASONS}
    return write_package(name or f"terrain-{tile_type}", out_dir, variants, category="terrain", tileable=True,
                         floor=False, pitch=35, size=size, tile_type=tile_type, subdiv=SUBDIV,
                         snow_blend=True, game_tile=next((k for k, v in GAME_TILES.items() if v == tile_type), None),
                         water_level=TILE_WATER_LEVEL if tile_type == "water" else None)


def sample_grid() -> list[list[str]]:
    """An 8x8 grid that shows every tile type and the seams between them (lake, trail, plot, forest)."""
    rows = ["ffgggwww",
            "fgggwwww",
            "ggtttwww",
            "gtttgggw",
            "ttddddgg",
            "tgddddgf",
            "tgddddff",
            "tggggfff"]
    key = {"g": "grass", "w": "water", "d": "dirt", "t": "trail", "f": "forest"}
    return [[key[c] for c in r] for r in rows]


def build_chunk(name: str, out_dir: str | Path, grid: Any | None = None, *, origin: tuple[int, int] = (0, 0),
                size: tuple[int, int] | None = None, seed: int = 0, skirt: bool = False, preview_size: int = 384) -> dict:
    """Package a terrain chunk (default: `sample_grid()`) in all three seasons."""
    grid = sample_grid() if grid is None else grid
    g = normalize_grid(grid)
    size = size or (min(CHUNK, len(g[0]) - origin[0]), min(CHUNK, len(g) - origin[1]))
    variants = {s: chunk(g, s, origin=origin, size=size, seed=seed, skirt=skirt, name=name) for s in SEASONS}
    return write_package(name, out_dir, variants, category="terrain", tileable=True, floor=False, pitch=35,
                         size=preview_size, origin=list(origin), size_tiles=list(size), subdiv=SUBDIV,
                         snow_blend=True, water_level=WATER_LEVEL)


def build_map(out_root: str | Path, seed: int, *, chunk_size: int = CHUNK, skirt: bool = False) -> dict[str, dict]:
    """Package the game map for a seed as chunks `terrain-map-<cx>-<cz>` (ragged last row/column)."""
    grid, _ = world_grid(seed)
    out = {}
    for z in range(0, MAP_H, chunk_size):
        for x in range(0, MAP_W, chunk_size):
            nm = f"terrain-map-{x // chunk_size}-{z // chunk_size}"
            sz = (min(chunk_size, MAP_W - x), min(chunk_size, MAP_H - z))
            out[nm] = build_chunk(nm, out_root, grid, origin=(x, z), size=sz, seed=seed, skirt=skirt, preview_size=256)
    return out


def build_all(out_root: str | Path) -> dict[str, dict]:
    """Every tile type package plus the sample chunk; returns {name: validate report}."""
    out = {f"terrain-{t}": build_tile(t, out_root) for t in TILE_TYPES}
    out["terrain-chunk-sample"] = build_chunk("terrain-chunk-sample", out_root)
    return out
