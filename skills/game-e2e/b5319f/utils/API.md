## `bookcheck`

Check a story's book button (its reading wiring) live in the game, per beat and route.

- `HERE` — constant
- `TOOL` — constant
- `DEFAULT_URL` — constant
- `SIZES` — constant
- `check(story_pkg: str | Path, game_root: str | Path, out: str | Path, *, url: str=DEFAULT_URL, viewports: Sequence[str]=SIZES, time_scale: int=4, shots: Sequence[str] | None=None, timeout: int=1200) -> dict` — Play every route at every size and report the book-button state of every beat.

## `e2e_run`

End-to-end tests of a story in its game: the game-e2e skill's runner.

- `STORY_FILE` — constant
- `E2E_FILE` — constant
- `E2E_DIR` — constant
- `REPORT` — constant
- `REVIEW` — constant
- `REVIEWED` — constant
- `SIZES` — constant
- `ADOPTED` — constant
- `tool(game_root: str | Path) -> Path` — The engine's story-e2e.mjs next to the game (games share one engine).
- `sha256(path: Path) -> str` — 
- `merge(out: str | Path, viewports: Sequence[str]=SIZES) -> dict | None` — Merge the per-size reports (`out/<size>/report.json`) into `out/report.json`.
- `merge_html(out: str | Path, game_root: str | Path) -> Path | None` — Write `out/report.html` for the merged report, rendered by the engine's own html().
- `run(story_pkg: str | Path, game_root: str | Path, out: str | Path, *, viewports: Sequence[str]=SIZES, timeout: int=1800) -> dict` — Play the whole story, every route, at each size (in parallel), into `out`.
- `cut(story: dict, tests: dict | None, until: str) -> tuple[dict, dict | None]` — The story up to and including beat `until` (which ends it), and its tests for those beats.
- `segment(story_pkg: str | Path, game_root: str | Path, until: str, out: str | Path, *, viewports: Sequence[str]=('desktop',), timeout: int=1800) -> dict` — Test the story only up to beat `until`: fast feedback on one section.
- `start(story_pkg: str | Path, game_root: str | Path, out: str | Path, *, timeout: int=1800) -> dict` — Start `run` in the background and return at once: {pid, log}. Then `wait(out)`.
- `wait(out: str | Path, timeout: float=540) -> dict` — Wait (up to `timeout` s) for a run `start` began: {done, passed, output}. Call again until done.
- `problem(story_pkg: str | Path, out: str | Path) -> str | None` — Why `out` holds no passing run for the story as it is now, or None.
- `review_problem(out: str | Path) -> str | None` — Why the visual review is missing or unfinished, or None.
- `adopt(story_pkg: str | Path, out: str | Path) -> list[str]` — For the story task: take a game-e2e task's passing report into the story's e2e/.
- `clear_shots(out: str | Path) -> int` — Delete the screenshots under `out` once reviewed; the reports and review stay. Returns how many.

## `route_diff`

Which alt-route e2e screenshots actually differ from the main route's: a

- `shots(out_dir, viewport: str, route: int) -> dict` — The e2e shots of one viewport and route: {(index, beat id, face): Path}.
- `mad(a, b, scale: int=4) -> float` — Mean absolute pixel difference of two shots, each averaged down `scale`×.
- `region_diff(a, b, box, threshold: int=30) -> float` — Fraction of pixels inside `box` (x0, y0, x1, y1) differing by over `threshold` between two shots.
- `route_diffs(out_dir, base: int=0, threshold: float=1.5, scale: int=4) -> list[dict]` — Every alt-route shot that differs from its `base`-route twin.