"""Checks for game-environment: seamless terrain, seasons, budgets, packages for each family."""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from utils import backdrop, catalog, flora, gltf, props, terrain  # noqa: E402


def _ground_points(scene: gltf.Scene) -> np.ndarray:
    return np.concatenate([m.positions for m in scene.find("ground").meshes]).astype(np.float64)


def test_tile_edges_are_flat() -> None:
    """Every tile type, every season: all edge vertices of the ground sit at y = 0."""
    for t in terrain.TILE_TYPES:
        for season in terrain.SEASONS:
            p = _ground_points(terrain.tile(t, season, at=(3, 5)))
            edge = (np.abs(np.abs(p[:, 0]) - 0.5) < 1e-6) | (np.abs(np.abs(p[:, 2]) - 0.5) < 1e-6)
            assert edge.sum() >= 4 * terrain.SUBDIV
            assert np.allclose(p[edge, 1], 0.0, atol=1e-6), (t, season)
            assert np.abs(p[:, [0, 2]]).max() <= 0.5 + 1e-6


def test_lone_water_covers_its_rim() -> None:
    s = terrain.tile("water")
    w = s.find("water")
    assert w.translation[1] - terrain.BOB > 0          # bob never dips below the y=0 rim
    assert "bob" in [c.name for c in s.clips]
    assert not terrain.tile("water", "winter").clips   # frozen


def _edge_heights(scene: gltf.Scene, axis: int, value: float, offset: np.ndarray) -> dict:
    p = _ground_points(scene) + offset
    sel = np.abs(p[:, axis] - value) < 1e-6
    return {(round(float(x), 4), round(float(z), 4)): float(y) for x, y, z in p[sel]}


def test_adjacent_chunks_meet_seamlessly() -> None:
    """Two chunks cut from one grid share identical heights and normals along their seam, lake included."""
    grid = [["water" if (x - 7) ** 2 + (z - 4) ** 2 < 9 else ("forest" if (x + z) % 3 == 0 else "grass")
             for x in range(16)] for z in range(8)]
    a = terrain.chunk(grid, origin=(0, 0), size=(8, 8))
    b = terrain.chunk(grid, origin=(8, 0), size=(8, 8))
    # chunk-local -> map coordinates: add the chunk centre
    ea = _edge_heights(a, 0, 8.0, np.array([4.0, 0, 4.0]))
    eb = _edge_heights(b, 0, 8.0, np.array([12.0, 0, 4.0]))
    assert set(ea) == set(eb) and len(ea) == 8 * terrain.SUBDIV + 1
    assert all(abs(ea[k] - eb[k]) < 1e-6 for k in ea)
    assert min(ea.values()) < -0.05                    # the lake crosses the seam and is sunk on both sides
    na = terrain.ground_normals(grid, (0, 0), (8, 8))[:, -1]
    nb = terrain.ground_normals(grid, (8, 0), (8, 8))[:, 0]
    assert np.allclose(na, nb)


def test_tile_boundaries_inside_a_chunk_are_flat_except_lakes() -> None:
    g = terrain.sample_grid()
    h = terrain.heightfield(g, (0, 0), (8, 8))
    n = terrain.SUBDIV
    for j in range(h.shape[0]):
        for i in range(h.shape[1]):
            if i % n == 0 or j % n == 0:
                xs = {min(max((i - 1) // n, 0), 7), min(i // n, 7)}
                zs = {min(max((j - 1) // n, 0), 7), min(j // n, 7)}
                if any(g[z][x] != "water" for x in xs for z in zs):
                    assert h[j, i] == 0.0


def test_seasons_share_geometry() -> None:
    """Terrain seasons differ only in textures, so the game can crossfade them."""
    g = terrain.sample_grid()
    s, w = terrain.chunk(g, "summer"), terrain.chunk(g, "winter")
    for ms, mw in zip(s.find("ground").meshes, w.find("ground").meshes):
        assert ms.material == mw.material
        assert np.array_equal(ms.positions, mw.positions) and np.array_equal(ms.uvs, mw.uvs)
    assert set(s.materials) == set(w.materials)


def test_chunk_budget_worst_case() -> None:
    grid = [["water"] * 8 for _ in range(8)]
    s = terrain.chunk(grid, skirt=True, size=(8, 8))
    tris = sum(m.triangles for n in s.nodes for m in n.meshes)
    assert tris <= catalog.TRIANGLE_BUDGET["environment"]


def test_world_grid_port() -> None:
    grid, trees = terrain.world_grid(1234)
    assert len(grid) == terrain.MAP_H and len(grid[0]) == terrain.MAP_W
    flat = [t for row in grid for t in row]
    assert {"grass", "water", "dirt", "trail", "forest"} <= set(flat)
    assert grid[25][25] == "dirt"                      # the bunker plot
    assert len(trees) > 100


def test_snowify_only_tops() -> None:
    from utils import mesh
    snow = terrain.snowify([mesh.box(1, 1, 1, "wood")], depth=0.05)
    assert snow and snow[0].positions[:, 1].min() >= 1 - 0.011
    assert snow[0].positions[:, 1].max() <= 1.05 + 1e-6


def test_every_model_in_budget_and_named() -> None:
    budget = catalog.TRIANGLE_BUDGET["environment"]
    scenes = [flora.plant(n, se) for n in flora.PLANTS for se in terrain.SEASONS]
    scenes += [props.prop(n, se) for n in props.PROPS for se in ("summer", "winter")]
    scenes += [backdrop.layer(n, se) for n in backdrop.LAYERS for se in terrain.SEASONS]
    scenes += [backdrop.sky(t) for t in backdrop.SKIES]
    for s in scenes:
        tris = sum(m.triangles for n in s.nodes for m in n.meshes)
        assert 0 < tris <= budget, (s.name, tris)
        lo = min(float(m.positions[:, 1].min()) for n in s.nodes for m in n.meshes)
        assert lo >= -0.6 or s.name == "backdrop-sky", (s.name, lo)   # the dome dips below the horizon


def test_backdrop_rings_surround_the_map() -> None:
    half = terrain.MAP_W / 2
    corner = half * math.sqrt(2)
    assert backdrop.RADII["treeline"][0] * half > corner
    order = [backdrop.RADII["treeline"][1], backdrop.RADII["hills"][0], backdrop.RADII["mountains"][0],
             backdrop.RADII["sky"]]
    assert order == sorted(order)
    sky = backdrop.sky("day")
    for n in sky.nodes:
        for m in n.meshes:   # the dome faces inward
            assert (np.einsum("ij,ij->i", m.normals, m.positions) < 0).all()
    tl = backdrop.treeline()
    for n in tl.nodes:
        for m in n.meshes:
            assert (np.einsum("ij,ij->i", m.normals[:, [0, 2]], m.positions[:, [0, 2]]) < 0).all()
    assert set(backdrop.near_sectors(45)) and len(backdrop.near_sectors(45)) < backdrop.SECTORS


def test_packages_validate(tmp_path: Path) -> None:
    reports = [terrain.build_tile("trail", tmp_path, size=96),
               flora.build_plant("spruce-small", tmp_path, size=96),
               props.build_prop("snowman", tmp_path, size=96),
               backdrop.build_layer("backdrop-treeline", tmp_path, size=96)]
    for r in reports:
        assert r["ok"], r["errors"]
    man = catalog.read_manifest(tmp_path / "terrain-trail")
    assert man["tileable"] is True and man["season"] == ["summer", "autumn", "winter"]
    assert man["seasons"]["winter"] == "terrain-trail-winter.glb"
    man = catalog.read_manifest(tmp_path / "snowman")
    assert man["season"] == ["winter"] and man["footprint"] == [1, 1]
    man = catalog.read_manifest(tmp_path / "spruce-small")
    assert man["seasons"]["autumn"] == "spruce-small.glb"
    assert "sway" in gltf.validate(tmp_path / "spruce-small" / "spruce-small.glb")["clips"]
    assert list((tmp_path / "backdrop-treeline" / "previews").glob("*_turntable.png"))
