## `bench`

Simulate Model Workshop's bench beat by beat: which panels a story leaves up, and which ui.hide misses.

- `PANEL_ACTIONS` — constant
- `bench_trace(beats: Iterable[dict], *, list_ids: bool=False) -> list[tuple[str, dict[str, str | None]]]` — The panels up after each beat (its acts in order, schema-2 lists and parallel groups too): [(beat id, {panel key: title or None})]; list_ids=True lets a ui.hide list hide each key.
- `ui_hide_misses(beats: Iterable[dict], *, list_ids: bool=False) -> list[str]` — Ids of beats with a ui.hide that removes nothing (nothing up under it, or a list id unless list_ids=True, as the game now accepts); schema-2 act lists are walked in order.
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
- `EDGE_GAP` — constant
- `edge_clear(sxy, vp, beat=None, bench=True, gap=None)` — clear(), also capped by the distance from outside to the card and bench minus a gap (EDGE_GAP per viewport), so a point just past their edge is not clear.
- `aim_snapshot(trace, beats, beat_id)` — The stage a beat's camera aims at: the previous beat's snapshot plus this beat's spawns (trace from story.simulate).
- `check(cam, aim_snap, snap, ids, beat=None, bench=True)` — {viewport: {id: margin px}}: the camera's aim from `aim_snap` (the stage when the camera is set),
- `fit(aim_snap, snap, ids, base, beat=None, bench=True, xs=None, zs=None, dists=(9, 10, 11, 12, 13, 14, 16), pitches=(30, 35, 40, 45, 50, 55, 60))` — The camera near `base` whose worst highlight margin over both viewports is largest: (margin, camera, margins).
- `fit_closest(aim_snap, snap, ids, base, beat=None, bench=True, min_margin=40, xs=None, zs=None, dists=(9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 22), pitches=(30, 35, 40, 45, 50, 55, 60))` — The shortest-distance camera near `base` whose worst highlight margin over both viewports is at least `min_margin` px (then the larger margin): (margin, camera, margins), or None.
- `highlight_ids(beat)` — The entity ids a beat's highlight rings (single object or list; `entity` and `entities`).
- `fit_beats(package, game_root, beat_ids=None, base=None, **fit_args)` — Fit a camera for every beat that rings entities, on story.explore's playthroughs: {beat id: {ids, now, fit}}.
- `fit_shared(package, game_root, beat_ids, extra_ids=(), xs=(-1, 0, 1), zs=(-2, -1, 0, 1), dists=(12, 14, 16, 18, 20, 22, 25), pitches=(40, 50, 60, 70), yaws=(0,), min_margin=40, bench=True)` — Rank cameras one story camera could share across several beats: [(worst margin px, camera, {beat id: margin})].
- `fit_routes(routes, beats, *, extra=None, stand_ins=None, keep_margin=40, bench=True, **fit_args)` — Fit cameras over every route of a story, each beat checked against the camera it inherits: (fitted {beat id: camera}, report {beat id: tuple}).
- `input_as_choice(beat)` — A copy of a beat whose wait.input is modelled as a one-option choice, so card_rect/check/fit count the input form.

## `code_board`

Story cameras over a range of a code-station program's lines, framed by their `code:line-<n>` ids.

- `board_lines_camera(source: str, first: int, last: int, *, duration: float=1) -> dict` — A story camera {frame, duration} framing `code:line-<first>`..`code:line-<last>` of `source`.

## `code_trace`

Line hits of a code-station program, counted as the station counts them (sys.settrace), for picking code.runTo targets.

- `FILENAME` — constant
- `line_hits(source, globals_=None, watch=())` — Run source with globals_ and return each line event as (line, hit, {watched local: value copy}) plus the final globals.
- `total_hits(events)` — The final hit count per line from line_hits' events: {line: hits}.

## `code_tree`

Which `obj:<n>` the code station gives each node of a level-order binary tree, for story highlights.

- `in_order(vals: list, i: int=0) -> list` — The node values of a level-order list (children of i at 2i + 1 and 2i + 2, None for an empty spot), in order.
- `tree_node_ids(acts: list[dict], vals: list, game_root: str | Path) -> dict` — {node value: "obj:<n>"} for the tree built from level-order `vals`, on the stage `acts` leave (story.simulate).

## `e2e_expect`

Evaluate a story's e2e.json `expect` expressions in the game's own engine, without a browser (via story.simulate).

- `unmet_expects(package_or_dict: str | Path | dict, game_root: str | Path, e2e: dict | None=None) -> list[dict]` — The e2e `expect` expressions not true on the main line: [{beat, expr, value}]; value None means the beat was not played.

## `e2e_expects`

A story's e2e.json `expect` expressions evaluated by the game's own rules on every route, without a browser.

- `SEP` — constant
- `expect_results(package: str | Path, game_root: str | Path, e2e: dict | None=None) -> list[dict]` — Every e2e expect on every story.explore route: [{route, beat, expect, value, ok}] (value as the game renders it; booleans print 1 / 0).

## `e2e_report`

What a story's end-to-end report found: every failed check, run error and page problem, as data.

- `e2e_failures(report_path) -> list[dict]` — Every failed check, run error and page problem in an e2e report.json: [{viewport, route, beat, check, detail}], empty when it passed.

## `expect_check`

A story's e2e.json `expect` expressions evaluated by the game's own expression engine, without a browser.

- `SEP` — constant
- `TRUE` — constant
- `expect_results(package_or_dict: str | Path | dict, game_root: str | Path, e2e: dict | None=None) -> dict` — Every route played with each beat's cue replaced by its expects: {"results": [{beat, expr, value, ok}], "unreached": [beat ids]}.

## `highlights`

Story highlight ids checked against what the game's simulator has on the stage at each beat, without a browser.

- `stage_ids(snapshot: dict) -> set[str]` — Every entity id on the stage in one story.simulate snapshot (its `entities` map of id prefix to the rest).
- `highlight_ids(beat: dict) -> list[str]` — The entity ids a beat's highlight rings (`entity` and `entities` items, in order); UI targets are left out.
- `missing_highlights(package_or_dict: str | Path | dict, game_root: str | Path, skip_prefixes: tuple[str, ...]=('code:',)) -> list[dict]` — Main-line beats ringing ids the simulated stage lacks there: [{beat, missing: [id, ...]}], empty when all are present.

## `mathtext`

Checks on how a story writes its maths: values in formulas versus values in plain text.

- `val_outside_math(story: Any, path: str='story') -> list[str]` — Paths of the strings in a story (any nesting) where \val{…} sits outside $$…$$; the game shows those raw.

## `more_render`

Beats' `more` explanations rendered as the game shows them, by playing every route through the game's simulator.

- `SEP` — constant
- `rendered_more(package_or_dict: str | Path | dict, game_root: str | Path) -> dict` — Every route played with each beat's cue replaced by its `more`: {"more": {beat id: [paragraph, …]}, "missing": [{"pick", "beat", "text"}, …]}, the first rendering of each beat reached.

## `nn_math`

Model Workshop's neural-network maths in Python (MLP start, forward, backprop, per-layer gradient sizes), checkable against the game's src/ml/nn.js.

- `sigmoid(t: float) -> float` — 1 / (1 + e^-t), 0 where JavaScript's Math.exp would overflow.
- `ACT` — constant
- `class MLP` — nn.js MLP: weights W[l][i][j] (one row per neuron), biases b[l][i], hidden `activation`, sigmoid output.
- `layer_gradient_sizes(dW)` — Mean |gradient| of each weight layer, input to output: what nn.gradients returns as `sizes`.

## `placement`

Where to put story spawns on the data floor so they don't sit on the data.

- `free_spot(points, near, r=0.55, step=0.25, limit=4.7, reach=8)` — The grid spot [x, z] nearest `near` at least `r` from every point ([x, z, ...] rows), within ±limit; None if there is none.

## `remedial`

Remedial beats for schema 2 stories that only a wrong choice enters, so correct answers need no goto.

- `remedial_flag(beat_id: str) -> str` — The story variable that marks a remedial beat as entered: 'seen_' + the beat id with '-' as '_'.
- `remedial_option(option: dict, beat_id: str) -> dict` — A copy of a (wrong) choice option that sets the remedial beat's flag and goes to it.
- `remedial_beat(beat: dict, back: str) -> dict` — A copy of a beat that runs only after its remedial_option (when the flag), clears the flag and goes to `back`.

## `review`

Review sheets for a story's end-to-end screenshots: each beat's desktop and phone shot side by side, small enough to open.

- `review_sheets(package, routes=('r0',), out_dir=None, width=620, quality=70)` — Write `<route>-<nnn>-<beat>.jpg` sheets (desktop left, phone right) from the package's e2e shots into out_dir; returns their paths.

## `rng`

The engine's seeded RNG (core/rng.js makeRng, mulberry32), bit-exact, to recompute what a seeded game action draws.

- `make_rng(seed: int) -> Callable[[], float]` — A callable returning the same floats in [0, 1) as the engine's makeRng(seed), in the same order.

## `shots`

Story end-to-end helpers: small copies of the screenshots for the visual review, and camera probes run in the game.

- `REPEATABLE` — constant
- `e2e_shot_dirs(package: str | Path) -> list[Path]` — The folders holding a package's e2e screenshots: e2e/shots if it has any, else e2e_start's e2e/<viewport>/shots.
- `review_copies(package: str | Path, patterns: Iterable[str]=('*.png',), *, desktop: int=512, phone: int=700, quality: int=72, out: str | Path | None=None, shot_dirs: Iterable[str | Path] | None=None) -> list[Path]` — JPEG copies of a package's e2e screenshots small enough to open (desktop and phone shots capped at their own long side): the paths.
- `candidates(grid: dict) -> list[dict]` — Every story camera in a grid {"x", "z", "distance", "pitch", "yaw": sequences}, in product order.
- `probe_story(story_dict: dict, beat_ids: Iterable[str], grid: dict, *, repeatable: Iterable[str]=REPEATABLE, duration: float=0.6) -> dict` — A throwaway story replaying `story_dict`'s main line (the beats before its first `end`) that repeats each named beat under every camera in `grid` (ids `<beat>--<n>`).
- `probe_cameras(story_dict: dict, beat_ids: Iterable[str], grid: dict, game_root: str | Path, work_dir: str | Path, *, keep: bool=False, port: int | None=None, repeatable: Iterable[str]=REPEATABLE) -> dict` — Run probe_story in the game's end-to-end test: {"passing": {beat: [camera, …]}, "candidates": [camera, …], "passed": bool, "probe": dir if kept else None}.
- `probe_failures(probe_dir: str | Path, cams: list[dict]) -> list[dict]` — Why each probed camera failed, from a kept probe_cameras run: [{"viewport", "beat", "camera", "fails": [detail, …]}, …].

## `story`

Stories: lesson scripts the game's story runner plays, packaged as kind "story".

- `SCHEMA` — constant
- `SCHEMAS` — constant
- `CALLOUT_MAX` — constant
- `KIND` — constant
- `STORY_FILE` — constant
- `EXPORT_SUBDIR` — constant
- `SAY_MAX` — constant
- `MORE_MAX` — constant
- `MORE_PARAS` — constant
- `HINT_KEYS` — constant
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
- `more_problems(more: Any) -> list[str]` — What is wrong with a beat's `more` (None when absent): the engine's rules, in Python.
- `hint_key_problems(story: dict) -> list[str]` — Hint fields the game does not read: any of HINT_KEYS anywhere, and `more` anywhere but on a beat.
- `asks(beat: dict) -> bool` — Whether a beat asks the player something: a choice, an input, an action or an `until`.
- `missing_more(story: dict) -> list[str]` — The ids of beats that ask something and have no `more` yet: each needs its explanation.
- `more_outline(package_or_dict: str | Path | dict) -> str` — Every beat's `more` paragraphs in story order, as Markdown to append to the outline (which does not show them).
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
- `e2e_start(package: str | Path, game_root: str | Path, *, viewports: str='desktop,phone', timeout: int=1800) -> dict` — Start the end-to-end test in the background and return at once: {pid, log}.
- `e2e_wait(package: str | Path, timeout: float=540) -> dict` — Wait (up to `timeout` s, under a shell command's limit) for a run e2e_start began: {done, passed, output}.
- `e2e_segment(package: str | Path, game_root: str | Path, until: str, *, viewports: str='desktop', timeout: int=1800) -> dict` — Test the lesson only up to beat `until`, to iterate on one section without replaying the rest.
- `e2e_problem(package: str | Path) -> str | None` — Why a package has no passing end-to-end run for its current content, or None (the game's pull gate, exactly).
- `e2e_skeleton(package_or_dict: str | Path | dict) -> dict` — A starting e2e.json for a story: every beat that needs an assertion or a player move, with what to fill in.
- `e2e_tests_problems(package: str | Path) -> list[str]` — What is missing from a package's e2e.json for the definition of done (empty when it is complete).
- `SCREENSHOT_SUFFIXES` — constant
- `clear_screenshots(package: str | Path) -> dict` — Delete a package's end-to-end screenshots, keeping the reports: {files, bytes}.
- `done(package: str | Path, game_root: str | Path, *, known: Iterable[str] | None=None, keep_screenshots: bool=False) -> dict` — The definition of done for a story package: {ok, errors, warnings, cleared}.
- `outline(package_or_dict: str | Path | dict, *, more: bool=False) -> str` — The story as a readable playthrough (Markdown): the review a model's previews give; more=True lists each beat's `more` paragraphs (" - ? …") under its header.

## `svm_geometry`

Hard-margin SVM geometry for Model Workshop's svm.line convention: signed sides, streets, the widest street and its posts.

- `normal(angle)` — The unit normal (−sin a, cos a) of a line whose direction is `angle` degrees from +x toward +z (svm.js).
- `side(p, angle, offset)` — Signed distance of point p ([x, z, ...]) from the line n·p = offset at `angle` (svm.line's angle, offset).
- `street(points, angle)` — (half-width, offset) of the widest street between class 0 and class 1 rows [x, z, cls] at `angle`; half-width < 0 when they overlap.
- `widest(points, step=0.1, refine=60)` — The hard-margin SVM by an angle scan (step degrees) and golden-section refinement: (angle, offset, half-width).
- `support(points, angle, offset, half, tol=0.001)` — The rows sitting on the street's edges (|side| within `tol` of `half`): the support vectors.
- `clearance(points, angle, offset)` — (distance, row) of the point nearest the line: the half-width of a street centred on it, and the seed that stops it.
- `separates(points, angle, offset)` — Whether the line puts every class 1 row on its positive side and every other row on its negative side.

## `svm_math`

Model Workshop's SVM maths in Python (max margin, Pegasos soft margin, RBF lift and cut), checkable against the game's own JS.

- `RAD` — constant
- `label(p) -> int` — A point's SVM label y: +1 for class 1, −1 for any other class.
- `score(m, p) -> float` — f(p) = w1·x + w2·z + b for model m at point p.
- `svm_fit(points, C=1.0, epochs=200, seed=1) -> dict` — svm.js svmFit: Pegasos on λ/2‖w‖² + mean hinge, λ = 1/(C n), bias as a feature, the second half averaged.
- `margin_width(m) -> float` — svm.js svmMargin: the street's width 2/‖w‖.
- `support_vectors(points, m, tol=0.01) -> list` — svm.js supportVectors: the points with y·f(p) ≤ 1 + tol (the svm actions pass tol 0.02).
- `hinge(points, m, C=1.0) -> float` — svm.js hinge: the soft-margin objective ½‖w‖² + C·Σ max(0, 1 − y·f(p)).
- `max_margin_separator(points) -> dict` — svm.js maxMarginSeparator: the hard-margin model by an angle scan and ternary refine, svm_fit(C=100) if not separable.
- `rbf_height(p, gamma=0.3, centre=(0.0, 0.0)) -> float` — svm.js rbfLift for one point: the height 4·e^(−γ‖p − centre‖²) (the game's lift uses the centre (0, 0)).
- `lifted_plane_cut(pts) -> tuple` — svm.js liftedPlaneCut on points carrying "y": (height, above class, errors), the first best in the game's scan order.
- `ring_boundary(height, gamma=0.3)` — svm.js ringBoundary: the radius where the RBF lift meets a cut at `height`, None outside (0, 4).
- `line_weights(angle_deg, offset, side) -> dict` — actions/svm.js weightsOf for a plain line (svm.line): a unit w, so f is the signed distance.
- `norm_line(angle, offset, side=1) -> tuple` — actions/svm.js normLine: the same line with its angle in [0, 180), as (angle, offset, side).
- `pick_side(points, angle, offset, side) -> int` — actions/svm.js pickSide: the side (±1) that puts class 1 where it makes fewer mistakes; a tie keeps `side`.

## `uitargets`

Bench names as the CSS selectors schema-2 highlight lists and e2e `ui` checks need (Model Workshop).

- `ui_css(name: str) -> str` — The CSS for a bench name: bench, formula/plaque, loss, meter:<id>, chart:<id>, slider:<p>, tool:<t>; anything else unchanged.

## `workshop_eval`

Model Workshop's evaluation maths in Python (splits, folds, metrics, the station polynomial, logistic fit), line for line with the game's JS.

- `XS` — constant
- `js_round(v: float) -> int` — JS Math.round: halves round up (Python's round() goes to even).
- `confusion(y_true, y_pred, classes: int=2) -> list[list[int]]` — Counts m[true][pred], as metrics.js's confusion.
- `precision_recall_f1(m, positive: int=1) -> tuple[float, float, float]` — (precision, recall, F1) of one class from a confusion matrix, 0 when undefined, as precisionRecallF1.
- `accuracy(y_true, y_pred) -> float` — Share of matching labels, 0 for none, as metrics.js's accuracy.
- `shuffled(items, seed: int) -> list` — The game's seeded Fisher–Yates shuffle (makeRng(seed)), same order as metrics.js.
- `train_val_test_split(items, train: float=0.6, val: float=0.2, test: float=0.2, seed: int=1) -> tuple[list, list, list]` — (train, val, test) as eval.split draws them: rounded shares of the shuffle, the test takes the rest.
- `split_points(items, train: float=0.6, val: float=0.2, test: float=0.2, seed: int=1, stratify: bool=False) -> tuple[list, list, list]` — (train, val, test) of point dicts exactly as eval.split draws them (evaluation.js splitPoints): stratified splits each "cls" with seed + 101·i, then reshuffles each group.
- `class_counts(train, val, test) -> dict` — Points per class in each group, as eval.split's `counts`: {cls: {"train", "val", "test"}} over labelled point dicts.
- `k_folds(items, k: int, seed: int=1) -> list[tuple[list, list]]` — [(val, train)] per fold: fold f holds the shuffled positions i with i % k == f, as kFolds.
- `ridge_poly_fit(points, degree: int, lam: float=0.0) -> list[float]` — Coefficients of (x/5)^k by ridge least squares (intercept unpenalised, 1e-9 on the diagonal), as ridgePolyFit.
- `poly_value(coefs, x: float) -> float` — The station polynomial at x (Horner on u = x/5), as polyValue.
- `mse_of(coefs, points) -> float` — Mean squared error of the polynomial over points (0 for none).
- `poly_sweep(train, val, max_deg: int=11, lam: float=0.0) -> list[tuple[float, float]]` — [(train MSE, val MSE)] for degrees 0..max_deg fitted on train: train.curve's sweep.
- `cross_validate(pool, folds: int, degree: int, lam: float=0.0, seed: int=1) -> tuple[list[tuple[list, list, float]], float, float]` — eval.crossValidate on heights: ([(val ids, coefs, fold MSE)], mean, population sd) over the clamped k folds.
- `sigmoid(t: float) -> float` — 1 / (1 + e^−t).
- `logistic_fit(points, lr: float=0.5, steps: int=600) -> tuple[float, float, float]` — (w1, w2, b) after batch gradient steps on log-loss from zero, as logisticFit (the game's first fit uses lr 0.5, 600 steps).
- `logistic_prob(m, p) -> float` — p(class 1) of point p under model (w1, w2, b).

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