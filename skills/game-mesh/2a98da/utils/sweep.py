"""Swept meshes along 3D curves: Catmull-Rom splines and tubes or ribbons of varying radius, rolled and twisted."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from . import mesh


def spline(ctrl: Sequence[Sequence[float]], n: int, *, closed: bool = False) -> np.ndarray:
    """`n` points along a uniform Catmull-Rom spline through the control points (end point excluded when closed)."""
    P = np.asarray(ctrl, float)
    if closed:
        P = np.vstack([P[-1:], P, P[:2]])
    else:
        P = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    segs = len(P) - 3
    out = []
    for k in range(n):
        u = k / n * segs if closed else k / (n - 1) * segs
        i = min(int(u), segs - 1)
        t = u - i
        p0, p1, p2, p3 = P[i:i + 4]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return np.asarray(out)


def stroke(path: Sequence[Sequence[float]], radii: float | Sequence[float], material: str = "default", *,
           sides: int = 7, flat: float = 1.0, up: Sequence[float] = (0.0, 1.0, 0.0), twist: float = 0.0,
           roll: float = 0.0, closed: bool = False) -> mesh.Mesh:
    """A flat-shaded tube swept along `path` with a radius per point (or one for all).

    The section is an ellipse `flat` times as thick as it is wide, wide along the
    parallel-transported frame normal that starts nearest `up`; it is rolled by
    `roll` degrees and twisted by `twist` degrees from end to end. Open paths get
    end caps. A closed path wraps without a seam; its twist is rounded to whole
    half-turns (a half-turn, a Moebius band, needs an even `sides`).
    """
    P = np.asarray(path, float)
    R = np.broadcast_to(np.asarray(radii, float), (len(P),))
    n = len(P)
    T = (np.roll(P, -1, 0) - np.roll(P, 1, 0)) if closed else np.gradient(P, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    up = np.asarray(up, float)
    N0 = up - up.dot(T[0]) * T[0]
    if np.linalg.norm(N0) < 1e-6:
        N0 = np.cross(T[0], (1.0, 0.0, 0.0))
    N = [N0 / np.linalg.norm(N0)]
    for i in range(1, n):                                  # parallel transport by projection
        v = N[-1] - N[-1].dot(T[i]) * T[i]
        N.append(v / np.linalg.norm(v))
    N = np.asarray(N)
    shift = 0
    turn = np.full(n, math.radians(roll)) + np.linspace(0, math.radians(twist), n)
    if closed:                                             # close the frame: spread the transport mismatch
        v = N[-1] - N[-1].dot(T[0]) * T[0]
        v /= np.linalg.norm(v)
        phi = math.atan2(np.cross(v, N[0]).dot(T[0]), v.dot(N[0]))
        halves = round(twist / 180.0)
        if halves % 2 and sides % 2:
            raise ValueError("a half-twisted closed stroke needs an even side count")
        turn = math.radians(roll) + (phi + halves * math.pi) * np.arange(n) / n
        shift = (halves * sides // 2) % sides
    B = np.cross(T, N)
    th = np.linspace(0, 2 * math.pi, sides, endpoint=False)
    pts = []
    for i in range(n):
        c, s = math.cos(turn[i]), math.sin(turn[i])
        Ni, Bi = c * N[i] + s * B[i], -s * N[i] + c * B[i]          # the section's axes, rolled and twisted
        pts.append(P[i] + R[i] * (np.cos(th)[:, None] * Ni + flat * np.sin(th)[:, None] * Bi))
    pts = np.vstack(pts)
    faces = []
    for i in range(n if closed else n - 1):
        for j in range(sides):
            j1 = (j + 1) % sides
            if closed and i == n - 1:
                faces.append([i * sides + j, i * sides + j1, (j1 + shift) % sides, (j + shift) % sides])
            else:
                faces.append([i * sides + j, i * sides + j1, (i + 1) * sides + j1, (i + 1) * sides + j])
    if not closed:
        faces.append(list(range(sides))[::-1])
        faces.append([(n - 1) * sides + j for j in range(sides)])
    return mesh.from_faces(pts, faces, material)
