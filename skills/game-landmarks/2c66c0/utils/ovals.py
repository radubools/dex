"""Oval rings: sweep a (d, y) wall profile round an ellipse along its normal, for amphitheatres, circuses and round towers.

d is the distance out from the base ellipse (semi-axes `ax` along X, `az`
along Z) along its outward normal, so a profile drawn once becomes a ring of
constant width (walls, stepped seating, cornices). Lines of constant t are
straight, so a partial ring can be capped flat (gate passages).

    from utils import ovals
    seats = ovals.oval_ring(1.36, 1.84, [(0.47, 1.0, "seat"), (0.07, 0.26, "trim"), (0.0, 0.26, "wall"),
                                         (0.0, 0.03, None), (0.47, 0.03, None)], 1.75, 4.5, cap="stone")
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from . import mesh


def oval_point(ax: float, az: float, t: float, d: float = 0.0) -> tuple[float, float, float, float]:
    """(x, z, nx, nz): the point at parameter t (radians) on the ellipse, pushed out by d along its unit normal."""
    c, s = math.cos(t), math.sin(t)
    nx, nz = c / ax, s / az
    n = math.hypot(nx, nz)
    nx, nz = nx / n, nz / n
    return ax * c + d * nx, az * s + d * nz, nx, nz


def tri_mesh(tris: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], mat: str) -> mesh.Mesh:
    """A flat-shaded mesh from (a, b, c, outward) triangles, each wound to face its outward vector."""
    pos, faces = [], []
    for a, b, c, out in tris:
        a, b, c = (np.asarray(v, dtype=float) for v in (a, b, c))
        if np.dot(np.cross(b - a, c - a), out) < 0:
            b, c = c, b
        i = len(pos)
        pos += [tuple(a), tuple(b), tuple(c)]
        faces.append((i, i + 1, i + 2))
    return mesh.from_faces(pos, faces, mat)


def oval_ring(ax: float, az: float, profile: Sequence[tuple[float, float, str | None]], t0: float = 0.0,
              t1: float = 2 * math.pi, *, cap: str | None = None, seg: int = 48) -> list[mesh.Mesh]:
    """Sweep a closed (d, y, material) profile, solid on its left, round the ellipse from t0 to t1; one mesh per material.

    Each edge takes the material of its first point; None leaves it out (a
    hidden face). `cap` closes both ends of a partial ring with flat faces.
    `seg` is the number of segments a full turn would have.
    """
    def at(t, d, y):
        x, z, _, _ = oval_point(ax, az, t, d)
        return np.array([x, y, z])

    n = max(2, int(round(seg * (t1 - t0) / (2 * math.pi))))
    ts = np.linspace(t0, t1, n + 1)
    by_mat: dict[str, list] = {}
    m = len(profile)
    for i in range(m):
        d0, y0, mat = profile[i]
        d1, y1, _ = profile[(i + 1) % m]
        if mat is None:
            continue
        nd, ny = (y1 - y0), -(d1 - d0)              # outward normal in the profile plane
        for k in range(n):
            ta, tb = ts[k], ts[k + 1]
            _, _, nx, nz = oval_point(ax, az, (ta + tb) / 2)
            out = np.array([nd * nx, ny, nd * nz])
            p00, p01 = at(ta, d0, y0), at(ta, d1, y1)
            p10, p11 = at(tb, d0, y0), at(tb, d1, y1)
            by_mat.setdefault(mat, []).extend([(p00, p10, p11, out), (p00, p11, p01, out)])
    if cap and (t1 - t0) < 2 * math.pi - 1e-6:
        pts2 = [(d, y) for d, y, _ in profile]
        for t, sgn in ((t0, -1), (t1, 1)):
            tan = at(t + 1e-4, 0, 0) - at(t - 1e-4, 0, 0)
            out = sgn * tan / np.linalg.norm(tan)
            for i, j, k in mesh.triangulate(pts2):
                by_mat.setdefault(cap, []).append((at(t, *pts2[i]), at(t, *pts2[j]), at(t, *pts2[k]), out))
    return [tri_mesh(tris, mat) for mat, tris in by_mat.items()]
