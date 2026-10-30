"""Story highlight ids checked against what the game's simulator has on the stage at each beat, without a browser."""
from __future__ import annotations

import json
from pathlib import Path

from . import story


def stage_ids(snapshot: dict) -> set[str]:
    """Every entity id on the stage in one story.simulate snapshot (its `entities` map of id prefix to the rest)."""
    ents = snapshot.get("entities") or {}
    if isinstance(ents, dict):
        return {f"{prefix}{rest}" for prefix, rests in ents.items() for rest in rests}
    return {str(e) for e in ents}


def highlight_ids(beat: dict) -> list[str]:
    """The entity ids a beat's highlight rings (`entity` and `entities` items, in order); UI targets are left out."""
    hs = beat.get("highlight") or []
    hs = hs if isinstance(hs, list) else [hs]
    ids: list[str] = []
    for h in hs:
        ids += list(h.get("entities", []))
        if "entity" in h:
            ids.append(h["entity"])
    return ids


def missing_highlights(package_or_dict: str | Path | dict, game_root: str | Path,
                       skip_prefixes: tuple[str, ...] = ("code:",)) -> list[dict]:
    """Main-line beats ringing ids the simulated stage lacks there: [{beat, missing: [id, ...]}], empty when all are present."""
    if isinstance(package_or_dict, dict):
        s = package_or_dict
    else:
        s = json.loads((Path(package_or_dict) / story.STORY_FILE).read_text())
    beats = {b["id"]: b for b in s["beats"]}
    out = []
    for snap in story.simulate(package_or_dict, game_root):
        beat = beats.get(snap.get("id"))
        if not beat:
            continue
        have = stage_ids(snap)
        missing = [i for i in highlight_ids(beat) if not i.startswith(skip_prefixes) and i not in have]
        if missing:
            out.append({"beat": beat["id"], "missing": missing})
    return out
