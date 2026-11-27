"""Small operations on built meshes: stand a primitive between two points, split triangles by a predicate."""

from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np

from . import mesh


def along(m: mesh.Mesh, p0: Sequence[float], p1: Sequence[float]) -> mesh.Mesh:
    """Stand a +Y primitive (built on y = 0) at p0, its axis turned to point at p1."""
    d = np.array(p1, float) - np.array(p0, float)
    d /= np.linalg.norm(d)
    y = np.array([0.0, 1.0, 0.0])
    axis = np.cross(y, d)
    ang = math.degrees(math.acos(float(np.clip(y @ d, -1, 1))))
    rot = mesh.rotation_matrix(axis if np.linalg.norm(axis) > 1e-9 else (1, 0, 0), ang)
    return m.transform(mesh.translation(*p0) @ rot)


def split_faces(m: mesh.Mesh, pick: Callable[[np.ndarray, np.ndarray], bool]) -> tuple[mesh.Mesh, mesh.Mesh]:
    """Split a mesh's triangles in two by `pick(centroid, normal) -> bool`: (picked, rest), same material."""
    tri = m.indices
    c = m.positions[tri].mean(axis=1)
    n = m.normals[tri].mean(axis=1)
    keep = np.array([bool(pick(ci, ni)) for ci, ni in zip(c, n)], dtype=bool)

    def sub(mask):
        return mesh.Mesh(m.positions.copy(), m.normals.copy(), m.uvs.copy(), tri[mask].copy(), m.material)
    return sub(keep), sub(~keep)
