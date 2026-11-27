"""Irregular flat outlines in the ground plane: patches of dirt, meadow, quarry dust."""

from __future__ import annotations

import math

import numpy as np


def blotch(rx: float, rz: float, n: int, rng: np.random.Generator, rough: float = 0.22) -> list[tuple[float, float]]:
    """An irregular closed outline of `n` (x, z) points about rx x rz round the origin, CCW; `rough` sets the wobble."""
    pts = []
    ph = rng.uniform(0, 2 * math.pi, 3)
    for i in range(n):
        t = 2 * math.pi * i / n
        k = 1 + rough * (0.6 * math.sin(2 * t + ph[0]) + 0.3 * math.sin(3 * t + ph[1]) + 0.25 * math.sin(5 * t + ph[2]))
        pts.append((rx * k * math.cos(t), rz * k * math.sin(t)))
    return pts
