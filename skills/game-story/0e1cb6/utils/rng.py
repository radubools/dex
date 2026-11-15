"""The engine's seeded RNG (core/rng.js makeRng, mulberry32), bit-exact, to recompute what a seeded game action draws."""
from __future__ import annotations

from typing import Callable

_M32 = 0xFFFFFFFF


def make_rng(seed: int) -> Callable[[], float]:
    """A callable returning the same floats in [0, 1) as the engine's makeRng(seed), in the same order."""
    a = seed & _M32

    def imul(x: int, y: int) -> int:
        return (x * y) & _M32

    def rng() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & _M32
        t = imul(a ^ (a >> 15), 1 | a)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & _M32) ^ t
        return ((t ^ (t >> 14)) & _M32) / 4294967296

    return rng
