"""Flat upright panels: rounded-rectangle outlines in the XY plane and the thin prisms (plaques, signs, faces) they extrude to."""

from __future__ import annotations

import math

from . import mesh


def rounded_rect(w: float, h: float, corner: float, per_corner: int = 4) -> list[tuple[float, float]]:
    """A CCW w x h rectangle outline in the XY plane, centred on the origin, corners rounded by `corner`."""
    hx, hy = w / 2 - corner, h / 2 - corner
    pts = []
    for cx, cy, a0 in ((hx, -hy, -90), (hx, hy, 0), (-hx, hy, 90), (-hx, -hy, 180)):
        for k in range(per_corner):
            a = math.radians(a0 + 90 * k / (per_corner - 1))
            pts.append((cx + corner * math.cos(a), cy + corner * math.sin(a)))
    return pts


def slab_xy(outline: list[tuple[float, float]], z0: float, z1: float, material: str = "default") -> mesh.Mesh:
    """A flat prism standing upright: an (x, y) outline in the XY plane, extruded from z = z0 to z = z1."""
    m = mesh.extrude([(x, -y) for x, y in outline], z1 - z0, material)
    return m.rotate_x(90).translate(0, 0, z0)  # extrude's (x, h, -y) -> (x, y, h)
