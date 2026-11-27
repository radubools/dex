"""Modular building blocks on the tile grid: walls, roofs, lattices, decks, gates — snap-together pieces.

Every block is a function returning a `Block`: meshes in the block's own frame
(origin = footprint centre at y=0, Y up, 1 unit = 1 tile, front faces +Z),
its bounding `size`, and named `sockets` — attachment points where other
blocks go (`top`, `bottom`, `+x`, `-x`, `+z`, `-z`, plus block-specific ones
like `left`/`right` on a wall). Stack by placing the next block at a socket:

    from utils import blocks
    base = blocks.plinth(2, 2, 0.2)
    body = blocks.window_wall(2, 1.0)
    parts = base.placed() + body.placed(*base.sockets["top"])

Meshes name **material roles** (`wall`, `roof`, `glass`, `steel`, ...), not
colours. `define_materials(scene, style)` declares every role in a style's
palette, so one landmark reads as one design and blocks are reusable across
landmarks. `placed()` re-projects world-space UVs (at each role's texel
density), so textures line up across the seams between blocks.

Moving parts (a crane jib, a lamp that blinks) are `Block.children`, each with
its pivot; `add_block()` turns a block into a node subtree with them as child
nodes the game can animate. `export_block()` writes any single block as its
own `kind="block"` package.
"""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from . import catalog, gltf, mesh, preview, texture
from .mesh import Mesh

Vec3 = tuple[float, float, float]
RoleFn = str | Callable[[float], str]

# ------------------------------------------------------------------ palettes

#: Every material role a block may name, with its default look. `kind` picks the
#: texture generator; `uv` is texture repeats per world unit (texel density).
ROLES: dict[str, dict] = {
    "wall": dict(kind="concrete", color="#d9d4c7", uv=0.5),
    "wall_alt": dict(kind="concrete", color="#b9b3a5", uv=0.5),
    "facade": dict(kind="windows", color="#c3c8d0", glass="#2c4660", cols=4, rows=4, frame=0.2, uv=0.5),
    "glass": dict(kind="windows", color="#6f8aa3", glass="#365a7a", cols=4, rows=4, frame=0.06, uv=0.5,
                  metallic=0.3, roughness=0.3),
    "window": dict(kind="solid", color="#2c4660", roughness=0.3, metallic=0.2),
    "window_lit": dict(kind="solid", color="#c9b07a", emissive="#ffd98a", roughness=0.3),
    "door": dict(kind="planks", color="#5a3a22", count=4, uv=2.0),
    "trim": dict(kind="solid", color="#f2efe8"),
    "stone": dict(kind="rock", color="#9b968c", uv=0.6),
    "plinth": dict(kind="tiles", color="#bdb6a6", count=4, uv=0.5),
    "roof": dict(kind="shingles", color="#3a3d44", rows=8, cols=8, uv=1.0),
    "roof_flat": dict(kind="concrete", color="#8d9096", uv=0.5),
    "roof_trim": dict(kind="solid", color="#2a2c31"),
    "wood": dict(kind="wood", color="#8a5a32", uv=1.0),
    "wood_dark": dict(kind="wood", color="#4a3020", uv=1.0),
    "column": dict(kind="solid", color="#b8322c"),
    "brick": dict(kind="brick", color="#a8503a", rows=16, cols=6, uv=0.5),
    "steel": dict(kind="metal", color="#9aa3ad", panels=2, metallic=0.4, roughness=0.5, uv=1.0),
    "steel_alt": dict(kind="metal", color="#f4f1ea", panels=2, metallic=0.3, roughness=0.5, uv=1.0),
    "frame": dict(kind="solid", color="#3d434b", metallic=0.5, roughness=0.4),
    "copper": dict(kind="metal", color="#5f8f80", panels=4, metallic=0.3, roughness=0.6, uv=1.0),
    "gold": dict(kind="solid", color="#c9a24a", metallic=0.8, roughness=0.35),
    "bronze": dict(kind="metal", color="#5e5440", panels=2, metallic=0.6, roughness=0.5),
    "accent": dict(kind="solid", color="#ff4fa3"),
    "sign": dict(kind="solid", color="#fbe7f1", emissive="#ff6fb5"),
    "screen": dict(kind="screen", color="#ffffff"),
    "lantern": dict(kind="solid", color="#c8302a", emissive="#3a0a06"),
    "lamp": dict(kind="solid", color="#fff1c9", emissive="#ffd98a"),
    "light": dict(kind="solid", color="#ff3b30", emissive="#ff2a1a"),
    "light_green": dict(kind="solid", color="#3bd46a", emissive="#20c050"),
    "ground": dict(kind="asphalt", uv=0.5),
    "paving": dict(kind="tiles", color="#b8b2a6", count=8, uv=0.5),
    "marking": dict(kind="solid", color="#f4f1ea"),
    "water": dict(kind="water", color="#3d6f8f", uv=0.5, roughness=0.15),
    "foliage": dict(kind="foliage", color="#2f6b3a", uv=1.0),
    "fabric": dict(kind="fabric", color="#a3242a", uv=2.0),
    "scaffold": dict(kind="metal", color="#c8ccd0", panels=1, metallic=0.5, roughness=0.5),
    "scaffold_plank": dict(kind="planks", color="#b48a4a", count=3, uv=2.0),
    "scaffold_net": dict(kind="solid", color="#3f8f5a", opacity=0.45, double_sided=True),
    "crane": dict(kind="metal", color="#f2b81e", panels=1, metallic=0.3, roughness=0.5),
}

#: Style palettes: role -> colour string or a dict overriding the role's spec.
STYLES: dict[str, dict[str, str | dict]] = {
    "default": {},
    "temple": {"wall": "#b33a2e", "wall_alt": "#efe8d8", "column": "#b8322c", "trim": "#e9e2cf",
               "roof": {"color": "#4a4e57"}, "roof_trim": "#25262b", "plinth": {"color": "#cfc6b0"},
               "stone": "#a39d90", "wood": "#7a4a2a", "accent": "#d9a521", "paving": {"color": "#c9c1ad"}},
    "shop": {"wall": "#c9a46b", "wall_alt": "#e8dcc0", "column": "#5a3a22", "roof": {"color": "#8e2b2b"},
             "roof_trim": "#3a2018", "wood": "#b07a45", "fabric": "#9b1f26", "trim": "#e8dcc0",
             "sign": {"color": "#f5ead2", "emissive": "#ffb35a"}, "plinth": {"color": "#8f8a80"}, "accent": "#c23b2a"},
    "brick": {"brick": "#b5533c", "trim": "#ece4d2", "wall": "#e6dcc8", "roof": {"color": "#4d5a60"},
              "roof_trim": "#38423f", "copper": "#5f7a70", "stone": "#b9b2a4", "column": "#ece4d2",
              "window": "#2a3a48", "plinth": {"color": "#9a958a"}},
    "steel": {"steel": {"kind": "solid", "color": "#ec5b2a"}, "steel_alt": {"kind": "solid", "color": "#f4f1ea"}, "glass": {"color": "#8fb0c8", "glass": "#3a6888"},
              "trim": "#f4f1ea", "wall": "#d8d4cc"},
    "skytree": {"steel": {"kind": "solid", "color": "#eef3f7"}, "steel_alt": {"kind": "solid", "color": "#d3dde6"}, "trim": "#eef3f7",
                "glass": {"color": "#a9bfd0", "glass": "#4a7090"}, "wall": "#d6dde3", "frame": "#7d8a96",
                "light": {"color": "#ff4a3a", "emissive": "#ff3020"}},
    "modern": {"wall": "#c3c8d0", "wall_alt": "#e9ecef", "facade": {"color": "#c3c8d0", "cols": 4, "rows": 6},
               "trim": "#f2f2f2", "accent": "#ff4fa3", "frame": "#8a9099",
               "glass": {"color": "#9fb0c0", "glass": "#40607a"}, "sign": {"color": "#ffe3f1", "emissive": "#ff4fa3"}},
    "granite": {"wall": "#868c94", "wall_alt": "#9aa1a9", "trim": "#b4b9bf", "frame": "#5a6068",
                "facade": {"color": "#9aa1a9", "glass": "#26384a", "cols": 12, "rows": 14, "frame": 0.3},
                "roof_flat": "#7d838b", "glass": {"color": "#6d8094", "glass": "#2b4258"}},
    "palace": {"wall": "#efeae0", "wall_alt": "#d9d2c2", "stone": "#8f8a80", "roof": {"color": "#4f6b62"},
               "roof_trim": "#3c5049", "copper": "#5d8577", "column": "#6b4a32", "trim": "#e8e2d4",
               "plinth": {"color": "#a8a294"}, "paving": {"color": "#cfc6ae"}},
    "street": {"wall": "#9fb0c0", "facade": {"color": "#aab6c2", "glass": "#2a4058", "cols": 4, "rows": 6},
               "trim": "#d5dbe1", "accent": "#ff4fa3", "kerb": "#b5b0a5"},
}

_TEX_CACHE: dict[str, tuple] = {}


def palette(style: str = "default") -> dict[str, dict]:
    """The full role -> spec table for a style (defaults overlaid with the style's overrides)."""
    if style not in STYLES:
        raise KeyError(f"unknown style {style!r}; have {sorted(STYLES)}")
    out = {k: dict(v) for k, v in ROLES.items()}
    for role, over in STYLES[style].items():
        if role not in out:
            out[role] = dict(ROLES["trim"])
        if isinstance(over, str):
            out[role]["color"] = over
        else:
            out[role].update(over)
    return out


def _textures(role: str, spec: dict, size: int) -> tuple:
    key = json.dumps([role, spec, size], sort_keys=True)
    if key in _TEX_CACHE:
        return _TEX_CACHE[key]
    k, c, seed = spec.get("kind", "solid"), spec.get("color", "#ffffff"), zlib.crc32(role.encode()) % 997
    emis = None
    if k == "windows":
        img, emis = texture.windows(size, cols=spec.get("cols", 4), rows=spec.get("rows", 4), wall=c,
                                    glass=spec.get("glass", "#2c4660"), frame=spec.get("frame", 0.18),
                                    lit=spec.get("lit", 0.35), seed=seed)
    elif k == "screen":
        n = texture.noise(size, 4, octaves=2, seed=seed)
        img = texture.to_image(texture.colorize(n, [(0.0, "#2a1a6a"), (0.35, "#ff4fa3"), (0.6, "#ffd34f"),
                                                     (0.8, "#4fd8ff"), (1.0, "#ffffff")]))
        emis = img
    elif k == "concrete":
        img = texture.concrete(size, c, seed=seed)
    elif k == "rock":
        img = texture.rock(size, c, seed=seed)
    elif k == "brick":
        img = texture.brick(size, rows=spec.get("rows", 8), cols=spec.get("cols", 4), color=c, seed=seed)
    elif k == "tiles":
        img = texture.tiles(size, count=spec.get("count", 8), color=c, seed=seed)
    elif k == "planks":
        img = texture.planks(size, count=spec.get("count", 6), color=c, seed=seed)
    elif k == "shingles":
        img = texture.shingles(size, rows=spec.get("rows", 8), cols=spec.get("cols", 8), color=c, seed=seed)
    elif k == "metal":
        img = texture.metal(size, c, panels=spec.get("panels", 4), seed=seed)
    elif k == "wood":
        img = texture.wood(size, c, seed=seed)
    elif k == "asphalt":
        img = texture.asphalt(size, seed=seed)
    elif k == "water":
        img = texture.water(size, c, seed=seed)
    elif k == "foliage":
        img = texture.foliage(size, c, seed=seed)
    elif k == "fabric":
        img = texture.fabric(128, c, seed=seed)
    else:
        img = texture.solid(c, 64, seed=seed)
    _TEX_CACHE[key] = (img, emis)
    return img, emis


def define_materials(scene: gltf.Scene, style: str = "default", *, size: int = 256) -> dict[str, dict]:
    """Declare every material role of `style` on `scene`; returns the palette used."""
    pal = palette(style)
    for role, spec in pal.items():
        img, emis = _textures(role, spec, size)
        kw = dict(texture=img, roughness=spec.get("roughness", 0.85), metallic=spec.get("metallic", 0.0),
                  double_sided=spec.get("double_sided", False), role=role, style=style)
        if spec.get("opacity", 1.0) < 1:
            kw.update(texture=None, color=spec["color"], opacity=spec["opacity"])
        if emis is not None:
            kw["emissive_texture"] = emis
        elif spec.get("emissive"):
            kw["emissive"] = spec["emissive"]
        scene.material(role, **kw)
    return pal


def uv_density(role: str) -> float:
    """Texture repeats per world unit for a role (what `placed()` re-projects UVs with)."""
    return float(ROLES.get(role, {}).get("uv", 1.0))


# ------------------------------------------------------------------ the Block type


@dataclass
class Block:
    """A reusable piece: meshes in its own frame, bounding size (w, h, d), sockets, moving children."""

    name: str
    meshes: list[Mesh]
    size: Vec3
    sockets: dict[str, Vec3] = field(default_factory=dict)
    #: Moving sub-parts: name -> (block, pivot in this block's frame). Become child nodes.
    children: dict[str, tuple["Block", Vec3]] = field(default_factory=dict)

    @property
    def triangles(self) -> int:
        """Triangle count, children included."""
        return sum(m.triangles for m in self.meshes) + sum(b.triangles for b, _ in self.children.values())

    def placed(self, x: float = 0.0, y: float = 0.0, z: float = 0.0, yaw: float = 0.0, *,
               children: bool = True, world_uv: bool = True) -> list[Mesh]:
        """Meshes moved to (x, y, z), turned `yaw` degrees about Y; children merged in at their pivots."""
        out = []
        parts = [(m, (0.0, 0.0, 0.0)) for m in self.meshes]
        if children:
            for b, piv in self.children.values():
                parts += [(m, piv) for m in b.placed(world_uv=False)]
        for m, piv in parts:
            mm = m.translate(*piv)
            if yaw:
                mm = mm.rotate_y(yaw)
            mm = mm.translate(x, y, z)
            if world_uv and mm.material != "screen":
                uvs = mm.reproject_uvs(uv_density(mm.material)).uvs
                mm.uvs = uvs
            out.append(mm)
        return out

    def socket(self, name: str, x: float = 0.0, y: float = 0.0, z: float = 0.0, yaw: float = 0.0) -> Vec3:
        """World position of socket `name` when the block is placed at (x, y, z, yaw)."""
        sx, sy, sz = self.sockets[name]
        a = math.radians(yaw)
        c, s = math.cos(a), math.sin(a)
        return (x + c * sx + s * sz, y + sy, z - s * sx + c * sz)


def _box_sockets(w: float, h: float, d: float, **extra: Vec3) -> dict[str, Vec3]:
    s = {"bottom": (0.0, 0.0, 0.0), "top": (0.0, h, 0.0), "+x": (w / 2, 0.0, 0.0), "-x": (-w / 2, 0.0, 0.0),
         "+z": (0.0, 0.0, d / 2), "-z": (0.0, 0.0, -d / 2)}
    s.update({k: tuple(map(float, v)) for k, v in extra.items()})
    return s


def _block(name: str, meshes: Sequence[Mesh], **extra_sockets: Vec3) -> Block:
    meshes = [m for m in meshes if m.triangles]
    lo = np.min([m.bounds()[0] for m in meshes], axis=0)
    hi = np.max([m.bounds()[1] for m in meshes], axis=0)
    w = float(2 * max(abs(lo[0]), abs(hi[0])))
    d = float(2 * max(abs(lo[2]), abs(hi[2])))
    h = float(hi[1])
    return Block(name, list(meshes), (round(w, 4), round(h, 4), round(d, 4)), _box_sockets(w, h, d, **extra_sockets))


def _role(mat: RoleFn, y: float) -> str:
    return mat(y) if callable(mat) else mat


def _closed(pos: Sequence[Sequence[float]], faces, material: str) -> Mesh:
    """`from_faces`, flipped if the faces came out wound inward (negative volume)."""
    m = mesh.from_faces(pos, faces, material)
    t = m.positions[m.indices].astype(np.float64)
    if np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() < 0:
        m = mesh.from_faces(pos, [list(f)[::-1] for f in faces], material)
    return m


def _bar(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, material: str) -> Mesh:
    """An axis-aligned box between two corners."""
    return mesh.box(abs(x1 - x0), abs(y1 - y0), abs(z1 - z0), material).translate(
        (x0 + x1) / 2, min(y0, y1), (z0 + z1) / 2)


def _profile_xy(points: Sequence[tuple[float, float]], depth: float, material: str) -> Mesh:
    """Extrude an elevation polygon of (x, y) points `depth` along Z, centred on z=0."""
    return mesh.extrude(points, depth, material).rotate_x(-90).translate(0, 0, depth / 2)


# ------------------------------------------------------------------ bases and floors


def slab(w: float = 1.0, d: float = 1.0, h: float = 0.08, *, mat: str = "plinth",
         edge: str | None = None) -> Block:
    """A floor slab or flat plinth w x d, h thick; `edge` adds a trim band round its top."""
    parts = [mesh.box(w, h, d, mat)]
    if edge:
        parts.append(mesh.box(w + 0.02, 0.02, d + 0.02, edge).translate(0, h - 0.02, 0))
    return _block("slab", parts)


def plinth(w: float = 1.0, d: float = 1.0, h: float = 0.15, *, steps: int = 2, inset: float = 0.05,
           mat: str = "stone", top: str | None = None) -> Block:
    """A stepped stone base: `steps` tiers each inset by `inset`; `top` is the top tier's material."""
    parts, sh = [], h / steps
    for i in range(steps):
        role = top if (top and i == steps - 1) else mat
        parts.append(mesh.box(w - 2 * inset * i, sh, d - 2 * inset * i, role).translate(0, sh * i, 0))
    return _block("plinth", parts)


def battered_base(w: float = 2.0, d: float = 2.0, h: float = 0.3, *, batter: float = 0.12,
                  mat: str = "stone", top: str | None = "paving") -> Block:
    """A sloping castle-style stone wall base (ishigaki): footprint w x d, sides leaning in by `batter`."""
    parts = [mesh.frustum(w, d, w - 2 * batter, d - 2 * batter, h, mat)]
    if top:
        parts.append(mesh.box(w - 2 * batter - 0.02, 0.02, d - 2 * batter - 0.02, top).translate(0, h, 0))
    return _block("battered_base", parts)


def stairs(width: float = 0.5, height: float = 0.2, depth: float = 0.4, *, steps: int | None = None,
           mat: str = "stone", cheeks: str | None = None) -> Block:
    """A flight of steps rising from the front (+Z, socket `bottom`) to the back (-Z, socket `top`)."""
    n = steps or max(2, round(height / 0.04))
    rh, rd = height / n, depth / n
    parts = []
    for i in range(n):
        z1 = depth / 2 - i * rd
        parts.append(_bar(-width / 2, width / 2, 0, rh * (i + 1), z1 - rd, z1, mat))
    if cheeks:
        prof = [(-depth / 2, 0.0), (depth / 2, 0.0), (depth / 2, height + 0.03), (-depth / 2, 0.03)]
        for sx in (-1, 1):
            parts.append(_profile_xy(prof, 0.03, cheeks).rotate_y(90).translate(sx * (width / 2 + 0.015), 0, 0))
    b = _block("stairs", parts)
    b.sockets.update(top=(0.0, height, -depth / 2), bottom=(0.0, 0.0, depth / 2))
    return b


# ------------------------------------------------------------------ walls


def wall(length: float = 1.0, height: float = 0.5, thickness: float = 0.08, *, mat: str = "wall") -> Block:
    """A plain wall segment along X (outer face +Z); sockets `left`/`right` at its ends for chaining."""
    b = _block("wall", [mesh.box(length, height, thickness, mat)])
    b.sockets.update(left=(-length / 2, 0.0, 0.0), right=(length / 2, 0.0, 0.0))
    return b


def _windows(length, height, thickness, cols, rows, win_w, win_h, frame, lit, seed, both, y0=0.0):
    rng = np.random.default_rng(abs(int(seed)))
    parts = []
    cw, rh = length / cols, height / rows
    pw, ph = cw * win_w, rh * win_h
    for r in range(rows):
        cy = y0 + rh * (r + 0.5) + rh * 0.05
        for c in range(cols):
            cx = -length / 2 + cw * (c + 0.5)
            for side in ((1, -1) if both else (1,)):
                role = "window_lit" if rng.random() < lit else "window"
                z = side * (thickness / 2 + 0.006)
                parts.append(mesh.box(pw, ph, 0.012, role).translate(cx, cy - ph / 2, z))
                if frame:
                    zf = side * (thickness / 2 + 0.012)
                    parts.append(mesh.box(pw + 0.03, 0.018, 0.03, frame).translate(cx, cy - ph / 2 - 0.018, zf))
                    parts.append(mesh.box(pw + 0.02, 0.014, 0.02, frame).translate(cx, cy + ph / 2, zf - side * 0.004))
    return parts


def window_wall(length: float = 1.0, height: float = 0.5, thickness: float = 0.08, *, cols: int | None = None,
                rows: int = 1, win_w: float = 0.55, win_h: float = 0.55, mat: str = "wall",
                frame: str | None = "trim", lit: float = 0.3, seed: int = 0, both: bool = False) -> Block:
    """A wall with `cols` x `rows` framed window panes on its +Z face (and -Z with `both`)."""
    cols = cols or max(1, round(length / 0.25))
    parts = [mesh.box(length, height, thickness, mat)]
    parts += _windows(length, height, thickness, cols, rows, win_w, win_h, frame, lit, seed, both)
    b = _block("window_wall", parts)
    b.sockets.update(left=(-length / 2, 0.0, 0.0), right=(length / 2, 0.0, 0.0))
    return b


def door_wall(length: float = 1.0, height: float = 0.5, thickness: float = 0.08, *, door_w: float = 0.3,
              door_h: float = 0.32, mat: str = "wall", door: str = "door", frame: str = "trim") -> Block:
    """A wall with a recessed doorway centred on its +Z face."""
    x = door_w / 2
    parts = [_bar(-length / 2, -x, 0, height, -thickness / 2, thickness / 2, mat),
             _bar(x, length / 2, 0, height, -thickness / 2, thickness / 2, mat),
             _bar(-x, x, door_h, height, -thickness / 2, thickness / 2, mat),
             _bar(-x, x, 0, door_h, -thickness / 2, 0, door),
             _bar(-x - 0.03, -x, 0, door_h + 0.03, thickness / 2 - 0.01, thickness / 2 + 0.02, frame),
             _bar(x, x + 0.03, 0, door_h + 0.03, thickness / 2 - 0.01, thickness / 2 + 0.02, frame),
             _bar(-x - 0.03, x + 0.03, door_h, door_h + 0.03, thickness / 2 - 0.01, thickness / 2 + 0.02, frame)]
    b = _block("door_wall", parts)
    b.sockets.update(left=(-length / 2, 0.0, 0.0), right=(length / 2, 0.0, 0.0), door=(0.0, 0.0, thickness / 2))
    return b


def facade_box(w: float = 1.0, h: float = 1.0, d: float = 1.0, *, mat: str = "facade", cap: str | None = "trim",
               base: str | None = None, base_h: float = 0.0) -> Block:
    """A building mass whose faces carry the windows texture (cheap facade); optional cap and base band."""
    parts = [mesh.box(w, h - base_h, d, mat).translate(0, base_h, 0)]
    if base and base_h:
        parts.append(mesh.box(w, base_h, d, base))
    if cap:
        parts.append(mesh.box(w + 0.03, 0.03, d + 0.03, cap).translate(0, h - 0.03, 0))
    return _block("facade_box", parts)


def curtain_wall(w: float = 1.0, h: float = 1.0, d: float = 1.0, *, mullion: float = 0.2, floor: float = 0.25,
                 mat: str = "glass", frame: str = "frame", fin: float = 0.02) -> Block:
    """A glass curtain-wall box with vertical mullion fins and floor bands on all four faces."""
    parts = [mesh.box(w, h, d, mat)]
    for side_len, axis in ((w, "x"), (d, "z")):
        n = max(1, round(side_len / mullion))
        for i in range(n + 1):
            t = -side_len / 2 + side_len * i / n
            for s in (-1, 1):
                if axis == "x":
                    parts.append(mesh.box(fin, h, fin, frame).translate(t, 0, s * (d / 2 + fin / 2)))
                else:
                    parts.append(mesh.box(fin, h, fin, frame).translate(s * (w / 2 + fin / 2), 0, t))
    nf = max(1, round(h / floor))
    for k in range(1, nf):
        y = h * k / nf
        parts.append(mesh.box(w + 2 * fin, 0.015, d + 2 * fin, frame).translate(0, y - 0.0075, 0))
    parts.append(mesh.box(w + 2 * fin + 0.01, 0.03, d + 2 * fin + 0.01, frame).translate(0, h - 0.03, 0))
    return _block("curtain_wall", parts)


# ------------------------------------------------------------------ structure


def pillar(height: float = 0.5, size: float = 0.08, *, round_: bool = False, segments: int = 8,
           mat: str = "column", base: str | None = "stone", cap: str | None = None) -> Block:
    """A corner pillar or column (square, or round with `round_`), with optional base and capital."""
    shaft = (mesh.cylinder(size / 2, height, mat, segments=segments) if round_
             else mesh.box(size, height, size, mat))
    parts = [shaft]
    if base:
        parts.append(mesh.box(size * 1.5, 0.04, size * 1.5, base))
    if cap:
        parts.append(mesh.box(size * 1.4, 0.035, size * 1.4, cap).translate(0, height - 0.035, 0))
    return _block("pillar", parts)


def colonnade(length: float = 1.0, height: float = 0.4, count: int = 5, *, size: float = 0.06,
              round_: bool = True, mat: str = "column", beam: str | None = "column", beam_h: float = 0.05,
              base: str | None = "stone") -> Block:
    """A row of `count` columns along X with an architrave beam on top."""
    parts = []
    for i in range(count):
        x = -length / 2 + size / 2 + (length - size) * i / max(1, count - 1)
        parts += pillar(height - (beam_h if beam else 0), size, round_=round_, mat=mat, base=base).placed(
            x, 0, 0, world_uv=False)
    if beam:
        parts.append(mesh.box(length + 0.02, beam_h, size * 1.3, beam).translate(0, height - beam_h, 0))
    return _block("colonnade", parts)


def beam_block(p0: Sequence[float] = (0, 0, 0), p1: Sequence[float] = (1, 0, 0), thickness: float = 0.04, *,
               mat: str = "steel") -> Block:
    """One straight beam or lattice member from p0 to p1; sockets `start` and `end`."""
    b = _block("beam", [mesh.beam(p0, p1, thickness, mat)])
    b.sockets.update(start=tuple(map(float, p0)), end=tuple(map(float, p1)))
    return b


def truss(length: float = 1.0, height: float = 0.2, *, panels: int = 4, thickness: float = 0.025,
          mat: str = "steel") -> Block:
    """A flat Warren truss in the XY plane: top and bottom chords with alternating diagonals."""
    t = thickness
    y0, y1 = t / 2, height - t / 2
    parts = [mesh.beam((-length / 2, y0, 0), (length / 2, y0, 0), t, mat),
             mesh.beam((-length / 2, y1, 0), (length / 2, y1, 0), t, mat)]
    for i in range(panels + 1):
        x = -length / 2 + length * i / panels
        parts.append(mesh.beam((x, y0, 0), (x, y1, 0), t * 0.8, mat))
        if i < panels:
            xn = -length / 2 + length * (i + 1) / panels
            a, b = ((x, y0, 0), (xn, y1, 0)) if i % 2 == 0 else ((x, y1, 0), (xn, y0, 0))
            parts.append(mesh.beam(a, b, t * 0.7, mat))
    blk = _block("truss", parts)
    blk.sockets.update(left=(-length / 2, 0.0, 0.0), right=(length / 2, 0.0, 0.0))
    return blk


def _square_ring(size: float, y: float) -> list[tuple[float, float, float]]:
    s = size / 2
    return [(-s, y, -s), (s, y, -s), (s, y, s), (-s, y, s)]


def lattice_tower(levels: Sequence[tuple[float, float]] = ((0, 1.0), (1, 0.6), (2, 0.3)), *, sides: int = 4,
                  shape: Callable[[float, float, int], list] | None = None, leg: float = 0.05,
                  brace: float = 0.025, mat: RoleFn = "steel", rings: bool = True, cross: bool = True,
                  sub: int = 1) -> Block:
    """A lattice tower through `levels` [(y, width)]: legs, horizontal rings and X-bracing per panel.

    `shape(y, width, sides)` returns the ring of (x, y, z) leg points at a level
    (default: a square). Pass levels that follow a curve for curved legs, and a
    shape that morphs (triangle to circle) for a twisting tube. `mat` may be a
    function of height, which is how red/white bands are painted. `sub` splits
    each panel's bracing into that many X's.
    """
    shape = shape or (lambda y, w, n: _square_ring(w, y))
    rings_pts = [np.asarray(shape(y, w, sides), dtype=np.float64) for y, w in levels]
    parts = []
    for k in range(len(rings_pts) - 1):
        a, b = rings_pts[k], rings_pts[k + 1]
        ym = (levels[k][0] + levels[k + 1][0]) / 2
        role = _role(mat, ym)
        n = len(a)
        for i in range(n):
            j = (i + 1) % n
            parts.append(mesh.beam(a[i], b[i], leg, role))
            if cross:
                for s in range(sub):
                    f0, f1 = s / sub, (s + 1) / sub
                    pa, pb = a[i] + (b[i] - a[i]) * f0, a[i] + (b[i] - a[i]) * f1
                    qa, qb = a[j] + (b[j] - a[j]) * f0, a[j] + (b[j] - a[j]) * f1
                    parts.append(mesh.beam(pa, qb, brace, role))
                    parts.append(mesh.beam(qa, pb, brace, role))
            if rings and k > 0:
                parts.append(mesh.beam(a[i], a[j], brace, role))
    top = rings_pts[-1]
    if rings:
        for i in range(len(top)):
            parts.append(mesh.beam(top[i], top[(i + 1) % len(top)], brace, _role(mat, levels[-1][0])))
    blk = _block("lattice_tower", parts)
    blk.sockets["top"] = (0.0, float(levels[-1][0]), 0.0)
    return blk


def lattice_segment(w0: float = 1.0, w1: float = 0.6, h: float = 1.0, *, panels: int = 3, **kw) -> Block:
    """A straight tapering four-legged lattice section, base w0 to top w1 over h; stacks on `top`."""
    levels = [(h * i / panels, w0 + (w1 - w0) * i / panels) for i in range(panels + 1)]
    return lattice_tower(levels, **kw)


def spire(height: float = 1.0, radius: float = 0.05, *, sections: int = 3, mat: RoleFn = "steel",
          ring: str | None = "steel_alt", segments: int = 8, tip: str | None = None) -> Block:
    """An antenna/spire: stacked cylinders narrowing upward with ring collars, and an optional tip light."""
    parts, y, r = [], 0.0, radius
    hs = [height * w for w in np.linspace(1.3, 0.7, sections) / sections]
    for i, sh in enumerate(hs):
        parts.append(mesh.cylinder(r, sh, _role(mat, y + sh / 2), segments=segments, radius_top=r * 0.82).translate(0, y, 0))
        if ring and i:
            parts.append(mesh.cylinder(r * 1.5, 0.03, ring, segments=segments).translate(0, y - 0.015, 0))
        y += sh
        r *= 0.62
    if tip:
        parts.append(mesh.sphere(max(r * 1.6, 0.012), tip, segments=6, rings=4).translate(0, y, 0))
    return _block("spire", parts)


def deck_ring(radius: float = 0.4, height: float = 0.25, *, sides: int = 16, levels: int = 1,
              glass: str = "glass", trim: str = "steel_alt", flare: float = 0.25) -> Block:
    """A round observation deck: flared underside, `levels` glass bands with trim rings, capped top."""
    r0 = radius * (1 - flare)
    hb = height * 0.2
    parts = [mesh.cylinder(r0, hb, trim, segments=sides, radius_top=radius)]
    band = (height - hb * 1.6) / levels
    y = hb
    for i in range(levels):
        parts.append(mesh.cylinder(radius * 0.97, band, glass, segments=sides).translate(0, y, 0))
        parts.append(mesh.cylinder(radius * 1.01, 0.02, trim, segments=sides).translate(0, y + band - 0.01, 0))
        y += band
    parts.append(mesh.cylinder(radius, hb * 0.6, trim, segments=sides, radius_top=r0).translate(0, y, 0))
    return _block("deck_ring", parts)


def deck_box(w: float = 1.0, h: float = 0.25, d: float = 1.0, *, floors: int = 2, glass: str = "glass",
             trim: str = "steel_alt") -> Block:
    """A square observation deck: glazed floors between trim slabs, slightly wider roof slab."""
    parts, fh = [], h / floors
    for i in range(floors):
        parts.append(mesh.box(w * 0.96, fh - 0.02, d * 0.96, glass).translate(0, fh * i + 0.02, 0))
        parts.append(mesh.box(w, 0.02, d, trim).translate(0, fh * i, 0))
    parts.append(mesh.box(w + 0.04, 0.03, d + 0.04, trim).translate(0, h - 0.01, 0))
    return _block("deck_box", parts)


# ------------------------------------------------------------------ roofs


def roof_flat(w: float = 1.0, d: float = 1.0, *, thickness: float = 0.04, parapet: float = 0.05,
              mat: str = "roof_flat", trim: str = "trim") -> Block:
    """A flat roof slab with a parapet ring."""
    t = 0.025
    parts = [mesh.box(w, thickness, d, mat)]
    if parapet:
        y = thickness
        parts += [_bar(-w / 2, w / 2, y, y + parapet, -d / 2, -d / 2 + t, trim),
                  _bar(-w / 2, w / 2, y, y + parapet, d / 2 - t, d / 2, trim),
                  _bar(-w / 2, -w / 2 + t, y, y + parapet, -d / 2 + t, d / 2 - t, trim),
                  _bar(w / 2 - t, w / 2, y, y + parapet, -d / 2 + t, d / 2 - t, trim)]
    return _block("roof_flat", parts)


def roof_gable(w: float = 1.0, d: float = 1.0, h: float = 0.35, *, overhang: float = 0.06, mat: str = "roof",
               trim: str | None = "roof_trim") -> Block:
    """A gable roof, ridge along X, with a ridge cap."""
    parts = [mesh.gable_roof(w, d, h, mat, overhang=overhang)]
    if trim:
        parts.append(mesh.box(w + 2 * overhang + 0.02, 0.035, 0.05, trim).translate(0, h - 0.02, 0))
    return _block("roof_gable", parts)


def roof_hip(w: float = 1.0, d: float = 1.0, h: float = 0.3, *, overhang: float = 0.06, mat: str = "roof",
             trim: str | None = "roof_trim") -> Block:
    """A hip roof, four slopes and a ridge along X, with a ridge cap."""
    parts = [mesh.hip_roof(w, d, h, mat, overhang=overhang)]
    ridge = max(0.0, w - d)
    if trim and ridge > 0.02:
        parts.append(mesh.box(ridge + 0.04, 0.03, 0.04, trim).translate(0, h - 0.015, 0))
    return _block("roof_hip", parts)


def roof_pyramid(w: float = 1.0, d: float = 1.0, h: float = 0.4, *, overhang: float = 0.04,
                 mat: str = "roof", finial: str | None = "gold") -> Block:
    """A pyramid roof (square towers, lantern caps) with an optional finial."""
    parts = [mesh.pyramid(w + 2 * overhang, d + 2 * overhang, h, mat)]
    if finial:
        parts.append(mesh.cone(0.02, 0.1, finial, segments=6).translate(0, h - 0.01, 0))
    return _block("roof_pyramid", parts)


def roof_mansard(w: float = 1.0, d: float = 0.6, h: float = 0.3, *, inset: float = 0.1, mat: str = "roof",
                 top: str = "roof_trim", dormers: int = 0, dormer_mat: str = "trim") -> Block:
    """A mansard roof: steep lower slopes, low hip on top, optional dormers along both long sides."""
    h0 = h * 0.7
    parts = [mesh.frustum(w, d, w - 2 * inset, d - 2 * inset, h0, mat),
             mesh.hip_roof(w - 2 * inset, d - 2 * inset, h - h0, top).translate(0, h0, 0)]
    for i in range(dormers):
        x = -w / 2 + inset + (w - 2 * inset) * (i + 0.5) / dormers
        for s in (-1, 1):
            z = s * (d / 2 - inset * 0.55)
            dw = min(0.12, (w - 2 * inset) / dormers * 0.5)
            parts.append(mesh.box(dw, h0 * 0.5, inset * 0.8, dormer_mat).translate(x, h0 * 0.3, z))
            parts.append(mesh.box(dw * 0.6, h0 * 0.3, 0.01, "window").translate(x, h0 * 0.36, z + s * inset * 0.4))
            parts.append(mesh.gable_roof(inset * 0.8, dw, h0 * 0.18, mat, overhang=0.01).rotate_y(90)
                         .translate(x, h0 * 0.8, z))
    return _block("roof_mansard", parts)


def _eave_ring(hx: float, hz: float, y: float, n: int, lift: float, kick: float) -> list[tuple[float, float, float]]:
    corners = [(-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz)]
    pts = []
    for c in range(4):
        (x0, z0), (x1, z1) = corners[c], corners[(c + 1) % 4]
        for i in range(n):
            s = i / n
            x, z = x0 + (x1 - x0) * s, z0 + (z1 - z0) * s
            f = abs(2 * s - 1) ** 3  # 1 at corners, 0 mid-side
            r = math.hypot(x, z) or 1.0
            pts.append((x + kick * f * x / r, y + lift * f, z + kick * f * z / r))
    return pts


def roof_curved(w: float = 1.0, d: float = 1.0, h: float = 0.4, *, overhang: float = 0.2, lift: float = 0.1,
                thickness: float = 0.04, gable: bool = False, steps: int = 5, segments: int = 4,
                mat: str = "roof", trim: str | None = "roof_trim", ornaments: str | None = None) -> Block:
    """An East Asian roof: concave slopes, eaves sweeping up at the corners, thick eave edge, ridge cap.

    `gable=True` makes it irimoya (hip below, gable on top), the temple and
    palace roof; otherwise a plain curved hip (yosemune). `ornaments` names a
    material for ridge-end ornaments.
    """
    x, z = w / 2 + overhang, d / 2 + overhang
    r = max(0.0, w - d) / 2
    t_end = 0.62 if gable else 1.0
    rings = []
    for s in range(steps + 1):
        t = t_end * s / steps
        k = t
        hx, hz = x + (r - x) * k, z * (1 - k)
        if gable:
            hx = x + (x * 0.55 - x) * k
        y = thickness + h * t ** 1.6
        f = (1 - t) ** 2
        rings.append(_eave_ring(hx, hz, y, segments, lift * f, lift * 0.8 * f))
    n = 4 * segments
    base = [(px_, py - thickness, pz) for px_, py, pz in rings[0]]
    pos = base + [p for ring in rings for p in ring]
    faces = []
    for i in range(n):  # eave fascia
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    for s in range(steps):
        a, b = n * (s + 1), n * (s + 2)
        for i in range(n):
            j = (i + 1) % n
            faces.append((a + i, a + j, b + j, b + i))
    c0 = len(pos)
    pos.append((0.0, 0.0, 0.0))
    for i in range(n):
        faces.append((c0, (i + 1) % n, i))
    top_ring0 = n * (steps + 1)
    if gable:
        pos.append((0.0, rings[-1][0][1], 0.0))
        c1 = len(pos) - 1
        for i in range(n):
            faces.append((c1, top_ring0 + i, top_ring0 + (i + 1) % n))
    parts = [_closed(pos, faces, mat)]
    ytop = thickness + h
    if gable:
        gx = abs(rings[-1][0][0]) * 0.92
        gz = abs(rings[-1][0][2]) * 0.98
        y0 = rings[-1][segments // 2][1]
        gh = (ytop - y0) * 1.25
        parts.append(mesh.gable_roof(2 * gx, 2 * gz, gh, mat, overhang=0.02).translate(0, y0 - 0.005, 0))
        tri = [(-gz * 0.92, y0), (gz * 0.92, y0), (0.0, y0 + gh * 0.88)]
        for sx in (-1, 1):  # gable-end boards (tsumakazari)
            parts.append(_profile_xy(tri, 0.012, trim or mat).rotate_y(90).translate(sx * (gx * 0.98), 0, 0))
        ridge_len, ytop = 2 * gx + 0.06, y0 + gh
    else:
        ridge_len = 2 * r + 0.04
    if trim and ridge_len > 0.05:
        rh = max(0.03, h * 0.08)
        parts.append(mesh.box(ridge_len, rh, rh * 1.2, trim).translate(0, ytop - rh * 0.4, 0))
        if ornaments:
            for sx in (-1, 1):
                parts.append(mesh.box(rh * 0.9, rh * 2.2, rh * 1.4, ornaments).translate(sx * ridge_len / 2, ytop - rh * 0.4, 0))
    return _block("roof_curved", parts)


def roof_dome(radius: float = 0.3, height: float = 0.35, *, sides: int = 8, mat: str = "roof",
              drum: float = 0.0, drum_mat: str = "trim", finial: str | None = "gold") -> Block:
    """A faceted dome (octagonal by default) on an optional drum, with a finial."""
    prof = [(0.0, 0.0), (radius, 0.0), (radius * 0.98, 0.22 * height), (radius * 0.86, 0.5 * height),
            (radius * 0.6, 0.76 * height), (radius * 0.28, 0.93 * height), (0.0, height)]
    parts = []
    if drum:
        parts.append(mesh.cylinder(radius * 1.04, drum, drum_mat, segments=sides))
        parts.append(mesh.cylinder(radius * 1.1, 0.025, drum_mat, segments=sides).translate(0, drum - 0.025, 0))
    parts.append(mesh.lathe(prof, mat, segments=sides, smooth=False).translate(0, drum, 0))
    if finial:
        parts.append(mesh.cylinder(0.025, 0.06, finial, segments=6).translate(0, drum + height - 0.01, 0))
        parts.append(mesh.cone(0.018, 0.1, finial, segments=6).translate(0, drum + height + 0.05, 0))
    return _block("roof_dome", parts)


def pagoda_tier(w: float = 0.5, wall_h: float = 0.25, *, roof_h: float | None = None, overhang: float | None = None,
                lift: float = 0.06, wall: str = "column", roof: str = "roof", trim: str = "roof_trim",
                bracket: str = "wood_dark", railing_mat: str | None = None) -> Block:
    """One pagoda storey: walls with corner posts, a bracket band, a wide curved roof; `top` is where the next sits."""
    roof_h = roof_h if roof_h is not None else w * 0.22
    overhang = overhang if overhang is not None else w * 0.35
    parts = [mesh.box(w * 0.86, wall_h, w * 0.86, wall)]
    for sx in (-1, 1):
        for sz in (-1, 1):
            parts.append(mesh.box(0.03, wall_h, 0.03, bracket).translate(sx * w * 0.43, 0, sz * w * 0.43))
    parts.append(mesh.box(w * 1.02, 0.04, w * 1.02, bracket).translate(0, wall_h - 0.02, 0))
    parts += roof_curved(w, w, roof_h, overhang=overhang, lift=lift, thickness=0.03, mat=roof, trim=None,
                         steps=4, segments=4).placed(0, wall_h + 0.02, 0, world_uv=False)
    if railing_mat:
        parts += railing_ring(w * 1.04, w * 1.04, 0.05, mat=railing_mat).placed(0, 0, 0, world_uv=False)
    blk = _block("pagoda_tier", parts)
    blk.sockets["top"] = (0.0, wall_h + 0.02 + roof_h * 0.55, 0.0)
    return blk


# ------------------------------------------------------------------ gates, rails, signs, lights


def torii(width: float = 0.6, height: float = 0.5, *, post: float = 0.045, mat: str = "column",
          top: str = "roof_trim") -> Block:
    """A torii gate across X: two posts, tie beam (nuki), upswept black lintel (kasagi)."""
    half = width / 2 - post
    parts = []
    for sx in (-1, 1):
        parts.append(mesh.cylinder(post / 2 * 1.1, height * 0.92, mat, segments=8, radius_top=post / 2 * 0.9)
                     .translate(sx * half, 0, 0))
        parts.append(mesh.cylinder(post * 0.7, 0.04, top, segments=8).translate(sx * half, 0, 0))
    parts.append(mesh.box(width * 0.95, post * 0.7, post * 0.6, mat).translate(0, height * 0.7, 0))
    parts.append(mesh.box(post * 0.8, height * 0.12, post * 0.5, mat).translate(0, height * 0.74, 0))
    L, t = width * 1.18 / 2, post * 0.8
    y0 = height * 0.86
    up = height * 0.07
    kas = [(-L, y0 + up), (-L * 0.75, y0 + up * 0.25), (0, y0), (L * 0.75, y0 + up * 0.25), (L, y0 + up),
           (L, y0 + up + t), (L * 0.75, y0 + up * 0.25 + t), (0, y0 + t), (-L * 0.75, y0 + up * 0.25 + t),
           (-L, y0 + up + t)]
    parts.append(_profile_xy([(px_, py - t * 0.9) for px_, py in kas], post * 0.9, mat))
    parts.append(_profile_xy([(px_ * 1.02, py + 0.004) for px_, py in kas], post * 1.1, top))
    return _block("torii", parts)


def lantern(radius: float = 0.12, height: float = 0.3, *, mat: str = "lantern", band: str = "roof_trim",
            segments: int = 10) -> Block:
    """A paper lantern (chochin): a barrel with dark rings top and bottom; hang it from `top`."""
    prof = [(0.0, 0.0), (radius * 0.78, 0.0), (radius * 0.97, 0.22 * height), (radius, 0.5 * height),
            (radius * 0.97, 0.78 * height), (radius * 0.78, height), (0.0, height)]
    parts = [mesh.lathe(prof, mat, segments=segments, smooth=False),
             mesh.cylinder(radius * 0.82, height * 0.07, band, segments=segments).translate(0, -height * 0.02, 0),
             mesh.cylinder(radius * 0.82, height * 0.07, band, segments=segments).translate(0, height * 0.95, 0)]
    blk = _block("lantern", [p.translate(0, height * 0.02, 0) for p in parts])
    return blk


def stone_lantern(height: float = 0.25, *, mat: str = "stone", light: str = "lamp") -> Block:
    """A stone garden lantern (toro): base, post, light box, pyramid cap."""
    h = height
    parts = [mesh.box(h * 0.4, h * 0.12, h * 0.4, mat), mesh.cylinder(h * 0.08, h * 0.38, mat, segments=6).translate(0, h * 0.12, 0),
             mesh.box(h * 0.34, h * 0.06, h * 0.34, mat).translate(0, h * 0.5, 0),
             mesh.box(h * 0.22, h * 0.16, h * 0.22, light).translate(0, h * 0.56, 0),
             mesh.pyramid(h * 0.5, h * 0.5, h * 0.2, mat).translate(0, h * 0.72, 0),
             mesh.sphere(h * 0.05, mat, segments=6, rings=4).translate(0, h * 0.94, 0)]
    return _block("stone_lantern", parts)


def railing(length: float = 1.0, height: float = 0.08, *, post_every: float = 0.15, mat: str = "trim",
            thickness: float = 0.012) -> Block:
    """A railing along X: posts, a top rail and a mid rail."""
    n = max(1, round(length / post_every))
    parts = [mesh.box(length, thickness, thickness, mat).translate(0, height - thickness, 0),
             mesh.box(length, thickness * 0.7, thickness * 0.7, mat).translate(0, height * 0.5, 0)]
    for i in range(n + 1):
        parts.append(mesh.box(thickness, height, thickness, mat).translate(-length / 2 + length * i / n, 0, 0))
    blk = _block("railing", parts)
    blk.sockets.update(left=(-length / 2, 0.0, 0.0), right=(length / 2, 0.0, 0.0))
    return blk


def railing_ring(w: float = 1.0, d: float = 1.0, height: float = 0.08, *, mat: str = "trim",
                 post_every: float = 0.15) -> Block:
    """Railings round a w x d rectangle (balconies, deck edges)."""
    parts = []
    for z in (-d / 2, d / 2):
        parts += railing(w, height, post_every=post_every, mat=mat).placed(0, 0, z, world_uv=False)
    for x in (-w / 2, w / 2):
        parts += railing(d, height, post_every=post_every, mat=mat).placed(x, 0, 0, 90, world_uv=False)
    return _block("railing_ring", parts)


def sign_panel(w: float = 0.6, h: float = 0.3, *, depth: float = 0.03, mat: str = "sign", frame: str = "frame",
               posts: float = 0.0) -> Block:
    """A blank signage / screen panel facing +Z in a frame, optionally raised on two posts."""
    f = 0.02
    y0 = posts + f
    parts = [mesh.box(w, h, depth, mat).translate(0, y0, 0),
             _bar(-w / 2 - f, w / 2 + f, y0 - f, y0, -depth / 2 - 0.005, depth / 2 + 0.005, frame),
             _bar(-w / 2 - f, w / 2 + f, y0 + h, y0 + h + f, -depth / 2 - 0.005, depth / 2 + 0.005, frame),
             _bar(-w / 2 - f, -w / 2, y0, y0 + h, -depth / 2 - 0.005, depth / 2 + 0.005, frame),
             _bar(w / 2, w / 2 + f, y0, y0 + h, -depth / 2 - 0.005, depth / 2 + 0.005, frame)]
    if posts:
        for sx in (-1, 1):
            parts.append(mesh.box(0.025, posts, 0.025, frame).translate(sx * w * 0.35, 0, 0))
    return _block("sign_panel", parts)


def noren(w: float = 0.5, h: float = 0.15, *, panels: int = 3, mat: str = "fabric", rod: str = "wood_dark") -> Block:
    """Split shop-entrance curtains hanging below a rod; the block's top is the rod."""
    gap = 0.012
    pw = (w - gap * (panels - 1)) / panels
    parts = [mesh.cylinder(0.008, w + 0.04, rod, segments=6).rotate_z(90).translate((w + 0.04) / 2, h, 0)]
    for i in range(panels):
        x = -w / 2 + pw / 2 + i * (pw + gap)
        parts.append(mesh.box(pw, h, 0.008, mat).translate(x, 0, 0))
    return _block("noren", parts)


def street_lamp(height: float = 0.3, *, mat: str = "frame", lamp: str = "lamp") -> Block:
    """A street lamp: pole, arm, glowing head over +Z."""
    parts = [mesh.cylinder(0.01, height, mat, segments=6), mesh.box(0.012, 0.012, 0.08, mat).translate(0, height - 0.012, 0.04),
             mesh.box(0.03, 0.015, 0.05, lamp).translate(0, height - 0.03, 0.07)]
    return _block("street_lamp", parts)


def traffic_light(height: float = 0.3, *, mat: str = "frame") -> Block:
    """A traffic signal post facing +Z, with `red` and `green` lamps as child parts (animate their scale)."""
    parts = [mesh.cylinder(0.012, height, mat, segments=6), mesh.box(0.05, 0.1, 0.035, mat).translate(0, height - 0.1, 0.02)]
    blk = _block("traffic_light", parts)
    red = _block("lamp", [mesh.box(0.026, 0.026, 0.008, "light").translate(0, -0.013, 0)])
    green = _block("lamp", [mesh.box(0.026, 0.026, 0.008, "light_green").translate(0, -0.013, 0)])
    blk.children = {"red": (red, (0.0, height - 0.025, 0.041)), "green": (green, (0.0, height - 0.075, 0.041))}
    return blk


def beacon(radius: float = 0.03, *, mat: str = "light") -> Block:
    """An aviation warning light (a glowing ball) — make it a child node and blink its scale."""
    return _block("beacon", [mesh.sphere(radius, mat, segments=8, rings=5).translate(0, radius, 0)])


def tree(height: float = 0.4, radius: float = 0.15, *, kind: str = "pine", trunk: str = "wood_dark",
         leaves: str = "foliage") -> Block:
    """A low-poly tree: `pine` (pruned cloud pads, Japanese garden style) or `round`."""
    parts = [mesh.cylinder(radius * 0.12, height * 0.6, trunk, segments=6, radius_top=radius * 0.07)]
    if kind == "pine":
        for i, (fy, fr, ox) in enumerate(((0.45, 1.0, 0.15), (0.65, 0.8, -0.2), (0.85, 0.55, 0.05))):
            pad = mesh.sphere(radius * fr, leaves, segments=7, rings=4, smooth=False).scale(1, 0.45, 1)
            parts.append(pad.translate(radius * ox, height * fy, radius * ox * (1 if i % 2 else -1)))
    else:
        parts.append(mesh.sphere(radius, leaves, segments=7, rings=5, smooth=False).translate(0, height - radius, 0))
    return _block("tree", parts)


def incense_burner(size: float = 0.2, *, mat: str = "bronze", roof: str = "bronze") -> Block:
    """A temple incense burner (jokoro): bowl on legs under a small curved roof on posts."""
    s = size
    prof = [(0.0, 0.0), (s * 0.3, 0.0), (s * 0.42, s * 0.2), (s * 0.45, s * 0.32), (0.0, s * 0.32)]
    parts = [mesh.lathe(prof, mat, segments=8, smooth=False).translate(0, s * 0.18, 0)]
    for a in range(3):
        ang = math.radians(120 * a)
        parts.append(mesh.box(0.02, s * 0.2, 0.02, mat).translate(s * 0.22 * math.cos(ang), 0, s * 0.22 * math.sin(ang)))
    for sx in (-1, 1):
        for sz in (-1, 1):
            parts.append(mesh.box(0.015, s * 0.35, 0.015, mat).translate(sx * s * 0.28, s * 0.5, sz * s * 0.28))
    parts += roof_curved(s * 0.6, s * 0.6, s * 0.18, overhang=s * 0.12, lift=s * 0.06, thickness=0.015, mat=roof,
                         trim=None, steps=3, segments=2).placed(0, s * 0.85, 0, world_uv=False)
    return _block("incense_burner", parts)


def bridge_arch(length: float = 1.0, width: float = 0.3, height: float = 0.2, *, arches: int = 2,
                mat: str = "stone", deck: str | None = "paving", rail: str | None = "stone") -> Block:
    """A stone arch bridge along X with `arches` round arches (spectacles bridge) and parapets."""
    pts = [(-length / 2, 0.0)]
    span = length / arches
    ar = span * 0.38
    for i in range(arches):
        cx = -length / 2 + span * (i + 0.5)
        pts.append((cx - ar, 0.0))
        for k in range(1, 8):
            a = math.pi - math.pi * k / 8
            pts.append((cx + ar * math.cos(a), min(height * 0.8, ar) * math.sin(a)))
        pts.append((cx + ar, 0.0))
    pts += [(length / 2, 0.0), (length / 2, height), (-length / 2, height)]
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 1e-6 or abs(p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    parts = [_profile_xy(clean, width, mat)]
    if deck:
        parts.append(mesh.box(length, 0.012, width * 0.9, deck).translate(0, height, 0))
    if rail:
        for sz in (-1, 1):
            parts.append(mesh.box(length, 0.05, 0.03, rail).translate(0, height, sz * (width / 2 - 0.015)))
    return _block("bridge_arch", parts)


# ------------------------------------------------------------------ construction


def scaffold(w: float = 1.0, d: float = 1.0, h: float = 1.0, *, bay: float = 0.35, lift: float = 0.25,
             pole: float = 0.02, mat: str = "scaffold", plank: str = "scaffold_plank", net: str | None = None,
             braces: bool = True, plank_every: int = 2, depth: float = 0.1) -> Block:
    """Tube-and-board scaffolding wrapping a w x d box up to h: posts, ledgers, boards, diagonal braces."""
    parts = []
    x, z = w / 2, d / 2
    nl = max(1, round(h / lift))
    ys = [h * k / nl for k in range(1, nl + 1)]
    sides = [((-x, -z), (x, -z)), ((x, -z), (x, z)), ((x, z), (-x, z)), ((-x, z), (-x, -z))]
    for si, ((x0, z0), (x1, z1)) in enumerate(sides):
        L = math.hypot(x1 - x0, z1 - z0)
        nb = max(1, round(L / bay))
        out = np.array([(z1 - z0), -(x1 - x0)]) / L  # outward normal in (x, z)
        for i in range(nb):
            f = i / nb
            px_, pz = x0 + (x1 - x0) * f, z0 + (z1 - z0) * f
            for o in (0.0, depth):
                parts.append(mesh.box(pole, h, pole, mat).translate(px_ + out[0] * o, 0, pz + out[1] * o))
        for k, y in enumerate(ys):
            for o in (0.0, depth):
                parts.append(mesh.beam((x0 + out[0] * o, y, z0 + out[1] * o), (x1 + out[0] * o, y, z1 + out[1] * o),
                                       pole * 0.8, mat))
            if k % plank_every == plank_every - 1 or k == len(ys) - 1:
                mx, mz = (x0 + x1) / 2 + out[0] * depth / 2, (z0 + z1) / 2 + out[1] * depth / 2
                board = mesh.box(L, 0.012, depth * 0.9, plank)
                ang = math.degrees(math.atan2(-(z1 - z0), x1 - x0))
                parts.append(board.rotate_y(ang).translate(mx, y + pole * 0.4, mz))
        if braces:
            for k in range(nl):
                ya, yb = (h * k / nl, h * (k + 1) / nl)
                fa, fb = (0.0, 1.0) if (k + si) % 2 == 0 else (1.0, 0.0)
                o = depth
                parts.append(mesh.beam((x0 + (x1 - x0) * fa + out[0] * o, ya + 0.01, z0 + (z1 - z0) * fa + out[1] * o),
                                       (x0 + (x1 - x0) * fb + out[0] * o, yb, z0 + (z1 - z0) * fb + out[1] * o),
                                       pole * 0.6, mat))
        if net:
            ang = math.degrees(math.atan2(-(z1 - z0), x1 - x0))
            mx, mz = (x0 + x1) / 2 + out[0] * (depth + 0.01), (z0 + z1) / 2 + out[1] * (depth + 0.01)
            parts.append(mesh.box(L, h * 0.55, 0.004, net).rotate_y(ang).translate(mx, h * 0.4, mz))
    return _block("scaffold", parts)


def crane(height: float = 2.0, jib: float = 1.2, *, mat: str = "crane", weight: str = "wall_alt",
          cab: str = "trim") -> Block:
    """A tower crane: lattice mast, with the slewing jib as child part `jib` (pivot at the mast top)."""
    m = 0.12
    mast = lattice_segment(m, m, height, panels=max(2, round(height / 0.22)), leg=0.018, brace=0.008, mat=mat,
                           rings=False)
    parts = mast.placed(world_uv=False) + [mesh.box(0.2, 0.04, 0.2, "stone")]
    blk = _block("crane", parts)
    t = 0.012
    jp = [mesh.box(0.1, 0.08, 0.1, cab).translate(0.02, 0, 0.06),
          mesh.beam((0, 0.0, 0), (0, 0.22, 0), 0.02, mat)]
    for sz in (-1, 1):
        jp.append(mesh.beam((0, 0.0, sz * 0.035), (jib, 0.0, sz * 0.035), t, mat))
        jp.append(mesh.beam((0, 0.0, sz * 0.035), (-jib * 0.3, 0.0, sz * 0.035), t, mat))
    jp.append(mesh.beam((0, 0.09, 0), (jib, 0.09, 0), t, mat))
    n = max(2, round(jib / 0.1))
    for i in range(n):
        xa, xb = jib * i / n, jib * (i + 1) / n
        for sz in (-1, 1):
            jp.append(mesh.beam((xa, 0.0, sz * 0.035), (xb, 0.09, 0), t * 0.6, mat))
    jp += [mesh.beam((0, 0.22, 0), (jib * 0.7, 0.09, 0), 0.006, "frame"),
           mesh.beam((0, 0.22, 0), (-jib * 0.28, 0.02, 0), 0.006, "frame"),
           mesh.box(0.1, 0.08, 0.09, weight).translate(-jib * 0.25, -0.04, 0),
           mesh.beam((jib * 0.6, 0.0, 0), (jib * 0.6, -0.35, 0), 0.004, "frame"),
           mesh.box(0.03, 0.03, 0.03, "frame").translate(jib * 0.6, -0.38, 0)]
    blk.children = {"jib": (Block("crane_jib", jp, (2 * jib, 0.6, 0.1), {}), (0.0, height, 0.0))}
    return blk


# ------------------------------------------------------------------ scene and export


def add_block(scene: gltf.Scene, block: Block, name: str, *, parent: gltf.Node | None = None,
              translation: Sequence[float] = (0, 0, 0), yaw: float = 0.0, **extras) -> gltf.Node:
    """Add a block as a node subtree: its meshes on `name`, each child part as node `<name>_<child>`."""
    rot = mesh.quat_euler(y=yaw) if yaw else (0, 0, 0, 1)
    node = scene.node(name, meshes=block.placed(children=False), parent=parent, translation=translation,
                      rotation=rot, **({"sockets": {k: list(v) for k, v in block.sockets.items()}} | extras))
    for cname, (child, pivot) in block.children.items():
        add_block(scene, child, f"{name}_{cname}", parent=node, translation=pivot)
    return node


#: Every block function by name — what `export_block` and the tests iterate over.
BLOCKS: dict[str, Callable[..., Block]] = {
    "slab": slab, "plinth": plinth, "battered_base": battered_base, "stairs": stairs, "wall": wall,
    "window_wall": window_wall, "door_wall": door_wall, "facade_box": facade_box, "curtain_wall": curtain_wall,
    "pillar": pillar, "colonnade": colonnade, "beam": beam_block, "truss": truss, "lattice_tower": lattice_tower,
    "lattice_segment": lattice_segment, "spire": spire, "deck_ring": deck_ring, "deck_box": deck_box,
    "roof_flat": roof_flat, "roof_gable": roof_gable, "roof_hip": roof_hip, "roof_pyramid": roof_pyramid,
    "roof_mansard": roof_mansard, "roof_curved": roof_curved, "roof_dome": roof_dome, "pagoda_tier": pagoda_tier,
    "torii": torii, "lantern": lantern, "stone_lantern": stone_lantern, "railing": railing,
    "railing_ring": railing_ring, "sign_panel": sign_panel, "noren": noren, "street_lamp": street_lamp,
    "traffic_light": traffic_light, "beacon": beacon, "tree": tree, "incense_burner": incense_burner,
    "bridge_arch": bridge_arch, "scaffold": scaffold, "crane": crane,
}


def export_block(name: str, out_dir: str | Path, *, style: str = "default", block: Block | None = None,
                 **params) -> dict:
    """Write one block as a `kind="block"` package (GLB, previews, manifest); returns `validate_package`."""
    blk = block or BLOCKS[name](**params)
    pkg = Path(out_dir)
    (pkg / "previews").mkdir(parents=True, exist_ok=True)
    key = name.replace("_", "-")
    scene = gltf.Scene(key, extras={"kind": "block", "style": style, "params": {k: repr(v) for k, v in params.items()}})
    define_materials(scene, style)
    add_block(scene, blk, name)
    glb = scene.write(pkg / f"{key}.glb")
    preview.render(glb, pkg / "previews" / "render.png", size=384)
    preview.turntable(glb, pkg / "previews" / "turntable.png", size=256)
    w, h, d = blk.size
    catalog.write_manifest(pkg, name=key, kind="block", files=[f"{key}.glb"], title=name.replace("_", " "),
                           footprint=[round(w, 3), round(d, 3)], height=round(h, 3), style=style,
                           sockets={k: [round(c, 4) for c in v] for k, v in blk.sockets.items()},
                           parts=sorted(f"{name}_{c}" for c in blk.children))
    return catalog.validate_package(pkg)
