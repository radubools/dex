"""Pose-function wrappers for characters that hold things: idle as a pose function, held poses, steadied parts."""

from __future__ import annotations

import math

from . import motion, rigs


def idle_pose(rig: rigs.Rig, style: motion.Style, period: float = 2.4) -> motion.PoseFn:
    """motion.idle's breathing idle as a pose function (not baked), so other poses can be layered on it."""
    L = rig.spec.leg

    def fn(t: float) -> dict:
        w = 2 * math.pi * t / period
        br = math.sin(w)
        out = motion.stance(style)
        out["torso"] = (style.hunch + 1.5 * br, 0.0, 0.0)
        out["head"] = (-style.hunch * 0.6 - 2.0 * br, 9.0 * math.sin(w) * math.cos(w), 0.0)
        out["arm_l"] = (1.5 * br, 0.0, style.arm_out + 2.5 * (1 - math.cos(w)) / 2)
        out["arm_r"] = (1.5 * br, 0.0, -style.arm_out - 2.5 * (1 - math.cos(w)) / 2)
        out["hips.pos"] = (0.0, -0.02 * L * (1 - math.cos(w)) / 2, 0.0)
        return out

    return fn


def with_carry(fn: motion.PoseFn, carry: dict) -> motion.PoseFn:
    """A pose function with the pose `carry` added (motion.merge) on top of every pose `fn` returns."""
    if not carry:
        return fn
    return lambda t: motion.merge(fn(t), carry)


def steady(fn: motion.PoseFn, hold: dict, keep: float = 0.25) -> motion.PoseFn:
    """A pose function whose `hold` parts take their given rotation plus only `keep` of `fn`'s X swing."""
    if not hold:
        return fn

    def out(t: float) -> dict:
        p = fn(t)
        for part, v in hold.items():
            x = p.get(part, (0.0, 0.0, 0.0))[0]
            p[part] = (v[0] + keep * x, v[1], v[2])
        return p

    return out
