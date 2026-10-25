"""Low-poly triangle meshes for game assets: primitives, transforms, UVs, merging.

Conventions every game asset follows (see `catalog` for the constants):

- **Y is up**, right-handed, glTF's own axes. A character faces **+Z**.
- **1 unit = 1 map tile.** A 2x2 landmark is 2 units wide and deep.
- An object's **origin is the centre of its footprint at ground level**, so the
  game places it by moving the root node and nothing has to be offset.

Meshes are flat-shaded by default: every triangle owns its three vertices, so
edges stay crisp at any zoom, which is the look a rotating isometric camera
needs. Pass `smooth=True` for spheres and organic shapes.

UVs are in **world units times `uv_scale`**, projected per face. Two blocks
using one tiling texture therefore line up across their seam and keep the same
texel density, which is what lets modular pieces snap together without visible
stretching.

    from utils import mesh
    wall = mesh.box(2, 1.2, 0.2, material="brick").translate(0, 0.6, 0)
    roof = mesh.gable_roof(2.2, 1.4, 0.6, material="tile").translate(0, 1.2, 0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np


@dataclass
class Mesh:
    """Triangles with one material: positions, normals, UVs, and an index list."""

    positions: np.ndarray
    normals: np.ndarray
    uvs: np.ndarray
    indices: np.ndarray
    material: str = "default"
    #: Free-form notes for the code building the asset. Not written to the GLB,
    #: and dropped by `merge`/`group`; put data the game needs on the node instead
    #: (`scene.node(..., key=value)` lands in the node's `extras`).
    extras: dict = field(default_factory=dict)

    @property
    def triangles(self) -> int:
        """Number of triangles."""
        return int(len(self.indices))

    def copy(self) -> "Mesh":
        """A deep copy."""
        return Mesh(self.positions.copy(), self.normals.copy(), self.uvs.copy(),
                    self.indices.copy(), self.material, dict(self.extras))

    def with_material(self, material: str) -> "Mesh":
        """The same geometry under another material name."""
        out = self.copy()
        out.material = material
        return out

    def transform(self, matrix: np.ndarray) -> "Mesh":
        """Apply a 4x4 matrix to positions and (inverse-transpose) normals."""
        m = np.asarray(matrix, dtype=np.float64)
        p = np.c_[self.positions, np.ones(len(self.positions))] @ m.T
        n = self.normals @ np.linalg.inv(m[:3, :3])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        idx = self.indices
        # A mirroring transform flips winding; swap two corners to keep faces out.
        if np.linalg.det(m[:3, :3]) < 0:
            idx = idx[:, [0, 2, 1]]
        return Mesh(p[:, :3].astype(np.float32), n.astype(np.float32),
                    self.uvs.copy(), idx.copy(), self.material, dict(self.extras))

    def translate(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> "Mesh":
        """Move by (x, y, z)."""
        return self.transform(translation(x, y, z))

    def scale(self, sx: float, sy: float | None = None, sz: float | None = None) -> "Mesh":
        """Scale about the origin; one argument scales uniformly."""
        sy = sx if sy is None else sy
        sz = sx if sz is None else sz
        return self.transform(np.diag([sx, sy, sz, 1.0]))

    def rotate_x(self, degrees: float) -> "Mesh":
        """Rotate about the X axis."""
        return self.transform(rotation_matrix((1, 0, 0), degrees))

    def rotate_y(self, degrees: float) -> "Mesh":
        """Rotate about the Y (up) axis."""
        return self.transform(rotation_matrix((0, 1, 0), degrees))

    def rotate_z(self, degrees: float) -> "Mesh":
        """Rotate about the Z axis."""
        return self.transform(rotation_matrix((0, 0, 1), degrees))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """(min, max) corners of the axis-aligned bounding box."""
        return self.positions.min(axis=0), self.positions.max(axis=0)

    def reproject_uvs(self, uv_scale: float = 1.0) -> "Mesh":
        """Recompute box-projected world-space UVs, after a transform moved things."""
        out = self.copy()
        out.uvs = _box_uvs(out.positions, out.indices, uv_scale)
        return out


# ------------------------------------------------------------------ matrices


def translation(x: float, y: float, z: float) -> np.ndarray:
    """A 4x4 translation matrix."""
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def rotation_matrix(axis: Sequence[float], degrees: float) -> np.ndarray:
    """A 4x4 rotation about `axis` by `degrees` (right-handed)."""
    a = np.asarray(axis, dtype=np.float64)
    a = a / np.linalg.norm(a)
    t = math.radians(degrees)
    c, s, C = math.cos(t), math.sin(t), 1 - math.cos(t)
    x, y, z = a
    m = np.eye(4)
    m[:3, :3] = [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                 [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                 [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]
    return m


def quat_axis_angle(axis: Sequence[float], degrees: float) -> tuple[float, float, float, float]:
    """A unit quaternion (x, y, z, w) — glTF's order — rotating about `axis`."""
    a = np.asarray(axis, dtype=np.float64)
    a = a / np.linalg.norm(a)
    h = math.radians(degrees) / 2
    s = math.sin(h)
    return (float(a[0] * s), float(a[1] * s), float(a[2] * s), float(math.cos(h)))


def quat_mul(q: Sequence[float], r: Sequence[float]) -> tuple[float, float, float, float]:
    """Hamilton product q*r of two (x, y, z, w) quaternions: r first, then q."""
    x1, y1, z1, w1 = q
    x2, y2, z2, w2 = r
    return (w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2)


def quat_euler(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> tuple[float, float, float, float]:
    """Quaternion from Euler degrees, applied X then Y then Z (intrinsic ZYX)."""
    q = quat_axis_angle((1, 0, 0), x)
    q = quat_mul(quat_axis_angle((0, 1, 0), y), q)
    return quat_mul(quat_axis_angle((0, 0, 1), z), q)


def quat_matrix(q: Sequence[float]) -> np.ndarray:
    """3x3 rotation matrix of an (x, y, z, w) quaternion."""
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


# ------------------------------------------------------------------ building


def from_faces(positions: Sequence[Sequence[float]], faces: Iterable[Sequence[int]],
               material: str = "default", *, smooth: bool = False,
               uv_scale: float = 1.0, uvs: np.ndarray | None = None) -> Mesh:
    """Build a mesh from shared vertices and polygon faces (fanned, counter-clockwise).

    Faces wind counter-clockwise seen from outside. Flat shading unshares the
    vertices; `smooth` keeps them shared and averages normals. `uvs`, if given,
    are per input vertex; otherwise UVs are box-projected world units.
    """
    pos = np.asarray(positions, dtype=np.float64)
    tris = []
    for f in faces:
        f = list(f)
        for i in range(1, len(f) - 1):
            tris.append((f[0], f[i], f[i + 1]))
    tri = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    # Collapsed corners (a pyramid's apex, a zero-length ridge) leave slivers
    # with no area and no normal; drop them rather than ship NaN-prone faces.
    area = np.linalg.norm(np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]), axis=1)
    tri = tri[area > 1e-10]
    if smooth:
        normals = np.zeros_like(pos)
        fn = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
        for k in range(3):
            np.add.at(normals, tri[:, k], fn)
        length = np.linalg.norm(normals, axis=1)
        # Vertices no triangle touches (a UV seam's spare pole) still need a unit normal.
        normals[length < 1e-12] = (0.0, 1.0, 0.0)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        uv = uvs if uvs is not None else _box_uvs(pos, tri, uv_scale, per_vertex=True)
        return Mesh(pos.astype(np.float32), normals.astype(np.float32),
                    np.asarray(uv, dtype=np.float32), tri.astype(np.uint32), material)
    p = pos[tri.reshape(-1)]
    idx = np.arange(len(p), dtype=np.uint32).reshape(-1, 3)
    fn = np.cross(p[1::3] - p[0::3], p[2::3] - p[0::3])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    n = np.repeat(fn, 3, axis=0)
    if uvs is not None:
        uv = np.asarray(uvs, dtype=np.float64)[tri.reshape(-1)]
    else:
        uv = _box_uvs(p, idx, uv_scale)
    return Mesh(p.astype(np.float32), n.astype(np.float32), uv.astype(np.float32), idx, material)


def _box_uvs(pos: np.ndarray, tri: np.ndarray, uv_scale: float, per_vertex: bool = False) -> np.ndarray:
    """World-space UVs projected along each triangle's dominant normal axis."""
    pos = np.asarray(pos, dtype=np.float64)
    uv = np.zeros((len(pos), 2))
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    fn = np.cross(b - a, c - a)
    axis = np.argmax(np.abs(fn), axis=1)
    sign = np.sign(fn[np.arange(len(fn)), axis])
    sign[sign == 0] = 1
    for t, (ax, sg) in enumerate(zip(axis, sign)):
        for v in tri[t]:
            x, y, z = pos[v]
            if ax == 0:      # facing ±X: u along -Z/+Z, v up
                u_, v_ = (-z if sg > 0 else z), y
            elif ax == 1:    # facing ±Y: top/bottom
                u_, v_ = x, (-z if sg > 0 else z)
            else:            # facing ±Z
                u_, v_ = (x if sg > 0 else -x), y
            uv[v] = (u_ * uv_scale, 1.0 - v_ * uv_scale)
    return uv


def merge(meshes: Iterable[Mesh], material: str | None = None) -> Mesh:
    """Concatenate meshes into one; they must share a material unless one is given."""
    meshes = [m for m in meshes if m.triangles]
    if not meshes:
        raise ValueError("nothing to merge")
    mats = {m.material for m in meshes}
    if material is None:
        if len(mats) > 1:
            raise ValueError(f"meshes have several materials {sorted(mats)}; use group()")
        material = meshes[0].material
    offset, idx = 0, []
    for m in meshes:
        idx.append(m.indices.astype(np.int64) + offset)
        offset += len(m.positions)
    return Mesh(np.concatenate([m.positions for m in meshes]).astype(np.float32),
                np.concatenate([m.normals for m in meshes]).astype(np.float32),
                np.concatenate([m.uvs for m in meshes]).astype(np.float32),
                np.concatenate(idx).astype(np.uint32), material)


def group(meshes: Iterable[Mesh]) -> list[Mesh]:
    """Merge meshes per material: one mesh per material, fewest draw calls."""
    by: dict[str, list[Mesh]] = {}
    for m in meshes:
        if m.triangles:
            by.setdefault(m.material, []).append(m)
    return [merge(ms) for ms in by.values()]


# ------------------------------------------------------------------ primitives


def box(w: float, h: float, d: float, material: str = "default", *,
        uv_scale: float = 1.0, centered_y: bool = False) -> Mesh:
    """A w (X) by h (Y) by d (Z) box, sitting on y=0 unless `centered_y`."""
    x, z = w / 2, d / 2
    y0, y1 = (-h / 2, h / 2) if centered_y else (0.0, h)
    v = [(-x, y0, -z), (x, y0, -z), (x, y1, -z), (-x, y1, -z),
         (-x, y0, z), (x, y0, z), (x, y1, z), (-x, y1, z)]
    faces = [(4, 5, 6, 7), (1, 0, 3, 2), (5, 1, 2, 6), (0, 4, 7, 3), (7, 6, 2, 3), (0, 1, 5, 4)]
    return from_faces(v, faces, material, uv_scale=uv_scale)


def frustum(w0: float, d0: float, w1: float, d1: float, h: float, material: str = "default", *,
            uv_scale: float = 1.0, shift: tuple[float, float] = (0.0, 0.0)) -> Mesh:
    """A tapered box: base w0 x d0 on y=0, top w1 x d1 at y=h, top offset by `shift`."""
    sx, sz = shift
    v = [(-w0 / 2, 0, -d0 / 2), (w0 / 2, 0, -d0 / 2), (w0 / 2, 0, d0 / 2), (-w0 / 2, 0, d0 / 2),
         (-w1 / 2 + sx, h, -d1 / 2 + sz), (w1 / 2 + sx, h, -d1 / 2 + sz),
         (w1 / 2 + sx, h, d1 / 2 + sz), (-w1 / 2 + sx, h, d1 / 2 + sz)]
    faces = [(3, 2, 6, 7), (1, 0, 4, 5), (2, 1, 5, 6), (0, 3, 7, 4), (7, 6, 5, 4), (0, 1, 2, 3)]
    if w1 <= 1e-9 and d1 <= 1e-9:  # a pyramid: drop the degenerate top
        faces = faces[:4] + [faces[5]]
    return from_faces(v, faces, material, uv_scale=uv_scale)


def pyramid(w: float, d: float, h: float, material: str = "default", **kw) -> Mesh:
    """A four-sided pyramid on y=0 with its apex at y=h."""
    return frustum(w, d, 0.0, 0.0, h, material, **kw)


def plane(w: float, d: float, material: str = "default", *, uv_scale: float = 1.0) -> Mesh:
    """A flat w x d rectangle at y=0 facing up."""
    v = [(-w / 2, 0, d / 2), (w / 2, 0, d / 2), (w / 2, 0, -d / 2), (-w / 2, 0, -d / 2)]
    return from_faces(v, [(0, 1, 2, 3)], material, uv_scale=uv_scale)


def cylinder(radius: float, height: float, material: str = "default", *, segments: int = 12,
             radius_top: float | None = None, caps: bool = True, smooth: bool = False,
             uv_scale: float = 1.0) -> Mesh:
    """A cylinder (or cone/frustum with `radius_top`) on y=0, axis along Y.

    Side UVs wrap around the circumference in world units, so a texture keeps
    its density on a fat column and a thin one alike.
    """
    rt = radius if radius_top is None else radius_top
    pos, uvs, faces = [], [], []
    circ = 2 * math.pi * max(radius, rt)
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        ca, sa = math.cos(a), math.sin(a)
        u = (i / segments) * circ * uv_scale
        pos += [(radius * ca, 0.0, radius * sa), (rt * ca, height, rt * sa)]
        uvs += [(u, 1.0), (u, 1.0 - height * uv_scale)]
    for i in range(segments):
        a, b, c, d = 2 * i, 2 * i + 2, 2 * i + 3, 2 * i + 1
        faces.append((a, d, c, b))
    side = from_faces(pos, faces, material, smooth=smooth, uvs=np.asarray(uvs))
    parts = [side]
    if caps:
        ring = [(math.cos(2 * math.pi * i / segments), math.sin(2 * math.pi * i / segments))
                for i in range(segments)]
        if rt > 1e-9:
            top = [(rt * c, height, rt * s) for c, s in ring]
            parts.append(from_faces(top, [list(range(segments))[::-1]], material, uv_scale=uv_scale))
        if radius > 1e-9:
            bot = [(radius * c, 0.0, radius * s) for c, s in ring]
            parts.append(from_faces(bot, [list(range(segments))], material, uv_scale=uv_scale))
    return merge(parts)


def cone(radius: float, height: float, material: str = "default", **kw) -> Mesh:
    """A cone on y=0 with its tip at y=height."""
    return cylinder(radius, height, material, radius_top=0.0, **kw)


def sphere(radius: float, material: str = "default", *, segments: int = 12, rings: int = 8,
           smooth: bool = True, centered: bool = True) -> Mesh:
    """A UV sphere, centred on the origin (or resting on y=0 when not `centered`)."""
    pos, uvs, faces = [], [], []
    cy = 0.0 if centered else radius
    for r in range(rings + 1):
        phi = math.pi * r / rings
        for s in range(segments + 1):
            th = 2 * math.pi * s / segments
            pos.append((radius * math.sin(phi) * math.cos(th), cy + radius * math.cos(phi),
                        radius * math.sin(phi) * math.sin(th)))
            uvs.append((s / segments, r / rings))
    row = segments + 1
    for r in range(rings):
        for s in range(segments):
            a, b = r * row + s, r * row + s + 1
            c, d = (r + 1) * row + s + 1, (r + 1) * row + s
            if r == 0:
                faces.append((a, c, d))
            elif r == rings - 1:
                faces.append((a, b, d))
            else:
                faces.append((a, b, c, d))
    return from_faces(pos, faces, material, smooth=smooth, uvs=np.asarray(uvs))


def gable_roof(w: float, d: float, h: float, material: str = "default", *,
               overhang: float = 0.0, uv_scale: float = 1.0) -> Mesh:
    """A gable roof on y=0: ridge along X at height h, slopes facing ±Z."""
    x, z = w / 2 + overhang, d / 2 + overhang
    v = [(-x, 0, -z), (x, 0, -z), (x, 0, z), (-x, 0, z), (-x, h, 0), (x, h, 0)]
    faces = [(3, 2, 5, 4), (1, 0, 4, 5), (2, 1, 5), (0, 3, 4), (0, 1, 2, 3)]
    return from_faces(v, faces, material, uv_scale=uv_scale)


def hip_roof(w: float, d: float, h: float, material: str = "default", *,
             overhang: float = 0.0, ridge: float | None = None, uv_scale: float = 1.0) -> Mesh:
    """A hip roof: four slopes, a ridge along X of length `ridge` (default w - d)."""
    x, z = w / 2 + overhang, d / 2 + overhang
    r = max(0.0, (w - d) if ridge is None else ridge) / 2
    v = [(-x, 0, -z), (x, 0, -z), (x, 0, z), (-x, 0, z), (-r, h, 0), (r, h, 0)]
    faces = [(3, 2, 5, 4), (1, 0, 4, 5), (2, 1, 5), (0, 3, 4), (0, 1, 2, 3)]
    return from_faces(v, faces, material, uv_scale=uv_scale)


def curved_roof(w: float, d: float, h: float, material: str = "default", *, overhang: float = 0.2,
                lift: float = 0.15, steps: int = 4, uv_scale: float = 1.0) -> Mesh:
    """A concave East Asian hip roof; `lift` raises the eave line (use `blocks.roof_curved` for corner upsweep)."""
    x, z = w / 2 + overhang, d / 2 + overhang
    r = max(0.0, w - d) / 2
    pos, faces = [], []

    def ring(t: float) -> list[tuple[float, float, float]]:
        # t=0 eave, t=1 ridge; concave profile, corners raised by lift*(1-t)^2
        k = t ** 0.7
        hx, hz = x + (r - x) * k, z * (1 - k)
        y = h * (1 - (1 - t) ** 1.6)
        up = lift * (1 - t) ** 2
        return [(-hx, y + up, -hz), (hx, y + up, -hz), (hx, y + up, hz), (-hx, y + up, hz)]

    for s in range(steps + 1):
        pos += ring(s / steps)
    for s in range(steps):
        a, b = 4 * s, 4 * (s + 1)
        for k in range(4):
            k2 = (k + 1) % 4
            faces.append((a + k2, a + k, b + k, b + k2))
    faces.append((0, 1, 2, 3))
    return from_faces(pos, faces, material, uv_scale=uv_scale)


def extrude(polygon: Sequence[tuple[float, float]], height: float, material: str = "default", *,
            uv_scale: float = 1.0, caps: bool = True) -> Mesh:
    """Extrude a simple 2D polygon of (x, z) points, in either winding, from y=0 up to `height`."""
    pts = [(float(x), float(z)) for x, z in polygon]
    if _signed_area(pts) > 0:  # make it CCW when viewed from +Y (x right, z toward viewer)
        pts = pts[::-1]
    n = len(pts)
    pos = [(x, 0.0, z) for x, z in pts] + [(x, height, z) for x, z in pts]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    tris = triangulate(pts)
    if caps:
        faces += [(n + a, n + c, n + b) for a, b, c in tris]
        faces += [(a, b, c) for a, b, c in tris]
    return from_faces(pos, faces, material, uv_scale=uv_scale)


def _signed_area(pts: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(pts, pts[1:] + pts[:1]))


def triangulate(pts: Sequence[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear-clip a simple 2D polygon into triangles of its point indices."""
    idx = list(range(len(pts)))
    if _signed_area(list(pts)) < 0:
        idx.reverse()
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(cross(a, b, pts[j]) >= 0 and cross(b, c, pts[j]) >= 0 and cross(c, a, pts[j]) >= 0
                   for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        out.append(tuple(idx))
    return out


def lathe(profile: Sequence[tuple[float, float]], material: str = "default", *, segments: int = 16,
          smooth: bool = True, uv_scale: float = 1.0) -> Mesh:
    """Spin a profile of (radius, y) points, bottom to top, around the Y axis."""
    pos, uvs, faces = [], [], []
    n = len(profile)
    for s in range(segments + 1):
        a = 2 * math.pi * s / segments
        for r, y in profile:
            pos.append((r * math.cos(a), y, r * math.sin(a)))
            uvs.append((s / segments * 2 * math.pi * max(p[0] for p in profile) * uv_scale,
                        1 - y * uv_scale))
    for s in range(segments):
        for k in range(n - 1):
            a, b = s * n + k, (s + 1) * n + k
            faces.append((a, a + 1, b + 1, b))
    return from_faces(pos, faces, material, smooth=smooth, uvs=np.asarray(uvs))


def beam(p0: Sequence[float], p1: Sequence[float], thickness: float, material: str = "default", *,
         depth: float | None = None, uv_scale: float = 1.0) -> Mesh:
    """A square-section bar from p0 to p1 — struts, lattice members, poles, rails."""
    a, b = np.asarray(p0, dtype=np.float64), np.asarray(p1, dtype=np.float64)
    length = float(np.linalg.norm(b - a))
    m = box(thickness, length, depth or thickness, material, uv_scale=uv_scale)
    d = (b - a) / max(length, 1e-12)
    up = np.array([0.0, 1.0, 0.0])
    axis = np.cross(up, d)
    s = np.linalg.norm(axis)
    if s > 1e-9:
        ang = math.degrees(math.atan2(s, float(np.dot(up, d))))
        m = m.transform(rotation_matrix(axis, ang))
    elif d[1] < 0:
        m = m.rotate_x(180)
    return m.translate(*a)


def array_around(m: Mesh, count: int, radius: float = 0.0, *, face_out: bool = True) -> Mesh:
    """`count` copies spaced evenly around the Y axis at `radius`."""
    parts = []
    for i in range(count):
        ang = 360.0 * i / count
        if face_out:
            parts.append(m.translate(radius, 0, 0).rotate_y(ang))
        else:
            a = math.radians(ang)
            parts.append(m.translate(radius * math.cos(a), 0, -radius * math.sin(a)))
    return merge(parts)
