## `bench`

Simulate Model Workshop's bench beat by beat: which panels a story leaves up, and which ui.hide misses.

- `PANEL_ACTIONS` — constant
- `bench_trace(beats: Iterable[dict], *, list_ids: bool=False) -> list[tuple[str, dict[str, str | None]]]` — The panels up after each beat: [(beat id, {panel key: title or None})]; list_ids=True lets a ui.hide list hide each key.
- `ui_hide_misses(beats: Iterable[dict], *, list_ids: bool=False) -> list[str]` — Ids of beats whose ui.hide removes nothing (nothing up under it, or a list id unless list_ids=True, as the game now accepts).

## `placement`

Where to put story spawns on the data floor so they don't sit on the data.

- `free_spot(points, near, r=0.55, step=0.25, limit=4.7, reach=8)` — The grid spot [x, z] nearest `near` at least `r` from every point ([x, z, ...] rows), within ±limit; None if there is none.

## `rng`

The engine's seeded RNG (core/rng.js makeRng, mulberry32), bit-exact, to recompute what a seeded game action draws.

- `make_rng(seed: int) -> Callable[[], float]` — A callable returning the same floats in [0, 1) as the engine's makeRng(seed), in the same order.

## `story`

Stories: lesson scripts the game's story runner plays, packaged as kind "story".

- `SCHEMA` — constant
- `KIND` — constant
- `STORY_FILE` — constant
- `EXPORT_SUBDIR` — constant
- `SAY_MAX` — constant
- `CHOICE_EVERY` — constant
- `BEATS_SOFT` — constant
- `BEATS_HARD` — constant
- `LEVELS` — constant
- `NAME` — constant
- `ID` — constant
- `ASSET` — constant
- `HEX` — constant
- `DATA_POINT` — constant
- `PALETTE` — constant
- `STEMS` — constant
- `PROJECT_DIR` — constant
- `actions() -> dict[str, dict]` — The `act` catalogue the game implements: `{name: {algorithm, summary, args}}`.
- `known_assets(game_root: str | Path | None=None, packages_dir: str | Path | None=None) -> set[str]` — `kind/name` of every asset in the game's index and in a project's packages.
- `fmt_number(value: Any, digits: int=2) -> str` — A number as the game prints it: whole as is, else JS toFixed(digits), real minus "−", no "−0".
- `fmt_fixed(value: Any, digits: int=2) -> str` — A number as the game's num() prints it (model.params notes, model.explain): always `digits` decimals, real minus, no "−0.00".
- `fmt_sig(value: Any) -> str` — A density as the game's sig() prints it (naive Bayes model.explain): 3 decimals ≥ 0.1, 3 significant digits ≥ 1e−4, else "1.58e−266".
- `fmt_score(value: Any) -> str` — A score as Model Workshop's evaluation labels print it (fmt in evaluation.js: eval.score MSE, CV means, AUC): 2 decimals below 10, 1 below 100, else none; "–" if not finite.
- `fmt_percent(value: Any) -> str` — A share as Model Workshop's pct() prints it (accuracy, recall, precision, F1 labels and meters): Math.round(value × 100) then "%".
- `validate_story(package_or_dict: str | Path | dict, game_root: str | Path | None=None, *, known: Iterable[str] | None=None, packages_dir: str | Path | None=None) -> dict` — Check a story (a package directory or the story dict) against schema 1.
- `spawned_assets(beats: Iterable[dict]) -> list[str]` — Every `kind/name` the beats spawn, once each in first-seen order: a story's `assets` list.
- `write_story(package: str | Path, story: dict, *, name: str, title: str='', tags: Sequence[str]=()) -> Path` — Write `story.json` and `manifest.json` into `package`.
- `export_story(package: str | Path, game_root: str | Path) -> dict` — Copy a valid story package into `<game_root>/public/assets/story/<name>/`.
- `story_packages(directory: str | Path) -> list[Path]` — Every story package at or below `directory`.
- `export_all_stories(directory: str | Path, game_root: str | Path) -> list[dict]` — Export every story package under `directory`.
- `simulate(package_or_dict: str | Path | dict, game_root: str | Path) -> list[dict]` — Play a story through the game's own action handlers (storysim.mjs, needs node): one snapshot dict per beat.
- `outline(package_or_dict: str | Path | dict) -> str` — The story as a readable playthrough (Markdown): the review a model's previews give.

## `workshop_math`

Model Workshop's regression maths in Python (datasets, least squares), checkable against the game's own JS.

- `HERE` — constant
- `GAME_ROOT` — constant
- `LIMIT` — constant
- `gauss(rng) -> float` — A standard normal sample from rng (Box–Muller), as datasets.js's gauss.
- `make_dataset(kind: str, n: int=40, noise: float=0.2, seed: int=1, points=None) -> list[dict]` — The game's makeDataset for the regression generators (and custom points).
- `least_squares(pts) -> dict` — The closed-form best line {w, b} for x → y, as linear.js's leastSquares.
- `solve(A, b)` — Gaussian elimination with partial pivoting, as linear.js's solve.
- `least_squares2(pts) -> dict` — The best plane {w1, w2, b} for (x, z) → y, as linear.js's leastSquares2.
- `poly_fit(pts, degree: int) -> list[float]` — Polynomial least squares on raw x, coefs[k] multiplying x^k, as linear.js's polyFit.
- `poly_predict(coefs, x: float) -> float` — The polynomial's value at x (Horner), as linear.js's polyPredict.
- `fit_stats(pts, f) -> dict` — SSE, MSE, MAE and R² of fit f(x, z) over pts, as regression.js's fitStats.
- `line_fn(m)` — A line model {w, b} as a function f(x, z=0) for fit_stats.
- `plane_fn(m)` — A plane model {w1, w2, b} as a function f(x, z) for fit_stats.
- `lad_line(pts) -> dict` — Least-absolute-deviations line: some optimum passes through two data points.
- `game_numbers(requests: list[dict], game_root: str | Path=GAME_ROOT) -> list[dict]` — The game's own JS (via node) for requests {dataset,n,noise,seed} or {points}, plus optional fit line|plane|poly.