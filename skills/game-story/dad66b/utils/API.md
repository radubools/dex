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

## `code_hash`

Which hash bucket the code station draws a dict or set key in (Model Workshop's view.js bucketOf / bucketCount).

- `bucket_of(key, m=8)` — The bucket (0..m-1) the station draws `key` in: ints (and bools) mod m, strings by h = h*31 + code mod 1000003, others by their JSON.
- `bucket_count(n)` — How many buckets the station draws for n keys: 8, doubling while n > 0.66 of them.

## `code_steps`

How many trace steps a code-station program takes, to keep it under the station's cap (engine code/trace.js MAX_STEPS).

- `MAX_STEPS` — constant
- `FILENAME` — constant
- `trace_steps(source, globals_=None)` — The line, call and return events of running `source` with `globals_` as its inputs, counted under sys.settrace.

## `code_trace`

Line hits of a code-station program, counted as the station counts them (sys.settrace), for picking code.runTo targets.

- `FILENAME` — constant
- `line_hits(source, globals_=None, watch=())` — Run source with globals_ and return each line event as (line, hit, {watched local: value copy}) plus the final globals.
- `total_hits(events)` — The final hit count per line from line_hits' events: {line: hits}.

## `code_tree`

Which `obj:<n>` the code station gives each node of a level-order binary tree, for story highlights.

- `in_order(vals: list, i: int=0) -> list` — The node values of a level-order list (children of i at 2i + 1 and 2i + 2, None for an empty spot), in order.
- `tree_node_ids(acts: list[dict], vals: list, game_root: str | Path) -> dict` — {node value: "obj:<n>"} for the tree built from level-order `vals`, on the stage `acts` leave (story.simulate).

## `dann_between`

DANN's local between-class matrices B_i and their average B-bar (ESL 13.4, (13.10)), as numpy arrays.

- `local_between(P, cls, i, k=6)` — B_i = sum_k pi_k (m_k - m)(m_k - m)^T over the k points of P (rows) nearest P[i], itself included, with class labels cls.
- `bbar(P, cls, k=6)` — (13.10): the average over every point of P of its local_between matrix.

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

## `floor_contour`

Zero contours of a gridded function as evenly spaced floor points, for drawing a curve as a row of spawned markers.

- `contour_dots(g, f, gap=0.25, limit=4.85, digits=2)` — [x, z] points of the zero contour of f (f[iz, ix] sampled at g[ix], g[iz]), at least `gap` apart and within ±limit.

## `floor_shapes`

Lines and ellipses on the data floor as evenly spaced [x, z] points, for drawing them as rows of spawned markers.

- `line_dots(n, c, gap=0.3, keep=None, limit=4.85, digits=2, reach=15)` — [x, z] points of the line n . p = c inside the floor (|x|, |z| <= limit), `gap` apart from s = -reach along it; keep(p) filters them.
- `ellipse_dots(mu, S, c=5.991464547107979, gap=0.32, limit=4.85, digits=2, samples=4000)` — [x, z] points of the ellipse (p - mu)^T S^-1 (p - mu) = c (default: a Gaussian's 95% contour), about `gap` apart, inside the floor.

## `forest_proximity`

Random-forest proximities (ESL 15.3.3) and classical MDS, as numpy arrays, for proximity-plot and MDS lessons.

- `forest_proximity(X, y, *, trees=500, max_features=1, seed=0)` — N x N out-of-bag proximity of a scikit-learn random forest on (X, y): the share of trees, among those where both points are out of bag, that put them in one leaf (diagonal 1).
- `classical_mds(d, k=2)` — Classical (Torgerson) MDS: an N x k array of coordinates whose distances best match the N x N dissimilarity matrix d.

## `fused_lasso`

The 1-D fused lasso signal approximator (ESL (18.22)-(18.24)) solved exactly by its fusion path, with a generic-solver check.

- `soft(v, t)` — Soft thresholding sign(v)(|v| - t)_+, elementwise.
- `flsa_path(y, w=None)` — Exact fusion events of the l1 = 0 path: [(l2, groups)] where groups = [(start, end, value at that l2, slope)].
- `flsa(y, l1, l2, w=None)` — The exact solution of (18.24) at (l1, l2): the l1 = 0 path at l2, soft-thresholded by l1/2.
- `objective(b, y, l1, l2, w=None)` — The value of the criterion at fit b (w: optional per-jump weights, e.g. 1/gap for divided differences).
- `solve_generic(y, l1, l2, w=None)` — The same criterion solved by SLSQP as a smooth constrained problem (b, |b| <= u, |Db| <= v): a reference to check flsa against.
- `segments(b, tol=1e-09)` — How many constant pieces a fit has: 1 + the jumps larger than tol.

## `gp_posterior`

The exact posterior of a 1-D RBF Gaussian process, as numbers and as llm-math plot.function formulas.

- `rbf(a, b, ell=1.0)` — The squared-exponential kernel exp(-(a - b)^2 / (2 ell^2)), signal variance 1.
- `class Posterior` — GP posterior (prior mean 0, RBF kernel of length-scale ell, noise variance s) after readings (xs, ys).

## `graph_walks`

Small-graph numbers for graph lessons: adjacency, hop distances, random-walk matrix, earth mover's distance, Ollivier-Ricci curvature.

- `adjacency(edges, n)` — Symmetric 0/1 adjacency matrix of an undirected graph on nodes 0..n-1 from (i, j) pairs.
- `hops(edges, n)` — All-pairs shortest-path lengths in edges (BFS); inf between disconnected nodes.
- `walk(edges, n, *, self_loops=True)` — Row-stochastic random-walk matrix D^-1 A (with self-loops by default: A + I, the GCN's A-hat read as a walk).
- `cloud(i, edges, n)` — Uniform distribution on node i and its neighbours (Ollivier's one-step measure mu_i).
- `w1(mu, nu, D)` — Earth mover's (Wasserstein-1) distance between two distributions on the nodes, under ground distances D, by linear programming.
- `ollivier_curvature(i, j, edges, n)` — Ollivier-Ricci curvature kappa(i, j) = 1 - W1(mu_i, mu_j) / d(i, j), mu uniform on each node and its neighbours.

## `highlights`

Story highlight ids checked against what the game's simulator has on the stage at each beat, without a browser.

- `stage_ids(snapshot: dict) -> set[str]` — Every entity id on the stage in one story.simulate snapshot (its `entities` map of id prefix to the rest).
- `highlight_ids(beat: dict) -> list[str]` — The entity ids a beat's highlight rings (`entity` and `entities` items, in order); UI targets are left out.
- `missing_highlights(package_or_dict: str | Path | dict, game_root: str | Path, skip_prefixes: tuple[str, ...]=('code:',)) -> list[dict]` — Main-line beats ringing ids the simulated stage lacks there: [{beat, missing: [id, ...]}], empty when all are present.

## `history`

Beats and e2e assertions for history lessons told over a timeline (timeline.show / timeline.focus).

- `event_index(events: list[dict], year, title_start: str='') -> int` — The index of the one event of `year` whose title starts with `title_start`; raises if not exactly one.
- `timeline_event_beat(bid: str, index: int, say: str, more, callout: str, *, section: str | None=None) -> dict` — A narration beat that runs timeline.focus on event `index` (stored as `ev<index>`), rings its plaque and waits for next.
- `link_selector(name: str) -> str` — The card element a [[story/<name>]] link becomes (an <a> once installed, a greyed span while coming soon).
- `history_e2e_beats(beats: list[dict], events: list[dict], *, show_as: str='line', show_beat: str | None=None, eras: int=0) -> dict` — e2e.json beat specs: the timeline.show count and pieces, each focused event's year, id and visibility, and every cue link on the card.

## `linear_smoothers`

Linear smoothers on 1-D data as numpy matrices (local linear, loess, smoothing spline), their CV/GCV/Cp scores, and a local linear fit as a plot.function formula.

- `local_linear_row(xs, x0, w)` — The equivalent kernel l(x0) of a weighted local linear fit at x0 with kernel weights w (ESL (6.8)-(6.9)).
- `local_linear_S(xs, lam)` — The smoother matrix of local linear regression with a Gaussian kernel of standard deviation lam: row i is l(x_i).
- `loess_S(xs, span)` — The smoother matrix of loess: local linear, tri-cube kernel (6.6), nearest-neighbour width holding round(span * N) points.
- `spline_penalty_K(xs)` — The natural cubic smoothing spline's penalty matrix K = Q R^-1 Q^T (Reinsch form) for sorted, distinct xs.
- `smoothing_spline_S(xs, df, *, iters=200)` — (S, lam): the smoothing spline's smoother (I + lam K)^-1 whose trace is df (2 < df < N), lam found by bisection on log lam.
- `smoother_scores(S, y, f=None, sigma2=None)` — {df, asr, cv, gcv, cp, pe} of a linear smoother S on y: LOO CV by (y - fit)/(1 - S_ii); cp needs sigma2, pe also the true f (else None).
- `local_linear_fn(xs, ys, *, lam='lam', var='x', xdigits=4, ydigits=2)` — The Gaussian-kernel local linear fit through (xs, ys) as one plain plot.function formula in `var` with parameter `lam`.

## `mathtext`

Checks on how a story writes its maths: values in formulas versus values in plain text.

- `val_outside_math(story: Any, path: str='story') -> list[str]` — Paths of the strings in a story (any nesting) where \val{…} sits outside $$…$$; the game shows those raw.

## `matrix_tiles`

A square matrix (proximities, dissimilarities, affinities) as a heat map of tinted blocks on the data floor: story spawns.

- `RAMP` — constant
- `ramp_color(t, ramp=RAMP)` — The '#rrggbb' at t in [0, 1] (clamped) along a ramp of (t, (r, g, b)) stops.
- `cell_positions(n, groups=None, *, step=0.68, gap=0.22)` — Station-local coordinates of n row/column slots `step` apart, centred on 0, with an extra `gap` between
- `matrix_tiles(M, prefix, order=None, groups=None, *, step=0.68, scale=0.9, gap=0.22, lo=None, hi=None, asset='environment/value-block', ramp=RAMP)` — Spawns {id: '<prefix>-<row>-<col>', asset, at, scale, color} drawing M[order][:, order] as a heat map;

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

## `optimal_scoring`

Optimal scoring and flexible discriminant analysis (ESL 12.5.1) for a linear-basis regression, as numpy arrays.

- `optimal_scoring(H, g, K=None)` — FDA's steps 1-2 on basis matrix H (N x M, constant column included) and labels g in 0..K-1: {theta, mu, B, eta, cent, A, D}.

## `placement`

Where to put story spawns on the data floor so they don't sit on the data.

- `free_spot(points, near, r=0.55, step=0.25, limit=4.7, reach=8)` — The grid spot [x, z] nearest `near` at least `r` from every point ([x, z, ...] rows), within ±limit; None if there is none.

## `plot_balls`

Unit balls |x1|^p + |x2|^p = 1 for the llm-math plot board: the plot.function acts and the equal-scale window that draw one.

- `BALL_X` — constant
- `BALL_Y` — constant
- `BALL_TOP` — constant
- `BALL_BOTTOM` — constant
- `p_slider(value, *, lo=0.25, hi=12, step=0.05, label='$$p$$')` — A plot.function parameter spec for p: its value and the slider's range.
- `unit_ball_acts(p, *, ids=('top', 'bottom'), color='class-0', label='$$\\|x\\|_p = 1$$', sliders=True, clear=False, x_label='$$x_1$$', y_label='$$x_2$$')` — The two plot.function acts drawing the l_p unit ball (p a number, or a p_slider spec when sliders).
- `ENET_TOP` — constant
- `ENET_BOTTOM` — constant
- `enet_ball_acts(alpha, *, ids=('en-top', 'en-bot'), color='class-1', label='elastic net', sliders=False, window=(BALL_X, BALL_Y))` — The two plot.function acts drawing the elastic-net ball (alpha a number, or a {value, min, max, step, label} spec when sliders); alpha 0 is the l1 diamond, 1 the l2 disk.

## `plot_bars`

Bars for the llm-math plot board: a bar of a given height over [x0, x1] as one plot.function formula, labelled at its own foot.

- `plot_bar(x0: float, x1: float, height: str | float, edge: float=0.15) -> str` — A plot.function formula in x: `height` (a number or formula) flat over [x0, x1], sides sloped over `edge`, undefined outside.

## `plot_bridges`

Brownian bridges for the llm-math plot board: a noisy path from a to b over t in [0, 1] as one plot.function formula.

- `GUARD` — constant
- `bridge_path_fn(a, b, draws, *, scale='sqrt(eps/2)', var='t', digits=4)` — The formula (1 - t)a + tb + scale * sqrt(2) sum_k z_k sin(k pi t)/(k pi): a Brownian bridge with fixed draws z_k, its noise `scale` itself a formula in the curve's parameters.

## `plot_check`

plot.function formulas compiled and evaluated by the game engine's own plot compiler, without a browser.

- `HERE` — constant
- `engine_plot(game_root) -> Path` — The engine's plot module a game uses: ~/games/<game>/../engine/src/plot/index.js.
- `formula_values(fn: str, envs=(), game_root='~/games/model-workshop') -> dict` — {fn, vars, values} of formula `fn` at each env ({name: value}) as the engine computes it (non-finite as text), or {fn, error} when it does not parse.
- `formulas_values(items, game_root='~/games/model-workshop') -> list[dict]` — formula_values for several (fn, envs) pairs in one node run.

## `plot_ellipse`

Ellipses for the llm-math plot board: a matrix's image of the unit circle (semi-axes a, b at angle t) and its diameters, as plot.function formulas.

- `MARGIN` — constant
- `ELLIPSE_TOP` — constant
- `ELLIPSE_BOTTOM` — constant
- `DIAMETER_A` — constant
- `DIAMETER_B` — constant
- `CIRCLE_TOP` — constant
- `CIRCLE_BOTTOM` — constant

## `plot_ellipses`

Ellipses of a symmetric 2x2 matrix for the llm-math plot board: the image of the unit circle as two plot.function curves, closed at the tips.

- `WIDE_X` — constant
- `WIDE_Y` — constant
- `NEAR_X` — constant
- `NEAR_Y` — constant
- `ellipse_fns(s='s', u='u', window=WIDE_X)` — (top, bottom) plot.function formulas of Q diag(s, u) Q^T applied to the unit circle, s along (1, 1) and u along (1, -1).
- `ellipse_acts(params, fns, *, ids=('e-top', 'e-bot'), window=(WIDE_X, WIDE_Y), color='class-0', label=None, sliders=None, clear=False, x_label='$$x_1$$', y_label='$$x_2$$')` — The two plot.function acts drawing one ellipse from ellipse_fns' (top, bottom); both halves share `params`.

## `plot_gaussian`

Gaussian contours Δ² = c of N(0, [[1, ρ], [ρ, 1]]) for the llm-math plot board: the plot.function acts and the equal-scale window that draw one.

- `GAUSS_X` — constant
- `GAUSS_Y` — constant
- `GAUSS_TOP` — constant
- `GAUSS_BOTTOM` — constant
- `rho_slider(value, *, lo=-0.95, hi=0.95, step=0.05, label='$$\\rho$$')` — A plot.function parameter spec for the correlation ρ: its value and the slider's range.
- `contour_acts(c, rho, *, ids=None, color='class-0', label=None, sliders=True, clear=False, x_label='$$x_1$$', y_label='$$x_2$$', tail=0.1)` — The two plot.function acts drawing the contour Δ² = c of the unit-variance Gaussian with correlation ρ (a number, or a rho_slider spec when sliders).

## `plot_hands`

Unit hands for the llm-math plot board: the segment from the origin to (cos a, sin a) as a plot.function formula, for vectors that turn.

- `MARGIN` — constant
- `hand_fn(angle, margin=MARGIN)` — The plot.function formula of the unit hand at `angle` (radians, itself a formula in the curve's parameters, e.g. "0.5 + t*θ").
- `segment_fn(angle, length, ox=0, oy=0, margin=MARGIN)` — The plot.function formula of the segment from (ox, oy) at `angle`, `length` long (angle and length may be formulas in the curve's parameters).
- `bar_fn(height, half, x0=0, margin=MARGIN)` — The plot.function formula of a horizontal bar at `height` from x0 to x0 + 2*half (`half` may be a formula), e.g. a length that grows with a slider.

## `plot_kernels`

1-D kernel smoothers (ESL 6.1) as plain plot.function formulas: Nadaraya-Watson, local linear, and their equivalent kernels.

- `num(v, digits=4)` — A number as plain formula text, without trailing zeros.
- `kernel_term(xi, x='x', lam='lam')` — The Epanechnikov weight (1 - ((x - xi)/lam)^2)_+ of a point at xi, without the 3/4 that cancels in every smoother here.
- `nadaraya_watson_fn(xs, ys, lam='lam')` — The Nadaraya-Watson fit (6.2) of points (xs, ys) as one formula in x, bandwidth parameter `lam`; NaN where no point is in reach.
- `local_linear_fn(xs, ys, lam='lam')` — Local linear regression (6.7) as one formula in x: (S2 T0 - S1 T1)/(S0 S2 - S1^2), S_j = sum K d^j, T_j = sum K d^j y, d = x_i - x.
- `equivalent_kernel_fn(xs, x0='x0', lam='lam')` — The local linear equivalent kernel at target `x0` as a curve over x: K(x0, x)(s2 - s1 (x - x0))/(s0 s2 - s1^2), moments over xs; at x_i it is l_i(x0).
- `nw_weights_fn(xs, x0='x0', lam='lam')` — The Nadaraya-Watson weights at target `x0` as a curve over x: K(x0, x) / sum_i K(x0, x_i); at x_i it is that point's weight.

## `plot_marks`

Marks for the llm-math plot board as plot.function formulas: upright lines at an x (knots, thresholds) and gaps where a curve jumps.

- `BOARD_SAMPLES` — constant
- `vline_fn(x0, x_window, y_window, *, samples=BOARD_SAMPLES, margin=0.85, var='x')` — A steep line through x0 that the board draws as an upright mark spanning the y window: slope just under its jump limit.
- `gap_fn(xs, *, half=0.02, var='x')` — Terms to add to a formula (" + ..." joined) that make it NaN within `half` of each x in xs, so the board leaves a gap there.
- `vline_expr_fn(x0_expr, x_window, y_window, *, samples=BOARD_SAMPLES, margin=0.85, var='x')` — vline_fn with its x given as a formula string (a slider parameter, a step function of one), so the mark follows it.

## `plot_normal`

The standard normal CDF, its inverse and binormal ROC curves as plain plot.function formulas.

- `pnorm_fn(arg)` — The formula Phi(arg) = (1 + erf(arg / sqrt 2)) / 2, for `arg` a formula or a number.
- `qnorm_fn(var='x')` — The formula Phi^-1(var) by Winitzki's erf^-1 approximation (|Phi(result) - var| < 2e-3 on [0.01, 0.999]); NaN at 0 and 1.
- `binormal_roc_fn(a, b=1, var='x')` — The binormal ROC sensitivity Phi(a - b * Phi^-1(var)) over specificity `var`; its AUC is Phi(a / sqrt(1 + b^2)).

## `plot_piecewise`

Piecewise-linear curves through computed knots as one plain plot.function formula (abs-value hinges), for paths with no closed form.

- `piecewise_formula(ts, vs, *, digits: int=4) -> str` — The interpolant through knots (ts increasing, vs) as a plain formula in x: v0 + s0 (x - t0) + sum_k c_k (|x - t_k| + x - t_k).

## `plot_points`

Data points placed exactly on the llm-math plot board, at a plotted value (x, y), as data.scatter rows.

- `BOARD_X` — constant
- `BOARD_H` — constant
- `BOARD_Z` — constant
- `board_xh(x, y, window_x, window_y)` — The stage (x, height) where plot.js draws the value (x, y) for a board window (window_x, window_y).
- `board_point(x, y, window_x, window_y, cls=0, digits=4)` — A data.scatter row [x, z, class, height] that sits on the plot board at (x, y); lift it with data.height.

## `plot_quadform`

Quadratic forms h(x)^T M h(x) of basis functions as one plot.function formula, e.g. a linear fit's pointwise variance.

- `quadratic_form_fn(fns, M)` — The plain formula sum_i M_ii h_i^2 + 2 sum_{i<j} M_ij h_i h_j for basis formulas `fns` and symmetric matrix `M`, coefficients at full precision.

## `plot_rbf`

Gaussian radial basis functions for the llm-math plot board: bumps, their sum, renormalized shares (ESL 6.30) and weighted fits as plot.function formulas.

- `bump_fn(c, lam='lam', var='x')` — The Gaussian bump e^{-((var - c)/lam)^2/2} centred at c as a formula; lam is a parameter name or a number.
- `sum_fn(centres, lam='lam', var='x')` — The sum of the Gaussian bumps at `centres` as a formula.
- `renorm_fn(c, centres, lam='lam', var='x')` — The renormalized bump at c, its share of the sum of the bumps at `centres` (ESL 6.30), as a formula.
- `fit_fn(betas, centres, lam='lam', var='x', digits=4)` — sum_j beta_j times the bump at centre_j as a formula, the betas written to `digits` decimals.

## `plot_runs`

Simulated runs for the llm-math plot board: a trace of values and a histogram of samples as plot.function formulas.

- `polyline_fn(values, var='k', digits=4)` — The trace values[0], values[1], ... as a plot.function formula in `var`: straight segments between whole steps 0, 1, 2, ...
- `polyline_xy_fn(xs, ys, var='x', digits=4)` — Straight segments through (xs[i], ys[i]) (xs increasing, any spacing) as a plot.function formula in `var`; flat outside.
- `histogram_fn(edges, heights, var='x', steep=1000, digits=4)` — Bars of `heights` between consecutive `edges` (len(edges) == len(heights) + 1), 0 outside, as a plot.function formula in `var`.

## `plot_select`

One plot.function formula that shows one of several precomputed curves, chosen by a slider parameter.

- `curve_selector_fn(curves, param='M')` — Join {whole number m: formula} into sum_m max(1 - |param - m|, 0)*(formula): exactly curve m at param = m, a blend between.
- `end_guard_fn(end, weight='1', var='x')` — A term to add to a formula that makes it NaN for var > end (sqrt of a negative) while `weight` > 0, and 0 elsewhere, so the curve stops at end.
- `curve_selector_until_fn(curves, ends, param='M', var='x')` — curve_selector_fn where curve m stops at var = ends[m]: NaN past it only while its own weight is above 0, so a short curve never blanks the others.

## `plot_smooth`

Local averages of data points for the llm-math plot board: band (moving) and Gaussian-kernel averages as one plot.function formula.

- `band_indicator_fn(xi, h='h', var='x', steep=40)` — A steep 0-1 ramp formula: 1 when |var - xi| < h, 0 beyond, 0.5 exactly at the edge (ramp width 1/steep).
- `band_average_fn(points, h='h', var='x', steep=40)` — The formula averaging y over the points [x, y] within h of var: NaN (a gap on the board) where none is near.
- `gaussian_average_fn(points, lam='lam', var='x', digits=4)` — The Nadaraya-Watson average of the points [x, y] with a Gaussian kernel of sd lam, repeated x merged into one weighted term, terms in increasing x.

## `plot_splines`

Cubic regression splines in the truncated power basis 1, x, x^2, x^3, (x - xi_k)_+^3 as plain plot.function formulas.

- `truncated_power_basis_fns(knots, var='x')` — The basis formulas 1, var, var^2, var^3, relu(var - xi_k)^3 for each knot, in that order (e.g. for plot_quadform).
- `truncated_power_fn(beta, knots, var='x', digits=10)` — The spline sum_j beta_j h_j(var) over truncated_power_basis_fns(knots) as one formula, coefficients to `digits` significant digits.
- `bspline_to_truncated_power(beta, knots, lo, hi, samples=301)` — Truncated-power coefficients (for truncated_power_fn) of the cubic B-spline with coefficients `beta`, interior `knots`, on [lo, hi].

## `plot_steps`

Step functions of a parameter as one plain plot.function formula, for piecewise-constant paths (tree size against alpha, support counts).

- `step_fn(breakpoints, param='x', *, steep=200, digits=4)` — breakpoints [[p_0, v_0], [p_1, v_1], ...] (p increasing) as a formula in `param`: v_k for p_k <= param < p_{k+1}, each p_k included (ramps 1/steep wide end at p_k).

## `plot_survival`

Survival curves for the llm-math plot board: Kaplan-Meier steps, the two-group log-rank P value, and the steps as one plot.function formula.

- `kaplan_meier(time, event)` — Kaplan-Meier steps [[t, S(t)], ...] from [0, 1], one per distinct event time (event 1 = death, 0 = censored).
- `logrank_p(time, event, group)` — The two-sided log-rank P value (chi-square, 1 df) for survival differing between `group` True and False.
- `km_points(steps, upto=None, gap=0.02)` — The steps up to `upto` (all when None), steps closer than `gap` in t merged into one (the later value).
- `km_formula(steps, upto=None, param='t', *, gap=0.02, steep=100, digits=4)` — Kaplan-Meier steps as one plain plot.function formula in `param` (km_points merged, ramps 1/steep wide before each step).

## `plot_tests`

Test statistics and confidence sets for the llm-math plot board: Student t tails and quadratic-form ellipses as plot.function formulas.

- `t_tail_fn(nu, var='x')` — Pr(|T| > var) for Student's t with an even nu, as a plot.function formula (exact finite sum, no special functions).
- `ellipse_fns(h, k, M, c, tail=0.025)` — (top, bottom, half x-width) of the ellipse (p - (h, k))^T M (p - (h, k)) = c as plot.function formulas in x; `tail` keeps both halves defined a little past each tip so they join.

## `plot_wavelets`

Wavelets for the llm-math plot board: Morlet bursts, their CWT modulus in closed form, Haar pyramids and step signals as plot.function formulas.

- `W0` — constant
- `bursts_fn(bursts, w0=W0)` — A signal of Morlet bursts [(c, s), ...] (centre c, scale s) as a plot.function formula in x.
- `bursts_at(x, bursts, w0=W0)` — The bursts signal of bursts_fn, evaluated with numpy at x.
- `morlet_fn(w0=W0)` — psi_{a,b}(x) = a^{-1/2} cos(w0 (x - b)/a) e^{-((x - b)/a)^2/2} as a formula with parameters a and b.
- `cwt_modulus_fn(bursts, w0=W0)` — |Wf(a, b)| of bursts_fn's signal under the complex Morlet wavelet, as a formula in b with parameter a.
- `cwt_modulus(a, b, bursts, w0=W0)` — cwt_modulus_fn's value at (a, b), computed in Python from the same closed form.
- `cwt_modulus_quad(a, b, bursts, w0=W0, span=30, n=200001)` — |Wf(a, b)| by trapezoid quadrature on [-span, span]: a check on the closed form.
- `R2` — constant
- `haar_step(a)` — One level of Mallat's fast wavelet transform with Haar filters (1, 1)/sqrt2 and (1, -1)/sqrt2: (approx, detail).
- `haar_pyramid(x)` — [(approx, detail)] for each Haar level, finest first, down to one approximation (len(x) a power of 2).
- `block_means(x, level)` — The level-j blur in the signal's own units: each block of 2^j samples replaced by its mean.
- `step_fn(values, start=0, steep=1000)` — A piecewise-constant signal, one value per unit interval from x = start, as a plot.function formula (steep ramps at the jumps).

## `remedial`

Remedial beats for schema 2 stories that only a wrong choice enters, so correct answers need no goto.

- `remedial_flag(beat_id: str) -> str` — The story variable that marks a remedial beat as entered: 'seen_' + the beat id with '-' as '_'.
- `remedial_option(option: dict, beat_id: str) -> dict` — A copy of a (wrong) choice option that sets the remedial beat's flag and goes to it.
- `remedial_beat(beat: dict, back: str) -> dict` — A copy of a beat that runs only after its remedial_option (when the flag), clears the flag and goes to `back`.

## `review`

Review sheets for a story's end-to-end screenshots: each beat's desktop and phone shot side by side, small enough to open.

- `review_sheets(package, routes=('r0',), out_dir=None, width=620, quality=70)` — Write `<route>-<nnn>-<beat>.jpg` sheets (desktop left, phone right) from the package's e2e shots into out_dir; returns their paths.

## `review_sheets`

Contact sheets of a story's e2e screenshots, so the visual review of every beat at both sizes reads a few images instead of hundreds.

- `contact_sheets(shots_dir: str | Path, pattern: str, out_dir: str | Path, *, per: int=6, cols: int=2, width: int=800, include_more: bool=False) -> list[Path]` — Tile the screenshots matching `pattern` in `shots_dir` into labelled JPEG sheets in `out_dir` (`-more` shots only with include_more); returns the sheet paths.

## `rips_persistence`

Vietoris-Rips persistent homology of a small point cloud over Z/2: filtration, barcodes, simplex counts and Betti numbers.

- `rips_filtration(points, max_dim=2, max_eps=inf)` — [(eps, dim, vertices)] for every simplex up to dimension max_dim entering by max_eps, sorted by (eps, dim).
- `persistence(points, top=1, max_eps=inf)` — {dim: sorted [(birth, death)]} for dims 0..top, death inf for features that never die; zero-length bars dropped.
- `counts(points, eps, max_dim=3)` — Simplices of VR_eps by dimension, [vertices, edges, triangles, ...] up to max_dim.
- `betti_at(diagram, eps)` — {dim: number of bars alive at eps (birth <= eps < death)} for a persistence() diagram.

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

## `spline_basis`

Natural cubic spline bases (ESL (5.4)-(5.5)) as numpy matrices, for spline and functional-data lessons.

- `ncs_basis(x, knots)` — The N x K natural cubic spline basis at x for K knots: N1 = 1, N2 = x, N_{k+2} = d_k - d_{K-1} (ESL (5.4)-(5.5)).

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
- `LINK` — constant
- `story_links(text: str) -> list[dict]` — The links in a text, in order: [{story, label}] (label None when the link names none).
- `bad_link(text: str) -> str | None` — A link meant but not well formed (`[[story…` that does not parse, `[[name|label]]` without story/): the text from it, or None.
- `link_text(text: str) -> str` — The text with each link as its label (a link with none: its story's id).
- `text_length(text: str) -> int` — A text's length as the limits count it: a $$…$$ formula counts half its LaTeX source (at least 1), a link its label.
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
- `catalog_stories(game_root: str | Path) -> set[str]` — Every story name the game's catalog plays (each tile's, and its variants'), installed or still a placeholder: what a link may name.
- `link_problems(story: dict, stories: Iterable[str] | None=None) -> tuple[list[str], list[str]]` — Links in a story's shown text and act args: (errors, warnings). A mistyped link is an error; so is one to a
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
- `captions_done(package: str | Path, voice_package: str | Path | None=None) -> dict` — Whether the story's spoken captions are complete: {ok, errors, voice, texts, missing}.
- `outline(package_or_dict: str | Path | dict, *, more: bool=False) -> str` — The story as a readable playthrough (Markdown): the review a model's previews give; more=True lists each beat's `more` paragraphs (" - ? …") under its header.

## `svm_exact`

The exact linear soft-margin SVM (ESL 12.2) by its Wolfe dual: a reference to check the game's Pegasos fit against.

- `soft_margin_dual(P, y, C, *, b0_grid=(-20.0, 20.0, 400001))` — Maximise the dual (12.13) over 0 <= alpha_i <= C with sum alpha_i y_i = 0: (alpha, beta, beta0); y in {-1, +1}.
- `path_knots(P, y, lo, hi, *, n=1200, tol=1e-06)` — The lambda (= 1/C) values in (lo, hi) where a point joins or leaves the margin (ESL 12.3.5's path breaks), ascending.

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
- `ui_highlight_fixes(beats: list) -> list` — Copies of story beats whose highlights the game can resolve: one-item highlight lists unwrapped to the object, `ui` names inside longer lists made CSS (ui_css).

## `weighted_splines`

Weighted cubic smoothing-spline terms for backfitting (ESL 9.1): lambda fixed by nominal df, refitted with any observation weights.

- `class SplineTerm` — One input's cubic smoothing spline on its distinct values, lambda set once so trace[S(lambda)] - 1 = df with unit weights.

## `workshop_boost`

Model Workshop's regression gradient boosting in Python (stumps on x; squared, absolute and Huber loss), line for line with src/ml/boosting.js and actions/boost.js.

- `fit_stump(xs, rs)` — boosting.js fitStump: (threshold, left mean, right mean, squared error) of the best x <= threshold split of rs.
- `sign(v)` — -1, 0 or 1, as boost.js sgn.
- `pseudo(r, kind=SQ, delta=1.0)` — boost.js pseudoResidual: -dL/dF at residual r (r, sign(r), or r clipped to +-delta).
- `point_loss(r, kind=SQ, delta=1.0)` — boost.js pointLoss: r^2/2, |r|, or Huber (r^2/2 inside delta, delta(|r| - delta/2) outside).
- `leaf(rs, kind=SQ, delta=1.0)` — boost.js leafValue: a leaf's constant for residuals rs (mean, median, or median plus mean clipped deviation).
- `class Booster` — The game's booster on points (xs, ys): F0 the mean (squared) or median, then step() adds lr times one stump.

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

## `workshop_kmeans`

Python replicas of Model Workshop's k-means (kmeans.iterate, kmeans.elbow) and data.normalize, to compute a lesson's numbers before its e2e.

- `kmeans_iterate(points, centroids, max_rounds=30)` — The game's kmeans.iterate from given centroids: assign, then update + assign until no point switches; returns (labels, centroids, inertia), labels 0-based.
- `assignment_ties(points, centroids, eps=1e-09)` — Indices of points whose two nearest centroids are within eps in squared distance (the game breaks such ties by index).
- `normalize(points, scale=1.8)` — data.normalize: each of x, z to (v - mean) / sd (divisor N), times `scale` floor units; returns an N x 2 array (the game also clamps to +-4.8).
- `plusplus_start(points, k, seed=1)` — The game's k-means++ start (src/ml/kmeans.js initCentroids 'plusplus') drawn from makeRng(seed): k centroids as (x, z) tuples.
- `seeded_kmeans(points, k, seed=1, iters=20)` — The game's kmeans(points, k, {init: 'plusplus', seed}) (what kmeans.elbow runs per k): (labels 0-based, centroids, inertia).
- `elbow(points, kmax=8, seed=1)` — What kmeans.elbow returns as inertias: seeded_kmeans' inertia for k = 1..min(kmax, len(points)).
- `elbow_bend(inertias)` — The 0-based index of the bar kmeans.elbow marks: the largest second difference of the inertias (first wins a tie).

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

## `workshop_tree`

Model Workshop's Gini decision tree (src/ml/tree.js) in Python, to design a tree lesson's floor data offline.

- `gini(counts)` — Gini impurity 1 - sum p_k^2 of a list of class counts (0 for an empty node).
- `class_counts(points, classes)` — The number of points of each class 0..classes-1.
- `thresholds(points, feature)` — The candidate thresholds on feature 0 (x) or 1 (z): midpoints between neighbouring distinct values.
- `split_score(points, feature, threshold, classes)` — Weighted Gini of the two sides of `feature <= threshold` (the game's split score; lower is purer).
- `best_split(points, classes)` — The game's best split of a node: (feature 0 or 1, threshold, gain), or None when no split lowers the Gini.
- `grow(points, classes, max_depth=3, min_size=2)` — The splits of the game's greedy tree to `max_depth`, parents first: [(node id 'n0', 'n0L', …, 'x' or 'z', threshold)].