"""Check a story's book button (its reading wiring) live in the game, per beat and route.

Plays the story's package with the engine's in-page e2e driver against a
running dev server and, at every settled beat of every route, verifies the
card's book button against the story's `reading` fields — shown on exactly
the beats a passage carries (the passage named by a beat holds until the next
names another or the beat says `false`), clicking opens the reading at that
passage (title and "N of 7" position from the installed reading package),
Back returns to the card. Returns data, not verdicts:

    from utils import bookcheck
    r = bookcheck.check(story_pkg, game_root, out=TASK_DIR / "bookcheck")
    r["passed"], r["problems"], r["observations"]   # also r["output"], r["shots"]

Needs the game's dev server up at `url` (default http://localhost:5176, the
port dex's ML briefs give) with the story's reading package installed; the
story itself is played from the package's story.json, not the game's copy.
About a minute per route and size. `bookcheck.mjs` beside this file does the
browser work.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
TOOL = HERE / "bookcheck.mjs"
DEFAULT_URL = "http://localhost:5176"
SIZES = ("desktop", "phone")


def _playwright(game_root: Path) -> str | None:
    """A playwright this script can import: the games workspace or engine's, else none."""
    for p in (game_root.parent / "node_modules" / "playwright" / "index.mjs",
              game_root.parent / "engine" / "node_modules" / "playwright" / "index.mjs",
              game_root / "node_modules" / "playwright" / "index.mjs"):
        if p.is_file():
            return str(p)
    return None


def check(story_pkg: str | Path, game_root: str | Path, out: str | Path, *,
          url: str = DEFAULT_URL, viewports: Sequence[str] = SIZES,
          time_scale: int = 4, shots: Sequence[str] | None = None,
          timeout: int = 1200) -> dict:
    """Play every route at every size and report the book-button state of every beat.

    Returns {passed, problems, observations, output, shots}: `problems` lists
    one line per beat whose book button is wrong, whose reading view fails to
    open or shows another passage, or where the page raised an error;
    `output` is the written `bookcheck.json`, `shots` its directory
    (the checked beats' cards and open reading views). `shots` (optional)
    names the beats to screenshot; the default is every beat that names a
    passage or turns the book off.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["node", str(TOOL), "--story", str(story_pkg), "--game", str(game_root),
           "--out", str(out), "--url", url, "--viewports", ",".join(viewports),
           "--time-scale", str(time_scale)]
    if shots:
        cmd += ["--shots", ",".join(shots)]
    pw = _playwright(Path(game_root).expanduser().resolve())
    if pw:
        cmd += ["--playwright", pw]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    report_file = out / "bookcheck.json"
    if not report_file.is_file():
        raise RuntimeError(f"bookcheck wrote no report (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    report = json.loads(report_file.read_text())
    return {
        "passed": bool(report["passed"]),
        "problems": report["problems"],
        "observations": report["observations"],
        "output": str(report_file),
        "shots": str(out / "shots"),
        "log": proc.stdout,
    }
