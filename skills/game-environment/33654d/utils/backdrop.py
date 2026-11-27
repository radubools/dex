"""The world around the map, for a free-orbiting camera: sky dome, ground apron, forest, treelines, hills, mountains, clouds.

Every layer is a ring centred on the **map centre** (put each GLB's origin at
world (MAP_W/2, 0, MAP_H/2) in map coordinates) and surrounds the map in 360
degrees. Radii are multiples of the map half-width `HALF = MAP_W / 2` (26
tiles) — pass another `map_w` to scale them:

| layer (package)        | radius (x HALF)        | what                                      |
|------------------------|------------------------|-------------------------------------------|
| `backdrop-ground`      | map square .. 8.0      | flat forest-floor apron under everything  |
| `backdrop-forest`      | square band +2..+14    | real low-poly spruces hugging the map     |
| `backdrop-treeline`    | 2.15 and 2.55          | two alpha-card silhouette bands (parallax)|
| `backdrop-hills`       | 3.0 .. 4.5             | low-poly forested hills, 6-15 high        |
| `backdrop-clouds`      | 3.5 .. 6.5, y 1.2-1.75 | puffy low-poly clouds, `drift` clip       |
| `backdrop-mountains`   | 5.8 .. 7.7             | ranges 18-42 high with snow caps          |
| `backdrop-sky`         | 9.5                    | gradient dome: day / dusk / night / overcast |

Camera far plane must reach ~10 x HALF (260). Use fog in the sky's
`horizon` colour (manifest `fog`) so the outer rings melt into it.

Each ring is split into eight `sector_<k>` nodes (azimuth k*45..(k+1)*45
degrees, measured from +X toward +Z), so engines can frustum-cull, and so
previews can hide the half nearest the camera and show the ring from inside.
Faces point inward and are single-sided: from outside the ring is invisible.
Seasons: every layer has summer and winter files (sky: four times of day,
`overcast` for snow days); autumn uses summer, plus autumn colours in the forest.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from PIL import Image, ImageDraw

from . import gltf, mesh, terrain, texture

MAP_W = terrain.MAP_W
HALF = MAP_W / 2
SECTORS = 8
#: Radii as multiples of HALF.
RADII = {"ground": 8.0, "forest": (2.0, 14.0), "treeline": (2.15, 2.55), "hills": (3.0, 4.5),
         "clouds": (3.5, 6.5), "mountains": (5.8, 7.7), "sky": 9.5}
#: Horizon haze per season (the day sky's horizon colour) that distant layers fade toward.
HAZE = {"summer": "#c9dcea", "autumn": "#d3d9df", "winter": "#dfe4ea"}
SKIES = {
    "day": [(0.0, "#3f7fcf"), (0.55, "#8dbbe6"), (0.86, "#cfe3f0"), (1.0, "#b9c9cf")],
    "dusk": [(0.0, "#24305e"), (0.45, "#7a5a8a"), (0.75, "#e0857a"), (0.86, "#ffc07a"), (1.0, "#8a6a6a")],
    "night": [(0.0, "#04071a"), (0.6, "#0d1838"), (0.86, "#22335a"), (1.0, "#141c2c")],
    "overcast": [(0.0, "#8f9bab"), (0.6, "#bcc5d0"), (0.86, "#dfe4ea"), (1.0, "#c8ced6")],
}
#: Lowest elevation of the dome (degrees, below the horizon so no gap shows at the edge).
SKY_BELOW = -12.0


def _season(season: str) -> str:
    return "summer" if season == "autumn" else season


def _haze(rgb: np.ndarray, season: str, amount: float) -> np.ndarray:
    return rgb * (1 - amount) + texture.rgb(HAZE[season]) * amount


def sector_of(angle: float) -> int:
    """The sector index of an azimuth in radians (0 = +X, increasing toward +Z)."""
    return int((angle % (2 * math.pi)) / (2 * math.pi) * SECTORS) % SECTORS


def near_sectors(yaw: float, *, cutoff: float = 0.25) -> list[str]:
    """Names of the sectors between a `preview` camera at `yaw` and the centre (hide them to look inside)."""
    cam = np.array([math.sin(math.radians(yaw)), math.cos(math.radians(yaw))])
    out = []
    for k in range(SECTORS):
        a = (k + 0.5) * 2 * math.pi / SECTORS
        if np.dot(cam, [math.cos(a), math.sin(a)]) > cutoff:
            out.append(f"sector_{k}")
    return out


def _add_sectors(s: gltf.Scene, parent: gltf.Node, sector_meshes: dict[int, list[mesh.Mesh]]) -> None:
    for k in range(SECTORS):
        if sector_meshes.get(k):
            s.node(f"sector_{k}", parent=parent, meshes=sector_meshes[k])


def _periodic(n: int, *, seed: int, octaves: Sequence[tuple[int, float]]) -> np.ndarray:
    """A smooth periodic 1D signal over n samples in [0, 1]: sums of harmonics with random phases."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / n * 2 * np.pi
    v = np.zeros(n)
    for freq, amp in octaves:
        v += amp * np.sin(freq * t + rng.uniform(0, 2 * np.pi))
    v = (v - v.min()) / max(np.ptp(v), 1e-9)
    return v


def _annulus(radii: np.ndarray, heights: np.ndarray, material: str, *, uv: Callable[[np.ndarray], np.ndarray],
             segments: int, jitter: float = 0.0, seed: int = 0) -> dict[int, list[mesh.Mesh]]:
    """Heightfield ring: radii (rows) x segments, heights (rows, segments); faces up/inward, per sector."""
    rng = np.random.default_rng(seed)
    rows = len(radii)
    a = np.arange(segments) / segments * 2 * np.pi
    r = radii[:, None] + rng.uniform(-jitter, jitter, (rows, segments)) * (np.arange(rows)[:, None] > 0)
    pts = np.stack([r * np.cos(a)[None, :], heights, r * np.sin(a)[None, :]], axis=-1)
    out: dict[int, list[mesh.Mesh]] = {}
    for k in range(segments):
        k2 = (k + 1) % segments
        faces, P = [], []
        for i in range(rows - 1):
            q = [pts[i, k], pts[i, k2], pts[i + 1, k2], pts[i + 1, k]]
            # Seen from above, inner k -> inner k+1 -> outer k+1 is counter-clockwise (faces up).
            P += [q[0], q[1], q[2], q[0], q[2], q[3]]
        P = np.asarray(P)
        tris = [(j, j + 1, j + 2) for j in range(0, len(P), 3)]
        m = mesh.from_faces(P, tris, material, uvs=uv(P))
        out.setdefault(sector_of((a[k] + np.pi / segments)), []).append(m)
    return out


# ------------------------------------------------------------------ textures


_TEX: dict = {}


def _cached(key: str, fn: Callable[[], Image.Image]) -> Image.Image:
    if key not in _TEX:
        _TEX[key] = fn()
    return _TEX[key]


def apron_texture(season: str) -> Image.Image:
    """Ground beyond the map: the map's grass darkened toward forest floor (snow in winter)."""
    def make() -> Image.Image:
        g = np.asarray(terrain.ground_texture("grass", season), dtype=np.float64) / 255
        f = np.asarray(terrain.ground_texture("forest", season), dtype=np.float64) / 255
        m = (texture.noise(g.shape[0], 4, octaves=3, seed=91) > 0.5)[..., None] * 0.6
        return texture.to_image(g * (1 - m) + f * m)
    return _cached(f"apron{season}", make)


def sky_texture(time: str = "day") -> tuple[Image.Image, Image.Image]:
    """The dome's (colour, emissive) images — the same image: elevation gradient top to bottom, stars at night."""
    def make() -> Image.Image:
        h, w = 256, (1024 if time == "night" else 64)
        v = np.linspace(0, 1, h)[:, None]
        col = texture.colorize(np.broadcast_to(v, (h, w)), SKIES[time])
        if time == "night":
            rng = np.random.default_rng(3)
            n = 900
            ys, xs = rng.integers(0, int(h * 0.8), n), rng.integers(0, w, n)
            br = rng.uniform(0.5, 1.0, n)
            col[ys, xs] = np.maximum(col[ys, xs], br[:, None] * texture.rgb("#f4f2e8"))
            moon_y, moon_x = int(h * 0.35), int(w * 0.62)
            yy, xx = np.mgrid[0:h, 0:w]
            moon = (((yy - moon_y) / 6.0) ** 2 + ((xx - moon_x) / 4.0) ** 2) < 1
            col[moon] = texture.rgb("#f2efe0")
        return texture.to_image(col)
    img = _cached(f"sky{time}", make)
    return img, img


def treeline_texture(season: str, *, seed: int = 0, haze: float = 0.3, width: int = 512, height: int = 128) -> Image.Image:
    """RGBA strip of spruce silhouettes (tiles horizontally), hazed toward the horizon; winter adds snow."""
    def make() -> Image.Image:
        rng = np.random.default_rng(seed)
        S = 2
        img = Image.new("RGBA", (width * S, height * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        base = _haze(texture.rgb("#1d3d2c"), season, haze)
        snow = _haze(texture.rgb("#eef3f8"), season, haze * 0.6)
        W, H = width * S, height * S
        d.rectangle([0, int(H * 0.82), W, H], fill=tuple(int(c * 255) for c in base) + (255,))
        x = 0.0
        while x < W:
            th = rng.uniform(0.45, 1.0) * H * 0.95
            tw = th * rng.uniform(0.28, 0.4)
            shade = base * rng.uniform(0.8, 1.15)
            col = tuple(int(np.clip(c, 0, 1) * 255) for c in shade) + (255,)
            for dx in (0, -W, W):
                cx = x + dx
                tiers = 4
                for k in range(tiers):
                    yb = H - th * (k / tiers) * 0.85 - H * 0.05
                    yt = yb - th * 0.42
                    ww = tw * (1 - 0.6 * k / tiers)
                    d.polygon([(cx - ww / 2, yb), (cx + ww / 2, yb), (cx, max(H - th, yt))], fill=col)
                    if _season(season) == "winter":
                        sc = tuple(int(np.clip(c, 0, 1) * 255) for c in snow) + (255,)
                        ty = max(H - th, yt)
                        d.polygon([(cx - ww * 0.28, ty + (yb - ty) * 0.55), (cx + ww * 0.28, ty + (yb - ty) * 0.55),
                                   (cx, ty)], fill=sc)
            x += tw * rng.uniform(0.35, 0.7)
        return img.resize((width, height), Image.LANCZOS)
    return _cached(f"treeline{season}{seed}{haze}", make)


def hills_texture(season: str, *, haze: float = 0.2) -> Image.Image:
    """Forested hillside seen from afar: dark canopy speckle (snow-dusted in winter)."""
    def make() -> Image.Image:
        s = 256
        n = texture.noise(s, 8, octaves=5, seed=71)
        dots = texture.noise(s, 64, octaves=1, seed=72)
        if _season(season) == "winter":
            a = texture.colorize(0.6 * n + 0.4 * dots, [(0, "#2a3f33"), (0.45, "#5f7068"), (0.6, "#dfe6ee"), (1, "#ffffff")])
        else:
            col = "#4a5f2c" if season == "autumn" else "#2f5233"
            a = texture.tint(col, 0.5 * n + 0.5 * dots, 0.45)
            if season == "autumn":
                a = np.where((dots > 0.72)[..., None], texture.rgb("#b8542a"), a)
                a = np.where((dots < 0.24)[..., None], texture.rgb("#d4a03a"), a)
        return texture.to_image(_haze(a, _season(season), haze))
    return _cached(f"hills{season}{haze}", make)


def mountain_texture(season: str, *, haze: float = 0.38) -> Image.Image:
    """Altitude bands for the far ranges (V = 0 at the top peak): forest, rock, snow; tiles along U."""
    def make() -> Image.Image:
        s = 256
        v = np.linspace(0, 1, s)[:, None] * np.ones((1, s))
        n = texture.noise(s, 8, octaves=5, seed=81)
        alt = 1 - v + (n - 0.5) * 0.18                    # altitude 0..1 with ragged band edges
        snowline = 0.7 if _season(season) == "summer" else 0.25
        treeline = 0.34 if _season(season) == "summer" else 0.12
        forest = texture.tint("#2a4632", n, 0.3)
        rockc = texture.tint("#58606c", texture.noise(s, 16, octaves=4, seed=82), 0.35)
        snowc = texture.colorize(texture.noise(s, 8, octaves=3, seed=83), [(0, "#cfd9e6"), (1, "#ffffff")])
        a = np.where((alt < treeline)[..., None], forest, rockc)
        a = np.where((alt > snowline)[..., None], snowc, a)
        if _season(season) == "winter":
            a = np.where(((alt < snowline) & (texture.noise(s, 32, octaves=2, seed=84) > 0.45))[..., None],
                         snowc * 0.92, a)
        return texture.to_image(_haze(a, _season(season), haze))
    return _cached(f"mountains{season}{haze}", make)


# ------------------------------------------------------------------ layers


def sky(time: str = "day", *, map_w: float = MAP_W, segments: int = 32, rings: int = 10) -> gltf.Scene:
    """The sky dome (inward, unlit) for a time of day: day, dusk, night or overcast."""
    R = RADII["sky"] * map_w / 2
    color, glow = sky_texture(time)
    name = "backdrop-sky"
    s = gltf.Scene(name, extras={"backdrop": True, "time": time, "radius": R})
    # Black base + emissive gradient: renders as an unlit dome in any PBR engine, untouched by the sun.
    s.material("sky", color="#000000", emissive_texture=glow, roughness=1.0, metallic=0.0, unlit=True)
    root = s.node(name)
    els = np.radians(np.linspace(SKY_BELOW, 90, rings + 1))
    secs: dict[int, list[mesh.Mesh]] = {}
    for k in range(segments):
        a0, a1 = 2 * math.pi * k / segments, 2 * math.pi * (k + 1) / segments
        P, U = [], []
        for i in range(rings):
            e0, e1 = els[i], els[i + 1]
            quad = [(a0, e0), (a1, e0), (a1, e1), (a0, e1)]
            pts = [(R * math.cos(e) * math.cos(a), R * math.sin(e), R * math.cos(e) * math.sin(a)) for a, e in quad]
            uvs = [(a / (2 * math.pi), 1 - (math.degrees(e) - SKY_BELOW) / (90 - SKY_BELOW) * 0.99 - 0.005)
                   for a, e in quad]
            # (a0,e0) -> (a1,e0) -> (a1,e1) winds facing the centre: the dome is seen from inside
            for t in ((0, 1, 2), (0, 2, 3)):
                P += [pts[j] for j in t]
                U += [uvs[j] for j in t]
        tris = [(j, j + 1, j + 2) for j in range(0, len(P), 3)]
        m = mesh.from_faces(np.asarray(P), tris, "sky", uvs=np.asarray(U))
        secs.setdefault(sector_of((a0 + a1) / 2), []).append(m)
    _add_sectors(s, root, secs)
    return s


def ground(season: str = "summer", *, map_w: float = MAP_W, segments: int = 64) -> gltf.Scene:
    """The ground apron: forest floor from the map's square edge out under every other ring."""
    half = map_w / 2
    R = RADII["ground"] * half
    name = "backdrop-ground"
    s = gltf.Scene(name, extras={"season": season, "backdrop": True, "inner": "map square", "radius": R})
    s.material("apron", texture=apron_texture(season), roughness=1.0)
    root = s.node(name)
    a = np.arange(segments) / segments * 2 * np.pi
    inner = half / np.maximum(np.abs(np.cos(a)), np.abs(np.sin(a)))     # ray hits the map square here
    rings = [inner, inner + 6, half * 2.6, half * 4.5, np.full(segments, R)]
    ys = [-0.01, -0.01, 0.0, 0.0, 0.0]
    pts = np.stack([np.stack([r * np.cos(a), np.full(segments, y), r * np.sin(a)], axis=-1) for r, y in zip(rings, ys)])
    secs: dict[int, list[mesh.Mesh]] = {}
    for k in range(segments):
        k2 = (k + 1) % segments
        P = []
        for i in range(len(rings) - 1):
            q = [pts[i, k], pts[i, k2], pts[i + 1, k2], pts[i + 1, k]]
            P += [q[0], q[1], q[2], q[0], q[2], q[3]]
        P = np.asarray(P)
        m = mesh.from_faces(P, [(j, j + 1, j + 2) for j in range(0, len(P), 3)], "apron",
                            uvs=P[:, [0, 2]] / terrain.UV_TILES)
        secs.setdefault(sector_of(a[k] + np.pi / segments), []).append(m)
    _add_sectors(s, root, secs)
    return s


def forest(season: str = "summer", *, map_w: float = MAP_W, count: int = 290, seed: int = 5) -> gltf.Scene:
    """A band of real low-poly spruces 2..14 tiles outside the map edge (and inside 2 x HALF)."""
    half = map_w / 2
    d0, d1 = half + RADII["forest"][0], half + RADII["forest"][1]
    rmax = half * 2.0
    rng = np.random.default_rng(seed)
    name = "backdrop-forest"
    s = gltf.Scene(name, extras={"season": season, "backdrop": True, "band": [d0, d1]})
    greens = ["#1f4d36", "#245a3d", "#2b5f3a"]
    if season == "autumn":
        greens[2] = "#b8642a"            # a few larches and birches turning
    for i, g in enumerate(greens):
        img = texture.foliage(64, g, seed=i)
        if _season(season) == "winter":
            img = texture.to_image(terrain.snow_cover(np.asarray(img, dtype=np.float64) / 255, 0.55, seed=i))
        s.material(f"needles_{i}", texture=img, roughness=0.9)
    root = s.node(name)
    secs: dict[int, list[mesh.Mesh]] = {}
    placed = 0
    while placed < count:
        x, z = rng.uniform(-rmax, rmax, 2)
        cheb = max(abs(x), abs(z))
        if not (d0 <= cheb <= d1) or math.hypot(x, z) > rmax:
            continue
        # denser near the map: accept with falling probability outward
        if rng.random() > 1.15 - (cheb - d0) / (d1 - d0):
            continue
        h = rng.uniform(2.2, 4.2) * (1 + 0.25 * (cheb - d0) / (d1 - d0))
        r = h * rng.uniform(0.22, 0.28)
        mat = f"needles_{int(rng.integers(0, 3))}"
        yaw = float(rng.uniform(0, 72))
        parts = [mesh.cone(r, h * 0.62, mat, segments=5, caps=False).translate(0, h * 0.12, 0),
                 mesh.cone(r * 0.68, h * 0.5, mat, segments=5, caps=False).translate(0, h * 0.5, 0)]
        parts = [p.rotate_y(yaw).translate(x, 0, z) for p in parts]
        secs.setdefault(sector_of(math.atan2(z, x)), []).extend(parts)
        placed += 1
    _add_sectors(s, root, secs)
    return s


def treeline(season: str = "summer", *, map_w: float = MAP_W, cards: int = 48) -> gltf.Scene:
    """Two bands of inward-facing alpha cards with spruce silhouettes, at 2.15 and 2.55 x HALF."""
    half = map_w / 2
    name = "backdrop-treeline"
    s = gltf.Scene(name, extras={"season": season, "backdrop": True})
    root = s.node(name)
    secs: dict[int, list[mesh.Mesh]] = {}
    for b, (rad, h, haze, seed) in enumerate(((RADII["treeline"][0] * half, 7.5, 0.18, 1),
                                               (RADII["treeline"][1] * half, 10.0, 0.38, 2))):
        mat = f"treeline_{b}"
        s.material(mat, texture=treeline_texture(season, seed=seed, haze=haze), roughness=1.0, alpha="MASK")
        circ = 2 * math.pi * rad
        repeat = circ / round(circ / (h * 4))          # whole repeats around the ring: no seam
        for k in range(cards):
            a0, a1 = 2 * math.pi * k / cards, 2 * math.pi * (k + 1) / cards
            p0 = (rad * math.cos(a0), rad * math.sin(a0))
            p1 = (rad * math.cos(a1), rad * math.sin(a1))
            u0, u1 = rad * a0 / repeat, rad * a1 / repeat
            quad = [(p0[0], -0.5, p0[1]), (p1[0], -0.5, p1[1]), (p1[0], h, p1[1]), (p0[0], h, p0[1])]
            uv = np.array([(u0, 1.0), (u1, 1.0), (u1, 0.0), (u0, 0.0)])
            m = mesh.from_faces(quad, [(0, 1, 2, 3)], mat, uvs=uv)
            if float(m.normals[0] @ np.array([-p0[0] - p1[0], 0, -p0[1] - p1[1]])) < 0:   # face the centre
                m = mesh.from_faces(quad[::-1], [(0, 1, 2, 3)], mat, uvs=uv[::-1])
            secs.setdefault(sector_of((a0 + a1) / 2), []).append(m)
    _add_sectors(s, root, secs)
    return s


def hills(season: str = "summer", *, map_w: float = MAP_W, segments: int = 96, seed: int = 11) -> gltf.Scene:
    """Low-poly forested hills ringing the map at 3 .. 4.5 x HALF."""
    half = map_w / 2
    r0, r1 = (x * half for x in RADII["hills"])
    name = "backdrop-hills"
    s = gltf.Scene(name, extras={"season": season, "backdrop": True})
    s.material("hills", texture=hills_texture(season), roughness=1.0)
    root = s.node(name)
    H = 6 + 9 * _periodic(segments, seed=seed, octaves=[(3, 1.0), (7, 0.6), (13, 0.35), (23, 0.2)])
    prof_r = np.array([0.0, 0.25, 0.55, 0.8, 1.0])
    prof_h = np.array([0.0, 0.55, 1.0, 0.7, 0.0])
    radii = r0 + (r1 - r0) * prof_r
    rng = np.random.default_rng(seed)
    heights = prof_h[:, None] * H[None, :] * (1 + rng.uniform(-0.12, 0.12, (len(prof_r), segments)))
    secs = _annulus(radii, heights, "hills", uv=lambda P: P[:, [0, 2]] / 24.0, segments=segments,
                    jitter=2.0, seed=seed)
    _add_sectors(s, root, secs)
    return s


def mountains(season: str = "summer", *, map_w: float = MAP_W, segments: int = 128, seed: int = 21) -> gltf.Scene:
    """Distant mountain ranges at 5.8 .. 7.7 x HALF, 18-42 high, snow-capped (much lower snow in winter)."""
    half = map_w / 2
    r0, r1 = (x * half for x in RADII["mountains"])
    name = "backdrop-mountains"
    s = gltf.Scene(name, extras={"season": season, "backdrop": True})
    s.material("mountains", texture=mountain_texture(season), roughness=1.0)
    root = s.node(name)
    base = _periodic(segments, seed=seed, octaves=[(2, 1.0), (5, 0.7), (11, 0.5), (19, 0.35), (37, 0.2)])
    ridged = 1 - np.abs(2 * _periodic(segments, seed=seed + 1, octaves=[(9, 1.0), (17, 0.6), (29, 0.4)]) - 1)
    H = 18 + 24 * (0.6 * base + 0.4 * ridged) ** 1.3
    hmax = float(H.max())
    prof_r = np.array([0.0, 0.22, 0.48, 0.75, 1.0])
    prof_h = np.array([0.0, 0.42, 1.0, 0.55, 0.0])
    radii = r0 + (r1 - r0) * prof_r
    rng = np.random.default_rng(seed)
    heights = prof_h[:, None] * H[None, :] * (1 + rng.uniform(-0.15, 0.15, (len(prof_r), segments)))
    heights[2] *= 1 + 0.25 * (rng.random(segments) > 0.8)            # a few sharper summits

    def uv(P: np.ndarray) -> np.ndarray:
        az = np.arctan2(P[:, 2], P[:, 0]) % (2 * np.pi)
        u = az / (2 * np.pi) * 8
        # keep a triangle's u continuous across the 0/2pi seam
        return np.stack([u, 1 - np.clip(P[:, 1] / (hmax * 1.25), 0, 1)], axis=1)

    secs = _annulus(radii, heights, "mountains", uv=uv, segments=segments, jitter=4.0, seed=seed)
    for ms in secs.values():
        for m in ms:
            u = m.uvs[:, 0].reshape(-1, 3)
            wrap = (u.max(1) - u.min(1)) > 4
            u[wrap] = np.where(u[wrap] < 4, u[wrap] + 8, u[wrap])
            m.uvs[:, 0] = u.reshape(-1)
    _add_sectors(s, root, secs)
    return s


def clouds(season: str = "summer", *, map_w: float = MAP_W, count: int = 22, seed: int = 31,
           period: float = 1200.0) -> gltf.Scene:
    """Puffy low-poly clouds at 3.5 .. 6.5 x HALF, 1.2 .. 1.75 x HALF up; `drift` turns them once per `period` s."""
    from .flora import blob
    half = map_w / 2
    r0, r1 = (x * half for x in RADII["clouds"])
    rng = np.random.default_rng(seed)
    name = "backdrop-clouds"
    s = gltf.Scene(name, extras={"season": season, "backdrop": True})
    winter = _season(season) == "winter"
    s.material("cloud", color="#c4cad4" if winter else "#ffffff", roughness=1.0)
    s.material("cloud_shade", color="#a3abb8" if winter else "#e3e8f0", roughness=1.0)
    root = s.node(name)
    drift = s.node("drift", parent=root)
    secs: dict[int, list[mesh.Mesh]] = {}
    for c in range(count):
        a = 2 * math.pi * (c + rng.uniform(0, 0.7)) / count
        r = rng.uniform(r0, r1)
        y = rng.uniform(1.2, 1.75) * half
        size = rng.uniform(5, 9) * (1.3 if winter else 1.0)
        cx, cz = r * math.cos(a), r * math.sin(a)
        parts = []
        for k in range(5):
            bx = (k - 2) * size * 0.55 + rng.uniform(-1, 1)
            br = size * (0.75 if k in (1, 2, 3) else 0.5) * rng.uniform(0.85, 1.1)
            b = blob(br, "cloud" if k % 2 == 0 else "cloud_shade", seed=seed * 50 + c * 5 + k, squash=0.45,
                     segments=6, rings=3, rough=0.12)
            parts.append(b.translate(bx, rng.uniform(-0.5, 1.0) * size * 0.2, rng.uniform(-1, 1) * size * 0.25))
        # long axis tangent to the ring
        parts = [p.rotate_y(-math.degrees(a) + 90).translate(cx, y, cz) for p in parts]
        secs.setdefault(sector_of(a), []).extend(parts)
    _add_sectors(s, drift, secs)
    ts = [period * k / 4 for k in range(5)]
    s.animation("drift").rotate(drift, ts, [mesh.quat_euler(y=-90 * k) for k in range(5)])
    return s


# ------------------------------------------------------------------ packages

LAYERS: dict[str, Callable[..., gltf.Scene]] = {
    "backdrop-ground": ground, "backdrop-forest": forest, "backdrop-treeline": treeline,
    "backdrop-hills": hills, "backdrop-clouds": clouds, "backdrop-mountains": mountains,
}


def layer(name: str, season: str = "summer", **kw) -> gltf.Scene:
    """The scene of one ring layer (`LAYERS`) for a season; `backdrop-sky` takes a time of day instead."""
    if name == "backdrop-sky":
        return sky(season if season in SKIES else ("overcast" if season == "winter" else "day"), **kw)
    return LAYERS[name](season, **kw)


def panorama(season: str = "summer", time: str = "day", *, layers: Sequence[str] | None = None,
             map_w: float = MAP_W) -> gltf.Scene:
    """Every backdrop layer (and the sky) composed into one scene — for previews, not for shipping."""
    names = list(layers) if layers is not None else ["backdrop-sky", *LAYERS]
    parts = [((sky(time, map_w=map_w) if n == "backdrop-sky" else LAYERS[n](season, map_w=map_w)), (0, 0, 0))
             for n in names]
    return terrain.compose(parts, name=f"panorama-{season}-{time}")


def _sector_hider(yaw: float) -> list[str]:
    return near_sectors(yaw)


def build_layer(name: str, out_dir: str | Path, *, size: int = 384, map_w: float = MAP_W) -> dict:
    """Package one backdrop layer with summer/autumn/winter files (sky: day/dusk/night/overcast)."""
    half = map_w / 2
    if name == "backdrop-sky":
        variants = {t: sky(t, map_w=map_w) for t in SKIES}
        seasons = {"summer": "day", "autumn": "day", "winter": "overcast"}
        fog = {t: dict(SKIES[t])[0.86] for t in SKIES}
        dia = int(math.ceil(2 * RADII["sky"] * half))
        return terrain.write_package(name, out_dir, variants, category="backdrop", footprint=[dia, dia],
                                     floor=False, pitch=15, size=size, hidden=_sector_hider, seasons=seasons,
                                     times={t: f"{name}.glb" if i == 0 else f"{name}-{t}.glb" for i, t in enumerate(SKIES)},
                                     fog=fog, radius=RADII["sky"] * half, radius_half_widths=RADII["sky"],
                                     centre="map centre", unlit=True)
    fn = LAYERS[name]
    keys = ("summer", "autumn", "winter") if name in ("backdrop-forest", "backdrop-hills", "backdrop-ground") \
        else ("summer", "winter")
    variants = {k: fn(k, map_w=map_w) for k in keys}
    key = name.split("-", 1)[1]
    rad = RADII[key]
    outer = (half + rad[1]) * 2 if key == "forest" else 2 * half * float(np.max(rad))
    dia = int(math.ceil(outer if key != "forest" else 4 * half))
    return terrain.write_package(name, out_dir, variants, category="backdrop", footprint=[dia, dia], floor=False,
                                 pitch=18, size=size, hidden=_sector_hider, centre="map centre",
                                 radius_half_widths=rad, radius=(np.asarray(rad) * half).tolist(), clip_frames=4)


def build_all(out_root: str | Path) -> dict[str, dict]:
    """Package the sky and every ring layer; returns {name: validate report}."""
    out = {"backdrop-sky": build_layer("backdrop-sky", out_root)}
    for n in LAYERS:
        out[n] = build_layer(n, out_root)
    return out
