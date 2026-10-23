"""Play a story's beats through Model Workshop's own action handlers (via node) and read back what each beat shows.

`simulate(story)` runs playback.mjs, which imports the game's src/actions with
a stub stage and bench, plays every beat's spawns, removes and act in order
(awaiting each action, tweens finished at once), and snapshots the scene after
each beat. A snapshot is a dict: `points` (id, x, z, cls, role, trueCls, and
the model's pred and prob), `logit` (the logistic model's weights, if any),
`params` (the model's params() rows), `meters` ({id: {label, value, shown}}),
`charts`, `panels` ({bench key: text rows}), `plaque`, `labels`, `rings`
({prefix: point ids} for miss:, flip:, warn:, split:, halo:, call:,
explain:), `entities` and the action's `result`. Use it to fill and check
every number a cue quotes from the game itself.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME_ROOT = Path.home() / "games" / "model-workshop"


def simulate(story: dict, game_root: str | Path = GAME_ROOT) -> dict[str, dict]:
    """The scene after each beat of `story`, played by the game's own actions: {beat id: snapshot}."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "story.json"
        f.write_text(json.dumps(story))
        out = subprocess.run(["node", str(HERE / "playback.mjs"), str(Path(game_root).expanduser()), str(f)],
                             capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"playback failed:\n{out.stderr}")
    return {s["id"]: s for s in json.loads(out.stdout)}


def params_table(snapshot: dict, key: str = "params") -> dict[str, str]:
    """A model.params bench table as {parameter: value text}, formatted as the bench prints it."""
    rows = snapshot["panels"][key]
    return {r.split(" | ")[0]: r.split(" | ")[1] for r in rows[2:]}  # title, header, then rows
