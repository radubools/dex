"""Open gable roofs, gable infills and lean-to roofs as prisms along X, so gable ends and pediments can show.

`blocks.roof_gable` closes its ends with roof material. These build the roof
as two separate slabs instead, so a gable wall (brick, plaster, a painted
pediment) can sit under it. All are meshes on y = 0, centred on the origin,
ridge or length along X; rotate them for other directions.

    from utils import gables
    roof = gables.gable_slopes(2.7, 1.3, 0.36)                     # two slopes + ridge cap
    ends = [gables.gable_wall(1.3, 0.36, x, "brick") for x in (-1.33, 1.33)]
"""

from __future__ import annotations

from typing import Sequence

from . import plaques
from .mesh import Mesh, box


def prism_x(profile: Sequence[tuple[float, float]], length: float, mat: str = "default", x: float = 0.0) -> Mesh:
    """A prism along X, centred on `x`, whose cross-section is `profile`, a polygon of (z, y) points."""
    return plaques.slab_xy(list(profile), -length / 2, length / 2, mat).rotate_y(-90).translate(x, 0, 0)


def gable_slopes(length: float, depth: float, h: float, mat: str = "roof", *, overhang: float = 0.07,
                 t: float = 0.04, ridge: str | None = "roof_trim") -> list[Mesh]:
    """An open-ended gable roof along X on y = 0: two slabs `t` thick to a ridge at `h`, and a ridge cap."""
    s = h / (depth / 2)
    ze, ye = depth / 2 + overhang, -overhang * s
    L = length + 2 * overhang
    out = [prism_x([(sz * ze, ye), (0, h), (0, h + t), (sz * ze, ye + t)], L, mat) for sz in (1, -1)]
    if ridge:
        out.append(box(L + 0.02, 0.035, 0.05, ridge).translate(0, h, 0))
    return out


def gable_wall(depth: float, h: float, x: float, mat: str = "wall", thick: float = 0.03) -> Mesh:
    """The triangular gable infill at `x`, under a `gable_slopes` roof of the same depth and height."""
    return plaques.slab_xy([(-depth / 2, 0), (depth / 2, 0), (0, h)], -thick / 2, thick / 2, mat) \
        .rotate_y(-90).translate(x, 0, 0)


def lean_to(length: float, z_out: float, z_in: float, y_out: float, y_in: float, mat: str = "roof",
            t: float = 0.04) -> Mesh:
    """A single-pitch roof slab `t` thick along X, at height y_out over z_out falling to y_in over z_in."""
    return prism_x([(z_out, y_out), (z_in, y_in), (z_in, y_in + t), (z_out, y_out + t)], length, mat)
