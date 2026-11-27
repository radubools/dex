"""The Many Builders cast as animated GLB packages: builder, foreman, clone, boss, turret.

    from utils import units
    report = units.build_unit("builder", "out/builder")   # GLB + previews + manifest, validated
    reports = units.build_all("out")                       # every unit, one package each

Sizes come from the game: the 2D sprites' pixel heights (`catalog.px`) and
the unit radii in `src/config.js`; speeds and cooldowns are read from that
file when it exists (`game_config()`), so walk cycles and attack loops follow
rebalancing. Re-run `build_all` after changing speeds.

The enemies are an original creature, the **Grump**: a squat, barrel-bodied,
frost-furred grouch of the northern muskeg, with an underbite, two blunt
tusks, a cold red nose, a furious unibrow and a hand-me-down toque. The
`clone` is a Grump; the `boss` is the Grump Elder — three times the size,
horned, ice-crusted, with glowing eyes and a ground `slam`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from . import catalog, gltf, mesh, motion, preview, rigs
from .rigs import block, mirror_x

#: Many Builders' tunables file, read for speeds, cooldowns and radii. This
#: module is that game's cast, so it names the game; nothing here exports.
CONFIG_JS = Path.home() / "games" / "many-builders" / "src" / "config.js"
#: Fallbacks, copied from config.js — used when the file is not there.
DEFAULT_CONFIG = {
    "BUILDER": {"speed": 2.4, "attackCooldown": 1.1, "attackRange": 3, "radius": 0.22, "hp": 100},
    "CLONE": {"speed": 1.5, "attackCooldown": 1.0, "reach": 0.8, "radius": 0.28, "hp": 70},
    "BOSS": {"speed": 1.15, "attackCooldown": 1.0, "slamCooldown": 2.8, "slamRadius": 2.2, "reach": 1.4,
             "radius": 0.9, "hp": 2600},
    "TURRET": {"cooldown": 0.5, "range": 5, "hp": 300},
}
#: What each material is for, written into the manifest (only the materials a model uses).
MATERIAL_NOTES = {
    rigs.TEAM: "clothing the game tints per team: set this material's base colour to TEAM_COLORS[team] "
               "(ships as TEAM_COLORS[0]); clone the material per team, not per unit",
    rigs.PALETTE: "flat colours from one small atlas; never tinted",
    rigs.GLOW: "emissive parts (muzzle flash, glowing eyes); scale emissive for effect",
}


def game_config(path: str | Path = CONFIG_JS) -> dict[str, dict[str, float]]:
    """Numeric fields of BUILDER, CLONE, BOSS and TURRET from the game's config.js (defaults if absent)."""
    cfg = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    try:
        src = Path(path).read_text()
    except OSError:
        return cfg
    for name in cfg:
        m = re.search(rf"export const {name}\s*=\s*\{{(.*?)\}};", src, re.S)
        if m:
            body = re.sub(r"//[^\n]*", "", m.group(1))
            for key, val in re.findall(r"(\w+)\s*:\s*(-?\d+(?:\.\d+)?)", body):
                cfg[name][key] = float(val)
    return cfg


# ------------------------------------------------------------------ props


def shotgun_mesh() -> list[mesh.Mesh]:
    """A pump shotgun along +Z, top +Y, origin at the grip; muzzle at z = 0.26."""
    return [block(0.03, 0.045, 0.1, "wood", y=-0.006, z=-0.07),        # stock
            block(0.022, 0.04, 0.035, "wood", y=-0.026, z=-0.012),     # grip
            block(0.032, 0.042, 0.085, "metal", y=0.012, z=0.02),      # receiver
            block(0.022, 0.022, 0.2, "dark", y=0.022, z=0.16),         # barrel
            block(0.03, 0.026, 0.075, "wood", y=-0.004, z=0.13)]       # pump


def hammer_mesh() -> list[mesh.Mesh]:
    """A claw hammer continuing the arm (-Y from the grip), striking face toward -Z."""
    return [block(0.02, 0.2, 0.02, "wood", y=-0.07),
            block(0.04, 0.04, 0.075, "steel", y=-0.165, z=-0.01),
            block(0.022, 0.024, 0.05, "steel", y=-0.158, z=0.045).rotate_x(-12)]


def flash_mesh(size: float = 1.0) -> list[mesh.Mesh]:
    """A muzzle-flash star pointing along -Y (the barrel of a hand-held gun)."""
    s = size
    return [mesh.pyramid(0.06 * s, 0.06 * s, 0.13 * s, "flash").rotate_x(180),
            mesh.pyramid(0.13 * s, 0.025 * s, 0.05 * s, "flash").rotate_x(180),
            mesh.pyramid(0.025 * s, 0.13 * s, 0.05 * s, "flash").rotate_x(180)]


# ------------------------------------------------------------------ builders


def builder_rig(name: str = "builder", *, foreman: bool = False) -> rigs.Rig:
    """The construction worker: hard hat, hi-vis `team` vest, tool belt, shotgun; foreman variant."""
    b = rigs.Body()
    colors = {"hat": "#ffd400", "hat_dark": "#e6b800", "hair": "#4a3426", "nose": "#d39a72", "pouch": "#5a3a1e",
              "flash": "#ffe066", "board": "#9a6a3a"}
    if foreman:
        b = rigs.Body(chest_w=0.215, waist_w=0.215, torso_d=0.14, pelvis_w=0.19, pelvis_d=0.13)
        colors.update({"hat": "#f4f4f0", "hat_dark": "#c9ccd2", "hair": "#8a8a8a", "shirt": "#6b3a3a"})
    rig = rigs.humanoid(name, b, colors=colors, glow={"flash"})
    H, W, D = b.head_h, b.head_w, b.head_d
    th, td = b.torso_h, b.torso_d
    # Head: face, ears, hair at the back, hard hat.
    rig.add("head",
            block(0.024, 0.03, 0.01, "eye", x=0.036, y=H * 0.55, z=D / 2 + 0.003),
            block(0.024, 0.03, 0.01, "eye", x=-0.036, y=H * 0.55, z=D / 2 + 0.003),
            block(0.03, 0.035, 0.03, "nose", y=H * 0.38, z=D / 2 + 0.012),
            block(0.02, 0.04, 0.03, "skin", x=W / 2 + 0.008, y=H * 0.5),
            block(0.02, 0.04, 0.03, "skin", x=-W / 2 - 0.008, y=H * 0.5),
            block(W + 0.008, 0.06, 0.03, "hair", y=H * 0.62, z=-D / 2 + 0.01),
            mesh.frustum(W + 0.03, D + 0.03, W * 0.72, D * 0.72, 0.06, "hat").translate(0, H - 0.014, 0),
            block(0.03, 0.014, D * 0.8, "hat_dark", y=H + 0.05))
    if foreman:  # white hat with a short cap-brim at the front, moustache
        rig.add("head", block(W + 0.04, 0.012, 0.06, "hat", y=H - 0.008, z=D / 2 + 0.02),
                block(W + 0.035, 0.012, D + 0.035, "hat_dark", y=H - 0.006),
                block(0.08, 0.02, 0.016, "hair", y=H * 0.28, z=D / 2 + 0.008))
    else:        # full brim, stubble beard
        rig.add("head", block(W + 0.06, 0.012, D + 0.08, "hat", y=H - 0.008, z=0.012),
                block(W * 0.78, 0.035, 0.012, "hair", y=0.022, z=D / 2 + 0.004))
    # Torso: the vest is the `team` frustum; shirt shows at the front opening, reflective bands.
    rig.add("torso",
            block(0.06, 0.03, 0.06, "skin", y=th),
            block(0.1, 0.025, td * 0.8, "shirt", y=th - 0.004),
            block(0.04, th * 0.92, 0.006, "shirt", y=th * 0.5, z=td / 2 + 0.002),
            block(b.chest_w * 0.95, 0.022, td + 0.008, "reflect", y=th * 0.38),
            block(0.022, th * 0.9, 0.006, "reflect", x=0.05, y=th * 0.5, z=td / 2 + 0.003),
            block(0.022, th * 0.9, 0.006, "reflect", x=-0.05, y=th * 0.5, z=td / 2 + 0.003),
            block(0.022, th * 0.9, 0.006, "reflect", x=0.045, y=th * 0.5, z=-td / 2 - 0.003),
            block(0.022, th * 0.9, 0.006, "reflect", x=-0.045, y=th * 0.5, z=-td / 2 - 0.003),
            mesh.beam((0.075, th - 0.01, -td / 2 - 0.006), (-0.07, 0.02, -td / 2 - 0.006), 0.014, "leather",
                      depth=0.006))
    # Tool belt with pouches and a buckle.
    pw, pd, ph = b.pelvis_w, b.pelvis_d, b.pelvis_h
    rig.add("hips",
            block(pw + 0.012, 0.028, pd + 0.012, "leather", y=ph / 2 - 0.016),
            block(0.026, 0.022, 0.006, "steel", y=ph / 2 - 0.016, z=pd / 2 + 0.008),
            block(0.04, 0.055, 0.05, "pouch", x=pw / 2 + 0.012, y=0.0, z=0.015),
            block(0.04, 0.055, 0.05, "pouch", x=-pw / 2 - 0.012, y=0.0, z=0.015),
            block(0.012, 0.05, 0.012, "steel", x=-pw / 2 - 0.012, y=0.03, z=0.03))
    for side in ("l", "r"):
        rig.add(f"foot_{side}", block(b.foot_w + 0.004, 0.014, b.foot_len + 0.006, "sole",
                                      y=-b.ankle + 0.007, z=b.foot_len / 2 - b.heel))
        rig.add(f"arm_{side}", block(b.arm_w + 0.006, 0.04, b.arm_w + 0.006, rigs.TEAM, y=-0.012))
    # Shotgun: slung across the back by default, in the right hand while attacking.
    back = [m.rotate_x(-90).rotate_z(-35).translate(0.0, th * 0.5, -td / 2 - 0.022) for m in shotgun_mesh()]
    rig.prop("shotgun_back", "torso", back)
    rig.prop("shotgun", "hand_r_socket", [m.rotate_x(90) for m in shotgun_mesh()],
             translation=(0, 0.005, 0.0), hidden=True)
    rig.prop("muzzle_flash", "shotgun", flash_mesh(), translation=(0, -0.27, 0.022), hidden=True)
    if foreman:
        board = [block(0.012, 0.16, 0.12, "board", y=-0.04, z=0.03),
                 block(0.015, 0.13, 0.1, "white", y=-0.05, z=0.03),
                 block(0.018, 0.02, 0.045, "steel", y=0.035, z=0.03)]
        rig.prop("clipboard", "hand_l", board, translation=(0.035, -0.03, 0.0))
    else:
        rig.prop("hammer", "hand_r_socket", hammer_mesh(), hidden=True)
    return rig


# ------------------------------------------------------------------ the Grump


GRUMP_COLORS = {"fur": "#7383aa", "fur_dark": "#4f5b80", "belly": "#dfe6f1", "nose": "#d8433b",
                "toque": "#c0392b", "toque_band": "#efe6d2", "tusk": "#f1e8cf", "brow": "#262a38",
                "eye_white": "#f2e6a0", "scarf": "#5c8a3a"}
ELDER_COLORS = {"fur": "#555f84", "fur_dark": "#353d5a", "belly": "#c9d4e6", "nose": "#6e2a3c",
                "toque": "#7d1f1a", "toque_band": "#b9ae96", "eye_glow": "#ff3b2f", "horn": "#d8c9a8",
                "ice": "#bfe6ff", "snow": "#f4f8ff", "scar": "#3a2f3f"}


def grump_body() -> rigs.Body:
    """Proportions of a Grump (the clone): squat, wide, tiny legs and stubby arms."""
    return rigs.Body(ankle=0.035, shin=0.06, thigh=0.065, leg_w=0.12, shin_taper=0.8, hip_spread=0.1, foot_len=0.2, foot_w=0.13,
                     heel=0.05, pelvis_h=0.09, pelvis_w=0.36, pelvis_d=0.3, torso_h=0.25, chest_w=0.42,
                     waist_w=0.46, torso_d=0.36, neck=-0.04, head_h=0.24, head_w=0.38, head_d=0.32,
                     upper_arm=0.1, forearm=0.085, arm_w=0.1, hand=0.1, shoulder_drop=0.06,
                     paint={"foot": "fur_dark", "shin": "fur", "thigh": "fur", "pelvis": "fur", "torso": "fur",
                            "upper_arm": "fur", "forearm": "fur", "hand": "fur_dark", "head": "fur"})


def grump_rig(name: str = "clone", *, elder: bool = False) -> rigs.Rig:
    """A Grump (clone), or with `elder` the boss: 3x, long gorilla arms, horns, glowing eyes, ice spikes."""
    k = 2.8 if elder else 1.0
    base = grump_body()
    b = base.scaled(k, thigh=0.085 * k, shin=0.08 * k, upper_arm=0.12 * k, forearm=0.1 * k, hand=0.12 * k,
                    arm_w=0.14 * k) if elder else base
    rig = rigs.humanoid(name, b, colors={**GRUMP_COLORS, **(ELDER_COLORS if elder else {})},
                        glow={"eye_glow"} if elder else ())

    def at(*ms: mesh.Mesh) -> list[mesh.Mesh]:  # author at Grump scale, scale to this body
        return [m.scale(k) for m in ms]

    # Face: underbite jaw, tusks, red nose, angry eyes under a V unibrow, ear tufts, toque.
    eye = "eye_glow" if elder else "eye_white"
    brow = 26.0 if elder else 18.0
    tusk_h = 0.1 if elder else 0.07
    face = [block(0.34, 0.08, 0.1, "fur_dark", y=0.045, z=0.16),
            block(0.26, 0.016, 0.012, "brow", y=0.09, z=0.162),
            mesh.pyramid(0.045, 0.045, tusk_h, "tusk").translate(0.11, 0.08, 0.19),
            mesh.pyramid(0.045, 0.045, tusk_h, "tusk").translate(-0.11, 0.08, 0.19),
            block(0.025, 0.025, 0.02, "tusk", x=0.04, y=0.092, z=0.2),
            block(0.025, 0.025, 0.02, "tusk", x=-0.04, y=0.092, z=0.2),
            block(0.09, 0.075, 0.07, "nose", y=0.132, z=0.182),
            block(0.075, 0.05, 0.012, eye, x=0.088, y=0.168, z=0.162),
            block(0.075, 0.05, 0.012, eye, x=-0.088, y=0.168, z=0.162),
            block(0.14, 0.038, 0.045, "brow", x=0.072, y=0.208, z=0.156).rotate_z(brow),
            block(0.14, 0.038, 0.045, "brow", x=-0.072, y=0.208, z=0.156).rotate_z(-brow),
            mesh.pyramid(0.06, 0.05, 0.09, "fur_dark").rotate_z(-55).translate(0.19, 0.17, -0.02),
            mesh.pyramid(0.06, 0.05, 0.09, "fur_dark").rotate_z(55).translate(-0.19, 0.17, -0.02)]
    if not elder:  # the clone: pupils and a hand-me-down toque with a pompom
        face += [block(0.032, 0.036, 0.014, "dark", x=0.078, y=0.162, z=0.165),
                 block(0.032, 0.036, 0.014, "dark", x=-0.078, y=0.162, z=0.165),
                 mesh.frustum(0.32, 0.28, 0.22, 0.2, 0.075, "toque").rotate_x(-8).translate(0, 0.228, -0.02),
                 block(0.335, 0.04, 0.3, "toque_band", y=0.245, z=-0.02),
                 block(0.075, 0.075, 0.075, "toque_band", y=0.33, z=-0.04)]
    else:          # the Elder: snow-crusted crown of icicles, big hooked horns, a scar
        face += [block(0.37, 0.035, 0.31, "snow", y=0.25)]
        for i, h in enumerate((0.13, 0.08, 0.11, 0.07, 0.12, 0.08, 0.1, 0.07)):
            a = 360.0 * i / 8 + 22.5
            face.append(mesh.pyramid(0.06, 0.06, h, "ice").rotate_z(-12).translate(0.13, 0.255, 0).rotate_y(a))
        for sx in (1, -1):
            face += [mesh.cone(0.05, 0.2, "horn", segments=6).rotate_z(-55 * sx).translate(0.17 * sx, 0.19, 0.0),
                     mesh.cone(0.032, 0.12, "horn", segments=6).rotate_z(-8 * sx).translate(0.32 * sx, 0.29, 0.0)]
        face.append(block(0.012, 0.09, 0.012, "scar", x=-0.12, y=0.15, z=0.163).rotate_z(-20))
    rig.add("head", *at(*face))
    # Body: frosty belly, scarf, fur tufts down the back (ice spikes on the Elder).
    tufts = []
    for x, y in ((0.0, 0.2), (0.11, 0.14), (-0.11, 0.14), (0.0, 0.08)):
        if elder:
            tufts.append(mesh.pyramid(0.09, 0.09, 0.17, "ice").rotate_x(-65).translate(x, y, -0.17))
        else:
            tufts.append(mesh.pyramid(0.08, 0.06, 0.07, "fur_dark").rotate_x(-60).translate(x, y, -0.175))
    rig.add("torso", *at(block(0.27, 0.17, 0.02, "belly", y=0.11, z=0.18),
                         block(0.43, 0.05, 0.37, "scarf", y=0.235),
                         block(0.07, 0.13, 0.03, "scarf", x=0.09, y=0.18, z=-0.19).rotate_z(8),
                         *tufts))
    for side, sx in (("l", 1), ("r", -1)):
        claws = [block(0.03, 0.025, 0.03, "tusk", x=dx, y=-0.022, z=0.15) for dx in (-0.04, 0.0, 0.04)]
        rig.add(f"foot_{side}", *at(*claws))
        thumb = block(0.04, 0.045, 0.045, "fur_dark", x=-0.055 if side == "l" else 0.055, y=-0.03, z=0.02)
        rig.add(f"hand_{side}", *at(thumb))
        if elder:
            rig.add(f"arm_{side}", *at(block(0.16, 0.035, 0.16, "snow", y=0.015),
                                       mesh.pyramid(0.05, 0.05, 0.09, "ice").translate(0.0, 0.02, -0.02)))
    if elder:
        rig.add("hips", *at(block(0.37, 0.03, 0.31, "fur_dark", y=-0.04)))
    return rig


# ------------------------------------------------------------------ the cast


def _builder(cfg: dict, foreman: bool) -> tuple[rigs.Rig, dict]:
    name = "foreman" if foreman else "builder"
    rig = builder_rig(name, foreman=foreman)
    rig.build(unit=name)
    c = cfg["BUILDER"]
    style = motion.Style(arm_swing=22, lean_walk=2, twist=4) if foreman else motion.Style()
    motion.standard_clips(rig, walk_speed=c["speed"] * 0.5, run_speed=c["speed"], style=style,
                          attack="shotgun", attack_period=c["attackCooldown"],
                          work="direct" if foreman else "hammer", cheer_kind="jump",
                          hide_when_busy=("clipboard",) if foreman else ())
    return rig, {"faction": "player", "radius": c["radius"],
                 "title": "Foreman" if foreman else "Builder",
                 "locomotion": {"speed": c["speed"], "clip": "run"}}


def _clone(cfg: dict) -> tuple[rigs.Rig, dict]:
    rig = grump_rig("clone")
    rig.build(unit="clone")
    c = cfg["CLONE"]
    style = motion.Style(arm_swing=35, arm_out=18, elbow=25, lean_walk=6, lean_run=16, twist=4, sway=9,
                         bounce=1.6, hunch=8, hop=True)
    motion.standard_clips(rig, walk_speed=c["speed"], run_speed=c["speed"] * 1.3, style=style, attack="melee",
                          attack_period=c["attackCooldown"], work="smash", cheer_kind="stomp")
    return rig, {"faction": "enemy", "radius": c["radius"], "title": "Grump (clone)",
                 "locomotion": {"speed": c["speed"], "clip": "walk"}}


def _boss(cfg: dict) -> tuple[rigs.Rig, dict]:
    rig = grump_rig("boss", elder=True)
    rig.build(unit="boss")
    c = cfg["BOSS"]
    style = motion.Style(arm_swing=24, arm_out=14, elbow=25, lean_walk=6, lean_run=14, twist=5, sway=7,
                         bounce=1.3, hunch=12)
    motion.standard_clips(rig, walk_speed=c["speed"], run_speed=c["speed"] * 1.5, style=style, attack="melee",
                          attack_period=c["attackCooldown"], work="smash", cheer_kind="stomp", fall_forward=True)
    motion.slam(rig, style, duration=min(1.7, c.get("slamCooldown", 2.8)))
    return rig, {"faction": "enemy", "radius": c["radius"], "title": "Grump Elder (boss)",
                 "locomotion": {"speed": c["speed"], "clip": "walk"}}


def _turret(cfg: dict) -> tuple[rigs.Rig, dict]:
    rig = rigs.turret("turret")
    rig.build(unit="turret")
    motion.turret_clips(rig, cooldown=cfg["TURRET"]["cooldown"])
    return rig, {"faction": "player", "radius": 0.45, "title": "Auto Turret", "locomotion": None}


MAKERS: dict[str, Callable[[dict], tuple[rigs.Rig, dict]]] = {
    "builder": lambda cfg: _builder(cfg, False),
    "foreman": lambda cfg: _builder(cfg, True),
    "clone": _clone,
    "boss": _boss,
    "turret": _turret,
}


def make(name: str, cfg: dict | None = None) -> tuple[rigs.Rig, dict]:
    """The built, animated rig for a unit name plus its manifest fields (nothing written)."""
    if name not in MAKERS:
        raise KeyError(f"unknown unit {name!r}; have {sorted(MAKERS)}")
    return MAKERS[name](cfg or game_config())


def _frame(g: gltf.Gltf, poses) -> tuple:
    import numpy as np
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for clip, t in poses:
        for p, _, _, _ in g.triangles(clip, t):
            if len(p):
                lo, hi = np.minimum(lo, p.reshape(-1, 3).min(0)), np.maximum(hi, p.reshape(-1, 3).max(0))
    lo[1] = min(lo[1], 0.0)
    return lo, hi


def _sheet(images, labels, out):
    from PIL import Image, ImageDraw
    w, h = images[0].size
    sheet = Image.new("RGB", (w * len(images), h + 16), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, (img, lab) in enumerate(zip(images, labels)):
        sheet.paste(img, (i * w, 16))
        d.text((i * w + 4, 2), lab, fill=(40, 40, 40))
    sheet.save(out)
    return sheet


def closeup_turntable(path: str | Path, out: str | Path, *, yaws=(0, 45, 90, 135, 180, 270), size: int = 200,
                      pitch: float = 20.0):
    """The rest pose from several sides, zoomed to the character (no floor) — judge silhouettes here."""
    g = gltf.read(path)
    frame = _frame(g, [(None, 0.0)])
    imgs = [preview.render(path, size=size, yaw=y, pitch=pitch, frame=frame, floor=False) for y in yaws]
    return _sheet(imgs, [f"yaw {y}" for y in yaws], out)


def closeup_strip(path: str | Path, out: str | Path, clip: str, *, frames: int = 8, size: int = 180,
                  yaw: float = 60.0, pitch: float = 15.0):
    """Evenly spaced frames of a clip, zoomed on one shared frame whose bottom edge is the floor (y=0)."""
    g = gltf.read(path)
    a = next(x for x in g.doc["animations"] if x.get("name") == clip)
    dur = max(float(g.accessor(a["samplers"][c["sampler"]]["input"])[-1, 0]) for c in a["channels"])
    times = [dur * i / frames for i in range(frames)] if a.get("extras", {}).get("loop", True) else \
        [dur * i / (frames - 1) for i in range(frames)]
    frame = _frame(g, [(clip, t) for t in times])
    imgs = [preview.render(path, size=size, yaw=yaw, pitch=pitch, clip=clip, time=t, frame=frame, floor=False)
            for t in times]
    return _sheet(imgs, [f"{clip} {t:.2f}s" for t in times], out)


def write_package(rig: rigs.Rig, meta: dict, out_dir: str | Path, *, previews: bool = True,
                  frames: int = 8) -> dict:
    """Write a built rig as a character package: GLB, previews, manifest; validate and check its clips."""
    name = rig.name
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    glb = rig.scene.write(out / f"{name}.glb")
    rep = gltf.validate(glb, max_triangles=catalog.TRIANGLE_BUDGET["character"])
    checks = motion.check(glb, rig.clip_info)
    shots = []
    if previews:
        pv = out / "previews"
        pv.mkdir(exist_ok=True)
        preview.turntable(glb, pv / "turntable.png")             # on the tile floor: scale
        closeup_turntable(glb, pv / "closeup.png")                # zoomed: silhouettes
        shots += [str(pv / "turntable.png"), str(pv / "closeup.png")]
        for clip in rig.clip_info:
            p = pv / f"clip_{clip}.png"
            closeup_strip(glb, p, clip, frames=frames)
            shots.append(str(p))
    meta = dict(meta)
    title = meta.pop("title", name.title())
    catalog.write_manifest(
        out, name=name, kind="character", files=[f"{name}.glb"], title=title,
        height=round(rep["bounds"]["max"][1], 3), clips=rig.clip_info, triangles=rep["triangles"],
        materials={m: MATERIAL_NOTES[m] for m in rep["materials"] if m in MATERIAL_NOTES},
        props=rig.props, sockets=[n for n in rig.parts if n.endswith("_socket")], **meta)
    pkg = catalog.validate_package(out)
    return {"name": name, "package": str(out), "glb": str(glb), "triangles": rep["triangles"],
            "height": round(rep["bounds"]["max"][1], 3), "clips": list(rig.clip_info), "validation": pkg,
            "checks": checks, "previews": shots}


def build_unit(name: str, out_dir: str | Path, *, previews: bool = True, cfg: dict | None = None) -> dict:
    """Build one unit (builder, foreman, clone, boss, turret) as a validated package in `out_dir`."""
    rig, meta = make(name, cfg)
    return write_package(rig, meta, out_dir, previews=previews)


def build_all(out_root: str | Path, *, previews: bool = True) -> dict[str, dict]:
    """Build every unit into `out_root/<name>/`; returns {name: report}, also saved as `report.json`."""
    cfg = game_config()
    root = Path(out_root)
    reports = {n: build_unit(n, root / n, previews=previews, cfg=cfg) for n in MAKERS}
    (root / "report.json").write_text(json.dumps(reports, indent=2, default=str) + "\n")
    return reports
