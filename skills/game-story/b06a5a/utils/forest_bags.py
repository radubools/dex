"""Which points Model Workshop's forest.bag draws for a seed (forest.js bootstrap), to plan and assert bootstrap-bag scenes."""
from __future__ import annotations

from .rng import make_rng

# forest.bag's seed when a story gives none: 11 + the tree index.
DEFAULT_SEED_BASE = 11


def bag_draws(seed: int, n: int) -> list[int]:
    """The n point indices (0-based, in draw order, repeats kept) forest.bag draws with this seed: floor(rng() * n), n times."""
    r = make_rng(seed)
    return [int(r() * n) for _ in range(n)]


def out_of_bag(draws: list[int], n: int) -> list[int]:
    """The indices in 0..n-1 that `draws` never picked, in order: the bag's out-of-bag points."""
    drawn = set(draws)
    return [i for i in range(n) if i not in drawn]
