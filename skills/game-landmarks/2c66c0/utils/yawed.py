"""Clips for detail nodes placed with a yaw: swings that keep the node's own heading while they play.

A glTF rotation channel replaces a node's rotation, so `Assembly.sway` on a
part made with `a.detail(..., yaw=...)` turns it back to face +Z for as long
as the clip runs. These key the yaw and the swing together.

    from utils import yawed
    yawed.sway_yawed(a, "banners", nodes, [yaw_0, yaw_1], degrees=10, period=3.2)
"""

from __future__ import annotations

from typing import Sequence

from . import mesh


def sway_yawed(a, clip: str, nodes: Sequence, yaws: Sequence[float], degrees: float = 6.0,
               period: float = 3.0) -> None:
    """Like `Assembly.sway` about local X, for detail nodes placed with `yaw=`: every key keeps the node's yaw."""
    c = a.scene.animation(clip)
    ts = [period * i / 4 for i in range(5)]
    for i, (n, yaw) in enumerate(zip(nodes, yaws)):
        angs = [0, degrees, 0, -degrees, 0] if i % 2 == 0 else [0, -degrees * 0.8, 0, degrees * 0.8, 0]
        q = mesh.quat_euler(y=yaw)
        c.rotate(n, ts, [mesh.quat_mul(q, mesh.quat_euler(x=ang)) for ang in angs])
