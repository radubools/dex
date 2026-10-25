## `bench`

Simulate Model Workshop's bench beat by beat: which panels a story leaves up, and which ui.hide misses.

- `PANEL_ACTIONS` — constant
- `bench_trace(beats: Iterable[dict], *, list_ids: bool=False) -> list[tuple[str, dict[str, str | None]]]` — The panels up after each beat: [(beat id, {panel key: title or None})]; list_ids=True lets a ui.hide list hide each key.
- `ui_hide_misses(beats: Iterable[dict], *, list_ids: bool=False) -> list[str]` — Ids of beats whose ui.hide removes nothing (nothing up under it, or a list id unless list_ids=True, as the game now accepts).
- `trace_hide_misses(trace: Iterable[dict], beats: Iterable[dict]) -> list[tuple[str, str]]` — (beat id, hide id) for each ui.hide id that removed nothing, read off a story.simulate trace (sees panels game actions draw).

## `camfit`

Predicts where a story camera puts highlighted things on a Model Workshop screen (desktop and phone), and fits cameras that keep them clear.

- `FOV` — constant
- `ROOMY` — constant
- `UNIT_PX` — constant
- `ZOOM` — constant
- `PITCH` — constant
- `VIEWPORTS` — constant
- `aim(pos, lifted_points, target)` — lesson.js aim(): half way up raised things, pulled toward them when they stand behind the target.
- `camera(cam, pos, lifted_points, vp)` — The eye, look point and projection constants a story camera ends at.
- `project(point, eye, look, vp)` — Screen pixels (x, y) of a world point (x, height, z), or None when behind the eye.
- `CARD` — constant
- `card_rect(vp, beat=None, options=0)` — The story card's rectangle for a beat: its cue, prompt and options wrapped to the card width.
- `clear(sxy, vp, beat=None, bench=True)` — Pixels to the nearest limit onScreen() applies (negative: covered or off screen).
- `aim_snapshot(trace, beats, beat_id)` — The stage a beat's camera aims at: the previous beat's snapshot plus this beat's spawns (trace from story.simulate).
- `check(cam, aim_snap, snap, ids, beat=None, bench=True)` — {viewport: {id: margin px}}: the camera's aim from `aim_snap` (the stage when the camera is set),
- `fit(aim_snap, snap, ids, base, beat=None, bench=True, xs=None, zs=None, dists=(9, 10, 11, 12, 13, 14, 16), pitches=(30, 35, 40, 45, 50, 55, 60))` — The camera near `base` whose worst highlight margin over both viewports is largest: (margin, camera, margins).
- `highlight_ids(beat)` — The entity ids a beat's highlight rings (single object or list; `entity` and `entities`).
- `fit_beats(package, game_root, beat_ids=None, base=None, **fit_args)` — Fit a camera for every beat that rings entities, on story.explore's playthroughs: {beat id: {ids, now, fit}}.
- `input_as_choice(beat)` — A copy of a beat whose wait.input is modelled as a one-option choice, so card_rect/check/fit count the input form.

## `placement`

Where to put story spawns on the data floor so they don't sit on the data.

- `free_spot(points, near, r=0.55, step=0.25, limit=4.7, reach=8)` — The grid spot [x, z] nearest `near` at least `r` from every point ([x, z, ...] rows), within ±limit; None if there is none.

## `review`

Review sheets for a story's end-to-end screenshots: each beat's desktop and phone shot side by side, small enough to open.

- `review_sheets(package, routes=('r0',), out_dir=None, width=620, quality=70)` — Write `<route>-<nnn>-<beat>.jpg` sheets (desktop left, phone right) from pkg/e2e/shots into out_dir; returns their paths.

## `rng`

The engine's seeded RNG (core/rng.js makeRng, mulberry32), bit-exact, to recompute what a seeded game action draws.

- `make_rng(seed: int) -> Callable[[], float]` — A callable returning the same floats in [0, 1) as the engine's makeRng(seed), in the same order.

## `story`

Stories: lesson scripts the game's story runner plays, packaged as kind "story".

- `SCHEMA` — constant
- `SCHEMAS` — constant
- `CALLOUT_MAX` — constant
- `KIND` — constant
- `STORY_FILE` — constant
- `EXPORT_SUBDIR` — constant
- `SAY_MAX` — constant
- `MATH` — constant
- `text_length(text: str) -> int` — A text's length as the limits count it: a $$…$$ formula counts half its LaTeX source (at least 1).
- `unclosed_math(text: str) -> str | None` — The text after a $$ that does not close, or None.
- `MATH_UNICODE` — constant
- `math_problems(story_or_beats: dict | list) -> list[dict]` — Every spot in a story's shown text where maths is not LaTeX: [{beat, text, problem}], empty when clean.
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
- `validate_story(package_or_dict: str | Path | dict, game_root: str | Path | None=None, *, known: Iterable[str] | None=None, packages_dir: str | Path | None=None, lab: str | Path | None=None) -> dict` — Check a story (a package directory or the story dict), schema 1 or 2.
- `act_entries(act: Any) -> list[dict]` — A beat's acts as a flat list (schema 2: one act, a list, `parallel` groups).
- `story_stats(story: dict) -> dict` — Beats, choices, inputs, actions used and level of a story (either schema): what the manifest records.
- `has_engine(game_root: str | Path) -> bool` — Whether a game has the engine's story module and node to run it (for validation parity).
- `check_with_engine(package_or_dict: str | Path | dict, game_root: str | Path, *, known: Iterable[str] | None=None) -> dict` — Validate a story with the game's own engine (`validateStory`, via storycheck.mjs): {ok, errors, warnings}.
- `reads(game_root: str | Path) -> list[dict]` — The `$` paths the game can read (its src/reads.js READS: path, what, example).
- `spawned_assets(beats: Iterable[dict]) -> list[str]` — Every `kind/name` the beats spawn, once each in first-seen order: a story's `assets` list.
- `write_story(package: str | Path, story: dict, *, name: str, title: str='', tags: Sequence[str]=()) -> Path` — Write `story.json` and `manifest.json` into `package`.
- `export_story(package: str | Path, game_root: str | Path) -> dict` — Copy a valid story package into `<game_root>/public/assets/story/<name>/`.
- `story_packages(directory: str | Path) -> list[Path]` — Every story package at or below `directory`.
- `export_all_stories(directory: str | Path, game_root: str | Path) -> list[dict]` — Export every story package under `directory`.
- `simulate(package_or_dict: str | Path | dict, game_root: str | Path) -> list[dict]` — Play a story through the game's own action handlers (storysim.mjs, needs node): one snapshot dict per beat.
- `has_lab_machines(game_root: str | Path) -> bool` — Whether a game runs its stations as lab machines (its src/actions/index.js exports machineFor).
- `check_lab(package_or_dict: str | Path | dict, game_root: str | Path) -> dict` — Play a story through the game (its engine's runner, its lab machines, its handlers) and report what goes wrong.
- `explore(package_or_dict: str | Path | dict, game_root: str | Path) -> list[dict]` — Every playthrough check_lab makes: [{pick, trace, finished, path}], the main line first.
- `lab_machine(algorithm: str, game_root: str | Path) -> str` — A station's lab machine as Markdown (its states, and what each accepts and does): read it before writing beats.
- `E2E_FILE` — constant
- `E2E_DIR` — constant
- `REVIEWED` — constant
- `e2e(package: str | Path, game_root: str | Path, *, viewports: str | Sequence[str] | None=None, port: int | None=None, timeout: int=1800, out: str | Path | None=None, parallel: bool=True) -> dict` — Play a story package in the game itself, in a real browser (the engine's story-e2e.mjs).
- `e2e_merge(package: str | Path, viewports: Sequence[str]=('desktop', 'phone')) -> dict | None` — Merge per-size reports (`e2e/<size>/report.json`) into `e2e/report.json`, which the game's pull reads.
- `e2e_start(package: str | Path, game_root: str | Path, *, viewports: str='desktop,phone') -> dict` — Start the end-to-end test in the background and return at once: {pid, log}.
- `e2e_wait(package: str | Path, timeout: float=540) -> dict` — Wait (up to `timeout` s, under a shell command's limit) for a run e2e_start began: {done, passed, output}.
- `e2e_segment(package: str | Path, game_root: str | Path, until: str, *, viewports: str='desktop', timeout: int=1800) -> dict` — Test the lesson only up to beat `until`, to iterate on one section without replaying the rest.
- `e2e_problem(package: str | Path) -> str | None` — Why a package has no passing end-to-end run for its current content, or None (the game's pull gate, exactly).
- `e2e_skeleton(package_or_dict: str | Path | dict) -> dict` — A starting e2e.json for a story: every beat that needs an assertion or a player move, with what to fill in.
- `e2e_tests_problems(package: str | Path) -> list[str]` — What is missing from a package's e2e.json for the definition of done (empty when it is complete).
- `done(package: str | Path, game_root: str | Path, *, known: Iterable[str] | None=None) -> dict` — The definition of done for a story package: {ok, errors, warnings}.
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