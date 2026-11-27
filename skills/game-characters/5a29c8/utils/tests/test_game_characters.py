"""Checks for game-characters: rigs pivot at joints, clips close and keep feet planted, packages validate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from utils import catalog, gltf, motion, rigs, units  # noqa: E402


def _rig() -> rigs.Rig:
    rig = rigs.humanoid("probe", rigs.Body())
    rig.prop("tool", "hand_r_socket", [rigs.block(0.02, 0.1, 0.02, "wood", y=-0.05)], hidden=True)
    rig.build()
    return rig


def test_hierarchy_and_pivots() -> None:
    rig = _rig()
    b = rig.spec
    for name in ("body", "hips", "torso", "head", "arm_l", "forearm_l", "hand_l", "arm_r", "forearm_r",
                 "hand_r", "hand_r_socket", "leg_l", "shin_l", "foot_l", "leg_r", "shin_r", "foot_r"):
        assert name in rig.nodes, name
    assert rig.parts["shin_l"].parent == "leg_l" and rig.parts["hand_r_socket"].parent == "hand_r"
    assert np.allclose(rig.joint("leg_l"), (b.hip_spread, b.hip_y, 0))
    assert np.allclose(rig.joint("foot_l"), (b.hip_spread, b.ankle, 0))
    assert rig.joint("arm_l")[0] > 0 > rig.joint("arm_r")[0]           # left is +X (facing +Z)
    assert motion.rest_points(rig)[:, 1].min() >= -1e-6               # nothing below the floor at rest


def test_leg_ik_reaches_target() -> None:
    th, kn, ft = motion.leg_ik(0.15, 0.15, 0.05, 0.25)
    a, k = np.radians(-th), np.radians(kn)
    knee = np.array([0.15 * np.sin(a), -0.15 * np.cos(a)])
    ankle = knee + 0.15 * np.array([np.sin(a - k), -np.cos(a - k)])
    assert np.allclose(ankle, (0.05, -0.25), atol=1e-6)
    assert abs(th + kn + ft) < 1e-9                                    # foot stays level


def test_gait_never_slides() -> None:
    body = rigs.Body()
    for speed, run in ((0.8, False), (1.2, False), (2.4, True), (4.0, True)):
        g = motion.plan_gait(body, speed, run=run)
        assert abs(g.stance_speed - speed) < 1e-9
        assert g.stride / 2 <= 0.98 * body.leg


def test_clips_close_and_feet_stay_planted(tmp_path: Path) -> None:
    rig = _rig()
    motion.standard_clips(rig, walk_speed=1.2, run_speed=2.4, attack="melee", work="smash")
    p = rig.scene.write(tmp_path / "probe.glb")
    rep = gltf.validate(p, max_triangles=catalog.TRIANGLE_BUDGET["character"])
    assert rep["ok"], rep["errors"]
    assert set(catalog.CHARACTER_CLIPS) <= set(rep["clips"])
    chk = motion.check(p, rig.clip_info)
    assert chk["ok"], chk["errors"]
    assert chk["clips"]["walk"]["foot_slip"] < 0.01
    assert rig.clip_info["die"]["loop"] is False


def test_props_toggle_per_clip(tmp_path: Path) -> None:
    rig = _rig()
    motion.idle(rig)
    motion.work_hammer(rig, prop="tool")
    g = gltf.read(rig.scene.write(tmp_path / "probe.glb"))
    i = g.node_index("tool")
    assert np.linalg.norm(dict(g.pose("idle", 0.5))[i][:3, :3], axis=0).max() < 0.01
    assert np.linalg.norm(dict(g.pose("work", 0.5))[i][:3, :3], axis=0).min() > 0.99


def test_team_material_and_palette() -> None:
    rig = _rig()
    mats = rig.scene.materials
    assert mats[rigs.TEAM].extras.get("tint") == "team"
    used = {m.material for n in rig.nodes.values() for m in n.meshes}
    assert used <= {rigs.TEAM, rigs.PALETTE, rigs.GLOW}


def test_units_package(tmp_path: Path) -> None:
    for name in ("builder", "clone", "boss", "turret"):
        rep = units.build_unit(name, tmp_path / name, previews=False)
        assert not rep["checks"]["errors"], (name, rep["checks"]["errors"])
        assert rep["triangles"] <= catalog.TRIANGLE_BUDGET["character"]
    rig, meta = units.make("boss")
    assert "slam" in rig.clip_info and meta["faction"] == "enemy"
    # Full package with previews for one unit: it must validate.
    rep = units.build_unit("clone", tmp_path / "pkg")
    assert rep["validation"]["ok"], rep["validation"]["errors"]
    man = catalog.read_manifest(tmp_path / "pkg")
    assert man["kind"] == "character" and man["faction"] == "enemy" and man["clips"]["walk"]["speed"] > 0
