"""Parametric blocky character rigs: a tree of rigid parts, each pivoting at its joint.

A rig is plain data until `build()` turns it into a `gltf.Scene`: named parts
(nodes) with their joint positions, meshes in each part's *local* frame (the
joint is 0,0,0), props that can be shown and hidden per clip, and a palette.

    from utils import rigs
    body = rigs.Body()                         # builder-sized defaults, 1 unit = 1 tile
    rig = rigs.humanoid("builder", body, colors={"pants": "#33415c"})
    rig.add("head", rigs.block(0.18, 0.02, 0.2, "hat", y=0.16))
    rig.prop("hammer", "hand_r_socket", [rigs.block(0.02, 0.02, 0.2, "wood", z=0.1)], hidden=True)
    scene = rig.build()                        # then motion.standard_clips(rig, ...)

Humanoid hierarchy (every name is a node the game can find):

    <name> (root, at the feet; the game moves this)
      body                 whole-body pivot at the feet: falls, jumps, lunges
        hips               pelvis, at the hip joints
          leg_l / leg_r    thigh, pivot at the hip joint
            shin_l/_r      pivot at the knee
              foot_l/_r    pivot at the ankle
          torso            pivot at the waist
            head           pivot at the neck
            arm_l / arm_r  upper arm, pivot at the shoulder
              forearm_l/_r pivot at the elbow
                hand_l/_r  pivot at the wrist
                  hand_r_socket   empty: tools and weapons parent here

The character faces +Z, so its **left is +X**. Limbs hang along -Y from their
joint. Rotation signs (degrees, `mesh.quat_euler`): negative X swings a limb
forward; positive Z swings the left arm out, negative Z the right arm.

Materials. Colours are drawn from one small palette texture, so a whole
character is at most three draw calls:

- `palette` — every named colour (`skin`, `boots`, …), flat cells of one atlas.
- `team` — clothing the game tints per team: set the material's base colour to
  `TEAM_COLORS[team]` (it ships as TEAM_COLORS[0], untextured, with
  `extras.tint = "team"`). Name a mesh's material `"team"` to put it there.
- `glow` — emissive colours (muzzle flash, angry eyes), listed in `glow=`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

from . import gltf, mesh, texture

#: Scale given to a hidden prop: glTF has no visibility flag, and 0 is singular.
HIDDEN = 1e-3
TEAM, PALETTE, GLOW = "team", "palette", "glow"
#: TEAM_COLORS[0] in the game's config: what the `team` material ships as.
DEFAULT_TEAM_COLOR = "#ff8c1a"

DEFAULT_COLORS = {
    "skin": "#e0b48c", "boots": "#4a3324", "sole": "#26201c", "pants": "#3b4f72", "shirt": "#4a4f5a",
    "gloves": "#c8a46a", "dark": "#1c1c22", "white": "#f2f2ee", "metal": "#4b5058", "steel": "#8d949c",
    "wood": "#8a5a2b", "leather": "#6b4423", "hat": "#ffd400", "reflect": "#e8f7a0", "eye": "#1c1c22",
}


def block(w: float, h: float, d: float, color: str, *, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> mesh.Mesh:
    """A w x h x d box centred on (x, y, z), in material/colour `color`."""
    return mesh.box(w, h, d, color, centered_y=True).translate(x, y, z)


def mirror_x(m: mesh.Mesh) -> mesh.Mesh:
    """Mirror a mesh left/right (X), keeping faces outward — left part to right part."""
    return m.scale(-1, 1, 1)


def _chamfer_ring(y: float, w: float, d: float, c: float, dx: float = 0.0, dz: float = 0.0) -> np.ndarray:
    a, b = w / 2, d / 2
    pts = [(a - c, b), (a, b - c), (a, -b + c), (a - c, -b), (-a + c, -b), (-a, -b + c), (-a, b - c), (-a + c, b)]
    return np.array([(x + dx, y, z + dz) for x, z in pts], dtype=np.float64)


def loft(rings: Sequence[Sequence[float]], color: str, *, caps: bool = True) -> mesh.Mesh:
    """A flat-shaded solid through chamfered-rectangle rings (y, w, d, chamfer[, dx, dz]), listed in order of y."""
    rs = [_chamfer_ring(*r) for r in rings]
    polys: list[tuple[np.ndarray, np.ndarray]] = []
    for r0, r1 in zip(rs, rs[1:]):
        c0, c1 = r0.mean(axis=0), r1.mean(axis=0)
        span = c1[1] - c0[1]
        for i in range(8):
            j = (i + 1) % 8
            quad = np.array([r0[i], r0[j], r1[j], r1[i]])
            cen = quad.mean(axis=0)
            f = 0.5 if abs(span) < 1e-9 else (cen[1] - c0[1]) / span
            polys.append((quad, cen - (c0 + (c1 - c0) * f)))
    if caps:
        lo, hi = (rs[0], rs[-1]) if rings[0][0] < rings[-1][0] else (rs[-1], rs[0])
        polys += [(lo, np.array([0.0, -1.0, 0.0])), (hi, np.array([0.0, 1.0, 0.0]))]
    pos, faces = [], []
    for pts, outward in polys:
        n = np.zeros(3)
        for i in range(len(pts)):  # Newell normal, so any planar polygon winds outward
            p, q = pts[i], pts[(i + 1) % len(pts)]
            n += np.array([(p[1] - q[1]) * (p[2] + q[2]), (p[2] - q[2]) * (p[0] + q[0]), (p[0] - q[0]) * (p[1] + q[1])])
        idx = list(range(len(pos), len(pos) + len(pts)))
        faces.append(idx if np.dot(n, outward) >= 0 else idx[::-1])
        pos += [tuple(v) for v in pts]
    return mesh.from_faces(pos, faces, color)


@dataclass
class Part:
    """One rig part: becomes one glTF node."""

    name: str
    parent: str | None
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    meshes: list[mesh.Mesh] = field(default_factory=list)
    prop: bool = False
    hidden: bool = False

    @property
    def rest_scale(self) -> tuple[float, float, float]:
        """Scale in the rest pose: tiny when the prop starts hidden."""
        return (HIDDEN,) * 3 if self.hidden else (1.0, 1.0, 1.0)


class Rig:
    """A named tree of parts plus palette; `build()` makes the scene, `nodes` maps names to nodes."""

    def __init__(self, name: str, *, colors: dict[str, str] | None = None, glow: Iterable[str] = (),
                 team_color: str = DEFAULT_TEAM_COLOR, kind: str = "custom"):
        self.name = name
        self.kind = kind
        self.colors = {**DEFAULT_COLORS, **(colors or {})}
        self.glow = set(glow)
        self.team_color = team_color
        self.parts: dict[str, Part] = {}
        self.nodes: dict[str, gltf.Node] = {}
        self.scene: gltf.Scene | None = None
        self.spec = None
        #: Clip name -> info (duration, loop, speed, event…) filled in by `motion`.
        self.clip_info: dict[str, dict] = {}
        self.parts[name] = Part(name, None)

    # ------------------------------------------------------------ authoring

    def part(self, name: str, parent: str, translation: Sequence[float] = (0, 0, 0),
             meshes: Sequence[mesh.Mesh] = (), rotation: Sequence[float] = (0, 0, 0, 1)) -> Part:
        """Add a jointed part under `parent`; `translation` is the joint in the parent's frame."""
        if name in self.parts:
            raise ValueError(f"part {name!r} already exists")
        if parent not in self.parts:
            raise KeyError(f"no parent part {parent!r}")
        p = Part(name, parent, tuple(map(float, translation)), tuple(map(float, rotation)), list(meshes))
        self.parts[name] = p
        return p

    def add(self, part: str, *meshes: mesh.Mesh) -> "Rig":
        """Add decoration meshes to a part, in its local frame (joint at the origin)."""
        self.parts[part].meshes.extend(meshes)
        return self

    def prop(self, name: str, parent: str, meshes: Sequence[mesh.Mesh], *, translation: Sequence[float] = (0, 0, 0),
             rotation: Sequence[float] = (0, 0, 0, 1), hidden: bool = False) -> Part:
        """Add a prop (tool, weapon, flash) — clips show or hide it by scale; `hidden` sets its rest state."""
        p = self.part(name, parent, translation, meshes, rotation)
        p.prop, p.hidden = True, hidden
        return p

    def color(self, name: str, hex_color: str, *, glow: bool = False) -> "Rig":
        """Define or change a palette colour (optionally emissive)."""
        self.colors[name] = hex_color
        if glow:
            self.glow.add(name)
        return self

    @property
    def props(self) -> list[str]:
        """Names of the prop parts."""
        return [n for n, p in self.parts.items() if p.prop]

    def children(self, name: str) -> list[str]:
        """Direct child part names."""
        return [n for n, p in self.parts.items() if p.parent == name]

    def joint(self, name: str) -> np.ndarray:
        """Rest-pose position of a part's joint in the root frame."""
        p, pos = self.parts[name], np.zeros(3)
        chain = []
        while p is not None:
            chain.append(p)
            p = self.parts.get(p.parent) if p.parent else None
        for q in reversed(chain):
            pos = pos + np.asarray(q.translation)  # rest rotations only sit on props/leaves
        return pos

    @property
    def triangles(self) -> int:
        """Triangles in all parts' meshes."""
        return sum(m.triangles for p in self.parts.values() for m in p.meshes)

    # ------------------------------------------------------------ building

    def _atlas(self) -> tuple[Image.Image, Image.Image, dict[str, tuple[float, float]]]:
        names = sorted(self.colors)
        n = 4
        while n * n < len(names):
            n *= 2
        cell = 8
        base = np.zeros((n * cell, n * cell, 3))
        emis = np.zeros_like(base)
        uv = {}
        for i, name in enumerate(names):
            r, c = divmod(i, n)
            col = texture.rgb(self.colors[name])
            base[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = col
            if name in self.glow:
                emis[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = col
            uv[name] = ((c + 0.5) / n, (r + 0.5) / n)
        return texture.to_image(base), texture.to_image(emis), uv

    def _paint(self, m: mesh.Mesh, uv: dict[str, tuple[float, float]]) -> mesh.Mesh:
        if m.material == TEAM:
            return m
        if m.material not in uv:
            raise ValueError(f"rig {self.name}: mesh colour {m.material!r} is not in the palette "
                             f"(add it with rig.color(name, '#rrggbb'))")
        out = m.with_material(GLOW if m.material in self.glow else PALETTE)
        out.uvs = np.tile(np.asarray(uv[m.material], dtype=np.float32), (len(out.positions), 1))
        return out

    def build(self, scene: gltf.Scene | None = None, **extras) -> gltf.Scene:
        """Create the materials and nodes in a (new) `gltf.Scene`; fills `self.nodes`."""
        s = scene or gltf.Scene(self.name, extras={"rig": self.kind, **extras})
        base, emis, uv = self._atlas()
        s.material(PALETTE, texture=base, roughness=0.85)
        s.material(GLOW, texture=base, emissive_texture=emis, emissive=(1, 1, 1), roughness=0.6)
        s.material(TEAM, color=self.team_color, roughness=0.8, tint="team")
        self.nodes = {}
        for name, p in self.parts.items():  # dict order is insertion order: parents first
            parent = self.nodes[p.parent] if p.parent else None
            meshes = [self._paint(m, uv) for m in p.meshes]
            kw = {"prop": True} if p.prop else {}
            self.nodes[name] = s.node(name, meshes=meshes, parent=parent, translation=p.translation,
                                      rotation=p.rotation, scale=p.rest_scale, **kw)
        self.scene = s
        return s


# ------------------------------------------------------------------ humanoid


@dataclass
class Body:
    """Proportions of a blocky humanoid, in tiles. Defaults are a builder (~0.8 tall with a hat)."""

    ankle: float = 0.045        # ankle joint above the ground (= foot block height)
    shin: float = 0.15
    thigh: float = 0.15
    leg_w: float = 0.075        # leg thickness, X and Z
    shin_taper: float = 0.92    # shin thickness / leg_w
    hip_spread: float = 0.05    # half the distance between the hip joints
    foot_len: float = 0.14
    foot_w: float = 0.085
    heel: float = 0.035         # foot behind the ankle
    pelvis_h: float = 0.08
    pelvis_w: float = 0.18
    pelvis_d: float = 0.12
    torso_h: float = 0.2
    chest_w: float = 0.2        # torso top width
    waist_w: float = 0.17       # torso bottom width
    torso_d: float = 0.12
    neck: float = 0.015         # gap torso top -> head joint (negative sinks the head in)
    head_h: float = 0.15
    head_w: float = 0.15
    head_d: float = 0.15
    upper_arm: float = 0.13
    forearm: float = 0.12
    arm_w: float = 0.06
    hand: float = 0.06
    shoulder_drop: float = 0.03  # shoulder joint below the torso top
    #: Part -> palette colour (or "team") of the default blocks.
    paint: dict = field(default_factory=lambda: {
        "foot": "boots", "shin": "pants", "thigh": "pants", "pelvis": "pants", "torso": TEAM,
        "upper_arm": "shirt", "forearm": "shirt", "hand": "gloves", "head": "skin"})

    @property
    def leg(self) -> float:
        """Thigh + shin: the leg's reach from hip to ankle."""
        return self.thigh + self.shin

    @property
    def hip_y(self) -> float:
        """Height of the hip joints."""
        return self.ankle + self.shin + self.thigh

    @property
    def waist_y(self) -> float:
        """Height of the waist (torso joint)."""
        return self.hip_y + self.pelvis_h / 2

    @property
    def shoulder_x(self) -> float:
        """Half the distance between the shoulder joints."""
        return self.chest_w / 2 + self.arm_w / 2 - 0.004

    @property
    def height(self) -> float:
        """Top of the head above the ground (hats not included)."""
        return self.waist_y + self.torso_h + self.neck + self.head_h

    def scaled(self, k: float, **changes) -> "Body":
        """Every length times `k`, then any field overrides — a bigger variant in one line."""
        vals = {f.name: getattr(self, f.name) * k for f in fields(self)
                if isinstance(getattr(self, f.name), float) and f.name not in _RATIOS}
        return replace(self, **{**vals, **changes})


#: Body fields that are proportions, not lengths: `scaled` leaves them alone.
_RATIOS = ("shin_taper",)


def _limb(length: float, w: float, d: float, color: str, overlap: float) -> mesh.Mesh:
    """A segment hanging from its joint at the origin down to -length, overlapping past the joint."""
    return mesh.box(w, length + overlap, d, color).translate(0, -length, 0)


def humanoid(name: str, body: Body | None = None, *, colors: dict[str, str] | None = None,
             glow: Iterable[str] = (), team_color: str = DEFAULT_TEAM_COLOR) -> Rig:
    """A blocky humanoid rig (hierarchy in the module doc) with default block meshes per part."""
    b = body or Body()
    pc = b.paint
    rig = Rig(name, colors=colors, glow=glow, team_color=team_color, kind="humanoid")
    rig.spec = b
    rig.part("body", name)
    rig.part("hips", "body", (0, b.hip_y, 0), [block(b.pelvis_w, b.pelvis_h, b.pelvis_d, pc["pelvis"])])
    for side, sx in (("l", 1), ("r", -1)):
        rig.part(f"leg_{side}", "hips", (sx * b.hip_spread, 0, 0),
                 [_limb(b.thigh, b.leg_w, b.leg_w, pc["thigh"], b.pelvis_h * 0.3)])
        # The shin stops short of the ankle (the foot block covers it) so a bent knee
        # does not swing the shin's bottom corners through the floor.
        rig.part(f"shin_{side}", f"leg_{side}", (0, -b.thigh, 0),
                 [_limb(b.shin - b.leg_w * 0.35, b.leg_w * b.shin_taper, b.leg_w * b.shin_taper, pc["shin"],
                        b.leg_w * 0.5)])
        rig.part(f"foot_{side}", f"shin_{side}", (0, -b.shin, 0),
                 [mesh.box(b.foot_w, b.ankle + b.leg_w * 0.42, b.foot_len, pc["foot"])
                  .translate(0, -b.ankle, b.foot_len / 2 - b.heel)])
    rig.part("torso", "hips", (0, b.pelvis_h / 2, 0),
             [mesh.frustum(b.waist_w, b.torso_d, b.chest_w, b.torso_d, b.torso_h, pc["torso"])
              .translate(0, -0.01, 0)])
    rig.part("head", "torso", (0, b.torso_h + b.neck, 0),
             [mesh.box(b.head_w, b.head_h, b.head_d, pc["head"])])
    for side, sx in (("l", 1), ("r", -1)):
        rig.part(f"arm_{side}", "torso", (sx * b.shoulder_x, b.torso_h - b.shoulder_drop, 0),
                 [_limb(b.upper_arm, b.arm_w, b.arm_w, pc["upper_arm"], b.arm_w * 0.5)])
        rig.part(f"forearm_{side}", f"arm_{side}", (0, -b.upper_arm, 0),
                 [_limb(b.forearm, b.arm_w * 0.9, b.arm_w * 0.9, pc["forearm"], b.arm_w * 0.3)])
        rig.part(f"hand_{side}", f"forearm_{side}", (0, -b.forearm, 0),
                 [block(b.hand * 0.9, b.hand, b.hand, pc["hand"], y=-b.hand / 2 + 0.005)])
    rig.part("hand_r_socket", "hand_r", (0, -b.hand * 0.55, 0))
    return rig


# ------------------------------------------------------------------ turret


@dataclass
class TurretSpec:
    """Proportions of an auto turret, in tiles."""

    ring_r: float = 0.34        # sandbag ring radius
    bags: int = 8
    post_h: float = 0.26        # yaw bearing height
    housing: float = 0.24       # housing width/depth
    housing_h: float = 0.14
    barrel_len: float = 0.34
    barrel_r: float = 0.028


def turret(name: str, spec: TurretSpec | None = None, *, colors: dict[str, str] | None = None) -> Rig:
    """An auto turret rig: base → yaw (turns about Y) → pitch (tilts about X) → barrel (recoils on Z) → muzzle_flash."""
    t = spec or TurretSpec()
    rig = Rig(name, colors={"sandbag": "#b39b6b", "sandbag2": "#a18a5c", "olive": "#5d6b4f",
                            "olive_dark": "#4a5642", "flash": "#ffe066", **(colors or {})},
              glow={"flash"}, kind="turret")
    rig.spec = t
    bags = []
    for i in range(t.bags):
        a = 360.0 * i / t.bags
        bag = mesh.frustum(0.13, 0.25, 0.1, 0.21, 0.1, "sandbag" if i % 2 else "sandbag2")
        bags.append(bag.translate(t.ring_r, 0, 0).rotate_y(a))
        top = mesh.frustum(0.12, 0.23, 0.08, 0.18, 0.08, "sandbag2" if i % 2 else "sandbag")
        bags.append(top.translate(t.ring_r - 0.015, 0.1, 0).rotate_y(a + 180.0 / t.bags))
    post = mesh.cylinder(0.05, t.post_h, "metal", segments=8)
    plate = mesh.cylinder(0.16, 0.03, "olive_dark", segments=8)
    feet = [mesh.beam((0, 0.16, 0), (0.15 * math.cos(a), 0.018, 0.15 * math.sin(a)), 0.03, "metal")
            for a in (0.5, 2.6, 4.7)]
    rig.part("base", name, (0, 0, 0), bags + [post, plate] + feet)
    rig.part("yaw", "base", (0, t.post_h, 0),
             [mesh.cylinder(0.09, 0.04, "metal", segments=8),
              block(t.housing, t.housing_h, t.housing, "olive", y=0.04 + t.housing_h / 2),
              block(0.08, 0.1, 0.14, "olive_dark", x=-t.housing / 2 - 0.04, y=0.1),       # ammo box
              block(0.02, 0.02, 0.1, "steel", x=-t.housing / 2 - 0.04, y=0.16),
              block(0.03, 0.12, 0.03, "metal", x=t.housing / 2 - 0.03, y=0.04 + t.housing_h + 0.04),
              block(0.03, 0.12, 0.03, "metal", x=-t.housing / 2 + 0.03, y=0.04 + t.housing_h + 0.04)])
    py = 0.04 + t.housing_h + 0.07
    rig.part("pitch", "yaw", (0, py, 0),
             [block(0.13, 0.09, 0.2, "olive_dark", z=-0.01),
              block(0.06, 0.04, 0.06, "dark", y=0.065, z=-0.04),            # sensor
              block(0.04, 0.025, 0.012, "flash", y=0.065, z=-0.008),        # sensor lens
              block(0.16, 0.025, 0.025, "steel")])                          # trunnion
    rig.part("barrel", "pitch", (0, 0.0, 0.08),
             [mesh.cylinder(t.barrel_r, t.barrel_len, "metal", segments=8).rotate_x(90),
              mesh.cylinder(t.barrel_r * 1.9, 0.08, "olive_dark", segments=8).rotate_x(90),
              block(t.barrel_r * 3, t.barrel_r * 2.2, 0.05, "dark", z=t.barrel_len - 0.02)])
    flash = [mesh.pyramid(0.07, 0.07, 0.14, "flash").rotate_x(90),
             mesh.pyramid(0.14, 0.03, 0.06, "flash").rotate_x(90),
             mesh.pyramid(0.03, 0.14, 0.06, "flash").rotate_x(90)]
    rig.prop("muzzle_flash", "barrel", flash, translation=(0, 0, t.barrel_len + 0.01), hidden=True)
    return rig
