"""Open containers as flat-shaded meshes: open-topped trays and bins (straight or tapered) and straps round them."""

from __future__ import annotations

import numpy as np

from . import mesh, scenes


def _rect(hx: float, hz: float, y: float, cx: float = 0.0, cz: float = 0.0) -> list[tuple[float, float, float]]:
    return [(cx - hx, y, cz - hz), (cx + hx, y, cz - hz), (cx + hx, y, cz + hz), (cx - hx, y, cz + hz)]


def _ring_quads(a: list, b: list, outward: float, centre=(0.0, 0.0)) -> list:
    polys = []
    for i in range(4):
        j = (i + 1) % 4
        quad = [a[i], a[j], b[j], b[i]]
        c = np.mean(quad, axis=0)
        polys.append((quad, (outward * (c[0] - centre[0]), 0.0, outward * (c[2] - centre[1]))))
    return polys


def _flat_ring(outer: list, inner: list, up: float) -> list:
    return [([outer[i], outer[(i + 1) % 4], inner[(i + 1) % 4], inner[i]], (0, up, 0)) for i in range(4)]


def tray(w0: float, d0: float, w1: float, d1: float, h: float, wall: float, floor: float, material: str = "default",
         cx: float = 0.0, cz: float = 0.0) -> mesh.Mesh:
    """An open-topped bin on y=0: outside w0 x d0 at the base, w1 x d1 at the rim h high, walls `wall` thick, inner floor at y=`floor`."""
    def half(y):
        t = y / h
        return (w0 + (w1 - w0) * t) / 2, (d0 + (d1 - d0) * t) / 2

    hb, ht, hf = half(0.0), half(h), half(floor)
    ob, ot = _rect(*hb, 0.0, cx, cz), _rect(*ht, h, cx, cz)
    it, if_ = _rect(ht[0] - wall, ht[1] - wall, h, cx, cz), _rect(hf[0] - wall, hf[1] - wall, floor, cx, cz)
    polys = _ring_quads(ob, ot, 1, (cx, cz))            # outside walls
    polys += _flat_ring(ot, it, 1)                       # the rim
    polys += _ring_quads(if_, it, -1, (cx, cz))         # inside walls, facing in
    polys.append((if_, (0, 1, 0)))                       # inner floor
    polys.append((ob, (0, -1, 0)))                       # bottom
    return scenes.poly_mesh(polys, material)


def band(w0: float, d0: float, w1: float, d1: float, h: float, y0: float, y1: float, proud: float,
         material: str = "default", cx: float = 0.0, cz: float = 0.0) -> mesh.Mesh:
    """A strap `proud` thick round the outside of a `tray` of the same w0, d0, w1, d1, h, between heights y0 and y1."""
    def half(y, grow=0.0):
        t = y / h
        return (w0 + (w1 - w0) * t) / 2 + grow, (d0 + (d1 - d0) * t) / 2 + grow

    o0, o1 = _rect(*half(y0, proud), y0, cx, cz), _rect(*half(y1, proud), y1, cx, cz)
    i0, i1 = _rect(*half(y0), y0, cx, cz), _rect(*half(y1), y1, cx, cz)
    return scenes.poly_mesh(_ring_quads(o0, o1, 1, (cx, cz)) + _flat_ring(o1, i1, 1) + _flat_ring(o0, i0, -1),
                            material)
