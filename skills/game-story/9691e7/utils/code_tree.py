"""Which `obj:<n>` the code station gives each node of a level-order binary tree, for story highlights."""
from __future__ import annotations

from pathlib import Path

from . import story


def in_order(vals: list, i: int = 0) -> list:
    """The node values of a level-order list (children of i at 2i + 1 and 2i + 2, None for an empty spot), in order."""
    if i >= len(vals) or vals[i] is None:
        return []
    return in_order(vals, 2 * i + 1) + [vals[i]] + in_order(vals, 2 * i + 2)


def tree_node_ids(acts: list[dict], vals: list, game_root: str | Path) -> dict:
    """{node value: "obj:<n>"} for the tree built from level-order `vals`, on the stage `acts` leave (story.simulate).

    `acts` are the story acts that put the tree on the floor, such as a code.show
    then a code.runTo past the build (and, for a later tree, the code.inputs and
    runTo after them). The station draws a tree in order across, so the objects
    sorted by x are the nodes in order. Values must be distinct.
    """
    beats = [{"id": "probe", "say": "probe", "act": list(acts), "wait": "next"}]
    d = {"schema": 2, "id": "probe", "title": "probe", "algorithm": "algorithms", "assets": [], "beats": beats}
    pos = story.simulate(d, game_root)[-1]["pos"]
    objs = sorted((p[0], k) for k, p in pos.items() if k.startswith("obj:"))
    order = in_order(vals)
    if len(objs) != len(order):
        raise ValueError(f"{len(objs)} objects on the stage for {len(order)} tree nodes: {objs}")
    return {v: k for v, (_, k) in zip(order, objs)}
