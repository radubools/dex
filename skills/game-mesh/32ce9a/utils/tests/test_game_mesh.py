"""Round-trip checks for the game-mesh core: write a GLB, read it back, render it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from utils import catalog, gltf, mesh, preview, texture  # noqa: E402


def _scene() -> gltf.Scene:
    s = gltf.Scene("probe")
    s.material("a", texture=texture.brick(64))
    s.material("b", color="#3366cc", metallic=0.5)
    root = s.node("root")
    s.node("stage_00", parent=root, meshes=[mesh.box(1, 0.5, 1, "a")])
    arm = s.node("stage_01", parent=root, translation=(0, 0.5, 0),
                 meshes=[mesh.sphere(0.2, "b"), mesh.cone(0.2, 0.4, "b"), mesh.cylinder(0.1, 0.5, "b", smooth=True)])
    s.animation("spin").rotate(arm, [0, 1, 2], [mesh.quat_euler(y=a) for a in (0, 180, 360)])
    return s


def test_roundtrip(tmp_path: Path) -> None:
    p = _scene().write(tmp_path / "probe.glb")
    rep = gltf.validate(p)
    assert rep["ok"], rep["errors"]
    assert rep["clips"] == {"spin": 2.0}
    assert abs(rep["bounds"]["min"][1]) < 1e-6


def test_primitives_face_outward() -> None:
    """A closed mesh wound outward encloses positive signed volume."""
    for m in (mesh.box(1, 1, 1), mesh.cylinder(0.5, 1), mesh.sphere(0.5), mesh.frustum(1, 1, 0.5, 0.5, 1),
              mesh.pyramid(1, 1, 1), mesh.extrude([(0, 0), (1, 0), (1, 1), (0.5, 0.4), (0, 1)], 1),
              mesh.extrude([(0, 0), (0, 1), (1, 1), (1, 0)], 1), mesh.gable_roof(1, 1, 0.5),
              mesh.hip_roof(2, 1, 0.5), mesh.box(1, 2, 1).rotate_x(30).scale(-1, 1, 1)):
        t = m.positions[m.indices].astype(np.float64)
        volume = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6
        assert volume > 0, volume


def test_preview_and_package(tmp_path: Path) -> None:
    p = _scene().write(tmp_path / "probe.glb")
    (tmp_path / "previews").mkdir()
    img = preview.render(p, tmp_path / "previews" / "iso.png", size=96)
    assert img.size == (96, 96)
    preview.stage_strip(p, tmp_path / "previews" / "stages.png", size=64)
    preview.clip_strip(p, tmp_path / "previews" / "spin.png", "spin", frames=3, size=64)
    catalog.write_manifest(tmp_path, name="probe", kind="landmark", files=["probe.glb"], footprint=[1, 1], stages=2)
    rep = catalog.validate_package(tmp_path)
    assert rep["ok"], rep["errors"]
    entry = catalog.export(tmp_path, game_root=tmp_path / "game")
    assert (tmp_path / "game" / "public" / "assets" / "landmark" / "probe" / "probe.glb").is_file()
    assert entry["base"] == "assets/landmark/probe/"
