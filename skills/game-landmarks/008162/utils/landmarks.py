"""The game's landmarks, assembled from `blocks` into staged, animated GLB packages.

Each landmark is one GLB whose node tree the game drives:

    <key>                    root, at the footprint centre on the ground
      stage_00 .. stage_NN   construction stages, bottom-up (foundation first);
                             the game shows stage_00..stage_k as work progresses
        <detail nodes>       moving parts (beacon, lantern, sign) live inside the
                             stage that builds them, so they appear with it
      scaffold               scaffolding (and crane) shown while under construction
        crane, crane_jib     the jib slews in the `crane` clip

Footprints are exactly the 2D game's `LANDMARKS` (config.js): its `w` runs
along +X, its `h` along +Z, and the front of each building faces +Z. Heights
follow the 2D boxes (`catalog.px`), somewhat taller and more detailed.

    from utils import landmarks
    report = landmarks.build_landmark("tower", "out/tower")   # -> validate_package report
    landmarks.build_all("out")
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from PIL import Image, ImageDraw

from . import blocks as B
from . import catalog, gltf, mesh, preview
from .mesh import Mesh


@dataclass(frozen=True)
class Spec:
    """A landmark as the game knows it: config key, title, footprint (tiles along X, Z), style."""

    key: str
    title: str
    footprint: tuple[int, int]
    style: str
    #: Height of the tallest 2D part in config.js pixels (for reference / sanity checks).
    px_height: float


SPECS: dict[str, Spec] = {s.key: s for s in [
    Spec("ramen", "Ichiran Ramen Stand", (1, 1), "shop", 30),
    Spec("scramble", "Shibuya Scramble Crossing", (3, 3), "street", 56),
    Spec("109", "Shibuya 109", (2, 2), "modern", 106),
    Spec("sensoji", "Sensō-ji Temple", (3, 3), "temple", 74),
    Spec("station", "Tokyo Station", (4, 2), "brick", 64),
    Spec("tower", "Tokyo Tower", (2, 2), "steel", 195),
    Spec("palace", "Imperial Palace", (4, 3), "palace", 52),
    Spec("shinjuku", "Tokyo Metropolitan Gov. Building", (3, 2), "granite", 152),
    Spec("skytree", "Tokyo Skytree", (2, 2), "skytree", 330),
]}

class Assembly:
    """Builds one landmark scene: stages in order, detail nodes inside stages, scaffold, clips."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.scene = gltf.Scene(spec.key, extras={"kind": "landmark", "key": spec.key, "title": spec.title,
                                                  "footprint": list(spec.footprint)})
        B.define_materials(self.scene, spec.style)
        self.root = self.scene.node(spec.key, title=spec.title, footprint=list(spec.footprint))
        self.stages: list[tuple[gltf.Node, str]] = []
        self.scaffold_node: gltf.Node | None = None

    def stage(self, label: str, *parts: Sequence[Mesh]) -> gltf.Node:
        """Add the next construction stage `stage_NN` holding these meshes."""
        meshes = [m for group in parts for m in group]
        node = self.scene.node(f"stage_{len(self.stages):02d}", meshes=meshes, parent=self.root,
                               label=label, index=len(self.stages))
        self.stages.append((node, label))
        return node

    def detail(self, name: str, meshes: Sequence[Mesh], *, at: Sequence[float], stage: gltf.Node | None = None,
               yaw: float = 0.0) -> gltf.Node:
        """A separately animatable part, pivot at `at`, meshes in its own frame, inside `stage` (default: last)."""
        parent = stage or self.stages[-1][0]
        rot = mesh.quat_euler(y=yaw) if yaw else (0, 0, 0, 1)
        return self.scene.node(name, meshes=list(meshes), parent=parent, translation=at, rotation=rot)

    def scaffold(self, *parts: Sequence[Mesh], crane: tuple[float, float, Sequence[float], float] | None = None) -> gltf.Node:
        """The `scaffold` node; `crane=(height, jib, (x, y, z), yaw)` adds a crane with a slewing jib."""
        node = self.scene.node("scaffold", meshes=[m for g in parts for m in g], parent=self.root,
                               shown="while building")
        self.scaffold_node = node
        if crane:
            h, jib, at, yaw = crane
            B.add_block(self.scene, B.crane(h, jib), "crane", parent=node, translation=at, yaw=yaw)
            jnode = self.scene.find("crane_jib")
            t = [0.0, 6.0, 12.0]
            self.scene.animation("crane").rotate(jnode, t, [mesh.quat_euler(y=a) for a in (-35, 35, -35)])
        return node

    def blink(self, clip: str, nodes: Sequence[gltf.Node], period: float = 1.2, duty: float = 0.5,
              phase: Sequence[float] | None = None) -> None:
        """A STEP on/off clip: each node is lit for `duty` of the period from its `phase` (0-1).

        Off is a scale of 0.02, not 0 — a zero scale makes a singular matrix.
        """
        c = self.scene.animation(clip)
        for i, n in enumerate(nodes):
            ph = (phase[i] if phase else 0.0) % 1.0
            marks = sorted({0.0, ph, (ph + duty) % 1.0})
            state = [((m - ph) % 1.0) < duty - 1e-9 for m in marks]
            times = [m * period for m in marks] + [period]
            vals = [(1, 1, 1) if on else (0.02, 0.02, 0.02) for on in state + state[:1]]
            c.scale(n, times, vals, interpolation="STEP")

    def sway(self, clip: str, nodes: Sequence[gltf.Node], degrees: float = 6.0, period: float = 3.0,
             axis: str = "x") -> None:
        """A gentle looping swing about one local axis (lanterns, curtains, flags)."""
        c = self.scene.animation(clip)
        ts = [period * i / 4 for i in range(5)]
        for i, n in enumerate(nodes):
            angs = [0, degrees, 0, -degrees, 0] if i % 2 == 0 else [0, -degrees * 0.8, 0, degrees * 0.8, 0]
            c.rotate(n, ts, [mesh.quat_euler(**{axis: a}) for a in angs])


def P(block: B.Block, x: float = 0.0, y: float = 0.0, z: float = 0.0, yaw: float = 0.0) -> list[Mesh]:
    """Shorthand: a block's meshes placed in the landmark frame."""
    return block.placed(x, y, z, yaw)


def _hanging(block: B.Block, cord: float = 0.02, cord_mat: str = "roof_trim") -> list[Mesh]:
    """A block hung below its pivot (origin at the hook), with a short cord."""
    h = block.size[1]
    return block.placed(0, -h - cord) + [mesh.box(0.008, cord, 0.008, cord_mat).translate(0, -cord, 0)]


# ------------------------------------------------------------------ the nine landmarks


def _ramen(a: Assembly) -> None:
    y0, H = 0.06, 0.56
    a.stage("foundation", P(B.plinth(0.9, 0.9, y0, steps=1, mat="stone")))
    frame = P(B.slab(0.76, 0.76, 0.03, mat="wood"), 0, y0)
    for sx in (-1, 1):
        for sz in (-1, 1):
            frame += P(B.pillar(H, 0.06, mat="column", base=None), sx * 0.36, y0, sz * 0.36)
    frame += [mesh.box(0.78, 0.05, 0.78, "column").translate(0, y0 + H - 0.05, 0)]
    a.stage("frame", frame)
    walls = P(B.wall(0.66, H - 0.05, 0.05, mat="wall"), 0, y0, -0.355)
    for sx in (-1, 1):
        walls += P(B.window_wall(0.66, H - 0.05, 0.05, cols=2, rows=1, win_w=0.6, win_h=0.42, mat="wall",
                                 frame="wood", lit=0.6, seed=3 + sx), sx * 0.355, y0, 0, yaw=90 * sx)
    walls += [mesh.box(0.66, 0.26, 0.05, "wood").translate(0, y0, 0.355),
              mesh.box(0.76, 0.03, 0.16, "wood_dark").translate(0, y0 + 0.26, 0.37),
              mesh.box(0.66, 0.1, 0.05, "wall").translate(0, y0 + H - 0.15, 0.355),
              mesh.box(0.6, 0.24, 0.14, "steel").translate(0, y0 + 0.03, -0.25),
              mesh.box(0.6, 0.1, 0.1, "wood_dark").translate(0, y0 + 0.36, -0.29)]
    a.stage("walls and counter", walls)
    a.stage("roof", P(B.roof_curved(0.8, 0.8, 0.26, overhang=0.1, lift=0.05, thickness=0.035, gable=True,
                                    mat="roof", trim="roof_trim", steps=4, segments=4), 0, y0 + H))
    front = P(B.sign_panel(0.5, 0.075, depth=0.02, mat="sign", frame="wood_dark"), 0, y0 + H - 0.13, 0.392)
    st = a.stage("curtains and sign", front)
    nor = a.detail("noren", B.noren(0.62, 0.15, panels=3, mat="fabric").placed(0, -0.15), at=(0, y0 + H - 0.135, 0.41),
                   stage=st)
    lan = [a.detail(f"lantern_{s}", _hanging(B.lantern(0.045, 0.11)), at=(sx * 0.43, y0 + H - 0.01, 0.43), stage=st)
           for s, sx in (("l", -1), ("r", 1))]
    a.sway("noren", [nor], degrees=7, period=3.2)
    a.sway("lanterns", lan, degrees=8, period=2.6, axis="z")
    det = []
    for x in (-0.22, 0, 0.22):
        det.append(mesh.cylinder(0.03, 0.2, "accent", segments=8).translate(x, 0, 0.465))
        det.append(mesh.cylinder(0.035, 0.02, "wood_dark", segments=8).translate(x, 0.2, 0.465))
        det.append(mesh.cylinder(0.03, 0.03, "trim", segments=8, radius_top=0.04).translate(x, y0 + 0.29, 0.38))
    det += [mesh.cylinder(0.03, 0.42, "steel", segments=8).translate(0.22, y0 + H + 0.12, -0.2),
            mesh.cylinder(0.045, 0.03, "steel", segments=8).translate(0.22, y0 + H + 0.54, -0.2)]
    det += P(B.sign_panel(0.12, 0.2, depth=0.02, mat="wall_alt", frame="wood_dark", posts=0.08), -0.38, 0, 0.47, yaw=20)
    a.stage("details", det)
    a.scaffold(P(B.scaffold(0.86, 0.86, 0.66, bay=0.3, lift=0.22, pole=0.015, depth=0.06)))


def _scramble(a: Assembly) -> None:
    g = 0.04
    a.stage("road bed", P(B.slab(3, 3, g, mat="ground")))
    walks = []
    for sx in (-1, 1):
        for sz in (-1, 1):
            walks += P(B.slab(0.9, 0.9, 0.025, mat="paving", edge="stone"), sx * 1.05, g, sz * 1.05)
    a.stage("sidewalks", walks)
    y, mk = g, []

    def bar(cx, cz, length, width, yaw):
        return mesh.box(width, 0.004, length, "marking").rotate_y(yaw).translate(cx, y, cz)

    for i in range(10):
        t = -0.54 + 0.12 * i
        for s in (-1, 1):
            mk.append(bar(t, s * 0.76, 0.24, 0.06, 0))
            mk.append(bar(s * 0.76, t, 0.24, 0.06, 90))
    for d in (45, -45):
        r = math.radians(d)
        for k in range(-5, 6):
            if abs(k) < 1:
                continue
            u = 0.1 * k
            cx, cz = u * math.sin(r), u * math.cos(r)
            mk.append(bar(cx, cz, 0.05, 0.22, d))
    for s in (-1, 1):
        for lane in (0.3, -0.3):
            mk.append(bar(lane * s, s * 0.95, 0.03, 0.5, 90))  # stop lines
            mk.append(bar(s * 0.95, lane * s, 0.03, 0.5, 0))
        for zc in (1.12, 1.38):
            mk.append(bar(0, s * zc, 0.14, 0.025, 0))
            mk.append(bar(s * zc, 0, 0.14, 0.025, 90))
    a.stage("road markings", mk)
    bx, bz, by = 1.1, -1.1, g + 0.025
    frame = P(B.slab(0.66, 0.66, 0.04, mat="roof_flat"), bx, by, bz)
    for sx in (-1, 1):
        for sz in (-1, 1):
            frame += P(B.pillar(1.3, 0.05, mat="frame", base=None), bx + sx * 0.3, by, bz + sz * 0.3)
    for k in range(1, 5):
        frame.append(mesh.box(0.64, 0.03, 0.64, "roof_flat").translate(bx, by + 0.26 * k, bz))
    a.stage("building frame", frame)
    a.stage("glass facade", P(B.curtain_wall(0.6, 1.26, 0.6, mullion=0.1, floor=0.13), bx, by + 0.04, bz),
            P(B.roof_flat(0.64, 0.64, parapet=0.04), bx, by + 1.3, bz))
    scr = P(B.sign_panel(0.46, 0.4, depth=0.02, mat="screen"), bx, by + 0.6, bz + 0.33) + \
        P(B.sign_panel(0.46, 0.4, depth=0.02, mat="screen"), bx - 0.33, by + 0.6, bz, yaw=-90)
    sy = by + 1.34
    scr += [mesh.box(0.6, 0.3, 0.6, "frame").translate(bx, sy, bz),
            mesh.box(0.64, 0.025, 0.64, "accent").translate(bx, sy, bz),
            mesh.box(0.64, 0.025, 0.64, "accent").translate(bx, sy + 0.3, bz)]
    st = a.stage("screens and sign", scr)
    sign = a.detail("sign", [mesh.box(0.62, 0.24, 0.62, "sign").translate(0, -0.12, 0)], at=(bx, sy + 0.165, bz), stage=st)
    a.blink("sign", [sign], period=1.6, duty=0.7)
    extra = []
    for x, z, yaw in ((-0.7, -1.45, 0), (-1.45, -0.7, 90), (1.45, 0.7, -90), (0.7, 1.45, 180), (-1.45, 0.7, 90),
                      (0.7, -1.45, 0)):
        extra += P(B.street_lamp(0.34), x, by, z, yaw)
    for x, z in ((-1.25, 1.25), (-0.85, 1.3), (-1.3, 0.85)):
        extra += P(B.tree(0.42, 0.14, kind="round"), x, by, z)
    extra += P(B.slab(0.5, 0.4, 0.02, mat="stone"), -1.1, by, -1.1)
    for sx in (-1, 1):
        for sz in (-1, 1):
            extra += P(B.pillar(0.26, 0.03, mat="frame", base=None), -1.1 + sx * 0.22, by, -1.1 + sz * 0.17)
    extra += P(B.roof_flat(0.56, 0.44, thickness=0.03, parapet=0.0, mat="glass"), -1.1, by + 0.26, -1.1)
    extra += P(B.sign_panel(0.2, 0.06, depth=0.02, mat="lamp"), -1.1, by + 0.3, -0.87)
    extra += P(B.facade_box(0.3, 0.3, 0.3, mat="facade"), 1.15, by, 1.15)
    extra += P(B.roof_flat(0.32, 0.32, parapet=0.02), 1.15, by + 0.3, 1.15)
    st = a.stage("street furniture", extra)
    lights = []
    for i, (sx, sz) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
        yaw = math.degrees(math.atan2(-sx, -sz))
        tl = B.traffic_light(0.32)
        B.add_block(a.scene, tl, f"signal_{i}", parent=st, translation=(sx * 0.66, by, sz * 0.66), yaw=yaw)
        lights.append((a.scene.find(f"signal_{i}_red"), a.scene.find(f"signal_{i}_green"), i % 2))
    a.blink("signals", [l[0] for l in lights] + [l[1] for l in lights], period=4.0,
            phase=[0.5 * p for _, _, p in lights] + [0.5 * (1 - p) for _, _, p in lights])
    a.scaffold(P(B.scaffold(0.66, 0.66, 1.4, bay=0.33, lift=0.28, pole=0.016, depth=0.07), bx, by, bz))


def _shibuya109(a: Assembly) -> None:
    a.stage("foundation", P(B.slab(2, 2, 0.04, mat="paving")))
    pod = [(-0.95, -0.95), (0.95, -0.95), (0.95, 0.25), (0.25, 0.95), (-0.95, 0.95)]
    body = [(-0.72, -0.72), (0.7, -0.72), (0.7, 0.0), (0.0, 0.7), (-0.72, 0.7)]
    y1, y2, ycyl = 0.56, 2.15, 2.95
    a.stage("podium", [mesh.extrude(pod, y1 - 0.04, "facade").translate(0, 0.04, 0),
                       mesh.extrude([(x * 1.01, z * 1.01) for x, z in pod], 0.04, "trim").translate(0, y1 - 0.02, 0)])
    a.stage("main block", [mesh.extrude(body, y2 - y1, "wall").translate(0, y1, 0)])
    cx = cz = 0.3
    r = 0.42
    cyl = [mesh.cylinder(r, ycyl - y1, "wall_alt", segments=20).translate(cx, y1, cz),
           mesh.cylinder(r + 0.01, 0.2, "glass", segments=20).translate(cx, y1 + 0.02, cz)]
    a.stage("cylinder tower", cyl)
    fins = []
    for (x0, z0), (x1, z1) in zip(body, body[1:] + body[:1]):
        L = math.hypot(x1 - x0, z1 - z0)
        n = max(1, int(L / 0.11))
        nx, nz = (z1 - z0) / L, -(x1 - x0) / L
        if nx * (x0 + x1) + nz * (z0 + z1) < 0:
            nx, nz = -nx, -nz
        ang = math.degrees(math.atan2(-(z1 - z0), x1 - x0))
        for i in range(1, n):
            f = i / n
            px_, pz = x0 + (x1 - x0) * f + nx * 0.012, z0 + (z1 - z0) * f + nz * 0.012
            fins.append(mesh.box(0.025, y2 - y1 - 0.1, 0.025, "trim").rotate_y(ang).translate(px_, y1 + 0.05, pz))
    fin = mesh.box(0.03, ycyl - y1 - 0.25, 0.03, "trim").translate(r + 0.005, 0, 0)
    fins.append(mesh.array_around(fin, 24).translate(cx, y1 + 0.24, cz))
    for k in range(3):
        fins.append(mesh.cylinder(r + 0.025, 0.025, "frame", segments=20).translate(cx, y1 + 0.24 + 0.72 * k, cz))
    a.stage("facade fins", fins)
    roof = P(B.roof_flat(1.0, 1.0, parapet=0.06), -0.25, y2, -0.25)
    roof.append(mesh.extrude(body, 0.03, "roof_flat").translate(0, y2, 0))
    roof += [mesh.box(0.3, 0.14, 0.24, "steel").translate(-0.4, y2 + 0.03, -0.35),
             mesh.box(0.2, 0.1, 0.2, "steel").translate(-0.05, y2 + 0.03, -0.45)]
    roof += [mesh.cylinder(r + 0.03, 0.06, "trim", segments=20).translate(cx, ycyl, cz),
             mesh.cylinder(r * 0.9, 0.04, "roof_flat", segments=20).translate(cx, ycyl + 0.06, cz)]
    a.stage("roof and crown", roof)
    sx, sz = cx, cz
    sign = P(B.sign_panel(0.62, 0.52, depth=0.08, mat="frame", frame="trim"), sx, ycyl + 0.1, sz, yaw=45)
    for k in (-1, 1):
        sign.append(mesh.box(0.03, 0.12, 0.03, "frame").translate(sx + k * 0.15, ycyl + 0.02, sz - k * 0.15))
    canopy = mesh.box(0.7, 0.025, 0.25, "frame").rotate_y(45).translate(0.64, 0.46, 0.64)
    sign.append(canopy)
    for i, (x, z) in enumerate(((0.95, -0.6), (-0.6, 0.95), (0.95, -0.2))):
        sign += P(B.street_lamp(0.32), x + 0.03 * (1 if z < 0 else 0), 0.04, z, yaw=90 if z < 0 else 0)
    for x, z, yaw in ((0.96, -0.4, 90), (-0.4, 0.96, 0)):
        sign += P(B.sign_panel(0.3, 0.36, depth=0.015, mat="screen"), x, 0.1, z, yaw)
    st = a.stage("sign and street", sign)
    face = B.sign_panel(0.56, 0.46, depth=0.01, mat="sign", frame="accent").placed(0, -0.23 - 0.02)
    yc = ycyl + 0.1 + 0.02 + 0.26
    off = 0.045 * 0.707
    nodes = [a.detail("sign", face, at=(sx + off, yc, sz + off), yaw=45, stage=st),
             a.detail("sign_back", face, at=(sx - off, yc, sz - off), yaw=225, stage=st)]
    a.blink("sign", nodes, period=2.0, duty=0.75)
    a.scaffold(P(B.scaffold(1.5, 1.5, 1.8, bay=0.37, lift=0.3, pole=0.018, depth=0.08), -0.01, y1, -0.01),
               crane=(2.7, 1.0, (-0.84, y1, 0.84), -45))


def _sensoji(a: Assembly) -> None:
    g = 0.04
    hz = -0.5
    found = P(B.slab(3, 3, g, mat="paving"))
    found += P(B.plinth(2.2, 1.7, 0.18, steps=2, inset=0.04, mat="stone"), 0, g, hz)
    found += P(B.stairs(0.6, 0.18, 0.3, steps=5, mat="stone"), 0, g, hz + 0.85 + 0.15)
    found += P(B.slab(0.3, 0.5, 0.012, mat="stone"), 0, g, 0.88)
    a.stage("foundation and platform", found)
    yb, ch = g + 0.18, 0.5
    st = P(B.colonnade(1.9, ch, 7, size=0.06), 0, yb, hz + 0.72) + P(B.colonnade(1.9, ch, 7, size=0.06), 0, yb, hz - 0.72)
    for sx in (-1, 1):
        st += P(B.colonnade(1.44, ch, 5, size=0.06), sx * 0.92, yb, hz, yaw=90)
    st.append(mesh.box(2.0, 0.06, 1.56, "wood_dark").translate(0, yb + ch, hz))
    a.stage("columns and brackets", st)
    walls = P(B.door_wall(1.6, ch, 0.06, door_w=0.66, door_h=0.36, mat="wall", door="wood_dark", frame="accent"), 0, yb, hz + 0.6)
    walls.append(mesh.box(1.6, ch, 1.15, "wall").translate(0, yb, hz - 0.02))
    walls.append(mesh.box(0.6, 0.08, 0.02, "accent").translate(0, yb + 0.4, hz + 0.64))
    a.stage("walls and doors", walls)
    yr = yb + ch + 0.06
    a.stage("lower roof", P(B.roof_curved(2.0, 1.56, 0.24, overhang=0.2, lift=0.08, thickness=0.04, steps=4), 0, yr, hz))
    yu = yr + 0.17
    up = [mesh.box(1.3, 0.34, 0.86, "wall").translate(0, yu, hz),
          mesh.box(1.38, 0.05, 0.94, "wood_dark").translate(0, yu + 0.32, hz)]
    for sx in (-1, 1):
        for sz in (-1, 1):
            up.append(mesh.box(0.05, 0.34, 0.05, "column").translate(sx * 0.65, yu, hz + sz * 0.43))
    up += [mesh.box(0.9, 0.12, 0.02, "wall_alt").translate(0, yu + 0.14, hz + 0.435)]
    a.stage("upper hall", up)
    a.stage("main roof", P(B.roof_curved(1.4, 0.96, 0.62, overhang=0.26, lift=0.11, thickness=0.05, gable=True,
                                         steps=5, ornaments="accent"), 0, yu + 0.37, hz))
    # five-storey pagoda, front left
    px_, pz = -1.08, 0.98
    pag = P(B.plinth(0.5, 0.5, 0.08, steps=2, inset=0.03, mat="stone"), px_, g, pz)
    y = g + 0.08
    for i, w in enumerate((0.4, 0.36, 0.32, 0.29, 0.26)):
        tier = B.pagoda_tier(w, 0.17 if i == 0 else 0.13, roof_h=0.07, overhang=0.11, lift=0.04,
                             railing_mat="wood_dark" if i else None)
        pag += P(tier, px_, y, pz)
        y += tier.sockets["top"][1]
    pag += P(B.spire(0.5, 0.025, sections=3, mat="bronze", ring="gold"), px_, y - 0.02, pz)
    a.stage("five-storey pagoda", pag)
    gz = 1.16
    gate = P(B.slab(0.86, 0.36, 0.03, mat="stone"), 0, g, gz)
    for s in (-1, 1):
        gate += P(B.colonnade(0.8, 0.46, 4, size=0.05), 0, g + 0.03, gz + s * 0.15)
        gate.append(mesh.box(0.16, 0.4, 0.26, "wall").translate(s * 0.31, g + 0.03, gz))
    gate.append(mesh.box(0.86, 0.05, 0.38, "wood_dark").translate(0, g + 0.49, gz))
    gate += P(B.roof_curved(0.84, 0.36, 0.22, overhang=0.13, lift=0.06, thickness=0.03, gable=True, steps=4,
                            segments=3), 0, g + 0.53, gz)
    st = a.stage("gate", gate)
    lan = a.detail("lantern", _hanging(B.lantern(0.1, 0.25), cord=0.03), at=(0, g + 0.49, gz), stage=st)
    a.sway("lantern", [lan], degrees=3, period=4.0, axis="x")
    det = P(B.incense_burner(0.24), 0, g, 0.8)
    for sx in (-1, 1):
        det += P(B.stone_lantern(0.22), sx * 0.36, g, 0.72)
    for x, z, h in ((1.15, 0.95, 0.5), (1.25, 0.45, 0.42), (1.2, -1.25, 0.55), (-1.25, -1.25, 0.5)):
        det += P(B.tree(h, 0.17, kind="pine"), x, g, z)
    a.stage("incense burner and garden", det)
    a.scaffold(P(B.scaffold(1.98, 1.5, 1.2, bay=0.4, lift=0.3, pole=0.018, depth=0.07), 0, yb, hz),
               crane=(2.2, 1.0, (1.3, g, -0.2), 180))


def _station(a: Assembly) -> None:
    y0 = 0.07
    found = P(B.slab(4.0, 1.8, y0, mat="stone"))
    found += P(B.slab(4.0, 0.1, 0.03, mat="paving"), 0, 0, 0.95)
    a.stage("foundation", found)
    body_d = 1.24
    gf = P(B.window_wall(3.96, 0.3, body_d, cols=24, rows=1, win_w=0.45, win_h=0.62, mat="brick", lit=0.35,
                         seed=1, both=True), 0, y0, 0)
    for sx in (-1, 1):
        gf += P(B.window_wall(body_d, 0.3, 0.02, cols=6, win_w=0.45, win_h=0.62, mat="brick", seed=2), sx * 1.98, y0, 0,
                yaw=90 * sx)
    gf.append(mesh.box(3.98, 0.035, body_d + 0.03, "trim").translate(0, y0 + 0.3, 0))
    a.stage("ground floor", gf)
    y1 = y0 + 0.335
    uf = P(B.window_wall(3.96, 0.6, body_d, cols=24, rows=2, win_w=0.42, win_h=0.6, mat="brick", lit=0.3,
                         seed=4, both=True), 0, y1, 0)
    for sx in (-1, 1):
        uf += P(B.window_wall(body_d, 0.6, 0.02, cols=6, rows=2, win_w=0.42, win_h=0.6, mat="brick", seed=5),
                sx * 1.98, y1, 0, yaw=90 * sx)
    uf += [mesh.box(3.98, 0.025, body_d + 0.025, "trim").translate(0, y1 + 0.29, 0),
           mesh.box(4.02, 0.05, body_d + 0.05, "trim").translate(0, y1 + 0.6, 0)]
    a.stage("upper floors", uf)
    ytop = y1 + 0.65
    pav = []
    for cx, w, d, h in ((-1.45, 0.72, 1.7, 1.2), (1.45, 0.72, 1.7, 1.2), (0.0, 0.62, 1.56, 1.13)):
        hh = h - y0
        pav += P(B.window_wall(w, hh, d, cols=4 if w > 0.7 else 3, rows=3, win_w=0.42, win_h=0.55, mat="brick", seed=int(cx * 10) + 9,
                               both=True), cx, y0, 0)
        for sx in (-1, 1):
            if cx * sx > 0:  # only the outer end of each wing shows; inner sides are inside the main block
                pav += P(B.window_wall(d, hh, 0.02, cols=8, rows=3, win_w=0.42, win_h=0.55, mat="brick", seed=7),
                         cx + sx * w / 2, y0, 0, yaw=90 * sx)
            for sz in (-1, 1):
                pav.append(mesh.box(0.05, hh, 0.05, "trim").translate(cx + sx * (w / 2 - 0.02), y0, sz * (d / 2 - 0.02)))
        for k in (0.33, 0.66):
            pav.append(mesh.box(w + 0.02, 0.025, d + 0.02, "trim").translate(cx, y0 + hh * k, 0))
        pav.append(mesh.box(w + 0.05, 0.05, d + 0.05, "trim").translate(cx, h, 0))
    pav += P(B.door_wall(0.46, 0.3, 0.04, door_w=0.24, door_h=0.24, mat="trim", door="window"), 0, y0, 0.79)
    a.stage("pavilions", pav)
    roofs = []
    for x0, x1 in ((-2.0, -1.81), (-1.09, -0.31), (0.31, 1.09), (1.81, 2.0)):
        L = x1 - x0
        roofs += P(B.roof_mansard(L + 0.02, body_d + 0.04, 0.26, inset=min(0.1, L * 0.3), dormers=2 if L > 0.3 else 0),
                   (x0 + x1) / 2, ytop, 0)
    roofs += P(B.roof_mansard(0.66, 1.6, 0.55, inset=0.14, mat="roof", top="roof"), 0, 1.18, 0)
    for cx in (-1.45, 1.45):
        roofs += P(B.roof_mansard(0.76, 1.74, 0.22, inset=0.12), cx, 1.25, 0)
    a.stage("roofs", roofs)
    domes = []
    for cx in (-1.45, 1.45):
        domes += P(B.roof_dome(0.33, 0.5, sides=8, mat="roof", drum=0.2, drum_mat="brick", finial="copper"), cx, 1.28, 0)
        domes += [mesh.cylinder(0.355, 0.03, "trim", segments=8).translate(cx, 1.46, 0)]
    a.stage("domes", domes)
    det = []
    for sx in (-1, 1):
        for sz in (-1, 1):
            det += P(B.roof_dome(0.05, 0.1, sides=6, mat="copper", drum=0.08, drum_mat="trim", finial="copper"),
                     sx * 0.27, 1.2, sz * 0.7)
    det += P(B.roof_pyramid(0.12, 0.12, 0.18, mat="copper", finial="copper"), 0, 1.66, 0)
    for x in (-1.92, -0.9, -0.5, 0.5, 0.9, 1.92):
        det += P(B.street_lamp(0.26), x, y0, 0.84, 180)
    for x in (-0.9, -0.6, 0.6, 0.9):
        det += [mesh.box(0.06, 0.12, 0.08, "brick").translate(x, ytop + 0.18, -0.25)]
    det += P(B.sign_panel(0.3, 0.06, depth=0.015, mat="lamp", frame="copper"), 0, 0.42, 0.89)
    a.stage("finials and lamps", det)
    a.scaffold(P(B.scaffold(4.0, 1.74, 1.2, bay=0.4, lift=0.3, pole=0.018, depth=0.07), 0, 0, 0),
               crane=(2.0, 1.0, (0.72, ytop, -0.2), 90))


def _tower_band(y: float) -> str:
    """Tokyo Tower's international-orange and white bands, by height (config.js bands)."""
    if y < 2.25:
        return ("steel", "steel_alt", "steel")[min(2, int(y / 0.75))]
    if y < 2.5:
        return "steel_alt"
    if y < 4.69:
        return ("steel", "steel_alt")[min(4, int((y - 2.5) / (2.19 / 5))) % 2]
    if y < 4.9:
        return "steel_alt"
    return ("steel", "steel_alt")[int((y - 4.9) / 0.2) % 2]


def _tower(a: Assembly) -> None:
    yf = 0.07

    def width(y):
        return 0.92 + 0.98 * max(0.0, 1 - (y - yf) / (2.25 - yf)) ** 1.7

    found = P(B.slab(2, 2, 0.03, mat="paving"))
    for sx in (-1, 1):
        for sz in (-1, 1):
            found += P(B.slab(0.24, 0.24, yf, mat="wall"), sx * 0.88, 0, sz * 0.88)
    a.stage("footings", found)
    ft = P(B.facade_box(1.1, 0.34, 1.1, mat="facade", cap="trim"), 0, 0.03, 0)
    ft += P(B.roof_flat(1.12, 1.12, parapet=0.04), 0, 0.37, 0)
    a.stage("foot town", ft)
    lv1 = [yf + (1.1 - yf) * i / 5 for i in range(6)]
    legs = B.lattice_tower([(y, width(y)) for y in lv1], leg=0.075, brace=0.03, mat=_tower_band, cross=False)
    low = P(legs)
    # arches between the legs on each face
    for face in range(4):
        pts = []
        for i in range(11):
            s = -1 + 2 * i / 10
            y = 0.3 + 0.62 * math.sqrt(max(0.0, 1 - s * s))
            hw = width(y) / 2
            u = s * hw * 0.98
            pts.append({0: (u, y, -hw), 1: (hw, y, u), 2: (-u, y, hw), 3: (-hw, y, -u)}[face])
        for p0, p1 in zip(pts, pts[1:]):
            low.append(mesh.beam(p0, p1, 0.045, _tower_band(p0[1])))
        top = 1.1
        hw = width(top) / 2
        for p in pts[3:8:2]:
            q = {0: (p[0], top, -hw), 1: (hw, top, p[2]), 2: (p[0], top, hw), 3: (-hw, top, p[2])}[face]
            low.append(mesh.beam(p, q, 0.022, _tower_band(p[1])))
    a.stage("legs and arches", low)
    lv2 = [1.1 + (2.25 - 1.1) * i / 5 for i in range(6)]
    a.stage("lower lattice", P(B.lattice_tower([(y, width(y)) for y in lv2], leg=0.07, brace=0.025, mat=_tower_band)))
    deck = P(B.deck_box(1.1, 0.25, 1.1, floors=2, glass="glass", trim="steel_alt"), 0, 2.25, 0)
    deck += P(B.railing_ring(1.12, 1.12, 0.05, mat="steel_alt"), 0, 2.52, 0)
    a.stage("main deck", deck)

    def width2(y):
        t = (y - 2.5) / (4.69 - 2.5)
        return 0.8 - 0.52 * t ** 0.9

    lv3 = [2.5 + (4.69 - 2.5) * i / 10 for i in range(11)]
    a.stage("upper lattice", P(B.lattice_tower([(y, width2(y)) for y in lv3[:6]], leg=0.05, brace=0.02,
                                               mat=_tower_band)))
    upper = P(B.lattice_tower([(y, width2(y)) for y in lv3[5:]], leg=0.045, brace=0.018, mat=_tower_band))
    upper += P(B.deck_box(0.42, 0.21, 0.42, floors=1, glass="glass", trim="steel_alt"), 0, 4.69, 0)
    a.stage("top deck", upper)
    ant = P(B.lattice_segment(0.16, 0.09, 0.55, panels=3, leg=0.025, brace=0.012, mat=_tower_band), 0, 4.92, 0)
    ant += P(B.spire(0.66, 0.04, sections=3, mat=_tower_band, ring="steel_alt"), 0, 5.46, 0)
    st = a.stage("antenna", ant)
    bn = a.detail("beacon", B.beacon(0.035).placed(), at=(0, 6.11, 0), stage=st)
    a.blink("beacon", [bn], period=1.5, duty=0.5)
    a.scaffold(P(B.scaffold(1.24, 1.24, 0.45, bay=0.31, lift=0.22, pole=0.016, depth=0.06), 0, 0.03, 0),
               crane=(3.2, 0.9, (0.0, 0, -0.9), 180))


def _palace(a: Assembly) -> None:
    moat = [mesh.box(4.0, 0.025, 0.78, "water").translate(0, 0, 1.11),
            mesh.box(4.0, 0.06, 0.06, "stone").translate(0, 0, 1.47)]
    a.stage("moat", moat)
    hb = 0.34
    a.stage("stone walls", P(B.battered_base(4.0, 2.25, hb, batter=0.14, mat="stone", top="paving"), 0, 0, -0.375))
    bx = -0.9
    br = P(B.bridge_arch(0.84, 0.32, 0.24, arches=2, mat="plinth", rail="plinth"), bx, 0, 1.08, yaw=90)
    for z in (0.7, 1.46):
        for sx in (-1, 1):
            br += P(B.street_lamp(0.22, lamp="lamp"), bx + sx * 0.13, 0.24, z)
    gy = hb + 0.02
    br += [mesh.box(0.5, 0.26, 0.26, "wall").translate(bx, gy, 0.45)]
    br += P(B.door_wall(0.5, 0.26, 0.04, door_w=0.2, door_h=0.18, mat="wall", door="wood_dark", frame="column"),
            bx, gy, 0.59)
    br += P(B.roof_curved(0.5, 0.28, 0.12, overhang=0.08, lift=0.04, thickness=0.025, gable=True, steps=3, segments=3),
            bx, gy + 0.26, 0.47)
    a.stage("bridge and gate", br)
    hx, hz, hl, hd = -0.3, -0.8, 2.2, 0.9
    ys = gy
    hall = P(B.plinth(hl + 0.16, hd + 0.16, 0.06, steps=1, mat="stone"), hx, ys, hz)
    yb = ys + 0.06
    for s in (-1, 1):
        hall += P(B.colonnade(hl, 0.34, 12, size=0.045, round_=False, beam="column", base=None), hx, yb, hz + s * hd / 2)
    hall += P(B.window_wall(hl - 0.1, 0.3, hd - 0.12, cols=16, rows=1, win_w=0.6, win_h=0.5, mat="wall", frame="column",
                            lit=0.4, both=True), hx, yb, hz)
    a.stage("hall structure", hall)
    a.stage("hall roof", P(B.roof_curved(hl, hd, 0.5, overhang=0.24, lift=0.08, thickness=0.04, gable=True, steps=5,
                                         mat="roof", trim="roof_trim"), hx, yb + 0.34, hz))
    tx, tz = 1.45, 0.3
    tur = P(B.window_wall(0.56, 0.26, 0.42, cols=4, rows=1, win_w=0.4, win_h=0.4, mat="wall", frame="roof_trim",
                          lit=0.0, both=True), tx, gy, tz)
    tur += P(B.roof_curved(0.56, 0.42, 0.13, overhang=0.1, lift=0.05, thickness=0.03, steps=3), tx, gy + 0.26, tz)
    tur += P(B.window_wall(0.4, 0.2, 0.3, cols=3, rows=1, win_w=0.4, win_h=0.4, mat="wall", frame="roof_trim",
                           lit=0.0, both=True), tx, gy + 0.33, tz)
    tur += P(B.roof_curved(0.4, 0.3, 0.2, overhang=0.09, lift=0.05, thickness=0.03, gable=True, steps=4, segments=3),
             tx, gy + 0.53, tz)
    for x0, x1 in ((-1.75, -1.17), (-0.63, 1.15)):
        L = x1 - x0
        tur += P(B.wall(L, 0.12, 0.06, mat="wall"), (x0 + x1) / 2, gy, 0.5)
        tur += P(B.roof_gable(L, 0.06, 0.04, overhang=0.025), (x0 + x1) / 2, gy + 0.12, 0.5)
    tur += P(B.wall(0.62, 0.12, 0.06, mat="wall"), 1.75, gy, -0.6, yaw=90)
    tur += P(B.roof_gable(0.62, 0.06, 0.04, overhang=0.025), 1.75, gy + 0.12, -0.6, yaw=90)
    a.stage("corner turret and walls", tur)
    gar = []
    for x, z, h in ((-1.55, 0.15, 0.4), (-1.68, -0.6, 0.45), (1.55, -0.55, 0.42), (1.2, -1.2, 0.5), (-1.55, -1.2, 0.38),
                    (0.0, 0.25, 0.32), (0.6, 0.2, 0.3)):
        gar += P(B.tree(h, 0.16, kind="pine"), x, gy, z)
    for x in (-1.25, -0.55, 0.35, 1.0):
        gar += P(B.stone_lantern(0.16), x, gy, 0.1)
    a.stage("pines and lanterns", gar)
    a.scaffold(P(B.scaffold(hl + 0.2, hd + 0.26, 0.6, bay=0.4, lift=0.3, pole=0.016, depth=0.07), hx, ys, hz),
               P(B.scaffold(0.62, 0.48, 0.75, bay=0.31, lift=0.25, pole=0.014, depth=0.05), tx, gy, tz))


def _shinjuku(a: Assembly) -> None:
    a.stage("plaza", P(B.slab(3, 2, 0.04, mat="paving")))
    y0, ym, yl = 0.04, 1.0, 1.94
    lower = []
    for cx, w, d in ((-0.93, 1.1, 1.9), (0.93, 1.1, 1.9), (0.0, 0.8, 1.4)):
        lower += P(B.facade_box(w, ym - y0, d, mat="facade", cap=None, base="glass", base_h=0.14), cx, y0, 0)
    a.stage("lower block", lower)
    up = []
    for cx, w, d in ((-0.93, 1.1, 1.9), (0.93, 1.1, 1.9), (0.0, 0.8, 1.4)):
        up += P(B.facade_box(w, yl - ym, d, mat="facade", cap="trim"), cx, ym, 0)
    up += P(B.roof_flat(0.8, 1.4, parapet=0.05), 0, yl, 0)
    a.stage("lower block top", up)

    def shaft(cx, y, h, w, d):
        out = P(B.facade_box(w, h, d, mat="facade", cap=None), cx, y, 0)
        for sx in (-1, 1):
            for sz in (-1, 1):
                out.append(mesh.box(0.12, h, 0.12, "wall_alt").translate(cx + sx * (w / 2 - 0.04), y, sz * (d / 2 - 0.04)))
            out.append(mesh.box(0.1, h, 0.03, "glass").translate(cx, y, sx * (d / 2 + 0.01)))
            out.append(mesh.box(0.03, h, 0.1, "glass").translate(cx + sx * (w / 2 + 0.01), y, 0))
        out.append(mesh.box(w + 0.05, 0.04, d + 0.05, "trim").translate(cx, y + h - 0.04, 0))
        return out

    tw = []
    for cx in (-0.85, 0.85):
        tw += shaft(cx, yl, 1.36, 1.08, 1.7)
    a.stage("tower shafts", tw)
    tu = []
    for cx in (-0.85, 0.85):
        tu += shaft(cx, yl + 1.36, 0.8, 0.96, 1.5)
    a.stage("upper towers", tu)
    yc = yl + 2.16
    cr = []
    for cx in (-0.85, 0.85):
        cr += P(B.facade_box(0.86, 0.38, 0.86, mat="facade", cap="trim"), cx, yc, 0, yaw=45)
        cr += P(B.curtain_wall(0.7, 0.16, 0.7, mullion=0.12, floor=0.2), cx, yc + 0.38, 0, yaw=45)
        cr += P(B.roof_flat(0.74, 0.74, parapet=0.04), cx, yc + 0.54, 0, yaw=45)
        for sz in (-1, 1):
            cr += [mesh.box(0.7, 0.14, 0.12, "wall_alt").translate(cx, yc, sz * 0.68)]
    a.stage("rotated crowns", cr)
    top = []
    beacons = []
    for cx in (-0.85, 0.85):
        top += P(B.spire(0.32, 0.025, sections=2, mat="steel", ring="frame"), cx - 0.12, yc + 0.58, 0.1)
        top += [mesh.box(0.2, 0.06, 0.16, "steel").translate(cx + 0.12, yc + 0.58, -0.1)]
    for x, z in ((-1.4, 0.92), (1.4, 0.92), (-0.25, 0.92), (0.25, 0.92)):
        top += P(B.street_lamp(0.26), x, y0, z, 180)
    for x in (-1.25, 1.25):
        top += P(B.tree(0.36, 0.12, kind="round"), x, y0, 0.98)
    st = a.stage("antennas and beacons", top)
    for i, cx in enumerate((-0.85, 0.85)):
        beacons.append(a.detail(f"beacon_{'lr'[i]}", B.beacon(0.025).placed(), at=(cx - 0.12, yc + 0.9, 0.1), stage=st))
    a.blink("beacon", beacons, period=1.6, duty=0.5, phase=[0.0, 0.5])
    a.scaffold(P(B.scaffold(3.0, 2.0, 1.9, bay=0.42, lift=0.32, pole=0.018, depth=0.07), 0, 0, 0),
               crane=(3.0, 0.9, (0.0, yl, 0.0), 90))


def _skytree_ring(y: float, r: float, n: int, morph: float) -> list[tuple[float, float, float]]:
    """Skytree's section: an equilateral triangle at the base easing into a circle higher up."""
    tri = [(math.cos(math.radians(90 + 120 * k)), math.sin(math.radians(90 + 120 * k))) for k in range(3)]
    pts = []
    for i in range(n):
        s = 3 * i / n
        k, f = int(s), s - int(s)
        (x0, z0), (x1, z1) = tri[k % 3], tri[(k + 1) % 3]
        tx, tz = x0 + (x1 - x0) * f, z0 + (z1 - z0) * f
        a = math.radians(90 + 360 * i / n)
        cx, cz = math.cos(a), math.sin(a)
        # scale the triangle so its sides do not cave in when blended with the circle
        x = tx * (1 - morph) + cx * morph
        z = tz * (1 - morph) + cz * morph
        pts.append((r * x, y, r * z))
    return pts


def _skytree(a: Assembly) -> None:
    yf, yd = 0.08, 6.25
    found = P(B.slab(2, 2, 0.03, mat="paving"))
    for p in _skytree_ring(0, 0.92, 3, 0):
        found += P(B.slab(0.26, 0.26, yf, mat="wall"), p[0], 0, p[2])
    found += [mesh.cylinder(0.26, yf, "wall", segments=12)]
    a.stage("foundations", found)

    def radius(y):
        return 0.27 + 0.65 * max(0.0, 1 - (y - yf) / (yd - yf)) ** 1.5

    def morph(y):
        return min(1.0, max(0.0, (y - yf) / (yd - yf))) ** 0.8

    shape = lambda y, w, n: _skytree_ring(y, w, 9, morph(y))  # noqa: E731
    levels = [yf + (yd - yf) * i / 14 for i in range(15)]
    core_r = 0.15
    labels = ["lower legs", "middle lattice", "upper lattice and core"]
    for part in range(3):
        lv = levels[part * 5: part * 5 + 6] if part < 2 else levels[10:]
        meshes = P(B.lattice_tower([(y, radius(y)) for y in lv], sides=9, shape=shape, leg=0.06 - 0.01 * part,
                                   brace=0.024, mat="steel"))
        y0, y1 = lv[0], lv[-1]
        meshes.append(mesh.cylinder(core_r, y1 - y0, "steel_alt", segments=12).translate(0, y0, 0))
        a.stage(labels[part], meshes)
    deck = P(B.deck_ring(0.48, 0.48, sides=18, levels=3, glass="glass", trim="trim", flare=0.42), 0, yd, 0)
    a.stage("tembo deck", deck)
    yu0, yu1 = yd + 0.48, 8.19
    lv = [yu0 + (yu1 - yu0) * i / 5 for i in range(6)]
    ring = lambda y, w, n: _skytree_ring(y, w, 9, 1.0)  # noqa: E731
    upper = P(B.lattice_tower([(y, 0.24 - 0.06 * (y - yu0) / (yu1 - yu0)) for y in lv], sides=9, shape=ring,
                              leg=0.035, brace=0.016, mat="steel"))
    upper.append(mesh.cylinder(0.12, yu1 - yu0, "steel_alt", segments=12).translate(0, yu0, 0))
    a.stage("upper lattice", upper)
    gal = P(B.deck_ring(0.3, 0.26, sides=16, levels=1, glass="glass", trim="trim", flare=0.4), 0, yu1, 0)
    a.stage("tembo galleria", gal)
    ys = yu1 + 0.26
    sp = P(B.lattice_segment(0.2, 0.12, 0.7, panels=3, leg=0.025, brace=0.012, mat="steel"), 0, ys, 0)
    sp.append(mesh.cylinder(0.07, 0.7, "steel_alt", segments=10, radius_top=0.05).translate(0, ys, 0))
    sp += P(B.spire(1.15, 0.05, sections=3, mat="steel", ring="steel_alt"), 0, ys + 0.7, 0)
    st = a.stage("antenna mast", sp)
    bn = a.detail("beacon", B.beacon(0.03).placed(), at=(0, ys + 1.85, 0), stage=st)
    a.blink("beacon", [bn], period=1.5, duty=0.5)
    a.scaffold(P(B.scaffold(0.5, 0.5, 1.4, bay=0.25, lift=0.28, pole=0.016, depth=0.06), 0, yf, 0),
               crane=(3.4, 0.6, (0.8, 0, 0.75), 135))


BUILDERS: dict[str, Callable[[Assembly], None]] = {
    "ramen": _ramen, "scramble": _scramble, "109": _shibuya109, "sensoji": _sensoji, "station": _station,
    "tower": _tower, "palace": _palace, "shinjuku": _shinjuku, "skytree": _skytree,
}


# ------------------------------------------------------------------ build, preview, package


def build_scene(key: str) -> Assembly:
    """Assemble landmark `key` (a config.js LANDMARKS key) into a scene, without writing it."""
    spec = SPECS[key]
    a = Assembly(spec)
    BUILDERS[key](a)
    labels = [lab for _, lab in a.stages]
    a.scene.extras.update(stages=len(a.stages), stage_labels=labels, scaffold="scaffold",
                          clips=[c.name for c in a.scene.clips])
    a.root.extras.update(stages=len(a.stages), stage_labels=labels)
    return a


def _sheet(images: Sequence[Image.Image], labels: Sequence[str], cols: int) -> Image.Image:
    w, h = images[0].size
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (w * cols, (h + 18) * rows), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for i, (img, lab) in enumerate(zip(images, labels)):
        x, y = (i % cols) * w, (i // cols) * (h + 18)
        sheet.paste(img, (x, y + 18))
        draw.text((x + 4, y + 3), lab, fill=(40, 40, 40))
    return sheet


def _bounds(g: gltf.Gltf, hidden: Sequence[str] = ()) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for p, _, _, _ in g.triangles(hidden=hidden):
        if len(p):
            lo, hi = np.minimum(lo, p.reshape(-1, 3).min(0)), np.maximum(hi, p.reshape(-1, 3).max(0))
    return lo, hi


def _subtree_bounds(g: gltf.Gltf, names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    nodes = g.doc["nodes"]
    want: set[int] = set()

    def mark(i):
        want.add(i)
        for c in nodes[i].get("children", []):
            mark(c)

    for n in names:
        mark(g.node_index(n))
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for i, world in g.pose():
        if i in want and "mesh" in nodes[i]:
            for prim in g.doc["meshes"][nodes[i]["mesh"]]["primitives"]:
                p = g.accessor(prim["attributes"]["POSITION"]).astype(np.float64)
                pw = (np.c_[p, np.ones(len(p))] @ world.T)[:, :3]
                lo, hi = np.minimum(lo, pw.min(0)), np.maximum(hi, pw.max(0))
    return lo, hi


def _lowered(glb: str | Path, dy: float, dest: Path) -> Path:
    """A copy of `glb` moved down by `dy`, for close-ups: `preview.render` always frames from y=0 up."""
    g = gltf.read(glb)
    doc = json.loads(json.dumps(g.doc))
    for r in doc["scenes"][doc.get("scene", 0)]["nodes"]:
        t = list(doc["nodes"][r].get("translation", [0.0, 0.0, 0.0]))
        t[1] -= dy
        doc["nodes"][r]["translation"] = t
    dest.write_bytes(gltf._pack(doc, g.blob))
    return dest


def render_previews(glb: str | Path, out_dir: str | Path) -> list[Path]:
    """Write a landmark's previews: render, turntable, stage_strip, construction, top and clip close-ups."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    g = gltf.read(glb)
    names = [n.get("name", "") for n in g.doc["nodes"]]
    stages = sorted(n for n in names if n.startswith("stage_"))
    hide = ["scaffold"] if "scaffold" in names else []
    written = []
    preview.render(glb, out / "render.png", size=640, hidden=hide)
    written.append(out / "render.png")
    imgs = [preview.render(glb, size=480, yaw=y, hidden=hide, floor=False) for y in (45, 225)]
    _sheet(imgs, [f"close-up, yaw {y}" for y in (45, 225)], 2).save(out / "closeup.png")
    written.append(out / "closeup.png")
    yaws = (45, 135, 225, 315)
    imgs = [preview.render(glb, size=360, yaw=y, hidden=hide) for y in yaws]
    _sheet(imgs, [f"yaw {y}" for y in yaws], 4).save(out / "turntable.png")
    written.append(out / "turntable.png")
    frame = _bounds(g, hide)
    labels = {n["name"]: n.get("extras", {}).get("label", "") for n in g.doc["nodes"] if n.get("name") in stages}
    imgs, labs = [], []
    for k in range(1, len(stages) + 1):
        imgs.append(preview.render(glb, size=260, hidden=list(stages[k:]) + hide, frame=frame))
        labs.append(f"{k}/{len(stages)} {labels[stages[k - 1]]}")
    _sheet(imgs, labs, min(5, len(imgs))).save(out / "stage_strip.png")
    written.append(out / "stage_strip.png")
    if hide:
        mid = max(1, len(stages) // 2)
        full = _bounds(g)
        imgs = [preview.render(glb, size=360, yaw=y, hidden=list(stages[mid:]), frame=full) for y in (45, 225)]
        _sheet(imgs, [f"stage {mid}/{len(stages)} with scaffold, yaw {y}" for y in (45, 225)], 2).save(out / "construction.png")
        written.append(out / "construction.png")
    lo, hi = frame
    if hi[1] > 2.5 * max(hi[0] - lo[0], hi[2] - lo[2]) / 2:
        tlo = np.array([lo[0], hi[1] * 0.62, lo[2]])
        low = _lowered(glb, tlo[1], out / "_lowered.glb")
        imgs = [preview.render(low, size=360, yaw=y, hidden=hide, frame=(tlo - [0, tlo[1], 0], hi - [0, tlo[1], 0]),
                               floor=False) for y in (45, 225)]
        _sheet(imgs, [f"upper part, yaw {y}" for y in (45, 225)], 2).save(out / "top.png")
        written.append(out / "top.png")
    for clip in g.clip_names():
        a = next(x for x in g.doc["animations"] if x.get("name") == clip)
        targets = sorted({names[ch["target"]["node"]] for ch in a["channels"]})
        clo, chi = _subtree_bounds(g, targets)
        pad = max(0.25, float((chi - clo).max()) * 0.6)
        box = (clo - pad, chi + pad)
        dur = max(float(g.accessor(a["samplers"][c["sampler"]]["input"])[-1, 0]) for c in a["channels"])
        times = [dur * i / 4 + 1e-3 for i in range(4)]
        hid = [] if clip == "crane" else hide
        dy = max(0.0, float(box[0][1]))
        low = _lowered(glb, dy, out / "_lowered.glb")
        shift = np.array([0, dy, 0])
        imgs = [preview.render(low, size=200, clip=clip, time=t, hidden=hid, frame=(box[0] - shift, box[1] - shift),
                               floor=False) for t in times]
        _sheet(imgs, [f"{clip} {t:.2f}s" for t in times], 4).save(out / f"clip_{clip}.png")
        written.append(out / f"clip_{clip}.png")
    (out / "_lowered.glb").unlink(missing_ok=True)
    return written


def build_landmark(key: str, out_dir: str | Path, *, previews: bool = True) -> dict:
    """Build landmark `key` as a package in `out_dir` (GLB, previews/, manifest); returns `validate_package`."""
    spec = SPECS[key]
    pkg = Path(out_dir)
    pkg.mkdir(parents=True, exist_ok=True)
    a = build_scene(key)
    glb = a.scene.write(pkg / f"{key}.glb")
    rep = gltf.validate(glb, max_triangles=catalog.TRIANGLE_BUDGET["landmark"])
    if previews:
        render_previews(glb, pkg / "previews")
    detail = sorted(n.name for n in a.scene.nodes if n.name not in (key, "scaffold")
                    and not n.name.startswith("stage_") and any(n is ch[0] for c in a.scene.clips for ch in c.channels))
    catalog.write_manifest(pkg, name=key, kind="landmark", files=[f"{key}.glb"], title=spec.title,
                           footprint=list(spec.footprint), stages=len(a.stages),
                           stage_labels=[lab for _, lab in a.stages], height=round(float(_bounds(gltf.read(glb), ["scaffold"])[1][1]), 3),
                           triangles=rep["triangles"], style=spec.style, scaffold="scaffold",
                           animated_parts=detail, clips=rep["clips"], front="+Z",
                           config_px_height=spec.px_height)
    return catalog.validate_package(pkg)


def build_all(out_root: str | Path, *, keys: Sequence[str] | None = None, previews: bool = True) -> dict[str, dict]:
    """Build every landmark (or `keys`) into `out_root/<key>/`; returns {key: validate_package report}."""
    root = Path(out_root)
    return {k: build_landmark(k, root / k, previews=previews) for k in (keys or SPECS)}
