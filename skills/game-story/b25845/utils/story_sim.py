"""Plays a story through a game's own action handlers in node (story_sim.mjs) and returns one snapshot per beat."""
import json
import subprocess
from pathlib import Path

MODULE = Path(__file__).with_name("story_sim.mjs")
"""The node module: import its loadGame / makeCtx / act / snapshot / play from your own .mjs scripts."""


def simulate(story: str | Path | dict, game_root: str | Path, *, engine_root: str | Path | None = None,
             node: str = "node") -> list[dict]:
    """A per-beat trace of `story` (a story.json path, a package directory or the dict) played on `game_root`."""
    if isinstance(story, dict):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(story, f)
        path = Path(f.name)
    else:
        path = Path(story)
        if path.is_dir():
            path = path / "story.json"
    args = [node, str(MODULE), str(Path(game_root).resolve()), str(path.resolve())]
    if engine_root is not None:
        args.append(str(Path(engine_root).resolve()))
    out = subprocess.run(args, capture_output=True, text=True)
    if isinstance(story, dict):
        path.unlink(missing_ok=True)
    if out.returncode:
        raise RuntimeError(f"story_sim failed:\n{out.stderr}")
    return json.loads(out.stdout)
