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
- `run(story_pkg: str | Path, game_root: str | Path, out: str | Path, *, viewports: Sequence[str]=SIZES, timeout: int=1800) -> dict` — Play the whole story, every route, at each size (in parallel), into `out`.
- `cut(story: dict, tests: dict | None, until: str) -> tuple[dict, dict | None]` — The story up to and including beat `until` (which ends it), and its tests for those beats.
- `segment(story_pkg: str | Path, game_root: str | Path, until: str, out: str | Path, *, viewports: Sequence[str]=('desktop',), timeout: int=1800) -> dict` — Test the story only up to beat `until`: fast feedback on one section.
- `start(story_pkg: str | Path, game_root: str | Path, out: str | Path, *, timeout: int=1800) -> dict` — Start `run` in the background and return at once: {pid, log}. Then `wait(out)`.
- `wait(out: str | Path, timeout: float=540) -> dict` — Wait (up to `timeout` s) for a run `start` began: {done, passed, output}. Call again until done.
- `problem(story_pkg: str | Path, out: str | Path) -> str | None` — Why `out` holds no passing run for the story as it is now, or None.
- `review_problem(out: str | Path) -> str | None` — Why the visual review is missing or unfinished, or None.
- `adopt(story_pkg: str | Path, out: str | Path) -> list[str]` — For the story task: take a game-e2e task's passing report into the story's e2e/.
- `clear_shots(out: str | Path) -> int` — Delete the screenshots under `out` once reviewed; the reports and review stay. Returns how many.