"""A story's e2e.json `expect` expressions evaluated by the game's own rules on every route, without a browser."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import story

SEP = " ## "


def expect_results(package: str | Path, game_root: str | Path, e2e: dict | None = None) -> list[dict]:
    """Every e2e expect on every story.explore route: [{route, beat, expect, value, ok}] (value as the game renders it; booleans print 1 / 0)."""
    pkg = Path(package)
    doc = json.loads((pkg / story.STORY_FILE).read_text())
    tests = e2e if e2e is not None else json.loads((pkg / story.E2E_FILE).read_text())
    expects = {bid: b.get("expect") or [] for bid, b in (tests.get("beats") or {}).items()}
    probe = copy.deepcopy(doc)
    for b in probe["beats"]:
        if expects.get(b["id"]):
            b["say"] = SEP.join("{" + e + "}" for e in expects[b["id"]])
            b.pop("more", None)
    out = []
    for run in story.explore(probe, game_root):
        for snap in run["trace"]:
            exp = expects.get(snap.get("id")) or []
            if not exp:
                continue
            got = (snap.get("shown") or {}).get("say", "").split(SEP)
            for i, e in enumerate(exp):
                value = got[i].strip() if i < len(got) else None
                out.append({"route": run.get("pick"), "beat": snap["id"], "expect": e,
                            "value": value, "ok": value in ("1", "true")})
    return out
