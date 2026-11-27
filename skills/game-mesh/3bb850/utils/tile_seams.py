"""Numeric seam checks for tileable models: compare the top surfaces of two written GLBs along a shared tile edge."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

from . import gltf as _gltf
from .mesh import rotation_matrix

_CACHE: dict = {}


def top_surface(glb: str | Path, rot: float = 0.0, x: float = 0.0, z: float = 0.0, *, min_up: float = 0.9):
    """Up-facing triangles of a written GLB, turned by `rot` degrees about +Y and moved to (x, z): (tris (n,3,3), material names (n,))."""
    key = (str(Path(glb).resolve()), float(rot), float(min_up))
    if key not in _CACHE:
        g = _gltf.read(glb)
        names = [m.get("name", "") for m in g.doc.get("materials", [])]
        ps, ms = [], []
        for p, _uv, n, mi in g.triangles():
            up = n.mean(axis=1)[:, 1] > min_up
            ps.append(p[up])
            ms += [names[mi] if 0 <= mi < len(names) else ""] * int(up.sum())
        tris = np.concatenate(ps) if ps else np.zeros((0, 3, 3))
        R = rotation_matrix((0, 1, 0), rot)[:3, :3]
        _CACHE[key] = (tris @ R.T, np.array(ms, dtype=object))
    tris, mats = _CACHE[key]
    return tris + np.array([x, 0.0, z]), mats


def surface_at(tris: np.ndarray, mats: np.ndarray, points: Sequence[Sequence[float]]):
    """Height and material of the highest triangle over each (x, z) point: (heights, materials); -1 and '' where nothing covers it."""
    out_y = np.full(len(points), -1.0)
    out_m = np.array([""] * len(points), dtype=object)
    if len(tris) == 0:
        return out_y, out_m
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    v0 = c[:, [0, 2]] - a[:, [0, 2]]
    v1 = b[:, [0, 2]] - a[:, [0, 2]]
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-14
    safe = np.where(ok, den, 1.0)
    for k, (px, pz) in enumerate(points):
        v2 = np.array([px, pz]) - a[:, [0, 2]]
        d02, d12 = (v0 * v2).sum(1), (v1 * v2).sum(1)
        u = np.where(ok, (d11 * d02 - d01 * d12) / safe, -1.0)
        v = np.where(ok, (d00 * d12 - d01 * d02) / safe, -1.0)
        inside = (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
        if inside.any():
            y = np.where(inside, a[:, 1] + u * (c[:, 1] - a[:, 1]) + v * (b[:, 1] - a[:, 1]), -np.inf)
            i = int(np.argmax(y))
            out_y[k], out_m[k] = y[i], mats[i]
    return out_y, out_m


def edge_mismatch(glb_a: str | Path, rot_a: float, glb_b: str | Path, rot_b: float, *, n: int = 400,
                  eps: float = 1e-6, tile: float = 1.0, height_tol: float = 1e-4) -> float:
    """Fraction of points along the edge between tile A (west, centred at 0) and tile B (east, one tile over) whose top height or material differ.

    Keep `eps` tiny: a joint running almost along the edge counts as a mismatch over a width that grows with it.
    For a north-south pair, add 90 to both rotations (a quarter turn makes the south tile the east one).
    """
    h = tile / 2
    zs = np.linspace(-h * 0.99, h * 0.99, n)
    ta, ma = top_surface(glb_a, rot_a, 0.0, 0.0)
    tb, mb = top_surface(glb_b, rot_b, tile, 0.0)
    ya, na = surface_at(ta, ma, [(h - eps, z) for z in zs])
    yb, nb = surface_at(tb, mb, [(h + eps, z) for z in zs])
    return float(((np.abs(ya - yb) > height_tol) | (na != nb)).mean())
