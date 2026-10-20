"""Procedural animation clips for `rigs` characters: poses in, baked node keys out.

A clip is a **pose function** `t -> pose` sampled by `bake()` into glTF keys.
A pose is a dict:

    {"arm_r": (-90, 0, 0),           # rotation, Euler degrees (x, y, z) on top of the rest pose
     "hips.pos": (0, -0.02, 0),       # translation offset from the rest joint
     "muzzle_flash.scale": 1.0,       # absolute scale (props: 1 shown, rigs.HIDDEN hidden)
     "_foot_l": (0.05, 0.0),          # humanoid foot target: (forward, lift) from its rest spot
     "_ik": 0}                        # 0 turns the leg IK off (falling, lying down)

On a humanoid the legs are solved by two-bone IK every frame: feet stay where
the pose puts them (planted by default) whatever the hips do, so crouches,
lunges and walk cycles never push a foot through the floor or slide it.
`keyframes()` turns a few key poses into a pose function with eased blends.

Locomotion is computed, not hand-keyed: `plan_gait(body, speed)` picks period,
stride and stance fraction so a planted foot moves backwards at exactly the
travel speed — play `walk`/`run` with timeScale = actual speed / authored
speed (`rig.clip_info[clip]["speed"]`) and feet never slide.

Every clip keys the rotation of every joint, the translation of `body`/`hips`
and the scale of every prop, so clips never inherit leftovers from the
previous one in engines that do not reset unkeyed channels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

from . import gltf, mesh, rigs

Pose = dict
PoseFn = Callable[[float], Pose]

#: Translation is always keyed on these parts (when the rig has them).
ALWAYS_TRANSLATE = ("body", "hips", "yaw", "barrel")


# ------------------------------------------------------------------ curves


def smooth(u: float) -> float:
    """Smoothstep 0..1 easing."""
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def pulse(t: float, t0: float, t1: float, peak: float = 1.0, low: float = rigs.HIDDEN) -> float:
    """`peak` between t0 and t1, `low` elsewhere — for a muzzle flash scale."""
    return peak if t0 <= t <= t1 else low


def _blend(a, b, f):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * f
    return tuple(x + (y - x) * f for x, y in zip(a, b))


def _neutral(key: str, like):
    if key.endswith(".scale"):
        return 1.0 if isinstance(like, (int, float)) else (1.0, 1.0, 1.0)
    if key == "_ik":
        return 1
    return tuple(0.0 for _ in like) if not isinstance(like, (int, float)) else 0.0


def keyframes(keys: Sequence[tuple[float, Pose]], *, ease: bool = True) -> PoseFn:
    """A pose function blending key poses [(time, pose)]; a key missing from a pose is neutral."""
    keys = sorted(keys, key=lambda k: k[0])
    names = sorted({n for _, p in keys for n in p})

    def fn(t: float) -> Pose:
        if t <= keys[0][0]:
            return dict(keys[0][1])
        if t >= keys[-1][0]:
            return dict(keys[-1][1])
        i = max(k for k in range(len(keys)) if keys[k][0] <= t)
        (t0, a), (t1, b) = keys[i], keys[i + 1]
        f = (t - t0) / (t1 - t0)
        f = smooth(f) if ease else f
        out = {}
        for n in names:
            if n not in a and n not in b:
                continue
            va = a[n] if n in a else _neutral(n, b[n])
            vb = b[n] if n in b else _neutral(n, va)
            out[n] = _blend(va, vb, f)
        return out

    return fn


def merge(*poses: Pose) -> Pose:
    """Combine poses; rotations and offsets of the same part add up, scales multiply."""
    out: Pose = {}
    for p in poses:
        for k, v in p.items():
            if k not in out:
                out[k] = v
            elif k.endswith(".scale"):
                out[k] = out[k] * v if isinstance(v, (int, float)) else tuple(x * y for x, y in zip(out[k], v))
            elif k == "_ik":
                out[k] = min(out[k], v)
            else:
                out[k] = tuple(x + y for x, y in zip(out[k], v))
    return out


def mirror(pose: Pose) -> Pose:
    """The same pose with left and right swapped (Y/Z rotations and X offsets negated)."""
    out = {}
    for k, v in pose.items():
        k2 = k.replace("_l", "\0").replace("_r", "_l").replace("\0", "_r")
        if k.endswith(".scale") or k == "_ik" or k.startswith("_foot"):
            out[k2] = v
        elif k.endswith(".pos"):
            out[k2] = (-v[0], v[1], v[2])
        else:
            out[k2] = (v[0], -v[1], -v[2])
    return out


# ------------------------------------------------------------------ IK + baking


def leg_ik(thigh: float, shin: float, forward: float, down: float) -> tuple[float, float, float]:
    """Two-bone leg IK in the sagittal plane: (thigh, shin, foot) X rotations in degrees.

    `forward`/`down` locate the ankle from the hip joint. The knee bends
    forward; the foot is counter-rotated to stay level with the body.
    """
    a, b = thigh, shin
    d = math.hypot(forward, down)
    d = min(max(d, abs(a - b) + 1e-4), (a + b) * 0.99999)
    alpha = math.atan2(forward, down)
    beta = math.acos(max(-1.0, min(1.0, (a * a + d * d - b * b) / (2 * a * d))))
    knee = math.pi - math.acos(max(-1.0, min(1.0, (a * a + b * b - d * d) / (2 * a * b))))
    th, kn = math.degrees(alpha + beta), math.degrees(knee)
    return -th, kn, th - kn


def _resolve(rig: rigs.Rig, pose: Pose, show: set, hide: set) -> dict[str, tuple]:
    """Full transforms {part: (quat, translation, scale)} for one pose."""
    pose = dict(pose)
    if rig.kind == "humanoid" and pose.get("_ik", 1) >= 0.5:
        b = rig.spec
        hx, hy, hz = pose.get("hips.pos", (0.0, 0.0, 0.0))
        for side in ("l", "r"):
            fz, lift = pose.get(f"_foot_{side}", (0.0, 0.0))
            th, kn, ft = leg_ik(b.thigh, b.shin, fz - hz, (b.hip_y + hy) - (b.ankle + lift))
            spread = pose.get(f"leg_{side}", (0.0, 0.0, 0.0))[2]
            for part, ang in ((f"leg_{side}", th), (f"shin_{side}", kn), (f"foot_{side}", ft)):
                x, y, z = pose.get(part, (0.0, 0.0, 0.0))
                pose[part] = (x + ang, y, z - spread if part.startswith("foot") else z)  # soles stay flat
    out = {}
    for name, p in rig.parts.items():
        if p.parent is None:
            continue
        e = pose.get(name, (0.0, 0.0, 0.0))
        q = mesh.quat_mul(p.rotation, mesh.quat_euler(*e)) if any(e) else tuple(p.rotation)
        off = pose.get(name + ".pos", (0.0, 0.0, 0.0))
        tr = tuple(a + o for a, o in zip(p.translation, off))
        if name + ".scale" in pose:
            s = pose[name + ".scale"]
            sc = (float(s),) * 3 if isinstance(s, (int, float)) else tuple(s)
        elif p.prop:
            visible = name in show or (not p.hidden and name not in hide)
            sc = (1.0,) * 3 if visible else (rigs.HIDDEN,) * 3
        else:
            sc = (1.0, 1.0, 1.0)
        out[name] = (q, tr, sc)
    return out


def bake(rig: rigs.Rig, name: str, duration: float, pose_fn: PoseFn, *, loop: bool = True, fps: float = 30.0,
         min_keys: int = 16, show: Iterable[str] = (), hide: Iterable[str] = (),
         extra_times: Iterable[float] = (), **info) -> gltf.Clip:
    """Sample `pose_fn` over `duration` s into a clip on the rig's built scene; loops close exactly.

    `show`/`hide` name props visible/hidden for the whole clip (others keep
    their rest state). Extra keyword arguments (speed, event, …) are stored in
    `rig.clip_info[name]` for the manifest.
    """
    if rig.scene is None:
        raise RuntimeError("call rig.build() before baking clips")
    n = max(min_keys, int(math.ceil(duration * fps)))
    times = sorted(set(np.round(np.r_[np.linspace(0, duration, n + 1),
                                      [t for t in extra_times if 0 < t < duration]], 5).tolist()))
    keep = [times[0]]
    for t in times[1:]:
        if t - keep[-1] > 1e-3:
            keep.append(t)
    keep[-1] = duration
    times = keep
    show, hide = set(show), set(hide)
    frames = [_resolve(rig, pose_fn(t), show, hide) for t in times]
    if loop:
        frames[-1] = frames[0]
    clip = rig.scene.animation(name, loop=loop)
    for part_name, part in rig.parts.items():
        if part.parent is None:
            continue
        node = rig.nodes[part_name]
        for path, idx in (("rotation", 0), ("translation", 1), ("scale", 2)):
            vals = np.asarray([f[part_name][idx] for f in frames], dtype=np.float64)
            if path == "rotation":  # one hemisphere, so "constant" is judged up to sign
                for i in range(1, len(vals)):
                    if np.dot(vals[i], vals[i - 1]) < 0:
                        vals[i] = -vals[i]
            varies = not np.allclose(vals, vals[0], atol=1e-6)
            wanted = (varies
                      or (path == "rotation" and not part.prop)
                      or (path == "translation" and part_name in ALWAYS_TRANSLATE)
                      or (path == "scale" and part.prop))
            if not wanted:
                continue
            if varies:
                clip.key(node, path, times, vals)
            else:
                clip.key(node, path, [0.0, duration], [vals[0], vals[0]])
    rig.clip_info[name] = {"duration": round(duration, 4), "loop": loop, **info}
    return clip


# ------------------------------------------------------------------ locomotion


@dataclass
class Gait:
    """A planned walk/run cycle: everything derived from leg length and travel speed."""

    speed: float     # tiles / s the clip is authored for
    period: float    # s per full cycle (two steps)
    stride: float    # how far a foot travels relative to the hips while planted
    duty: float      # fraction of the cycle each foot is planted (< 0.5 = flight phases)
    drop: float      # hips lowered so the legs reach the stride
    bounce: float    # vertical hip bob amplitude
    lift: float      # swing-foot lift
    run: bool

    @property
    def stance_speed(self) -> float:
        """Speed of a planted foot relative to the hips — equals `speed` (no sliding)."""
        return self.stride / (self.duty * self.period)


@dataclass
class Style:
    """Personality knobs shared by all of a character's clips."""

    arm_swing: float = 30.0     # degrees, walk (run x1.5)
    arm_out: float = 6.0        # resting arm spread
    elbow: float = 12.0         # resting elbow bend
    lean_walk: float = 4.0
    lean_run: float = 14.0
    twist: float = 6.0          # shoulder counter-rotation
    sway: float = 2.0           # side-to-side waddle roll
    bounce: float = 1.0         # hip bob multiplier
    hunch: float = 0.0          # forward head/torso hunch (grumpy creatures)
    cadence: float = 1.0        # >1 = quicker, shorter steps
    hop: bool = False           # move by two-footed hops (stubby creatures) instead of steps


def plan_gait(body: rigs.Body, speed: float, *, run: bool = False, style: Style | None = None) -> Gait:
    """Choose period, stride and stance so a planted foot moves at exactly `speed` tiles/s.

    Starts from a natural pendulum cadence for the leg length; if the stride
    that needs is longer than the legs reach, it takes quicker steps, and if
    even the quickest natural cadence is too slow it shortens the stance —
    the cycle gains flight phases (a hop or a run) instead of sliding feet.
    """
    st = style or Style()
    L = body.leg
    duty_pref, crouch, nat = (0.38, 0.8, 0.78) if run else (0.6, 0.88, 1.15)
    if st.hop:  # one hop per cycle: a short stance and a long flight
        duty_pref, crouch, nat = (0.25, 0.8, 1.0) if run else (0.32, 0.8, 1.3)
    t_nat = nat * math.sqrt(L) / st.cadence
    reach = 0.98 * L
    s_max = 2 * math.sqrt(reach ** 2 - (crouch * L) ** 2)
    period, duty = t_nat, duty_pref
    stride = speed * duty * period
    if stride > s_max:
        stride = s_max
        period = stride / (speed * duty)
        if period < 0.6 * t_nat:
            period = 0.6 * t_nat
            duty = stride / (speed * period)
    floor = 0.3 if run else 0.4  # quicker cycles flicker at game frame rates: hop (flight) instead
    if period < floor:
        period = floor
        duty = stride / (speed * period)
    if duty < 0.18:  # too fast even for a hop: shorter strides rather than sliding feet
        duty = 0.18
        stride = speed * duty * period
    bounce = (0.07 if run else 0.025) * L * st.bounce
    drop = max(0.0, L - math.sqrt(reach ** 2 - (stride / 2) ** 2)) + bounce + (0.05 * L if run and not st.hop else 0.0)
    lift = (0.32 if run else 0.18) * L
    return Gait(speed, period, stride, duty, drop, bounce, lift, run)


def stance(style: Style) -> Pose:
    """The relaxed base pose: arms a little out, elbows a little bent, the style's hunch."""
    s = style
    return {"arm_l": (0.0, 0.0, s.arm_out), "arm_r": (0.0, 0.0, -s.arm_out),
            "forearm_l": (-s.elbow, 0.0, 0.0), "forearm_r": (-s.elbow, 0.0, 0.0),
            "torso": (s.hunch, 0.0, 0.0), "head": (-s.hunch * 0.6, 0.0, 0.0)}


def locomotion(rig: rigs.Rig, gait: Gait, style: Style | None = None) -> PoseFn:
    """The walk/run pose function for a gait: IK feet on a stance/swing path, arms in counter-swing."""
    st = style or Style()
    g = gait
    swing = st.arm_swing * (1.5 if g.run else 1.0)
    lean = (st.lean_run if g.run else st.lean_walk) + st.hunch

    if st.hop:
        return _hop(rig, g, st, swing, lean * 0.5)

    def fn(t: float) -> Pose:
        p = (t / g.period) % 1.0
        out = dict(stance(st))
        for side, off in (("l", 0.0), ("r", 0.5)):
            q = (p + off) % 1.0
            if q < g.duty:
                z, lift = g.stride / 2 - g.stride * q / g.duty, 0.0
            else:
                u = (q - g.duty) / (1 - g.duty)
                z = -g.stride / 2 + g.stride * (1 - math.cos(math.pi * u)) / 2
                lift = g.lift * math.sin(math.pi * u)
            out[f"_foot_{side}"] = (z, lift)
        c = math.cos(2 * math.pi * p)
        mid = math.cos(4 * math.pi * (p - g.duty / 2))         # +1 at each mid-stance
        bob = g.bounce * (-mid if g.run else mid)
        out["hips.pos"] = (0.0, -g.drop + bob, 0.0)
        roll = math.cos(2 * math.pi * (p - g.duty / 2))        # +1 mid-stance of the left foot
        out["torso"] = (lean, st.twist * c, -st.sway * roll)
        out["head"] = (-lean * 0.6, -st.twist * 0.5 * c, st.sway * 0.6 * roll)
        bend = st.elbow + (45.0 if g.run else 0.0)
        out["arm_r"] = (-swing * c, 0.0, -st.arm_out)
        out["arm_l"] = (swing * c, 0.0, st.arm_out)
        out["forearm_r"] = (-bend - 12 * max(0.0, c), 0.0, 0.0)
        out["forearm_l"] = (-bend - 12 * max(0.0, -c), 0.0, 0.0)
        return out

    return fn


def _hop(rig: rigs.Rig, g: Gait, st: Style, swing: float, lean: float) -> PoseFn:
    """Both feet together: land, squash, spring, fly with arms flung up — feet planted while down."""
    L, H = rig.spec.leg, rig.spec.height
    hop_h = 0.08 * H * st.bounce * (1.3 if g.run else 1.0)

    def fn(t: float) -> Pose:
        q = (t / g.period) % 1.0
        out = dict(stance(st))
        if q < g.duty:
            u = q / g.duty
            z, lift, up, squash = g.stride / 2 - g.stride * u, 0.0, 0.0, math.sin(math.pi * u)
        else:
            u = (q - g.duty) / (1 - g.duty)
            z = -g.stride / 2 + g.stride * (1 - math.cos(math.pi * u)) / 2
            lift, up, squash = g.lift * math.sin(math.pi * u), math.sin(math.pi * u), 0.0
        out["_foot_l"] = out["_foot_r"] = (z, lift)
        out["body.pos"] = (0.0, hop_h * up, 0.0)
        out["hips.pos"] = (0.0, -g.drop - 0.1 * L * squash, 0.0)
        out["torso"] = (lean + 10.0 * squash - 6.0 * up, 0.0, 0.0)
        out["head"] = (-lean * 0.6 - 8.0 * squash + 6.0 * up, 0.0, 0.0)
        out["arm_l"] = (-swing * 1.6 * up + 10.0 * squash, 0.0, st.arm_out + 25.0 * up)
        out["arm_r"] = (-swing * 1.6 * up + 10.0 * squash, 0.0, -st.arm_out - 25.0 * up)
        out["forearm_l"] = out["forearm_r"] = (-st.elbow - 20.0 * up, 0.0, 0.0)
        return out

    return fn


# ------------------------------------------------------------------ the standard set


def _fall_lift(rig: rigs.Rig, forward: bool) -> float:
    """How far to raise a body lying flat so its back (or front) rests on the floor."""
    pts = rest_points(rig)
    return float(pts[:, 2].max() if forward else -pts[:, 2].min())


def rest_points(rig: rigs.Rig, *, include_hidden: bool = False) -> np.ndarray:
    """Every vertex of the rig in the rest pose, in the root frame."""
    world: dict[str, np.ndarray] = {}
    out = []
    for name, p in rig.parts.items():
        m = np.eye(4)
        m[:3, :3] = mesh.quat_matrix(p.rotation) * np.asarray(p.rest_scale)[None, :]
        m[:3, 3] = p.translation
        world[name] = (world[p.parent] @ m) if p.parent else m
        if p.hidden and not include_hidden:
            continue
        for ms in p.meshes:
            out.append((np.c_[ms.positions, np.ones(len(ms.positions))] @ world[name].T)[:, :3])
    return np.concatenate(out) if out else np.zeros((1, 3))


def idle(rig: rigs.Rig, style: Style | None = None, *, period: float = 2.4) -> gltf.Clip:
    """Breathing idle: chest rise, slow look-around, a little knee give (loop)."""
    st = style or Style()
    L = rig.spec.leg

    def fn(t: float) -> Pose:
        w = 2 * math.pi * t / period
        br = math.sin(w)
        out = stance(st)
        out["torso"] = (st.hunch + 1.5 * br, 0.0, 0.0)
        out["head"] = (-st.hunch * 0.6 - 2.0 * br, 9.0 * math.sin(w) * math.cos(w), 0.0)
        out["arm_l"] = (1.5 * br, 0.0, st.arm_out + 2.5 * (1 - math.cos(w)) / 2)
        out["arm_r"] = (1.5 * br, 0.0, -st.arm_out - 2.5 * (1 - math.cos(w)) / 2)
        out["hips.pos"] = (0.0, -0.02 * L * (1 - math.cos(w)) / 2, 0.0)
        return out

    return bake(rig, "idle", period, fn, loop=True)


def walk_cycle(rig: rigs.Rig, name: str, speed: float, *, run: bool = False, style: Style | None = None,
               **bake_kw) -> Gait:
    """Bake a no-slide walk or run cycle authored for `speed` tiles/s; returns the gait."""
    st = style or Style()
    g = plan_gait(rig.spec, speed, run=run, style=st)
    bake(rig, name, g.period, locomotion(rig, g, st), loop=True, min_keys=32, speed=round(speed, 4),
         stride=round(g.stride, 4), duty=round(g.duty, 3), **bake_kw)
    return g


def work_hammer(rig: rigs.Rig, style: Style | None = None, *, period: float = 0.9, prop: str = "hammer",
                hide: Iterable[str] = ()) -> gltf.Clip:
    """Hammering swing with the right arm (loop); `event` is the impact time."""
    st = style or Style()
    b = stance(st)
    L = rig.spec.leg
    ready = {**b, "arm_r": (-140.0, 0.0, -10.0), "forearm_r": (-40.0, 0.0, 0.0), "torso": (2.0, -10.0, 0.0),
             "head": (12.0, 0.0, 0.0), "arm_l": (-35.0, 0.0, 12.0), "forearm_l": (-35.0, 0.0, 0.0),
             "hips.pos": (0.0, -0.05 * L, 0.0), "_foot_l": (0.04 * L / 0.3, 0.0), "_foot_r": (-0.03 * L / 0.3, 0.0)}
    wind = {**ready, "arm_r": (-165.0, 0.0, -8.0), "forearm_r": (-50.0, 0.0, 0.0), "torso": (-5.0, -14.0, 0.0)}
    hit = {**ready, "arm_r": (-48.0, 0.0, -6.0), "forearm_r": (-22.0, 0.0, 0.0), "torso": (20.0, 8.0, 0.0),
           "head": (22.0, 0.0, 0.0), "hips.pos": (0.0, -0.09 * L, 0.0)}
    rebound = {**hit, "arm_r": (-68.0, 0.0, -6.0), "torso": (16.0, 6.0, 0.0)}
    k = [(0.0, ready), (0.4 * period, wind), (0.55 * period, hit), (0.64 * period, rebound), (period, ready)]
    return bake(rig, "work", period, keyframes(k), show={prop}, hide=hide, event=round(0.55 * period, 4))


def work_direct(rig: rigs.Rig, style: Style | None = None, *, period: float = 2.2, hide: Iterable[str] = ()) -> gltf.Clip:
    """A foreman's work: read the clipboard in the left hand, look up, point and jab (loop)."""
    st = style or Style()
    b = stance(st)
    read = {**b, "arm_l": (-50.0, -25.0, 4.0), "forearm_l": (-75.0, 0.0, 0.0), "head": (24.0, 8.0, 0.0),
            "torso": (6.0, 0.0, 0.0)}
    point = {**read, "arm_l": (-25.0, -10.0, 6.0), "forearm_l": (-50.0, 0.0, 0.0), "head": (-4.0, -15.0, 0.0),
             "arm_r": (-100.0, -10.0, -4.0), "forearm_r": (-4.0, 0.0, 0.0), "torso": (2.0, -12.0, 0.0)}
    jab = {**point, "arm_r": (-108.0, -10.0, -4.0), "head": (-8.0, -15.0, 0.0)}
    k = [(0.0, read), (0.3 * period, {**read, "head": (28.0, -4.0, 0.0)}), (0.45 * period, point),
         (0.6 * period, jab), (0.66 * period, point), (0.74 * period, jab), (0.82 * period, point), (period, read)]
    return bake(rig, "work", period, keyframes(k), hide=hide)


def work_smash(rig: rigs.Rig, style: Style | None = None, *, period: float = 0.7, name: str = "work") -> gltf.Clip:
    """Alternate fists pounding down in front — a creature wrecking a structure (loop)."""
    st = style or Style()
    b = stance(st)
    L = rig.spec.leg
    left = {**b, "arm_l": (-160.0, 0.0, 12.0), "forearm_l": (-25.0, 0.0, 0.0), "arm_r": (-40.0, 0.0, -14.0),
            "forearm_r": (-10.0, 0.0, 0.0), "torso": (14.0 + st.hunch, 12.0, 0.0), "head": (-6.0, -6.0, 0.0),
            "hips.pos": (0.0, -0.12 * L, 0.0)}
    right = mirror(left)
    k = [(0.0, left), (0.5 * period, right), (period, left)]
    return bake(rig, name, period, keyframes(k), event=[0.0, round(0.5 * period, 4)])


def attack_shotgun(rig: rigs.Rig, style: Style | None = None, *, period: float = 1.1, fire: float = 0.12,
                   gun: str = "shotgun", stowed: Iterable[str] = ("shotgun_back",), flash: str = "muzzle_flash",
                   hide: Iterable[str] = ()) -> gltf.Clip:
    """Aim, fire with recoil and a muzzle flash, settle (loop, one shot per cycle: period = cooldown)."""
    st = style or Style()
    b = stance(st)
    L = rig.spec.leg
    aim = {**b, "arm_r": (-82.0, 14.0, 0.0), "forearm_r": (-8.0, 0.0, 0.0), "arm_l": (-74.0, -40.0, 0.0),
           "forearm_l": (-22.0, 0.0, 0.0), "torso": (3.0, 0.0, 0.0), "head": (2.0, 0.0, 0.0),
           "hips.pos": (0.0, -0.04 * L, 0.0), "_foot_l": (0.05 * L / 0.3, 0.0), "_foot_r": (-0.04 * L / 0.3, 0.0)}
    kick = {**aim, "arm_r": (-104.0, 14.0, 0.0), "arm_l": (-92.0, -40.0, 0.0), "torso": (-9.0, 0.0, 0.0),
            "head": (-8.0, 0.0, 0.0), "hips.pos": (0.0, -0.04 * L, -0.02 * L / 0.3)}
    kf = keyframes([(0.0, aim), (fire, aim), (fire + 0.04, kick), (fire + 0.36, aim), (period, aim)])
    flash_on = (fire, fire + 0.06)

    def fn(t: float) -> Pose:
        p = kf(t)
        if flash in rig.parts:
            p[flash + ".scale"] = pulse(t, *flash_on, peak=1.0)
        return p

    return bake(rig, "attack", period, fn, show={gun}, hide=set(stowed) | set(hide),
                extra_times=(fire - 0.005, fire, fire + 0.04, flash_on[1], flash_on[1] + 0.005),
                event=round(fire, 4))


def attack_melee(rig: rigs.Rig, style: Style | None = None, *, period: float = 1.0, strike: float = 0.5) -> gltf.Clip:
    """Double-fist overhead smash with a lunge (loop, one blow per cycle: period = cooldown)."""
    st = style or Style()
    b = stance(st)
    L = rig.spec.leg
    ready = {**b, "arm_l": (-40.0, 0.0, 28.0), "arm_r": (-40.0, 0.0, -28.0), "forearm_l": (-35.0, 0.0, 0.0),
             "forearm_r": (-35.0, 0.0, 0.0), "torso": (10.0 + st.hunch, 0.0, 0.0), "head": (-4.0, 0.0, 0.0),
             "hips.pos": (0.0, -0.08 * L, 0.0), "_foot_l": (0.12 * L, 0.0), "_foot_r": (-0.08 * L, 0.0)}
    wind = {**ready, "arm_l": (-170.0, 0.0, 14.0), "arm_r": (-170.0, 0.0, -14.0), "forearm_l": (-25.0, 0.0, 0.0),
            "forearm_r": (-25.0, 0.0, 0.0), "torso": (-14.0, 0.0, 0.0), "head": (-12.0, 0.0, 0.0),
            "hips.pos": (0.0, -0.04 * L, -0.05 * L)}
    hit = {**ready, "arm_l": (-38.0, 0.0, 8.0), "arm_r": (-38.0, 0.0, -8.0), "forearm_l": (-5.0, 0.0, 0.0),
           "forearm_r": (-5.0, 0.0, 0.0), "torso": (30.0 + st.hunch, 0.0, 0.0), "head": (8.0, 0.0, 0.0),
           "hips.pos": (0.0, -0.18 * L, 0.16 * L)}
    k = [(0.0, ready), (strike - 0.18, wind), (strike, hit), (strike + 0.15, hit), (period, ready)]
    return bake(rig, "attack", period, keyframes(k), event=round(strike, 4))


def hit(rig: rigs.Rig, style: Style | None = None, *, duration: float = 0.45, hide: Iterable[str] = ()) -> gltf.Clip:
    """A flinch back and recover (one-shot, ends in the base stance so it blends out)."""
    st = style or Style()
    b = stance(st)
    L = rig.spec.leg
    flinch = {**b, "torso": (-16.0, 6.0, 5.0), "head": (-24.0, -8.0, 0.0), "arm_l": (-25.0, 0.0, 38.0),
              "arm_r": (-25.0, 0.0, -38.0), "forearm_l": (-40.0, 0.0, 0.0), "forearm_r": (-40.0, 0.0, 0.0),
              "hips.pos": (0.0, -0.05 * L, -0.06 * L)}
    k = [(0.0, b), (0.07, flinch), (0.16, {**flinch, "torso": (-11.0, 4.0, 3.0)}), (duration, b)]
    return bake(rig, "hit", duration, keyframes(k), loop=False, hide=hide)


def die(rig: rigs.Rig, style: Style | None = None, *, duration: float = 1.3, forward: bool = False,
        hide: Iterable[str] = ()) -> gltf.Clip:
    """Stagger, topple onto the back (or face), bounce once and lie still (one-shot, holds the last pose)."""
    st = style or Style()
    b = {**stance(st), "_ik": 0}
    sgn = 1.0 if forward else -1.0
    lift = _fall_lift(rig, forward)
    stagger = {**b, "torso": (-sgn * 14.0, 0.0, 6.0), "head": (-sgn * 22.0, 0.0, 0.0),
               "arm_l": (-30.0, 0.0, 40.0), "arm_r": (-30.0, 0.0, -40.0),
               "leg_l": (-10.0, 0.0, 0.0), "shin_l": (14.0, 0.0, 0.0)}
    tip = {**stagger, "body": (sgn * 50.0, 0.0, 0.0),
           "arm_l": (-150.0, 0.0, 30.0), "arm_r": (-150.0, 0.0, -30.0),
           "leg_l": (-30.0, 0.0, 6.0), "shin_l": (40.0, 0.0, 0.0), "leg_r": (-8.0, 0.0, -4.0)}
    flat = {**b, "body": (sgn * 90.0, 0.0, 0.0), "body.pos": (0.0, lift, 0.0),
            "arm_l": (-20.0, 0.0, 75.0), "arm_r": (-20.0, 0.0, -75.0), "forearm_l": (-20.0, 0.0, 0.0),
            "forearm_r": (-20.0, 0.0, 0.0), "head": (0.0, 35.0, 0.0), "torso": (0.0, 0.0, 0.0),
            "leg_l": (0.0, 0.0, 10.0), "leg_r": (0.0, 0.0, -10.0)}
    bounce = {**flat, "body": (sgn * 80.0, 0.0, 0.0), "body.pos": (0.0, lift * 1.05, 0.0),
              "arm_l": (-40.0, 0.0, 60.0), "arm_r": (-40.0, 0.0, -60.0), "head": (-sgn * 10.0, 20.0, 0.0)}
    k = [(0.0, b), (0.14, stagger), (0.55, tip), (0.82, flat), (0.94, bounce), (1.08, flat), (duration, flat)]
    return bake(rig, "die", duration, keyframes(k), loop=False, hide=hide)


def cheer(rig: rigs.Rig, style: Style | None = None, *, period: float = 0.9, kind: str = "jump",
          hide: Iterable[str] = ()) -> gltf.Clip:
    """Celebrate: `jump` (hop, both arms pumping) or `stomp` (side-to-side gloat, one fist up) — loop."""
    st = style or Style()
    b = stance(st)
    L, H = rig.spec.leg, rig.spec.height
    if kind == "jump":
        down = {**b, "hips.pos": (0.0, -0.14 * L, 0.0), "arm_l": (-150.0, 0.0, 40.0), "arm_r": (-150.0, 0.0, -40.0),
                "forearm_l": (-45.0, 0.0, 0.0), "forearm_r": (-45.0, 0.0, 0.0), "torso": (8.0, 0.0, 0.0),
                "head": (-8.0, 0.0, 0.0)}
        up = {**down, "hips.pos": (0.0, 0.0, 0.0), "body.pos": (0.0, 0.14 * H, 0.0),
              "_foot_l": (-0.02, 0.06 * L), "_foot_r": (-0.02, 0.06 * L), "arm_l": (-170.0, 0.0, 48.0),
              "arm_r": (-170.0, 0.0, -48.0), "forearm_l": (-5.0, 0.0, 0.0), "forearm_r": (-5.0, 0.0, 0.0),
              "torso": (-6.0, 0.0, 0.0), "head": (-18.0, 0.0, 0.0)}
        k = [(0.0, down), (0.38 * period, up), (0.62 * period, {**up, "body.pos": (0.0, 0.06 * H, 0.0)}),
             (0.8 * period, down), (period, down)]
    else:
        left = {**b, "body.pos": (0.0, 0.05 * H, 0.0), "_foot_r": (0.0, 0.12 * L), "arm_l": (-172.0, 0.0, 16.0),
                "forearm_l": (-30.0, 0.0, 0.0), "arm_r": (-20.0, 0.0, -45.0), "forearm_r": (-60.0, 0.0, 0.0),
                "torso": (-6.0 + st.hunch, 0.0, -10.0), "head": (-14.0, 10.0, 6.0)}
        land = {**b, "hips.pos": (0.0, -0.1 * L, 0.0), "arm_l": (-120.0, 0.0, 30.0), "arm_r": (-120.0, 0.0, -30.0),
                "forearm_l": (-60.0, 0.0, 0.0), "forearm_r": (-60.0, 0.0, 0.0), "torso": (6.0 + st.hunch, 0.0, 0.0)}
        k = [(0.0, land), (0.25 * period, left), (0.5 * period, land), (0.75 * period, mirror(left)), (period, land)]
    return bake(rig, "cheer", period, keyframes(k), loop=True, hide=hide)


def slam(rig: rigs.Rig, style: Style | None = None, *, duration: float = 1.7, impact: float = 0.9) -> gltf.Clip:
    """A big creature's ground slam: crouch, leap, both fists into the ground, recover (one-shot)."""
    st = style or Style()
    b = stance(st)
    L, H = rig.spec.leg, rig.spec.height
    crouch = {**b, "hips.pos": (0.0, -0.25 * L, -0.05 * L), "arm_l": (-175.0, 0.0, 22.0),
              "arm_r": (-175.0, 0.0, -22.0), "forearm_l": (-30.0, 0.0, 0.0), "forearm_r": (-30.0, 0.0, 0.0),
              "torso": (-14.0, 0.0, 0.0), "head": (-16.0, 0.0, 0.0)}
    air = {**crouch, "hips.pos": (0.0, 0.0, 0.0), "body.pos": (0.0, 0.22 * H, 0.0),
           "_foot_l": (0.05 * L, 0.15 * L), "_foot_r": (0.05 * L, 0.15 * L), "arm_l": (-185.0, 0.0, 10.0),
           "arm_r": (-185.0, 0.0, -10.0), "torso": (-20.0, 0.0, 0.0)}
    smash = {**b, "hips.pos": (0.0, -0.32 * L, 0.0), "_foot_l": (0.04 * L, 0.0), "_foot_r": (0.04 * L, 0.0),
             "arm_l": (-48.0, 0.0, 10.0), "arm_r": (-48.0, 0.0, -10.0), "forearm_l": (-10.0, 0.0, 0.0),
             "forearm_r": (-10.0, 0.0, 0.0), "torso": (35.0, 0.0, 0.0), "head": (-20.0, 0.0, 0.0),
             "leg_l": (0.0, 0.0, 6.0), "leg_r": (0.0, 0.0, -6.0)}
    k = [(0.0, b), (0.4, crouch), (0.68, air), (impact, smash), (impact + 0.3, smash), (duration, b)]
    return bake(rig, "slam", duration, keyframes(k), loop=False, extra_times=(impact,), event=round(impact, 4))


def standard_clips(rig: rigs.Rig, *, walk_speed: float, run_speed: float, style: Style | None = None,
                   attack: str = "shotgun", attack_period: float = 1.0, work: str = "hammer",
                   cheer_kind: str = "jump", fall_forward: bool = False,
                   hide_when_busy: Iterable[str] = ()) -> dict[str, dict]:
    """Bake all of `catalog.CHARACTER_CLIPS` onto a built humanoid rig; returns `rig.clip_info`.

    `attack`: "shotgun" (needs props `shotgun`, `shotgun_back`, `muzzle_flash`)
    or "melee". `work`: "hammer" (needs prop `hammer`), "direct" (clipboard
    foreman) or "smash". `hide_when_busy` props are hidden in work and attack.
    """
    st = style or Style()
    busy = set(hide_when_busy)
    idle(rig, st)
    walk_cycle(rig, "walk", walk_speed, style=st)
    walk_cycle(rig, "run", run_speed, run=True, style=st)
    if work == "hammer":
        work_hammer(rig, st, hide=busy)
    elif work == "direct":
        work_direct(rig, st)
    else:
        work_smash(rig, st)
    if attack == "shotgun":
        attack_shotgun(rig, st, period=attack_period, hide=busy)
    else:
        attack_melee(rig, st, period=attack_period)
    hit(rig, st)
    die(rig, st, forward=fall_forward)
    cheer(rig, st, kind=cheer_kind, hide=busy)
    return rig.clip_info


def turret_clips(rig: rigs.Rig, *, cooldown: float = 0.5) -> dict[str, dict]:
    """The standard clip names for a `rigs.turret`: scan, fire with recoil, shake, collapse."""
    def scan(period, sweep, nod):
        return lambda t: {"yaw": (0.0, sweep * math.sin(2 * math.pi * t / period), 0.0),
                          "pitch": (-4.0 + nod * math.sin(4 * math.pi * t / period), 0.0, 0.0)}
    bake(rig, "idle", 4.0, scan(4.0, 55.0, 3.0))
    bake(rig, "walk", 2.0, scan(2.0, 25.0, 2.0))
    bake(rig, "run", 1.2, scan(1.2, 15.0, 2.0))
    fire = 0.03
    kf = keyframes([(0.0, {"pitch": (-3.0, 0.0, 0.0)}), (fire, {"pitch": (-3.0, 0.0, 0.0)}),
                    (fire + 0.03, {"pitch": (-9.0, 0.0, 0.0), "barrel.pos": (0.0, 0.0, -0.07)}),
                    (0.3, {"pitch": (-3.0, 0.0, 0.0)}), (cooldown, {"pitch": (-3.0, 0.0, 0.0)})])
    bake(rig, "attack", cooldown, lambda t: {**kf(t), "muzzle_flash.scale": pulse(t, fire, fire + 0.05)},
         extra_times=(fire - 0.005, fire, fire + 0.03, fire + 0.05, fire + 0.055), event=fire)
    bake(rig, "work", 1.6, keyframes([(0.0, {}), (0.4, {"pitch": (-25.0, 0.0, 0.0), "yaw": (0.0, 20.0, 0.0)}),
                                       (0.8, {"pitch": (5.0, 0.0, 0.0), "yaw": (0.0, -20.0, 0.0)}), (1.2, {}),
                                       (1.6, {})]))
    bake(rig, "hit", 0.35, keyframes([(0.0, {}), (0.05, {"yaw": (0.0, 9.0, 6.0), "pitch": (-10.0, 0.0, 0.0)}),
                                       (0.14, {"yaw": (0.0, -6.0, -4.0)}), (0.35, {})]), loop=False)
    bake(rig, "die", 1.2, keyframes([(0.0, {}), (0.1, {"yaw": (0.0, 10.0, 8.0)}),
                                      (0.7, {"yaw": (0.0, 25.0, 24.0), "yaw.pos": (0.0, -0.06, 0.0),
                                             "pitch": (38.0, 0.0, 0.0), "barrel.pos": (0.0, 0.0, -0.08)}),
                                      (0.85, {"yaw": (0.0, 25.0, 20.0), "yaw.pos": (0.0, -0.05, 0.0),
                                              "pitch": (32.0, 0.0, 0.0), "barrel.pos": (0.0, 0.0, -0.08)}),
                                      (1.2, {"yaw": (0.0, 25.0, 24.0), "yaw.pos": (0.0, -0.06, 0.0),
                                             "pitch": (38.0, 0.0, 0.0), "barrel.pos": (0.0, 0.0, -0.08)})]),
         loop=False)
    bake(rig, "cheer", 1.2, keyframes([(0.0, {}), (0.3, {"pitch": (-30.0, 0.0, 0.0), "yaw": (0.0, 25.0, 0.0)}),
                                        (0.6, {"pitch": (-30.0, 0.0, 0.0), "yaw": (0.0, -25.0, 0.0)}),
                                        (0.9, {"pitch": (-20.0, 0.0, 0.0)}), (1.2, {})]))
    return rig.clip_info


# ------------------------------------------------------------------ checks


def check(path, clip_info: dict[str, dict], *, feet: Sequence[str] = ("foot_l", "foot_r"),
          max_slip: float = 0.02, floor_tolerance: float = 0.01) -> dict:
    """Read a written `.glb` back and check its clips: loops close, nothing under the floor
    (except `die`), and planted feet do not slide at the authored speed. Returns {ok, errors, clips}."""
    g = gltf.read(path)
    errors: list[str] = []
    report: dict[str, dict] = {}
    names = [n.get("name") for n in g.doc["nodes"]]
    for clip in g.clip_names():
        info = clip_info.get(clip, {})
        a = next(x for x in g.doc["animations"] if x.get("name") == clip)
        dur = max(float(g.accessor(a["samplers"][c["sampler"]]["input"])[-1, 0]) for c in a["channels"])
        r: dict = {"duration": round(dur, 3)}
        loop = a.get("extras", {}).get("loop", info.get("loop", True))
        if loop:
            p0 = dict(g.pose(clip, 0.0))
            p1 = dict(g.pose(clip, dur))
            gap = max(float(np.abs(p0[i] - p1[i]).max()) for i in p0)
            r["loop_gap"] = round(gap, 6)
            if gap > 1e-4:
                errors.append(f"{clip}: loop does not close (gap {gap:.4f})")
        lows = []
        for t in np.linspace(0, dur, 17):
            ys = [p[:, :, 1].min() for p, _, _, _ in g.triangles(clip, float(t)) if len(p)]
            lows.append(min(ys))
        r["min_y"] = round(float(min(lows)), 4)
        if clip != "die" and r["min_y"] < -floor_tolerance:
            errors.append(f"{clip}: goes {-r['min_y']:.3f} below the floor")
        speed = info.get("speed")
        if speed and all(f in names for f in feet):
            slips = []
            ts = np.linspace(0, dur, 121)
            for f in feet:
                fi = names.index(f)
                pos = np.array([dict(g.pose(clip, float(t)))[fi][:3, 3] for t in ts])
                planted = pos[:, 1] <= pos[:, 1].min() + 0.002
                ground = pos[:, 2] + speed * ts      # world z if the root walks forward at `speed`
                run_start = None
                for i, on in enumerate(list(planted) + [False]):
                    if on and run_start is None:
                        run_start = i
                    elif not on and run_start is not None:
                        if i - run_start > 2:
                            seg = ground[run_start:i]
                            slips.append(float(seg.max() - seg.min()))
                        run_start = None
            r["foot_slip"] = round(max(slips), 4) if slips else None
            if slips and max(slips) > max_slip:
                errors.append(f"{clip}: planted foot slides {max(slips):.3f} tiles")
        report[clip] = r
    return {"ok": not errors, "errors": errors, "clips": report}
