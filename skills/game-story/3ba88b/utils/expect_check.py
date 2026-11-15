"""A story's e2e.json `expect` expressions evaluated by the game's own expression engine, without a browser."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import story

#: Joins a beat's expressions in its probe cue; split on it again afterwards.
SEP = " | "
#: How the game prints a true expression in text.
TRUE = ("true", "1")


def expect_results(package_or_dict: str | Path | dict, game_root: str | Path, e2e: dict | None = None) -> dict:
    """Every route played with each beat's cue replaced by its expects: {"results": [{beat, expr, value, ok}], "unreached": [beat ids]}.

    `e2e` defaults to the package's e2e.json (required when a dict is passed).
    Each beat is judged at its first rendering on any route, after its acts and
    `set` have run, as `story.explore` plays it: inputs answered with their
    answer and player tasks (`wait.until`, a `do`) not performed, so an expect
    that depends on a `do` reads the state before it.
    """
    if isinstance(package_or_dict, dict):
        src = package_or_dict
        if e2e is None:
            raise ValueError("pass e2e= with a story dict")
    else:
        pkg = Path(package_or_dict)
        src = json.loads((pkg / story.STORY_FILE).read_text())
        if e2e is None:
            e2e = json.loads((pkg / story.E2E_FILE).read_text())
    wanted = {bid: b["expect"] for bid, b in (e2e.get("beats") or {}).items() if b.get("expect")}
    probe = copy.deepcopy(src)
    for b in probe.get("beats") or []:
        if b.get("id") in wanted:
            b["say"] = SEP.join("{" + e + "}" for e in wanted[b["id"]])
    results, seen = [], set()
    for run in story.explore(probe, game_root):
        for snap in run["trace"]:
            bid = snap.get("id")
            if bid not in wanted or bid in seen:
                continue
            seen.add(bid)
            values = (snap.get("shown") or {}).get("say", "").split(SEP)
            for i, expr in enumerate(wanted[bid]):
                value = values[i] if i < len(values) else None
                results.append({"beat": bid, "expr": expr, "value": value, "ok": value in TRUE})
    return {"results": results, "unreached": [bid for bid in wanted if bid not in seen]}
