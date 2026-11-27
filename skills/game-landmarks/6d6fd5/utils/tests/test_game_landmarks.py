"""Checks for the game-landmarks kit: every block is sane, landmarks package and validate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from utils import blocks, catalog, gltf, landmarks  # noqa: E402


def _volume(meshes) -> float:
    v = 0.0
    for m in meshes:
        t = m.positions[m.indices].astype(np.float64)
        v += np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6
    return v


@pytest.mark.parametrize("name", sorted(blocks.BLOCKS))
def test_block_defaults_are_sane(name: str) -> None:
    """Every block builds with defaults, sits on the ground, faces outward, fits the block budget."""
    b = blocks.BLOCKS[name]()
    meshes = b.placed()
    assert b.triangles > 0 and b.triangles <= catalog.TRIANGLE_BUDGET["block"], b.triangles
    lo = min(float(m.bounds()[0][1]) for m in meshes)
    assert lo > -0.03, lo
    assert _volume(meshes) > 0
    assert {"top", "bottom", "+x", "-x", "+z", "-z"} <= set(b.sockets)
    roles = set(blocks.palette("default"))
    assert {m.material for m in meshes} <= roles


def test_sockets_follow_placement() -> None:
    w = blocks.wall(1.0, 0.5)
    assert np.allclose(w.socket("right", 2, 0, 0, yaw=90), (2, 0, -0.5))
    base = blocks.plinth(2, 2, 0.2)
    assert base.sockets["top"][1] == pytest.approx(0.2)


def test_styles_define_every_role() -> None:
    for style in blocks.STYLES:
        s = gltf.Scene("t")
        blocks.define_materials(s, style)
        assert set(blocks.ROLES) <= set(s.materials)


def test_export_block(tmp_path: Path) -> None:
    rep = blocks.export_block("crane", tmp_path / "crane", style="steel")
    assert rep["ok"], rep["errors"]
    man = catalog.read_manifest(tmp_path / "crane")
    assert man["kind"] == "block" and "top" in man["sockets"] and man["parts"] == ["crane_jib"]


def test_specs_match_the_game() -> None:
    assert set(landmarks.SPECS) == {"ramen", "scramble", "109", "sensoji", "station", "tower", "palace",
                                    "shinjuku", "skytree"}
    assert landmarks.SPECS["station"].footprint == (4, 2)


@pytest.mark.parametrize("key", sorted(landmarks.SPECS))
def test_landmark_scene(key: str, tmp_path: Path) -> None:
    """Stages, scaffold, budget and footprint for every landmark (no previews: fast)."""
    a = landmarks.build_scene(key)
    p = a.scene.write(tmp_path / f"{key}.glb")
    rep = gltf.validate(p, max_triangles=catalog.TRIANGLE_BUDGET["landmark"])
    assert rep["ok"], rep["errors"]
    names = rep["node_names"]
    stages = [n for n in names if n.startswith("stage_")]
    assert 6 <= len(stages) <= 10
    assert stages == [f"stage_{i:02d}" for i in range(len(stages))]
    assert names[0] == key and "scaffold" in names
    g = gltf.read(p)
    lo, hi = landmarks._bounds(g, ["scaffold"])
    w, d = landmarks.SPECS[key].footprint
    assert lo[1] > -0.03
    assert hi[0] - lo[0] <= w + 0.6 and hi[2] - lo[2] <= d + 0.6
    # each stage adds something, and stages build bottom-up: stage_00 starts on the ground
    first = landmarks._subtree_bounds(g, ["stage_00"])
    assert first[0][1] < 0.05


def test_build_landmark_package(tmp_path: Path) -> None:
    rep = landmarks.build_landmark("ramen", tmp_path / "ramen")
    assert rep["ok"], rep["errors"]
    man = catalog.read_manifest(tmp_path / "ramen")
    assert man["kind"] == "landmark" and man["stages"] == len(man["stage_labels"])
    assert man["footprint"] == [1, 1]
    for f in ("render.png", "turntable.png", "stage_strip.png", "construction.png", "closeup.png"):
        assert (tmp_path / "ramen" / "previews" / f).is_file(), f
    assert {"noren", "lanterns"} <= set(man["clips"])
