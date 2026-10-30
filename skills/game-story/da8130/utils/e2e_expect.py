"""Evaluate a story's e2e.json `expect` expressions in the game's own engine, without a browser (via story.simulate)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from . import story


def unmet_expects(package_or_dict: str | Path | dict, game_root: str | Path, e2e: dict | None = None) -> list[dict]:
    """The e2e `expect` expressions not true on the main line: [{beat, expr, value}]; value None means the beat was not played."""
    if isinstance(package_or_dict, dict):
        s = package_or_dict
        if e2e is None:
            raise ValueError("pass e2e= with a story dict")
    else:
        pkg = Path(package_or_dict)
        s = json.loads((pkg / story.STORY_FILE).read_text())
        if e2e is None:
            e2e = json.loads((pkg / story.E2E_FILE).read_text())
    asserted = e2e.get("beats", {})
    beats, names = [], {}
    for b in s["beats"]:
        beats.append(b)
        exprs = asserted.get(b["id"], {}).get("expect", [])
        # A check beat after the hand-off would sit after the sandbox: those stay for the real e2e run.
        if not exprs or story.act_entries(b.get("act")) and any(
                a.get("action") == "sandbox.open" for a in story.act_entries(b.get("act"))):
            continue
        sets = {}
        for j, expr in enumerate(exprs):
            key = f"chk_{len(names)}"
            sets[key] = expr
            names[key] = (b["id"], expr)
        beats.append({"id": f"chk-{b['id']}", "say": "check", "set": sets, "wait": "next"})
    t = copy.deepcopy(s)
    t["beats"] = beats
    snaps = story.simulate(t, game_root)
    values: dict[str, Any] = snaps[-1].get("vars", {}) if snaps else {}
    return [{"beat": beat, "expr": expr, "value": values.get(key)}
            for key, (beat, expr) in names.items() if values.get(key) is not True]
