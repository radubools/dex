"""Compose built scenes and custom polygons: instance one scene in another, wind polygons outward."""

from __future__ import annotations

import dataclasses
from typing import Sequence

import numpy as np

from . import gltf, mesh


def poly_mesh(polys: Sequence[tuple[Sequence[Sequence[float]], Sequence[float]]], material: str = "default",
              *, smooth: bool = False) -> mesh.Mesh:
    """A mesh from (points, outward direction) polygons, each wound so its normal faces its outward direction."""
    pos, faces = [], []
    for pts, outward in polys:
        p = np.asarray(pts, dtype=np.float64)
        n = np.zeros(3)
        for i in range(len(p)):  # Newell normal: robust for any planar polygon
            a, b = p[i], p[(i + 1) % len(p)]
            n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]),
                           (a[0] - b[0]) * (a[1] + b[1])])
        idx = list(range(len(pos), len(pos) + len(p)))
        faces.append(idx if np.dot(n, outward) >= 0 else idx[::-1])
        pos += [tuple(v) for v in p]
    return mesh.from_faces(pos, faces, material, smooth=smooth)


def place(dst: gltf.Scene, src: gltf.Scene, label: str, *, t: Sequence[float] = (0, 0, 0),
          r: Sequence[float] = (0, 0, 0, 1), s: Sequence[float] = (1, 1, 1),
          parent: gltf.Node | None = None) -> gltf.Node:
    """Instance `src` in `dst` under a wrapper node `label` (translation, rotation quat, scale); returns the wrapper.

    Materials are copied in as `<src.name>:<material>` and nodes as `<label>/<node>`, so the same piece can be
    placed many times and two pieces may share material names.
    """
    wrap = dst.node(label, parent=parent, translation=t, rotation=r, scale=s)
    for name, mat in src.materials.items():
        key = f"{src.name}:{name}"
        if key not in dst.materials:
            dst.materials[key] = dataclasses.replace(mat, name=key)

    def copy(n: gltf.Node, under: gltf.Node) -> None:
        meshes = [m.with_material(f"{src.name}:{m.material}") for m in n.meshes]
        c = dst.node(f"{label}/{n.name}", meshes=meshes, parent=under, translation=n.translation,
                     rotation=n.rotation, scale=n.scale)
        for ch in n.children:
            copy(ch, c)

    for root in src.roots:
        copy(root, wrap)
    return wrap
