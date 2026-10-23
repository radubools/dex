"""Render a `.glb` to PNG with a small software rasteriser — look at what you built.

No GPU, no browser: the file is read back with `gltf.read`, posed, and drawn
with textures, a sun, and a tile-checker floor for scale. That is the point of
it — a preview made from the written file proves the file, not the Python that
meant to write it. Use the PNGs to judge an asset by eye before calling it done.

The camera is orthographic. `yaw` turns around the vertical axis (45 is the
classic isometric corner view), `pitch` is the elevation; 30 degrees gives the
2:1 pixel ratio of the game's current isometric tiles.

    from utils import preview
    preview.render("tower.glb", "tower.png")
    preview.turntable("tower.glb", "tower_turntable.png")          # 4 corners
    preview.clip_strip("builder.glb", "builder_walk.png", "walk")  # 8 frames
    preview.stage_strip("tower.glb", "tower_stages.png")           # build-up
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw

from . import gltf as _gltf

SUN = np.array([-0.45, 0.8, 0.4]) / np.linalg.norm([-0.45, 0.8, 0.4])
AMBIENT, DIFFUSE = 0.42, 0.68


def _camera(yaw: float, pitch: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y, p = math.radians(yaw), math.radians(pitch)
    toward = np.array([math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)])
    right = np.cross(-toward, [0, 1, 0])
    right /= np.linalg.norm(right)
    up = np.cross(right, -toward)
    return right, up, toward


def _scene_triangles(g: _gltf.Gltf, clip, time, hidden):
    mats = g.doc.get("materials", [])
    tex_cache: dict[int, np.ndarray] = {}
    out = []
    for p, uv, n, mi in g.triangles(clip, time, hidden):
        m = mats[mi] if 0 <= mi < len(mats) else {}
        pbr = m.get("pbrMetallicRoughness", {})
        base = np.asarray(pbr.get("baseColorFactor", [1, 1, 1, 1]), dtype=np.float64)
        tex = None
        if "baseColorTexture" in pbr:
            ti = pbr["baseColorTexture"]["index"]
            if ti not in tex_cache:
                tex_cache[ti] = np.asarray(g.image(ti), dtype=np.float64) / 255
            tex = tex_cache[ti]
        emis_tex = None
        if "emissiveTexture" in m:
            ti = m["emissiveTexture"]["index"]
            if ti not in tex_cache:
                tex_cache[ti] = np.asarray(g.image(ti), dtype=np.float64) / 255
            emis_tex = tex_cache[ti]
        out.append((p, uv, n, base, tex, np.asarray(m.get("emissiveFactor", [0, 0, 0])), emis_tex,
                    m.get("alphaMode", "OPAQUE"), bool(m.get("doubleSided"))))
    return out


def _bounds(groups) -> tuple[np.ndarray, np.ndarray]:
    pts = np.concatenate([g[0].reshape(-1, 3) for g in groups]) if groups else np.zeros((1, 3))
    return pts.min(0), pts.max(0)


def _floor(lo: np.ndarray, hi: np.ndarray):
    """Checker tiles one unit wide under the model, one tile of margin each side."""
    x0, x1 = math.floor(lo[0]) - 1, math.ceil(hi[0]) + 1
    z0, z1 = math.floor(lo[2]) - 1, math.ceil(hi[2]) + 1
    tris, cols = [], []
    for x in range(x0, x1):
        for z in range(z0, z1):
            c = 0.80 if (x + z) % 2 == 0 else 0.70
            a, b, cc, d = (x, -0.002, z), (x + 1, -0.002, z), (x + 1, -0.002, z + 1), (x, -0.002, z + 1)
            tris += [(a, d, cc), (a, cc, b)]
            cols += [c, c]
    return np.asarray(tris, dtype=np.float64), np.asarray(cols)


def render(path: str | Path, out: str | Path | None = None, *, size: int = 512, yaw: float = 45.0,
           pitch: float = 30.0, clip: str | None = None, time: float = 0.0, hidden: Sequence[str] = (),
           frame: tuple[np.ndarray, np.ndarray] | None = None, floor: bool = True,
           background: tuple[int, int, int] = (238, 240, 244), supersample: int = 2) -> Image.Image:
    """Render one view of a `.glb`; also write it to `out` if given.

    `frame` is a world-space (min, max) box to fit instead of the model's own
    bounds — pass the same one to every frame of a strip so nothing jumps.
    """
    g = _gltf.read(path)
    groups = _scene_triangles(g, clip, time, hidden)
    lo, hi = frame if frame is not None else _bounds(groups)
    right, up, toward = _camera(yaw, pitch)
    W = H = size * supersample

    # An explicit frame is fitted as given, so it can crop to a spire; the model's
    # own bounds are fitted from the ground up.
    y0 = lo[1] if frame is not None else min(lo[1], 0)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (y0, hi[1]) for z in (lo[2], hi[2])])
    if floor and frame is None:
        fl_lo = np.array([math.floor(lo[0]) - 1, 0, math.floor(lo[2]) - 1])
        fl_hi = np.array([math.ceil(hi[0]) + 1, 0, math.ceil(hi[2]) + 1])
        corners = np.vstack([corners, [[x, 0, z] for x in (fl_lo[0], fl_hi[0]) for z in (fl_lo[2], fl_hi[2])]])
    sx, sy = corners @ right, corners @ up
    span = max(sx.max() - sx.min(), sy.max() - sy.min(), 1e-6) * 1.08
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2
    k = W / span

    def project(p: np.ndarray) -> np.ndarray:
        return np.stack([(p @ right - cx) * k + W / 2, H / 2 - (p @ up - cy) * k, p @ toward], axis=-1)

    color = np.empty((H, W, 3))
    color[:] = np.asarray(background) / 255
    depth = np.full((H, W), -np.inf)

    def raster(tri_s: np.ndarray, shade_fn, blend: float | None = None, cull: bool = True):
        for t, s in enumerate(tri_s):
            (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = s
            area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(area) < 1e-9 or (cull and area > 0):  # screen y points down: CCW-from-front is negative
                continue
            minx, maxx = max(int(math.floor(min(x0, x1, x2))), 0), min(int(math.ceil(max(x0, x1, x2))), W - 1)
            miny, maxy = max(int(math.floor(min(y0, y1, y2))), 0), min(int(math.ceil(max(y0, y1, y2))), H - 1)
            if minx > maxx or miny > maxy:
                continue
            ys, xs = np.mgrid[miny:maxy + 1, minx:maxx + 1] + 0.5
            w0 = ((x1 - xs) * (y2 - ys) - (x2 - xs) * (y1 - ys)) / area
            w1 = ((x2 - xs) * (y0 - ys) - (x0 - xs) * (y2 - ys)) / area
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = w0 * z0 + w1 * z1 + w2 * z2
            region = depth[miny:maxy + 1, minx:maxx + 1]
            vis = inside & (z > region)
            if not vis.any():
                continue
            rgb, alpha = shade_fn(t, w0[vis], w1[vis], w2[vis])
            if alpha is not None:
                keep = alpha >= 0.5
                if blend is None:
                    vis_idx = np.argwhere(vis)[keep]
                    rgb = rgb[keep]
                else:
                    vis_idx = np.argwhere(vis)
            else:
                vis_idx = np.argwhere(vis)
            yy, xx = vis_idx[:, 0] + miny, vis_idx[:, 1] + minx
            if blend is not None:
                a = blend if alpha is None else (alpha * blend)[:, None]
                color[yy, xx] = color[yy, xx] * (1 - a) + rgb * a
            else:
                color[yy, xx] = rgb
                depth[yy, xx] = z[vis][keep] if alpha is not None else z[vis]

    if floor:
        ftris, fcols = _floor(lo, hi)
        fs = project(ftris)
        raster(fs, lambda t, *_: (np.full((_[0].size, 3), fcols[t]), None))

    blended = []
    for p, uv, n, base, tex, emis, emis_tex, alpha_mode, double in groups:
        s = project(p)

        def shade(t, a, b, c, p=p, uv=uv, n=n, base=base, tex=tex, emis=emis, emis_tex=emis_tex, s=s, double=double):
            nn = n[t, 0] * a[:, None] + n[t, 1] * b[:, None] + n[t, 2] * c[:, None]
            nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9)
            if double and np.dot(nn.mean(0), toward) < 0:  # back face of a double-sided material
                nn = -nn
            light = AMBIENT + DIFFUSE * np.clip(nn @ SUN, 0, 1)
            col = np.broadcast_to(base[:3], (a.size, 3)).copy()
            alpha = None
            if tex is not None or emis_tex is not None:
                u = uv[t, 0, 0] * a + uv[t, 1, 0] * b + uv[t, 2, 0] * c
                v = uv[t, 0, 1] * a + uv[t, 1, 1] * b + uv[t, 2, 1] * c
            if tex is not None:
                th, tw = tex.shape[:2]
                px = tex[(np.floor(v * th).astype(int)) % th, (np.floor(u * tw).astype(int)) % tw]
                col *= px[:, :3]
                if tex.shape[2] == 4:
                    alpha = px[:, 3] * base[3]
            out = col * light[:, None]
            if emis_tex is not None:
                eh, ew = emis_tex.shape[:2]
                out += emis_tex[(np.floor(v * eh).astype(int)) % eh, (np.floor(u * ew).astype(int)) % ew][:, :3] * emis * 0.6
            elif emis.any():
                out += emis * 0.6
            return np.clip(out, 0, 1), alpha

        if alpha_mode == "BLEND":
            blended.append((s, shade, base[3], double))
        else:
            raster(s, shade if alpha_mode == "MASK" else (lambda t, a, b, c, f=shade: (f(t, a, b, c)[0], None)),
                   cull=not double)
    for s, shade, op, double in blended:
        order = np.argsort(s[:, :, 2].mean(1))  # far to near
        raster(s[order], lambda t, a, b, c, f=shade, o=order: f(o[t], a, b, c), blend=float(op), cull=not double)

    img = Image.fromarray((np.clip(color, 0, 1) * 255).astype(np.uint8), "RGB")
    if supersample > 1:
        img = img.resize((size, size), Image.LANCZOS)
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
    return img


def _sheet(images: Sequence[Image.Image], labels: Sequence[str], cols: int) -> Image.Image:
    w, h = images[0].size
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (w * cols, (h + 18) * rows), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for i, (img, lab) in enumerate(zip(images, labels)):
        x, y = (i % cols) * w, (i // cols) * (h + 18)
        sheet.paste(img, (x, y + 18))
        draw.text((x + 4, y + 3), lab, fill=(40, 40, 40))
    return sheet


def _union_frame(g: _gltf.Gltf, poses: Sequence[tuple[str | None, float, Sequence[str]]]):
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for clip, t, hidden in poses:
        for p, _, _, _ in g.triangles(clip, t, hidden):
            if len(p):
                lo = np.minimum(lo, p.reshape(-1, 3).min(0))
                hi = np.maximum(hi, p.reshape(-1, 3).max(0))
    return lo, hi


def bounds(path: str | Path, *, pad: float = 0.0, clip: str | None = None, time: float = 0.0,
           hidden: Sequence[str] = ()) -> tuple[np.ndarray, np.ndarray]:
    """A `.glb`'s world (min, max) box, grown by `pad` x its largest span — pass as `frame=` to zoom on a small model."""
    lo, hi = _union_frame(_gltf.read(path), [(clip, time, hidden)])
    margin = pad * float((hi - lo).max())
    return lo - margin, hi + margin


def turntable(path: str | Path, out: str | Path | None = None, *, yaws: Sequence[float] = (45, 135, 225, 315),
              pitch: float = 30.0, size: int = 320, clip: str | None = None, time: float = 0.0,
              floor: bool = True, frame: tuple[np.ndarray, np.ndarray] | None = None,
              hidden: Sequence[str] = ()) -> Image.Image:
    """The model from several corners in one sheet — check every side, not just the front.

    Small models (characters are under one tile) read better with `floor=False`,
    which frames the model alone.
    """
    imgs = [render(path, size=size, yaw=y, pitch=pitch, clip=clip, time=time, floor=floor, frame=frame, hidden=hidden)
            for y in yaws]
    sheet = _sheet(imgs, [f"yaw {y:g}" for y in yaws], len(imgs))
    if out is not None:
        sheet.save(out)
    return sheet


def clip_strip(path: str | Path, out: str | Path | None = None, clip: str = "idle", *, frames: int = 8,
               yaw: float = 45.0, pitch: float = 30.0, size: int = 220, floor: bool = True,
               hidden: Sequence[str] = ()) -> Image.Image:
    """Evenly spaced frames of one clip on a shared frame, so motion reads left to right.

    A looping clip is sampled up to (not including) its end, which equals its
    start; a one-shot clip (`extras.loop` false, e.g. `die`) includes its final pose.
    """
    g = _gltf.read(path)
    a = next((a for a in g.doc.get("animations", []) if a.get("name") == clip), None)
    if a is None:
        raise KeyError(f"no clip {clip!r}; have {g.clip_names()}")
    dur = max(float(g.accessor(a["samplers"][c["sampler"]]["input"])[-1, 0]) for c in a["channels"])
    loop = a.get("extras", {}).get("loop", True)
    times = [dur * i / (frames if loop else max(frames - 1, 1)) for i in range(frames)]
    frame = _union_frame(g, [(clip, t, hidden) for t in times])
    imgs = [render(path, size=size, yaw=yaw, pitch=pitch, clip=clip, time=t, frame=frame, floor=floor, hidden=hidden)
            for t in times]
    sheet = _sheet(imgs, [f"{clip} {t:.2f}s" for t in times], len(imgs))
    if out is not None:
        sheet.save(out)
    return sheet


def stage_strip(path: str | Path, out: str | Path | None = None, *, prefix: str = "stage_",
                yaw: float = 45.0, pitch: float = 30.0, size: int = 240,
                hidden: Sequence[str] = ("scaffold",)) -> Image.Image:
    """A landmark at each construction stage: stage nodes hidden from the top down (scaffold hidden too)."""
    g = _gltf.read(path)
    stages = sorted(n["name"] for n in g.doc["nodes"] if n.get("name", "").startswith(prefix))
    if not stages:
        raise ValueError(f"no nodes named {prefix}*")
    poses = [(None, 0.0, list(stages[k:]) + list(hidden)) for k in range(1, len(stages) + 1)]
    frame = _union_frame(g, [(None, 0.0, tuple(hidden))])
    imgs = [render(path, size=size, yaw=yaw, pitch=pitch, hidden=h, frame=frame) for _, _, h in poses]
    sheet = _sheet(imgs, [f"{k}/{len(stages)}" for k in range(1, len(stages) + 1)], min(len(imgs), 6))
    if out is not None:
        sheet.save(out)
    return sheet
