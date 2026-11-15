"""Beats' `more` explanations rendered as the game shows them, by playing every route through the game's simulator."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import story

#: Joins a beat's paragraphs into its probe cue; split on it again afterwards.
SEP = " "


def rendered_more(package_or_dict: str | Path | dict, game_root: str | Path) -> dict:
    """Every route played with each beat's cue replaced by its `more`: {"more": {beat id: [paragraph, …]}, "missing": [{"pick", "beat", "text"}, …]}, the first rendering of each beat reached."""
    src = package_or_dict if isinstance(package_or_dict, dict) else \
        json.loads((Path(package_or_dict) / story.STORY_FILE).read_text())
    probe = copy.deepcopy(src)
    for b in probe.get("beats") or []:
        m = b.get("more")
        if m is not None:
            b["say"] = SEP.join([m] if isinstance(m, str) else m)
    has_more = {b.get("id") for b in probe.get("beats") or [] if b.get("more") is not None}
    out: dict = {"more": {}, "missing": []}
    for run in story.explore(probe, game_root):
        for snap in run["trace"]:
            bid = snap.get("id")
            if bid not in has_more:
                continue
            text = (snap.get("shown") or {}).get("say") or ""
            out["more"].setdefault(bid, text.split(SEP))
            if "–" in text:  # the runner's mark for a read with no value
                out["missing"].append({"pick": run.get("pick"), "beat": bid, "text": text})
    return out
