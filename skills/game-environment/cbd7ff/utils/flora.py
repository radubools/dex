"""Canadian-wilderness vegetation: spruces, pines, firs, birch, maple, bushes, grass, cattails.

Every plant is a real low-poly model (no cards), so it reads from any side of
the orbit camera. Structure the game relies on:

- root node `<name>` at the base, origin at ground centre (y = 0);
- `trunk` (static) and `crown` — the moving part, its pivot at the top of
  the trunk — carrying a looping `sway` clip (4 s; grass/cattails 2.5 s on
  `blades`). Play it with a random start time per instance so a forest
  doesn't sway in lockstep.

Seasons are separate files with the same node names: conifers have summer
and winter (snow-laden: a snow cap on each tier); deciduous plants have
summer, autumn (birch gold, maple red/orange) and winter (bare branches).

    from utils import flora
    scene = flora.plant("spruce", "winter")
    flora.build_plant("maple", "out/")
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

from . import gltf, mesh, terrain, texture

GREENS = {"spruce": "#1f4d36", "spruce-dark": "#1a4230", "fir": "#2c6243", "pine": "#2b5a35"}


# ------------------------------------------------------------------ textures


_TEX: dict = {}


def _tex(key: str, fn: Callable[[], Image.Image]) -> Image.Image:
    if key not in _TEX:
        _TEX[key] = fn()
    return _TEX[key]


def birch_bark(size: int = 128) -> Image.Image:
    """White birch bark with dark horizontal lenticels."""
    def make() -> Image.Image:
        n = texture.noise(size, 4, octaves=3, seed=5)
        img = texture.to_image(texture.tint("#e9e5db", n, 0.08))
        d = ImageDraw.Draw(img)
        rng = np.random.default_rng(6)
        for _ in range(26):
            x, y = int(rng.integers(0, size)), int(rng.integers(0, size))
            w, h = int(rng.integers(size // 10, size // 4)), int(rng.integers(1, 4))
            for dx in (0, -size):   # wrap horizontally so the texture still tiles
                d.rectangle([x + dx, y, x + dx + w, y + h], fill=(38, 34, 30))
        return img
    return _tex(f"birch{size}", make)


def _bark(color: str = "#5a3f2a") -> Image.Image:
    return _tex(f"bark{color}", lambda: texture.bark(128, color, seed=2))


def _leaves(color: str) -> Image.Image:
    return _tex(f"leaf{color}", lambda: texture.foliage(128, color, seed=3))


def _materials(scene: gltf.Scene, **colors: str) -> None:
    for name, col in colors.items():
        if name.startswith("bark"):
            scene.material(name, texture=_bark(col), roughness=0.95)
        elif name == "birch_bark":
            scene.material(name, texture=birch_bark(), roughness=0.9)
        elif name == "branches":
            scene.material(name, texture=_bark(col), roughness=0.95)
        else:
            scene.material(name, texture=_leaves(col), roughness=0.9, double_sided=name.startswith("blade"))


# ------------------------------------------------------------------ pieces


def blob(radius: float, material: str, *, seed: int = 0, squash: float = 1.0, segments: int = 7,
         rings: int = 5, rough: float = 0.18) -> mesh.Mesh:
    """A lumpy low-poly ball — a leaf cluster, a bush lobe, a cloud puff, a rock."""
    m = mesh.sphere(radius, material, segments=segments, rings=rings, smooth=False)
    m = m.scale(1.0, squash, 1.0)
    return terrain.jitter(m, radius * rough, seed=seed, keep_floor=False)


def tier(radius: float, height: float, material: str, *, segments: int = 8, seed: int = 0,
         droop: float = 0.18) -> mesh.Mesh:
    """One conifer tier: a cone with a hollowed underside and a ragged rim, base at y=0."""
    prof = [(0.0, height * droop), (radius, 0.0), (0.0, height)]
    m = mesh.lathe(prof, material, segments=segments, smooth=False)
    return terrain.jitter(m.rotate_y(seed * 37 % 360), radius * 0.07, seed=seed, keep_floor=False)


def _sway(scene: gltf.Scene, node: gltf.Node, degrees: float = 1.6, period: float = 4.0, name: str = "sway") -> None:
    ts = [period * k / 4 for k in range(5)]
    qs = [mesh.quat_euler(x=degrees * math.sin(2 * math.pi * k / 4), z=degrees * 0.6 * math.cos(2 * math.pi * k / 4))
          for k in range(5)]
    qs[-1] = qs[0]
    scene.animation(name).rotate(node, ts, qs)


def _branches(rng: np.random.Generator, base: float, top: float, count: int, reach: float, thick: float,
              material: str = "branches") -> list[mesh.Mesh]:
    out = []
    for k in range(count):
        a = 2 * math.pi * (k / count) + rng.uniform(-0.4, 0.4)
        y0 = base + (top - base) * rng.uniform(0.0, 0.6)
        r = reach * rng.uniform(0.7, 1.0)
        p1 = (math.cos(a) * r * 0.6, y0 + r * 0.7, math.sin(a) * r * 0.6)
        p2 = (math.cos(a) * r, y0 + r * 1.25, math.sin(a) * r)
        out.append(mesh.beam((0, y0, 0), p1, thick, material))
        out.append(mesh.beam(p1, p2, thick * 0.6, material))
        b = a + rng.choice([-0.9, 0.9])
        out.append(mesh.beam(p1, (p1[0] + math.cos(b) * r * 0.35, p1[1] + r * 0.4, p1[2] + math.sin(b) * r * 0.35),
                             thick * 0.5, material))
    return out


# ------------------------------------------------------------------ plants


def conifer(name: str, season: str = "summer", *, height: float = 2.1, tiers: int = 5, radius: float = 0.5,
            trunk: float = 0.3, color: str = GREENS["spruce"], seed: int = 0, segments: int = 8) -> gltf.Scene:
    """A spruce / fir: stacked tiers on a short trunk; winter puts a snow cap on every tier."""
    s = gltf.Scene(name, extras={"season": season, "kind": "conifer"})
    _materials(s, bark="#5a3f2a", needles=color)
    terrain.snow_material(s)
    root = s.node(name)
    s.node("trunk", parent=root, meshes=[mesh.cylinder(0.06 * radius / 0.5, trunk + 0.25, "bark", segments=6)])
    hc = height - trunk
    parts = []
    for k in range(tiers):
        y = hc * 0.8 * k / tiers
        h = hc - y if k == tiers - 1 else hc * (0.42 - 0.1 * k / tiers)
        r = radius * (1 - 0.72 * k / tiers)
        parts.append(tier(r, h, "needles", segments=segments, seed=seed * 13 + k).translate(0, y, 0))
        if season == "winter":
            f = 0.12
            cap = mesh.lathe([(0.0, h * (f + 0.15)), (r * (1 - f) * 1.07, h * f), (0.0, h * 1.03)], "snow",
                             segments=segments, smooth=False).rotate_y(seed * 37 % 360 + 11)
            parts.append(terrain.jitter(cap, r * 0.05, seed=seed * 7 + k, keep_floor=False).translate(0, y, 0))
    crown = s.node("crown", parent=root, meshes=parts, translation=(0, trunk, 0))
    _sway(s, crown)
    return s


def pine(name: str, season: str = "summer", *, height: float = 2.5, seed: int = 0,
         color: str = GREENS["pine"]) -> gltf.Scene:
    """A jack / lodgepole pine: tall bare trunk, a ragged crown of needle clumps near the top."""
    rng = np.random.default_rng(seed)
    s = gltf.Scene(name, extras={"season": season, "kind": "conifer"})
    _materials(s, bark="#6b4a30", needles=color, branches="#5a3f2a")
    terrain.snow_material(s)
    root = s.node(name)
    trunk_h = height * 0.45
    s.node("trunk", parent=root, meshes=[mesh.cylinder(0.065, trunk_h + 0.3, "bark", segments=6, radius_top=0.04)])
    parts, clumps = [], []
    hc = height - trunk_h
    pads = 7
    for k in range(pads):
        t = k / (pads - 1)
        a = math.radians(137.5 * k) + rng.uniform(-0.2, 0.2)
        y = hc * (0.05 + 0.7 * t)
        ring = 0.26 * (1 - 0.7 * t)
        rr = 0.36 * (1 - 0.45 * t) * rng.uniform(0.9, 1.1)
        c = blob(rr, "needles", seed=seed * 5 + k, squash=0.42).translate(math.cos(a) * ring, y, math.sin(a) * ring)
        parts.append(c)
        clumps.append(c)
        parts.append(mesh.beam((0, max(0.0, y - 0.18), 0), (math.cos(a) * ring, y, math.sin(a) * ring), 0.035, "branches"))
    top = blob(0.17, "needles", seed=seed + 99, squash=0.7).translate(0, hc * 0.9, 0)
    parts.append(top)
    clumps.append(top)
    if season == "winter":
        parts += terrain.snowify(clumps, depth=0.035, threshold=0.6)
    crown = s.node("crown", parent=root, meshes=parts, translation=(0, trunk_h, 0))
    _sway(s, crown, degrees=2.0)
    return s


def deciduous(name: str, season: str = "summer", *, species: str = "maple", height: float = 1.8,
              seed: int = 0) -> gltf.Scene:
    """Birch (slim white trunk, small airy crown) or maple (broad dome); winter is bare branches."""
    rng = np.random.default_rng(seed)
    birch = species == "birch"
    s = gltf.Scene(name, extras={"season": season, "kind": "deciduous", "species": species})
    if birch:
        leaves = {"summer": ["#7aa846", "#6a9a3c"], "autumn": ["#e2b53a", "#d49a2a"]}
        trunk_mat, trunk_r, fork = "birch_bark", 0.045, height * 0.55
        _materials(s, birch_bark="", branches="#cfc9bd")
    else:
        leaves = {"summer": ["#4d7f2c", "#5a8c32"], "autumn": ["#c63a26", "#e2762a"]}
        trunk_mat, trunk_r, fork = "bark", 0.075, height * 0.42
        _materials(s, bark="#5b4330", branches="#5b4330")
    terrain.snow_material(s)
    root = s.node(name)
    lean = rng.uniform(-4, 4) if birch else 0.0
    tr = mesh.cylinder(trunk_r, fork + 0.12, trunk_mat, segments=6, radius_top=trunk_r * 0.75)
    s.node("trunk", parent=root, meshes=[tr.rotate_z(lean)], **{})
    off = (-math.sin(math.radians(lean)) * fork, fork * math.cos(math.radians(lean)), 0)
    hc = height - fork
    parts = _branches(rng, 0.0, hc * 0.35, 5 if birch else 6, hc * (0.55 if birch else 0.6),
                      0.03 if birch else 0.045, "branches")
    if season != "winter":
        cols = leaves[season]
        _materials(s, leaves_a=cols[0], leaves_b=cols[1])
        n = 4 if birch else 6
        for k in range(n):
            a = 2 * math.pi * k / n + rng.uniform(-0.3, 0.3)
            ring = 0.0 if k == 0 else (0.17 if birch else 0.33)
            y = hc * (0.78 if k == 0 else rng.uniform(0.38, 0.6))
            r = (0.3 if birch else 0.42) * rng.uniform(0.85, 1.1)
            parts.append(blob(r, "leaves_a" if k % 2 == 0 else "leaves_b", seed=seed * 11 + k,
                              squash=1.15 if birch else 0.8)
                         .translate(math.cos(a) * ring, y, math.sin(a) * ring))
    else:
        parts += terrain.snowify(parts, depth=0.018, threshold=0.2)
    crown = s.node("crown", parent=root, meshes=parts, translation=off)
    _sway(s, crown, degrees=1.4 if not birch else 2.2)
    return s


def bush(name: str, season: str = "summer", *, seed: int = 0, size: float = 1.0) -> gltf.Scene:
    """A low shrub of leafy lobes (blueberry red in autumn); winter piles snow on top."""
    rng = np.random.default_rng(seed)
    s = gltf.Scene(name, extras={"season": season, "kind": "bush"})
    cols = {"summer": ("#3f6e2e", "#4a7a34"), "autumn": ("#9c3f2a", "#b5562e"), "winter": ("#4c5a3a", "#5a5a3c")}[season]
    _materials(s, leaves_a=cols[0], leaves_b=cols[1])
    terrain.snow_material(s)
    lobes = []
    for k in range(5):
        a = 2 * math.pi * k / 5 + rng.uniform(-0.4, 0.4)
        r = size * rng.uniform(0.17, 0.25)
        ring = 0.0 if k == 0 else size * 0.17
        lobes.append(blob(r, "leaves_a" if k % 2 else "leaves_b", seed=seed * 7 + k, squash=0.8)
                     .translate(math.cos(a) * ring, r * 0.65 + (size * 0.08 if k == 0 else 0), math.sin(a) * ring))
    parts = list(lobes)
    if season == "winter":
        parts += terrain.snowify(lobes, depth=0.05, threshold=0.35)
    lo = min(float(p.positions[:, 1].min()) for p in parts)
    parts = [p.translate(0, -lo, 0) if lo < 0 else p for p in parts]
    s.node(name, meshes=parts)
    return s


def _blade(h: float, w: float, a: float, lean: float) -> mesh.Mesh:
    tip = (math.cos(a) * lean, h, math.sin(a) * lean)
    px, pz = -math.sin(a) * w / 2, math.cos(a) * w / 2
    return mesh.from_faces([(px, 0, pz), (-px, 0, -pz), tip], [(0, 1, 2)], "blades")


def grass_tuft(name: str, season: str = "summer", *, seed: int = 0, blades: int = 11) -> gltf.Scene:
    """A clump of grass blades (double-sided); autumn turns gold, winter is straw poking out of snow."""
    rng = np.random.default_rng(seed)
    s = gltf.Scene(name, extras={"season": season, "kind": "grass"})
    col = {"summer": "#6f9a3e", "autumn": "#b49a4c", "winter": "#c2ad7a"}[season]
    s.material("blades", texture=_leaves(col), roughness=0.9, double_sided=True)
    terrain.snow_material(s)
    root = s.node(name)
    parts = []
    for k in range(blades):
        a = 2 * math.pi * k / blades + rng.uniform(-0.3, 0.3)
        h = rng.uniform(0.14, 0.3) * (0.7 if season == "winter" else 1.0)
        r = rng.uniform(0.0, 0.08)
        parts.append(_blade(h, 0.035, a, rng.uniform(0.04, 0.1)).translate(math.cos(a) * r, 0, math.sin(a) * r))
    if season == "winter":
        mound = mesh.sphere(0.14, "snow", segments=8, rings=4, smooth=False).scale(1, 0.35, 1)
        mound = mesh.Mesh(mound.positions, mound.normals, mound.uvs, mound.indices[mound.positions[mound.indices][:, :, 1].min(1) >= -1e-6], "snow")
        s.node("drift", parent=root, meshes=[mound])
    node = s.node("blades", parent=root, meshes=parts)
    _sway(s, node, degrees=5.0, period=2.5)
    return s


def cattails(name: str, season: str = "summer", *, seed: int = 0) -> gltf.Scene:
    """Lake-shore reeds with brown seed heads; dry and pale in autumn/winter."""
    rng = np.random.default_rng(seed)
    s = gltf.Scene(name, extras={"season": season, "kind": "reeds"})
    col = {"summer": "#5f8a3a", "autumn": "#a59a5a", "winter": "#b8a878"}[season]
    s.material("blades", texture=_leaves(col), roughness=0.9, double_sided=True)
    s.material("heads", texture=_bark("#5a3a22"), roughness=0.95)
    terrain.snow_material(s)
    root = s.node(name)
    parts = []
    for k in range(7):
        a = 2 * math.pi * k / 7 + rng.uniform(-0.4, 0.4)
        r = rng.uniform(0.03, 0.15)
        x, z = math.cos(a) * r, math.sin(a) * r
        h = rng.uniform(0.42, 0.62)
        parts.append(mesh.cylinder(0.008, h, "blades", segments=4).translate(x, 0, z))
        if k % 2 == 0:
            parts.append(mesh.cylinder(0.022, 0.1, "heads", segments=6).translate(x, h - 0.16, z))
        parts.append(_blade(h * 0.8, 0.03, a + 1.2, 0.12).translate(x, 0, z))
    if season == "winter":
        parts += terrain.snowify([p for p in parts if p.material == "heads"], depth=0.015, threshold=0.5)
    node = s.node("blades", parent=root, meshes=parts)
    _sway(s, node, degrees=4.0, period=2.5)
    return s


# ------------------------------------------------------------------ catalogue

#: name -> (builder(season) -> Scene, seasons with their own file, title)
PLANTS: dict[str, tuple[Callable[[str], gltf.Scene], tuple[str, ...], str]] = {
    "spruce": (lambda se: conifer("spruce", se, seed=1), ("summer", "winter"), "White spruce"),
    "spruce-tall": (lambda se: conifer("spruce-tall", se, height=3.0, tiers=6, radius=0.56, trunk=0.35,
                                       color=GREENS["spruce-dark"], seed=2), ("summer", "winter"), "Tall black spruce"),
    "spruce-small": (lambda se: conifer("spruce-small", se, height=1.15, tiers=3, radius=0.38, trunk=0.18, seed=3),
                     ("summer", "winter"), "Young spruce"),
    "fir": (lambda se: conifer("fir", se, height=1.8, tiers=4, radius=0.62, trunk=0.26, color=GREENS["fir"], seed=4),
            ("summer", "winter"), "Balsam fir"),
    "pine": (lambda se: pine("pine", se, seed=5), ("summer", "winter"), "Jack pine"),
    "birch": (lambda se: deciduous("birch", se, species="birch", height=1.9, seed=6), ("summer", "autumn", "winter"),
              "Paper birch"),
    "maple": (lambda se: deciduous("maple", se, species="maple", height=1.75, seed=7), ("summer", "autumn", "winter"),
              "Sugar maple"),
    "bush": (lambda se: bush("bush", se, seed=8), ("summer", "autumn", "winter"), "Blueberry bush"),
    "grass-tuft": (lambda se: grass_tuft("grass-tuft", se, seed=9), ("summer", "autumn", "winter"), "Grass tuft"),
    "cattails": (lambda se: cattails("cattails", se, seed=10), ("summer", "autumn", "winter"), "Cattails"),
}


def plant(name: str, season: str = "summer") -> gltf.Scene:
    """The scene of a catalogue plant in a season (falls back to summer when it has no own variant)."""
    fn, seasons, _ = PLANTS[name]
    return fn(season if season in seasons else ("winter" if season == "winter" and "winter" in seasons else "summer"))


def build_plant(name: str, out_dir: str | Path, *, size: int = 320) -> dict:
    """Package one catalogue plant with its seasonal variants; returns `catalog.validate_package`."""
    fn, seasons, title = PLANTS[name]
    variants = {se: fn(se) for se in seasons}
    return terrain.write_package(name, out_dir, variants, category="flora", footprint=[1, 1], size=size,
                                 title=title, sway_clip="sway", sway_node="blades" if name in ("grass-tuft", "cattails") else "crown")


def build_all(out_root: str | Path) -> dict[str, dict]:
    """Package every plant in `PLANTS`; returns {name: validate report}."""
    return {n: build_plant(n, out_root) for n in PLANTS}
