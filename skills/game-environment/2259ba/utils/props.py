"""Scenery props: rocks, logs, lumber, crates, barrels, fences, lamps, cones, benches, signs, winter decor.

Each prop is one small model, origin at its footprint centre on the ground.
Winter variants (`<name>-winter.glb`) are the same model with snow slabs on
every up-facing surface (`terrain.snowify`), in a separate `snow` node so the
game can also just toggle that node. Winter-only props (`snowman`,
`christmas-tree`) ship a single winter file.

Lights: anything that glows uses an emissive material (the game scales
emissive strength with time of day) and has an empty node named `light` (or
`light_*`) where a point light belongs, with `extras.light` = {color,
intensity, range}.

Fences connect along X: a `fence` segment spans x = -0.5 .. +0.5 with its
post at -0.5; put a `fence-post` at the open end of a run.

    from utils import props
    scene = props.prop("street-lamp", "winter")
    props.build_prop("crate", "out/")
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from PIL import Image, ImageDraw

from . import flora, gltf, mesh, terrain, texture

_TEX: dict = {}


def _tex(key: str, fn: Callable[[], Image.Image]) -> Image.Image:
    if key not in _TEX:
        _TEX[key] = fn()
    return _TEX[key]


def log_end(size: int = 128) -> Image.Image:
    """Cut end of a log: pale growth rings and a dark bark rim."""
    def make() -> Image.Image:
        y, x = (np.mgrid[0:size, 0:size] + 0.5) / size * 2 - 1
        r = np.hypot(x, y)
        n = texture.noise(size, 4, octaves=3, seed=3)
        rings = 0.5 + 0.5 * np.sin((r * 9 + n * 0.6) * 2 * np.pi)
        a = texture.tint("#c9a06a", 0.5 * rings + 0.5 * n, 0.25)
        a[r > 0.86] = texture.rgb("#4f3826")
        return texture.to_image(a)
    return _tex(f"logend{size}", make)


def sign_face(text: str = "TOKYO", size: tuple[int, int] = (256, 64)) -> Image.Image:
    """A painted wooden sign board with blocky lettering and an arrow."""
    def make() -> Image.Image:
        board = texture.planks(256, count=1, color="#a9763e", seed=4).resize(size).rotate(0)
        small = Image.new("L", (size[0] // 4, size[1] // 4), 0)
        d = ImageDraw.Draw(small)
        d.text((4, 2), text, fill=255)
        w = small.width
        d.polygon([(w - 12, 5), (w - 5, 8), (w - 12, 11)], fill=255)
        d.line([(w - 18, 8), (w - 10, 8)], fill=255, width=2)
        mask = small.resize(size, Image.NEAREST)
        paint = Image.new("RGB", size, (246, 240, 222))
        board.paste(paint, (0, 0), mask)
        return board
    return _tex(f"sign{text}", make)


def _mats(s: gltf.Scene) -> None:
    """Every prop material; only the used ones are written."""
    s.material("rock", texture=_tex("rock", lambda: _lichen()), roughness=0.95)
    s.material("bark", texture=_tex("bark", lambda: texture.bark(128, "#5a3f2a", seed=2)), roughness=0.95)
    s.material("log_end", texture=log_end(), roughness=0.9)
    s.material("lumber", texture=_tex("lumber", lambda: texture.wood(128, "#cfa66e", seed=5)), roughness=0.85)
    s.material("planks", texture=_tex("planks", lambda: texture.planks(128, count=4, color="#a8783f", seed=6)),
               roughness=0.85)
    s.material("wood_dark", texture=_tex("wooddark", lambda: texture.wood(128, "#6e4a2a", seed=7)), roughness=0.9)
    s.material("staves", texture=_tex("staves", lambda: texture.planks(128, count=8, color="#8a5a32", seed=8)),
               roughness=0.85)
    s.material("iron", texture=_tex("iron", lambda: texture.metal(64, "#3c4044", panels=1, seed=9)), roughness=0.5,
               metallic=0.7)
    s.material("lamp_green", texture=_tex("lampg", lambda: texture.metal(64, "#2f4a3c", panels=1, seed=10)),
               roughness=0.45, metallic=0.6)
    s.material("glow", color="#fff2c4", emissive="#ffd98a", roughness=0.3)
    s.material("cone_orange", texture=_tex("orange", lambda: texture.solid("#f26a1b", 64, seed=11)), roughness=0.6)
    s.material("reflective", color="#f2f2ee", roughness=0.3)
    s.material("black", color="#1c1c1e", roughness=0.7)
    s.material("coal", color="#202022", roughness=0.9)
    s.material("carrot", color="#f07a22", roughness=0.7)
    s.material("scarf", texture=_tex("scarf", lambda: texture.stripes(64, 4, a="#c8202c", b="#f2efe6")),
               roughness=0.9)
    s.material("needles", texture=_tex("needles", lambda: texture.foliage(128, "#1f4d36", seed=3)), roughness=0.9)
    s.material("light_red", color="#ff4040", emissive="#ff3030")
    s.material("light_gold", color="#ffd040", emissive="#ffc830")
    s.material("light_blue", color="#60a0ff", emissive="#4a8cff")
    s.material("star", color="#ffe27a", emissive="#ffd84a", metallic=0.4, roughness=0.3)
    s.material("present_a", texture=_tex("pa", lambda: texture.stripes(64, 2, a="#c8202c", b="#c8202c", vertical=True)),
               roughness=0.6)
    s.material("present_b", color="#2f7a4a", roughness=0.6)
    s.material("present_c", color="#3a5ac8", roughness=0.6)
    s.material("ribbon", color="#f4d24a", roughness=0.4)
    terrain.snow_material(s)


def _lichen() -> Image.Image:
    a = np.asarray(texture.rock(128, "#7d8085", seed=1), dtype=np.float64) / 255
    n = texture.noise(128, 8, octaves=3, seed=2)
    a[n > 0.66] = a[n > 0.66] * 0.5 + texture.rgb("#8f9a5a") * 0.5
    return texture.to_image(a)


GLOWING = ("glow", "light_red", "light_gold", "light_blue", "star")


def _finish(name: str, season: str, parts: Sequence[mesh.Mesh], *, extra_nodes: Callable[[gltf.Scene, gltf.Node], None] | None = None,
            snow_skip: Sequence[str] = (), snow_depth: float = 0.035, threshold: float = 0.6) -> gltf.Scene:
    s = gltf.Scene(name, extras={"season": season, "kind": "prop"})
    _mats(s)
    root = s.node(name, meshes=list(parts))
    if season == "winter":
        snow = terrain.snowify(parts, depth=snow_depth, threshold=threshold, skip=tuple(snow_skip) + GLOWING + ("snow",))
        if snow:
            s.node("snow", parent=root, meshes=snow)
    if extra_nodes:
        extra_nodes(s, root)
    return s


# ------------------------------------------------------------------ geometry


def rock(radius: float = 0.3, *, seed: int = 0, squash: float = 0.62) -> mesh.Mesh:
    """A lumpy boulder of `radius`, flat-bottomed and slightly sunk into the ground."""
    m = flora.blob(radius, "rock", seed=seed, squash=squash, segments=8, rings=6, rough=0.22)
    return terrain.sink(m, radius * squash * 0.25)


def log(length: float = 0.9, radius: float = 0.12, *, seed: int = 0) -> list[mesh.Mesh]:
    """A felled log lying along X: bark sides and ringed cut ends."""
    side = mesh.cylinder(radius, length, "bark", segments=8, caps=False)
    ends = []
    for y, order in ((0.0, 1), (length, -1)):
        pts = [(radius * math.cos(2 * math.pi * k / 8), y, radius * math.sin(2 * math.pi * k / 8)) for k in range(8)]
        uv = [(0.5 + 0.5 * math.cos(2 * math.pi * k / 8), 0.5 + 0.5 * math.sin(2 * math.pi * k / 8)) for k in range(8)]
        idx = list(range(8)) if order > 0 else list(range(8))[::-1]
        ends.append(mesh.from_faces(pts, [idx], "log_end", uvs=np.asarray(uv)))
    parts = [p.translate(0, -length / 2, 0).rotate_z(90).translate(0, radius * 0.92, 0) for p in [side] + ends]
    stub = mesh.cylinder(0.035, 0.12, "bark", segments=5).rotate_x(-60).translate(length * 0.2, radius * 1.6, 0.04)
    return [m.rotate_y(seed * 23 % 30) for m in parts + [stub]]


# ------------------------------------------------------------------ props


def boulder(name: str, season: str, radius: float = 0.42, seed: int = 0) -> gltf.Scene:
    """One boulder."""
    return _finish(name, season, [rock(radius, seed=seed)], threshold=0.5)


def rock_cluster(name: str, season: str, seed: int = 0) -> gltf.Scene:
    """Three rocks of different sizes huddled on one tile."""
    rng = np.random.default_rng(seed)
    spots = ((0.3, 0.62, (-0.14, 0.04)), (0.19, 0.7, (0.27, -0.16)), (0.13, 0.85, (0.16, 0.3)))
    parts = [rock(r, seed=seed + k, squash=sq).rotate_y(rng.uniform(0, 360)).translate(x, 0, z)
             for k, (r, sq, (x, z)) in enumerate(spots)]
    return _finish(name, season, parts, threshold=0.5)


def felled_log(name: str, season: str) -> gltf.Scene:
    """A fallen log."""
    return _finish(name, season, log(), snow_skip=("log_end",), threshold=0.45)


def stump(name: str, season: str) -> gltf.Scene:
    """A cut stump with roots."""
    parts = [mesh.cylinder(0.16, 0.22, "bark", segments=8, radius_top=0.14, caps=False)]
    top = [(0.14 * math.cos(2 * math.pi * k / 8), 0.22, 0.14 * math.sin(2 * math.pi * k / 8)) for k in range(8)]
    uv = [(0.5 + 0.5 * math.cos(2 * math.pi * k / 8), 0.5 + 0.5 * math.sin(2 * math.pi * k / 8)) for k in range(8)]
    parts.append(mesh.from_faces(top, [list(range(8))[::-1]], "log_end", uvs=np.asarray(uv)))
    for k in range(4):
        a = 2 * math.pi * k / 4 + 0.4
        parts.append(mesh.beam((0.1 * math.cos(a), 0.08, 0.1 * math.sin(a)), (0.28 * math.cos(a), 0.0, 0.28 * math.sin(a)),
                               0.06, "bark"))
    return _finish(name, season, parts, threshold=0.5)


def lumber_pile(name: str, season: str) -> gltf.Scene:
    """Fresh-sawn boards stacked in three courses on sticks — building material for the sites."""
    parts = [mesh.box(0.06, 0.05, 0.86, "wood_dark").translate(x, 0, 0) for x in (-0.32, 0.0, 0.32)]
    y = 0.05
    for course in range(3):
        n = 4 if course < 2 else 3
        for k in range(n):
            z = (k - (n - 1) / 2) * 0.19
            parts.append(mesh.box(0.9, 0.05, 0.17, "lumber").translate(0, y, z))
        y += 0.05
        if course < 2:
            parts += [mesh.box(0.04, 0.025, 0.8, "wood_dark").translate(x, y, 0) for x in (-0.32, 0.32)]
            y += 0.025
    return _finish(name, season, parts)


def crate(name: str, season: str, size: float = 0.5) -> gltf.Scene:
    """A wooden shipping crate with a darker frame."""
    h = size
    parts = [mesh.box(size * 0.96, h * 0.96, size * 0.96, "planks").translate(0, h * 0.02, 0)]
    e, t = size / 2, 0.05
    for sx in (-1, 1):
        for sz in (-1, 1):
            parts.append(mesh.box(t, h, t, "wood_dark").translate(sx * (e - t / 2), 0, sz * (e - t / 2)))
    for y in (0.0, h - t):
        parts += [mesh.box(size, t, t, "wood_dark").translate(0, y, z) for z in (-(e - t / 2), e - t / 2)]
        parts += [mesh.box(t, t, size, "wood_dark").translate(x, y, 0) for x in (-(e - t / 2), e - t / 2)]
    for sz in (-1, 1):
        parts.append(mesh.beam((-e + t, t, sz * (e + 0.004)), (e - t, h - t, sz * (e + 0.004)), t * 0.8, "wood_dark",
                               depth=0.012))
    return _finish(name, season, parts)


def barrel(name: str, season: str) -> gltf.Scene:
    """A wooden barrel with iron hoops and a lid."""
    h = 0.56
    prof = [(0.0, 0.0), (0.19, 0.0)] + [(0.19 + 0.04 * math.sin(math.pi * k / 6), h * k / 6) for k in range(1, 6)] + \
        [(0.19, h), (0.0, h)]
    body = mesh.lathe(prof, "staves", segments=12, smooth=False)
    parts = [body]
    for y in (0.06, 0.2, h - 0.2, h - 0.06):
        r = 0.19 + 0.04 * math.sin(math.pi * y / h) + 0.008
        parts.append(mesh.cylinder(r, 0.03, "iron", segments=12, caps=False).translate(0, y - 0.015, 0))
    parts.append(mesh.cylinder(0.17, 0.012, "wood_dark", segments=12).translate(0, h - 0.004, 0))
    return _finish(name, season, parts)


def fence(name: str, season: str) -> gltf.Scene:
    """A split-rail fence segment along X (-0.5..0.5), post at x=-0.5; chains seamlessly."""
    parts = [mesh.box(0.09, 0.62, 0.09, "wood_dark").translate(-0.5, 0, 0),
             mesh.pyramid(0.09, 0.09, 0.04, "wood_dark").translate(-0.5, 0.62, 0)]
    for y, z in ((0.26, 0.0), (0.5, 0.0)):
        parts.append(mesh.beam((-0.5, y, z + 0.055), (0.5, y + 0.01, z + 0.055), 0.06, "bark", depth=0.05))
    return _finish(name, season, parts, threshold=0.5)


def fence_post(name: str, season: str) -> gltf.Scene:
    """The lone post that closes the open end of a fence run."""
    parts = [mesh.box(0.09, 0.62, 0.09, "wood_dark"), mesh.pyramid(0.09, 0.09, 0.04, "wood_dark").translate(0, 0.62, 0)]
    return _finish(name, season, parts, threshold=0.5)


def street_lamp(name: str, season: str) -> gltf.Scene:
    """A Tokyo-style park lamp: green iron pole, curved arm, glowing lantern (emissive + `light` node)."""
    parts = [mesh.cylinder(0.1, 0.12, "lamp_green", segments=8, radius_top=0.07),
             mesh.cylinder(0.04, 1.9, "lamp_green", segments=8, radius_top=0.03).translate(0, 0.12, 0),
             mesh.sphere(0.045, "lamp_green", segments=8, rings=4, smooth=False).translate(0, 2.03, 0)]
    pts = [(0, 1.85), (0.12, 1.98), (0.26, 2.0), (0.36, 1.94)]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        parts.append(mesh.beam((x0, y0, 0), (x1, y1, 0), 0.03, "lamp_green"))
    parts.append(mesh.frustum(0.08, 0.08, 0.2, 0.2, 0.07, "lamp_green").rotate_x(180).translate(0.36, 1.95, 0))
    parts.append(mesh.frustum(0.12, 0.12, 0.08, 0.08, 0.13, "glow").translate(0.36, 1.75, 0))
    parts.append(mesh.box(0.12, 0.02, 0.12, "lamp_green").translate(0.36, 1.73, 0))

    def light(s: gltf.Scene, root: gltf.Node) -> None:
        s.node("light", parent=root, translation=(0.36, 1.8, 0),
               light={"type": "point", "color": "#ffd98a", "intensity": 1.0, "range": 4.0})
    return _finish(name, season, parts, extra_nodes=light)


def traffic_cone(name: str, season: str) -> gltf.Scene:
    """An orange traffic cone with a reflective band (construction-site dressing)."""
    parts = [mesh.box(0.3, 0.03, 0.3, "cone_orange"),
             mesh.cylinder(0.11, 0.14, "cone_orange", segments=10, radius_top=0.085, caps=False).translate(0, 0.03, 0),
             mesh.cylinder(0.085, 0.1, "reflective", segments=10, radius_top=0.064, caps=False).translate(0, 0.17, 0),
             mesh.cylinder(0.064, 0.18, "cone_orange", segments=10, radius_top=0.02).translate(0, 0.27, 0)]
    return _finish(name, season, parts, threshold=0.7)


def bench(name: str, season: str) -> gltf.Scene:
    """A park bench along X (1 tile), facing +Z: iron side frames, wooden seat and back slats."""
    parts = []
    for x in (-0.42, 0.42):
        parts += [mesh.beam((x, 0, 0.14), (x, 0.24, 0.12), 0.04, "iron"),
                  mesh.beam((x, 0, -0.14), (x, 0.24, -0.12), 0.04, "iron"),
                  mesh.beam((x, 0.24, 0.17), (x, 0.24, -0.16), 0.04, "iron"),
                  mesh.beam((x, 0.24, -0.16), (x, 0.56, -0.22), 0.04, "iron")]
    for z in (0.12, 0.03, -0.06, -0.15):
        parts.append(mesh.box(0.98, 0.03, 0.075, "planks").translate(0, 0.26, z))
    for y in (0.36, 0.47):
        parts.append(mesh.box(0.98, 0.07, 0.025, "planks").translate(0, 0, 0).rotate_x(-10).translate(0, y, -0.18 - (y - 0.36) * 0.15))
    return _finish(name, season, parts, threshold=0.7)


def sign_post(name: str, season: str, text: str = "TOKYO") -> gltf.Scene:
    """A wooden trail sign on a post, its board pointing +X, lettered on the +Z face."""
    w, h, t = 0.62, 0.17, 0.035
    parts = [mesh.box(0.07, 1.05, 0.07, "wood_dark"), mesh.pyramid(0.07, 0.07, 0.04, "wood_dark").translate(0, 1.05, 0)]
    x0, y0 = -0.08, 0.8
    parts.append(mesh.box(w, h, t, "planks").translate(x0 + w / 2, y0, 0.05))
    tip = [(x0 + w, y0, 0.05 - t / 2), (x0 + w + 0.08, y0 + h / 2, 0.05 - t / 2), (x0 + w, y0 + h, 0.05 - t / 2),
           (x0 + w, y0, 0.05 + t / 2), (x0 + w + 0.08, y0 + h / 2, 0.05 + t / 2), (x0 + w, y0 + h, 0.05 + t / 2)]
    parts.append(mesh.from_faces(tip, [(3, 4, 5), (2, 1, 0), (0, 1, 4, 3), (1, 2, 5, 4)], "planks"))
    zf = 0.05 + t / 2 + 0.002
    face = [(x0 + 0.01, y0 + 0.01, zf), (x0 + w - 0.01, y0 + 0.01, zf), (x0 + w - 0.01, y0 + h - 0.01, zf),
            (x0 + 0.01, y0 + h - 0.01, zf)]
    parts.append(mesh.from_faces(face, [(0, 1, 2, 3)], "sign_face", uvs=np.array([(0, 1), (1, 1), (1, 0), (0, 0.0)])))
    s = _finish(name, season, parts, snow_skip=("sign_face",), threshold=0.7)
    s.material("sign_face", texture=sign_face(text), roughness=0.85)
    return s


def snowman(name: str, season: str = "winter") -> gltf.Scene:
    """A snowman facing +Z: three snowballs, coal eyes and buttons, carrot nose, stick arms, scarf, top hat."""
    parts = [mesh.sphere(0.3, "snow", segments=12, rings=8).translate(0, 0.27, 0),
             mesh.sphere(0.22, "snow", segments=12, rings=8).translate(0, 0.68, 0),
             mesh.sphere(0.155, "snow", segments=12, rings=8).translate(0, 0.98, 0)]
    for x in (-0.055, 0.055):
        parts.append(mesh.box(0.03, 0.03, 0.02, "coal").translate(x, 1.01, 0.14))
    for y, z in ((0.76, 0.205), (0.66, 0.218), (0.56, 0.2)):
        parts.append(mesh.box(0.035, 0.035, 0.02, "coal").translate(0, y, z))
    parts.append(mesh.cone(0.03, 0.17, "carrot", segments=6).rotate_x(90).translate(0, 0.97, 0.14))
    for sx in (-1, 1):
        parts.append(mesh.beam((sx * 0.18, 0.74, 0), (sx * 0.45, 0.92, 0.02), 0.025, "bark"))
        parts.append(mesh.beam((sx * 0.38, 0.87, 0.015), (sx * 0.44, 0.98, 0.03), 0.018, "bark"))
    parts.append(mesh.cylinder(0.16, 0.06, "scarf", segments=12).translate(0, 0.83, 0))
    parts.append(mesh.box(0.06, 0.2, 0.025, "scarf").rotate_z(12).translate(0.08, 0.66, 0.17))
    parts.append(mesh.cylinder(0.15, 0.02, "black", segments=12).translate(0, 1.1, 0))
    parts.append(mesh.cylinder(0.1, 0.18, "black", segments=12).translate(0, 1.1, 0))
    parts.append(mesh.cylinder(0.102, 0.035, "scarf", segments=12, caps=False).translate(0, 1.13, 0))
    s = gltf.Scene(name, extras={"season": "winter", "kind": "prop"})
    _mats(s)
    s.node(name, meshes=parts)
    return s


def christmas_tree(name: str, season: str = "winter") -> gltf.Scene:
    """The town Christmas tree (2x2): snowy spruce, two blinking light strings, glowing star, presents.

    `twinkle` (1 s, STEP) alternates the `lights_a` / `lights_b` nodes; the
    star and both strings are emissive. Light nodes carry `extras.light`.
    """
    rng = np.random.default_rng(12)
    s = gltf.Scene(name, extras={"season": "winter", "kind": "prop"})
    _mats(s)
    root = s.node(name)
    height, tiers, radius, trunk = 3.0, 6, 0.85, 0.3
    s.node("trunk", parent=root, meshes=[mesh.cylinder(0.1, trunk + 0.3, "bark", segments=8)])
    hc = height - trunk
    crown, lights = [], {"lights_a": [], "lights_b": []}
    for k in range(tiers):
        y = hc * 0.8 * k / tiers
        h = hc - y if k == tiers - 1 else hc * (0.42 - 0.1 * k / tiers)
        r = radius * (1 - 0.72 * k / tiers)
        crown.append(flora.tier(r, h, "needles", segments=10, seed=40 + k).translate(0, trunk + y, 0))
        cap = mesh.lathe([(0.0, h * 0.3), (r * 0.85 * 1.06, h * 0.15), (0.0, h * 1.03)], "snow", segments=10,
                         smooth=False)
        crown.append(terrain.jitter(cap, r * 0.04, seed=60 + k, keep_floor=False).translate(0, trunk + y, 0))
        n = 7 - k // 2
        for j in range(n):
            a = 2 * math.pi * (j + 0.5 * (k % 2)) / n + rng.uniform(-0.15, 0.15)
            f = 0.05
            rr, yy = r * (1 - f) * 1.02, trunk + y + h * f
            mat = ("light_red", "light_gold", "light_blue")[(j + k) % 3]
            bulb = mesh.box(0.06, 0.06, 0.06, mat, centered_y=True).translate(rr * math.cos(a), yy, rr * math.sin(a))
            lights["lights_a" if (j + k) % 2 else "lights_b"].append(bulb)
    s.node("crown", parent=root, meshes=crown)
    nodes = {k: s.node(k, parent=root, meshes=v) for k, v in lights.items()}
    star_pts = [(0.16 * math.cos(math.pi / 2 + k * math.pi / 5) * (1 if k % 2 == 0 else 0.45),
                 0.16 * math.sin(math.pi / 2 + k * math.pi / 5) * (1 if k % 2 == 0 else 0.45)) for k in range(10)]
    star = mesh.extrude(star_pts, 0.04, "star").translate(0, -0.02, 0).rotate_x(90)
    s.node("star", parent=root, meshes=[star], translation=(0, height + 0.1, 0),
           light={"type": "point", "color": "#ffd84a", "intensity": 1.5, "range": 6.0})
    s.node("light", parent=root, translation=(0, 1.4, 0),
           light={"type": "point", "color": "#ffb46a", "intensity": 1.2, "range": 5.0})
    presents = []
    for k, (x, z, w, hh, mat) in enumerate(((0.55, 0.35, 0.26, 0.2, "present_a"), (-0.45, 0.5, 0.2, 0.16, "present_b"),
                                            (0.1, 0.7, 0.3, 0.14, "present_c"), (-0.6, -0.3, 0.22, 0.22, "present_a"))):
        presents.append(mesh.box(w, hh, w, mat).rotate_y(k * 25).translate(x, 0, z))
        presents.append(mesh.box(w + 0.01, hh + 0.01, 0.04, "ribbon").rotate_y(k * 25).translate(x, 0, z))
        presents.append(mesh.box(0.04, hh + 0.01, w + 0.01, "ribbon").rotate_y(k * 25).translate(x, 0, z))
    s.node("presents", parent=root, meshes=presents)
    clip = s.animation("twinkle")
    on, off = (1.0, 1.0, 1.0), (0.4, 0.4, 0.4)
    clip.scale(nodes["lights_a"], [0, 0.5, 1.0], [on, off, on], "STEP")
    clip.scale(nodes["lights_b"], [0, 0.5, 1.0], [off, on, off], "STEP")
    return s


# ------------------------------------------------------------------ catalogue

_ALL = ("summer", "winter")
#: name -> (builder(name, season) -> Scene, seasons with their own file, footprint, title)
PROPS: dict[str, tuple[Callable[[str, str], gltf.Scene], tuple[str, ...], list[int], str]] = {
    "rock": (lambda n, se: boulder(n, se, 0.26, seed=1), _ALL, [1, 1], "Rock"),
    "boulder": (lambda n, se: boulder(n, se, 0.45, seed=2), _ALL, [1, 1], "Boulder"),
    "boulder-large": (lambda n, se: boulder(n, se, 0.9, seed=3), _ALL, [2, 2], "Glacial erratic"),
    "rock-cluster": (lambda n, se: rock_cluster(n, se, seed=4), _ALL, [1, 1], "Rock cluster"),
    "log": (felled_log, _ALL, [1, 1], "Fallen log"),
    "stump": (stump, _ALL, [1, 1], "Tree stump"),
    "lumber-pile": (lumber_pile, _ALL, [1, 1], "Lumber pile"),
    "crate": (crate, _ALL, [1, 1], "Crate"),
    "barrel": (barrel, _ALL, [1, 1], "Barrel"),
    "fence": (fence, _ALL, [1, 1], "Split-rail fence"),
    "fence-post": (fence_post, _ALL, [1, 1], "Fence post"),
    "street-lamp": (street_lamp, _ALL, [1, 1], "Street lamp"),
    "traffic-cone": (traffic_cone, _ALL, [1, 1], "Traffic cone"),
    "bench": (bench, _ALL, [1, 1], "Park bench"),
    "sign-post": (sign_post, _ALL, [1, 1], "Trail sign"),
    "snowman": (snowman, ("winter",), [1, 1], "Snowman"),
    "christmas-tree": (christmas_tree, ("winter",), [2, 2], "Christmas tree"),
}


def prop(name: str, season: str = "summer") -> gltf.Scene:
    """The scene of a catalogue prop in a season (autumn uses the summer model)."""
    fn, seasons, _, _ = PROPS[name]
    if season not in seasons:
        season = "summer" if "summer" in seasons else seasons[0]
    return fn(name, season)


def build_prop(name: str, out_dir: str | Path, *, size: int = 320) -> dict:
    """Package one catalogue prop with its seasonal variants; returns `catalog.validate_package`."""
    fn, seasons, footprint, title = PROPS[name]
    variants = {se: fn(name, se) for se in seasons}
    season_map = {"winter": "winter"} if seasons == ("winter",) else None
    fields = {}
    if name == "fence":
        fields["connects"] = {"axis": "x", "length": 1.0, "post_at": -0.5}
    return terrain.write_package(name, out_dir, variants, category="props", footprint=footprint, size=size,
                                 title=title, seasons=season_map, **fields)


def build_all(out_root: str | Path) -> dict[str, dict]:
    """Package every prop in `PROPS`; returns {name: validate report}."""
    return {n: build_prop(n, out_root) for n in PROPS}
