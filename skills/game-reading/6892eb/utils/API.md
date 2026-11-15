## `adaboost_stumps`

AdaBoost.M1 (ESL Algorithm 10.1) with decision stumps, numpy only: exact weighted stump search and staged train/test error.

- `best_stump(x, y, w, order=None)` — The stump s * sign(x_j - t) of least weighted error on rows x, labels y in {-1, 1}, weights w: (err, j, t, s); order = np.argsort(x, axis=0) if precomputed.
- `adaboost(x, y, xt, yt, M=400, checkpoints=None)` — AdaBoost.M1 with stumps for M rounds: {m, train, test} misclassification rates at each checkpoint (default every round).

## `arxiv`

arXiv metadata for arXiv links, to check that a reference's arxiv.org/abs/… URL is the work it names.

- `API` — constant
- `UA` — constant
- `arxiv_id_of(url: str) -> str | None` — The arXiv id in a https://arxiv.org/abs/… (or /pdf/…) link, version kept, or None for any other URL.
- `arxiv_metadata(arxiv_id: str, *, timeout: float=20.0) -> dict` — {id, title, authors (full names), year (of first version)} for an arXiv id from the arXiv API; raises on a failed lookup.

## `best_first_tree`

Best-first least-squares regression trees (CART cuts, ESL 9.2.2), numpy only: fitted values at every leaf count up to a maximum.

- `best_split(X, y, idx, min_leaf=5)` — The best squared-error cut of rows `idx` of X: (RSS decrease, j, s, left idx, right idx), or None if no cut keeps min_leaf a side.
- `grow_fits(X, y, max_leaves=10, min_leaf=5)` — Fitted values of the tree grown best first (largest RSS decrease anywhere goes next) at 1..max_leaves leaves: array (leaves, N); stops early if no cut is left.

## `best_subset`

Best-subset linear regression (ESL Section 3.3.1) and exact leave-one-out least-squares fits, numpy only.

- `design(X, subset)` — The least-squares design for the columns `subset` of X: a column of ones, then those columns in order.
- `best_subsets(X, y)` — The lowest-RSS subset of each size k = 0..p by exhaustive search, intercept always in: list of (subset tuple, rss).
- `loo_predictions(X, y, subset)` — Leave-one-out fitted values of the least-squares fit on `subset` (with intercept), from the hat matrix, no refitting.

## `cairo_axes`

Framed linear and log-scale axes in any font (STIX Two Text has ℓ, ℝ, ν) and polylines, on top of cairo_plot.

- `MATH_FONT` — constant
- `polyline(c, pts, colour=cp.INK, width=2.0, dash=None)` — A connected line through the pixel points pts, optionally dashed (dash = [on, off, ...]).
- `axes(c, box, xr, yr, xticks, yticks, xlabel, ylabel, size=20, font=MATH_FONT, grid=cp.LIGHT)` — cairo_plot.axes with numbers and titles in `font`, minus signs typeset; returns to(x, y) -> pixel.
- `log_axes(c, box, n, lo_exp, hi_exp, xlabel, ylabel, *, step=2, xstep=10, size=20, font=MATH_FONT)` — Axes for index 0..n+1 against 10^lo_exp..10^hi_exp (ticks every `step` decades); returns to(k, v) -> pixel, v clipped at the floor.

## `cairo_graph`

Directed-graph pieces for reading figures on top of cairo_plot: arrows between disc nodes and labels that sit above the edges.

- `arrow(c, x0, y0, x1, y1, r0, r1, offset=0.0, colour=cp.INK, width=2.4, head=16)` — An arrow from the node at (x0, y0) to the node at (x1, y1), stopping at their rims r0, r1, shifted sideways by `offset` px (use ± for a reciprocal pair).
- `label(c, x, y, s, size=21, colour=cp.INK)` — Text centred on (x, baseline y) over a white backing, so edges passing behind it do not cut through it.

## `cairo_plot`

Small pycairo drawing kit for reading figures where matplotlib is not installed: canvas, text with sub/superscripts, discs, lines, framed axes.

- `rgb(hex_colour: str) -> tuple[float, float, float]` — '#RRGGBB' as a cairo (r, g, b) triple in 0..1.
- `INK` — constant
- `LIGHT` — constant
- `FONT` — constant
- `canvas(width: int, height: int) -> tuple[cairo.ImageSurface, cairo.Context]` — A white RGB image surface and its context; save with surface.write_to_png(path).
- `text(c, x, y, parts, size=22, colour=INK, align='left', italic=False, font=FONT)` — Draw runs of text at baseline y; each part is a string or (string, 'base'|'sub'|'sup'); align 'left', 'centre' or 'right'.
- `disc(c, x, y, r, fill, edge=INK, edge_width=2)` — A filled circle of radius r at (x, y) with an outline.
- `line(c, x0, y0, x1, y1, colour=INK, width=1.5, dash=None)` — A straight segment, optionally dashed (dash = [on, off, ...] in pixels).
- `axes(c, box, xr, yr, xticks, yticks, xlabel, ylabel, size=20, grid=LIGHT)` — Framed plot area box=(left, top, right, bottom) in pixels for data ranges xr, yr; returns to(x, y) -> pixel.

## `cca`

Canonical correlation analysis by the SVD of ESL (3.87) and reduced-rank regression (3.69), numpy only.

- `msqrt(A, power)` — A symmetric positive-definite matrix raised to a real power (0.5, -0.5, ...) through its eigen decomposition.
- `cca(X, Y)` — CCA of centred X (N x p) and Y (N x K) by (3.87): (U (K x M), V (p x M), correlations c, U* the left singular vectors).
- `reduced_rank(X, Y, m)` — Rank-m reduced-rank regression coefficients (3.69), B-hat U_m U_m^- with Sigma = Y^T Y / N, for centred X and Y.

## `chart`

Figures without matplotlib: a small pycairo chart kit (canvas, axes panels, ticks, lines, bands, rugs, points, legends) writing PNGs.

- `INK` — constant
- `GREY` — constant
- `FONT` — constant
- `hexrgb(h)` — '#RRGGBB' to an (r, g, b) tuple of floats in [0, 1], the colour form every drawing call takes.
- `class Canvas` — A white canvas of w x h points, written at `scale` pixels per point.
- `class Panel` — Axes in a box (left, top, right, bottom) of the canvas, mapping data coordinates to points.

## `chart_box`

Boxplots for chart.Panel figures: quartile box, median, 1.5-IQR whiskers, outlier dashes, off-scale counts.

- `OFF_SCALE` — constant
- `boxplot(p, x, vals, color=hexrgb('#56B4E9'), w=0.32, off_color=OFF_SCALE)` — An R-style boxplot of `vals` at data x on chart.Panel `p` (half-width w); values above the panel are counted under a triangle at its top.

## `chart_shapes`

Shapes for chart.Panel figures: filled polygons, edged dots, class-shape markers, and a canvas capped at the reading figure width.

- `canvas(w, h, max_px=1400)` — A chart.Canvas of w x h points at up to 2 pixels per point, never wider than max_px pixels.
- `fill_poly(p, pts, color, alpha=0.35, edge=None, width=1.6)` — A polygon through data points `pts` on chart.Panel `p`, filled at `alpha` and outlined in `edge` if given.
- `dot(p, x, y, color, r=4.5, edge=INK)` — A filled dot of radius r points with an `edge` outline at data point (x, y), with no line joining it to earlier drawing.
- `shape_marker(p, x, y, shape, color, r=5.0, edge=INK, width=0.8)` — A filled class marker at data point (x, y) on chart.Panel `p`: shape 'circle', 'square' or 'triangle', radius r points, outlined in `edge`.

## `coef_paths`

Coefficient paths of linear-model fitting procedures (LAR, forward stepwise, forward stagewise, incremental stagewise, exact FS0) and their L1 arc length, numpy only (fs0_path also uses scipy); the lasso path is lasso_path.lasso_path.

- `standardize(X, y)` — Centre the columns of X and scale them to unit norm (ESL Algorithm 3.2, step 1), centre y; returns (X, y, column norms).
- `lar_path(X, y, tol=1e-09)` — Breakpoints of least angle regression (ESL Algorithm 3.2) on unit-norm X: (betas (steps+1, p), entered variable per breakpoint, None at the end).
- `forward_stepwise_path(X, y, steps=None)` — Forward stepwise selection (ESL Section 3.3.2): least-squares fits on a growing active set; returns (betas (steps+1, p), entry order).
- `forward_stagewise_path(X, y, max_steps=4000, tol=1e-06)` — Forward stagewise (ESL Section 3.3.3) on unit-norm X: each step adds <x_j, r> to the most correlated variable's coefficient; betas per step.
- `incremental_stagewise_path(X, y, eps=0.01, max_steps=60000, tol=None, keep_every=20)` — Incremental forward stagewise FS_eps (ESL Algorithm 3.4) on unit-norm X: steps of eps toward the top correlation's sign; every keep_every-th beta.
- `fs0_path(X, y, tol=1e-09, max_steps=2000)` — Exact infinitesimal forward stagewise FS0 (ESL Algorithm 3.2 + 3.2b) on unit-norm X: knot betas, each direction a sign-constrained LS fit (scipy nnls).
- `arc_length(betas)` — Cumulative L1 arc length at each row of a piecewise-linear coefficient path (ESL footnote 2, p. 74).
- `densify(betas, per_segment=60)` — Points along a piecewise-linear path, per_segment per segment: (arc lengths, coefficient rows).
- `at_fraction(betas, fracs)` — Coefficients at the given fractions of a path's total L1 arc length, linear between rows: array (len(fracs), p).

## `contours`

Contour lines without matplotlib: zero-contour segments of a gridded field (marching squares) and their chaining into polylines.

- `marching_segments(F, g)` — Zero-contour segments of F (F[i, j] at x = g[j], y = g[i]) as ((x0, y0), (x1, y1)) pairs, linearly interpolated.
- `marching_segments_xy(F, gx, gy)` — Zero-contour segments of F on a rectangular grid (F[i, j] at x = gx[j], y = gy[i]), as marching_segments returns them.
- `chain(segs, digits=6)` — Join segments sharing endpoints (rounded to `digits`) into polylines, open curves first, so a dash pattern runs along each curve.

## `crossref`

Crossref metadata for DOI links, to check a reference whose doi.org page refuses scripts (403).

- `API` — constant
- `UA` — constant
- `doi_of(url: str) -> str | None` — The DOI in a https://doi.org/… link, or None for any other URL.
- `doi_metadata(doi: str, *, timeout: float=20.0) -> dict` — {doi, title, authors (family names), year, container} for a DOI from Crossref; raises on a failed lookup.

## `demographics_data`

ESL's demographic (marketing) data of Table 14.1 as complete cases, rule support/confidence/lift, and permuted (product-of-marginals) reference samples, numpy only.

- `DATA` — constant
- `COLS` — constant
- `CATEGORICAL` — constant
- `load(path=DATA)` — Rows of marketing.data with no NA as an int array (rows, 14), columns in COLS order (6876 rows, as ESL 14.2.3 uses).
- `col(X, name)` — The column of X named `name` (one of COLS).
- `rule_stats(A, B)` — (support T(A=>B), confidence (ESL 14.8), lift) of the rule A => B from boolean masks over the same rows.
- `permuted_reference(X, seed=0)` — A sample from the product of the column marginals (ESL 14.15): each column of X permuted independently.

## `derived_directions`

Derived-input regressions of ESL Section 3.5, numpy only: principal components regression, partial least squares (Algorithm 3.3) and conjugate gradients.

- `pcr(X, y, M)` — Principal components regression (ESL 3.61–3.62) on centred X: (beta of the first M components, all thetas, U, d, V).
- `pls(X, y, M, tol=1e-20)` — Partial least squares (ESL Algorithm 3.3) on standardized X: (beta_pls(m) for m = 1..M, the z_m, the phi_m); stops early when z_m vanishes.
- `cg(A, b, steps, tol=1e-24)` — Conjugate-gradient iterates for A beta = b (A symmetric positive definite) from beta = 0: a list of `steps` vectors.

## `equation_check`

Compare a reading's numbered display equations with a reference text's (the study notes), ignoring spacing and sizing.

- `normalize(tex: str) -> str` — LaTeX without spacing commands, \big/\Big sizing, whitespace, commas and full stops, for comparing formulas.
- `equation_diffs(markdown: str, reference_markdown: str) -> list[str]` — Numbers of the display equations in `markdown` whose LaTeX differs from (or is missing in) `reference_markdown`, normalized.

## `eslr_mixture`

ESL's simulated two-class mixture (Section 2.3.3, the data of Figures 2.1-2.5 and 12.2) and its Bayes rule, numpy only.

- `SD` — constant
- `draw(rng, means, n, sd=SD)` — n points: each picks one of `means` (rows) with equal probability, then adds N(0, sd^2 I) noise.
- `simulate(seed=22, n_per_class=100, n_test=10000, sd=SD)` — Means (10 per class from N((1,0), I) for BLUE, N((0,1), I) for ORANGE), then train and test draws: (blue_means, orange_means, x, y, tx, ty), y 0 = BLUE, 1 = ORANGE.
- `class_density(q, means, sd=SD)` — A class's density at the rows of q, up to a constant shared by both classes: the mean of exp(-|q - m|^2 / 2 sd^2) over its means.
- `bayes_field(q, blue_means, orange_means, sd=SD)` — Pr(ORANGE | x) - 1/2 at the rows of q under the true mixture with equal priors: positive where the Bayes rule says ORANGE.

## `figure_panel`

Draw simple line and scatter charts with PIL (no matplotlib): axes, ticks, lines, fills, points and legends, supersampled.

- `FONT` — constant
- `INK` — constant
- `S` — constant
- `font(size, *, scale=S, path=FONT)` — A TrueType font at `size` points on a canvas drawn at `scale`.
- `canvas(w, h, *, scale=S, background='white')` — A blank RGB image of w x h final pixels, drawn at `scale` times that size.
- `save(img, path, *, scale=S)` — Shrink a supersampled canvas to its final size and write it as a PNG; returns (width, height).
- `class Panel` — One set of axes on a canvas (box in final pixels): data to pixels, lines, fills, points, then frame() for ticks and labels.
- `dashed(panel, xs, ys, color, width=1.6, on=7, off=5)` — A dashed polyline on `panel`, clipped to its y range; dash and gap lengths `on`, `off` in final pixels.
- `errbars(panel, xs, mid, se, color, cap=3.0, width=1.2)` — Vertical bars from mid - se to mid + se at each x on `panel`, with caps of half-width `cap` final pixels.
- `boxplot(panel, x, values, color, half=0.28)` — A Tukey boxplot of `values` at x on `panel` (quartile box, median, whiskers to 1.5 IQR, outliers), clipped to its y range.

## `forest_kernel`

A fitted random forest as a weighted nearest-neighbour rule (ESL 15.4.3): the weight each training point gets at a target point.

- `forest_weights(forest, X, x0)` — Weights w_i(x0) of the training rows X under a fitted sklearn forest (bootstrap=True): per tree, in-bag count in x0's leaf over the leaf's in-bag total, averaged over trees; they sum to 1 and sum(w * y) is the forest's prediction.

## `gaussian_discriminants`

Gaussian discriminant analysis of ESL Section 4.3, numpy only: class estimates, QDA scores (4.12), RDA covariances (4.13)-(4.14), decision-boundary contours.

- `gauss_fit(X, g, K)` — Estimates of Section 4.3 from rows X, labels g in 0..K-1: (priors N_k/N, means (K, p), class covariances over N_k - 1, pooled covariance over N - K).
- `qda_scores(X, pis, mus, Sigmas)` — Quadratic discriminant functions (4.12) of the rows of X, one column per class, evaluated through each covariance's eigendecomposition (Section 4.3.2).
- `rda_sigmas(Sk, Sp, alpha, gamma=1.0)` — RDA covariances Sigma_k(alpha) (4.13), the pooled Sigma first shrunk by (4.14) to gamma Sigma + (1 - gamma) sigma^2 I with sigma^2 = trace(Sigma)/p.
- `region_boundaries(score, xlim, ylim, n=301)` — Polylines where the argmax of score(points (M, 2)) -> (M, K) changes over a grid on xlim x ylim, and (gx, gy, label grid) for painting regions.

## `kernel_fits`

Penalised two-class fits in a kernel or feature basis (ESL 12.25, 12.29): the hinge-loss SVM as a QP and kernel logistic regression by Newton's method.

- `hinge_qp(B, P, y, lam)` — min over (beta0, c) of sum [1 - y_i (beta0 + B_i c)]_+ + lam/2 c^T P c (B = H, P = I for 12.25; B = P = K for 12.29), labels y in {-1, 1}, by SLSQP with slacks: (beta0, c, criterion value).
- `kernel_logistic(K, y, lam, iters=100, tol=1e-12)` — Kernel logistic regression: min over (beta0, alpha) of sum log(1 + exp(-y_i f_i)) + lam/2 alpha^T K alpha, f = beta0 + K alpha, labels y in {-1, 1}, by Newton's method in K's eigen-coordinates: (beta0, alpha, f at the training points).

## `lasso_path`

Exact lasso coefficient paths by least angle regression with the lasso modification (ESL Algorithm 3.2 + 3.2a), numpy only.

- `lasso_path(X, y, tol=1e-12)` — Every knot of the lasso path for (3.52) with centred y and standardized X: a list of (lambda, beta), lambda decreasing to 0.
- `at_s(knots, s)` — The lasso coefficients at shrinkage factor s = t / t0 (t0 the L1 norm at the path's end), linear between knots.

## `local_poly`

Local polynomial regression weights (ESL 6.1): the tri-cube kernel and the equivalent kernel l(x0) of a degree-d local fit.

- `tricube(t)` — The tri-cube function D(t) = (1 - |t|^3)^3 for |t| <= 1, else 0 (ESL 6.6); works on arrays.
- `local_poly_weights(x0, x, lam, d, kernel=tricube)` — Equivalent-kernel weights l(x0) of a degree-d local polynomial fit (ESL 6.8–6.9, 6.11): f-hat(x0) = l @ y.

## `logistic_glm`

Unpenalized logistic regression by IRLS (ESL 4.26-4.28): coefficients, standard errors, Z scores and deviance, numpy only.

- `irls(A, y, offset=0.0, tol=1e-12, max_iter=100)` — Maximum-likelihood coefficients of logit p = offset + A beta by IRLS from beta = 0; returns (beta, fitted p).
- `deviance(y, p)` — Residual deviance of fitted probabilities p for 0/1 outcomes y: -2 times the log-likelihood.
- `fit(A, y, offset=0.0, tol=1e-12, max_iter=100)` — IRLS fit of design A (include a column of ones for an intercept): {beta, se, z, p, cov, dev}, se from (A^T W A)^-1.

## `mars`

A small MARS (ESL Section 9.4), numpy only: forward pass over reflected pairs, backward deletion, GCV.

- `hinge(x, t, s)` — (x - t)+ for s = +1, (t - x)+ for s = -1.
- `term_column(X, factors)` — The basis function of a term: the product of its hinge factors [(j, t, s), ...] (1 for none).
- `candidate_knots(X, max_knots=None)` — Per input, the knots to try: every distinct observed value, or at most `max_knots` quantiles of them.
- `forward(X, y, *, max_terms=21, max_degree=2, knots=None, tol=1e-09)` — The forward pass: from the constant, add the product of a model term with the reflected pair that
- `basis(X, terms)` — The N x M matrix of the terms' basis functions.
- `gcv(rss, N, r, c=3.0)` — GCV (9.20) for a model of r basis functions, M = r + c K with K = (r - 1)/2 knots (one per pair).
- `backward(B, y, c=3.0)` — Backward deletion: drop, one at a time, the term (never the constant) whose removal least increases
- `fit(X, y, *, max_terms=21, max_degree=2, knots=None, c=3.0)` — Forward pass, backward pass, and the GCV choice: {terms, path, best} (best indexes path).
- `predict(X, terms, step)` — Predictions of a backward-path model `step` = {keep, beta}.
- `pair_rss_sweep(x, y)` — RSS of y on the reflected pair at every knot t (every distinct x but the largest), knots moved from
- `pair_rss_brute(x, y, knots)` — The same RSS by a fresh least-squares fit of y on [1, (x - t)+, (t - x)+] at every knot.

## `matrix_factor`

Non-negative matrix factorization by Lee and Seung's updates (ESL 14.72-14.74) and archetypal analysis (ESL 14.75-14.77), numpy and scipy.

- `EPS` — constant
- `poisson_loglik(X, W, H)` — L(W, H) of ESL (14.73): the Poisson log-likelihood of X with means WH, without the log x! term.
- `nmf(X, r, iters=500, seed=0)` — Rank-r NMF of non-negative X by the multiplicative updates (14.74) from a random positive start: (W, H, L after each step).
- `simplex_ls(A, b, M=200.0)` — argmin ||A w - b|| over w >= 0 with sum(w) = 1: NNLS with the sum constraint as an extra row weighted by M.
- `archetypes(X, r, iters=60, starts=6, seed=0)` — Archetypal analysis of the rows of X with r archetypes by Cutler and Breiman's alternating fit, best of `starts`: (H, W, B, J).

## `mds`

Multidimensional scaling, numpy only (ESL 14.8): classical, least squares, Sammon and nonmetric maps from a dissimilarity matrix.

- `dists(Z)` — All pairwise Euclidean distances between the rows of Z, as an N x N array.
- `double_centre(D)` — Centred inner products from distances (ESL 18.31): (I - M)(-D^2 / 2)(I - M), M = 11^T / N.
- `classical(S, k=2)` — Classical scaling (ESL 14.100, Ex. 14.11): (Z = E_k D_k from the top-k eigenpairs of S, all eigenvalues largest first).
- `stress(Z, D)` — Least squares stress S_M (ESL 14.98): squared misfit of map distances to D over ordered pairs i != i'.
- `stress_grad(Z, D)` — Gradient of S_M (14.98) in each z_i: -4 sum_i' (d - r)(z_i - z_i') / r, each unordered pair counted twice.
- `least_squares(Z0, D, lr=0.01, steps=400)` — Gradient descent on S_M (14.98) from Z0: (final map, stress before and after every step).
- `sammon_stress(Z, D)` — Sammon stress S_Sm (ESL 14.99): each pair's squared error divided by its dissimilarity (D > 0 off the diagonal).
- `sammon_grad(Z, D)` — Gradient of S_Sm (14.99) in each z_i.
- `sammon(Z0, D, lr=0.005, steps=4000)` — Gradient descent on S_Sm (14.99) from Z0: the final map.
- `isotonic(y)` — Pool-adjacent-violators: the non-decreasing sequence closest to y in least squares.
- `theta_fit(Z, D)` — Disparities theta(d) for nonmetric scaling (ESL 14.101): isotonic regression of map distances on the order of D (ties by map distance), N x N.
- `nonmetric_stress(Z, T)` — Nonmetric stress S_NM (ESL 14.101) for fixed disparities T = theta(d).
- `nonmetric_grad(Z, T)` — Gradient of S_NM (14.101) in each z_i for fixed disparities T (quotient rule).
- `nonmetric(Z0, D, outer=60, inner=200, lr=2.0)` — Shephard-Kruskal nonmetric scaling (14.101): alternate gradient descent on Z and isotonic theta; (map, final disparities, stress per round).
- `procrustes(A, B)` — B shifted, rotated and reflected (no scaling) to best match A, for comparing maps that are unique only up to rigid motion.

## `mixture_em`

One-dimensional Gaussian mixtures fitted by EM (ESL 6.32, 6.33, Section 8.5), numpy only.

- `gaussian_density(x, mu, var)` — Gaussian density with mean mu and variance var at x (broadcasts over arrays).
- `responsibilities(x, mu, var, alpha)` — ESL (6.33): an (N, M) array of the probability that each x_i came from each component.
- `gaussian_mixture_em_1d(x, mu, var, alpha, iters=3000, fix_var=False)` — EM for a 1-D Gaussian mixture from starting (mu, var, alpha); fix_var keeps var fixed. Returns (mu, var, alpha, log-likelihood).

## `multiple_testing`

Multiple testing with permutation nulls (ESL 18.7), numpy only: two-sample t, pooled p-values, BH, plug-in FDR, pi0, SAM cuts.

- `two_sample_t(X, g2)` — Two-sample t-statistics (ESL 18.38-18.39), pooled SE, of every row of X (genes x samples) for each 0/1 column of g2 (samples x B) marking group 2: (genes, B).
- `random_labellings(rng, n, n2, K)` — K random relabellings of n samples with n2 in group 2, as an (n, K) 0/1 matrix for two_sample_t.
- `pooled_pvalues(t, tk)` — Pooled permutation p-values (ESL 18.41): for each t_j, the share of all permuted |t| (array tk, any shape) strictly above |t_j|.
- `bh_count(p, alpha)` — Benjamini-Hochberg L (ESL 18.44): the largest j with p_(j) < alpha j / M, 0 if none; reject the L smallest p-values.
- `plugin_fdr(t, tk, C)` — Plug-in FDR (ESL Algorithm 18.3) at cut C on |t|, counting values at or above C: (R_obs, E(V) hat, FDR hat or nan if R_obs = 0); tk is (M, K).
- `pi0_hat(t, tk)` — Storey's share of true nulls (ESL Ex. 18.19c): #{t_j in (q.25, q.75) of the permuted t} / (0.5 M), capped at 1.
- `expected_order_stats(tk)` — SAM's expected order statistics: each permutation column of tk (M, K) sorted, then averaged across permutations.
- `sam_cuts(ts, tt, delta)` — SAM cut-points (ESL 18.7.2) for sorted observed ts against expected tt and band delta: (C_low, C_hi, last called index below, first called index above); None/inf when a side calls nothing.

## `natural_splines`

Natural cubic spline bases (ESL 5.4-5.5) evaluated at given points, plain and orthonormalised, with their smoothing-spline penalty matrices, numpy only.

- `ns_basis(x, knots)` — Natural cubic spline basis at points x for the given sorted knots (boundary knots first and last): (len(x), len(knots)).
- `orthonormal_ns_basis(x, knots)` — An orthonormal basis (Q of a QR decomposition) of the same column space as ns_basis(x, knots).
- `ns_basis_d2(x, knots)` — Second derivatives of the ns_basis(x, knots) columns at points x (piecewise linear): (len(x), len(knots)).
- `ns_penalty(knots)` — The smoothing-spline penalty matrix Omega_jk = int N_j''(t) N_k''(t) dt of ns_basis (ESL 5.11), exact.
- `reinsch_K(x)` — The Reinsch penalty matrix K = Q R^-1 Q^T (ESL 5.17-5.18) for distinct sorted x: f^T K f = int g''^2 for the natural interpolant g.

## `nn_reduction`

Nearest-neighbour training-set reduction (ESL 13.5), numpy only: the 1-NN rule, Devijver-Kittler multi-edit and Hart's condensing.

- `nn1(train_x, train_y, query)` — Labels of the `query` rows under the 1-nearest-neighbour rule (Euclidean) on (train_x, train_y).
- `multi_edit(x, y, rng, s=3, quiet=10)` — Indices kept by multi-edit: random split into s parts, part i classified by 1-NN on part (i+1) mod s, misclassified points deleted, until `quiet` rounds in a row delete nothing.
- `condense(x, y, idx, rng)` — Indices of Hart's condensed set from the points `idx`: start from one random point, add each point the current set misclassifies under 1-NN, repeat passes until one adds nothing.

## `nsc`

Nearest shrunken centroids (ESL 18.2, (18.2)-(18.7)), numpy only: soft thresholding, the fit, and prediction at a threshold.

- `soft(d, delta)` — Soft thresholding (18.5): sign(d)(|d| - delta)_+, elementwise.
- `nsc_fit(X, y, K)` — Fit on rows X with labels y in 0..K-1: {xbar, s, s0, m, d, prior}, d the K x p gaps d_kj of (18.4), s0 the median s_j.
- `nsc_predict(fit, X, delta)` — Classes (18.3), scores (18.2) with shrunken centroids (18.7) and s_j + s_0, and the number of features kept, at threshold delta.

## `panel_marks`

Marks for a figure_panel.Panel: lines clipped to the axes box, dashed segments, Gaussian contour ellipses, shape markers.

- `INK` — constant
- `clip_line(p, v, xlim, ylim)` — The part of the line p + t v inside the box xlim x ylim, as two points (arrays), or None if it misses.
- `segment(panel, a, b, color, width=2.0, dash=None)` — A straight segment between data points a and b on `panel`, dashed when dash = (on, off) in final pixels.
- `gaussian_contour(mu, sigma, c2, n=200)` — n points (rows) on the ellipse (x - mu)^T sigma^-1 (x - mu) = c2; c2 = 5.991 encloses 95% of a 2-D Gaussian.
- `marker(panel, x, y, shape, color, r=5.0, edge=INK)` — A filled class marker at data point (x, y): shape 'circle', 'square' or 'triangle', radius r in final pixels.
- `plus(panel, x, y, color=INK, r=7.0, width=2.5)` — A '+' (a centroid) at data point (x, y), arm length r and stroke width in final pixels.
- `open_marker(panel, x, y, shape, color, r=4.0, width=1.3)` — An unfilled class marker at data point (x, y): shape 'circle', 'square' or 'triangle', radius r and stroke width in final pixels.
- `arrow(panel, a, b, color, width=2.0, head=9.0)` — A straight arrow from data point a to data point b, head length `head` final pixels (shortened for very short arrows).

## `pdf_slice`

Slice a reading's prose out of a PDF's own text by start and end phrases, so passages are never retyped.

- `LIGATURES` — constant
- `MATHISH` — constant
- `page_text(pdf: str | Path, page: int) -> str` — A PDF page's text (1-based PDF page), NFC-normalised with ligatures undone; line breaks kept.
- `hyphenated_words(texts) -> frozenset` — The lowercased hyphenated words ("least-squares") printed within a line anywhere in `texts`.
- `join_lines(text: str, keep_hyphen: frozenset=frozenset()) -> str` — Text as one line: end-of-line hyphens dropped unless the word is in `keep_hyphen`, dashes rejoined.
- `between(text: str, start: str, end: str) -> str` — `text` from the first `start` through the next `end`, both included; ValueError naming the one missing.
- `substitute(text: str, subs) -> str` — Apply exact (old, new) replacements in order; ValueError if an `old` is absent, so stale fixes surface.
- `mathish_fragments(text: str, *, pad: int=10, merge: int=12, ignore=('a', 'A', 'I')) -> list[str]` — Windows around what still looks like extracted maths outside $…$ and #ref: links (close hits merged).

## `pil_plot`

A small supersampled line chart drawn with PIL, for reading figures where matplotlib is not installed.

- `FONT` — constant
- `INK` — constant
- `class Plot` — A line chart drawn with PIL: data-space to pixels, a framed axis box with ticks, lines, points, legend.

## `prim`

PRIM bump hunting (ESL Algorithm 9.3), numpy only: top-down peeling and bottom-up pasting of one box, and whole-point peel counts.

- `inside(X, box)` — Boolean mask of the rows of X strictly inside box.
- `peel_candidates(X, y, box, alpha)` — Every one-face peel of `box`: list of (mean, width, j, side, new box), side 0 = low face, 1 = high face.
- `peel(X, y, alpha=0.1, min_size=10, box=None)` — Top-down peeling: the sequence of boxes, starting with `box` (default: all the data), each peel
- `paste_candidates(X, y, box, alpha, bounds)` — Every one-face paste of `box`: the face moved out past the ceil(alpha * n) nearest outside points
- `paste(X, y, box, alpha=0.1, bounds=None)` — Bottom-up pasting: grow `box` one face at a time, taking the paste with the highest box mean,
- `peel_counts(N, alpha, stop=1)` — Points left after each peel of ceil(alpha * n) whole points, from N down to `stop` or fewer.

## `principal_curves`

Principal curves and surfaces by the alternating algorithm (ESL 14.62), numpy only.

- `local_linear(lam, y, at, h)` — Local linear Gaussian-kernel smooth of y (n,) or (n, p) on lam (n, d), evaluated at `at` (m, d).
- `project_polyline(X, P)` — Closest point on the polyline P (m, p) for each row of X: (arc length along P, the point, squared distance).
- `first_pc(X)` — Mean and leading right singular vector of X.
- `principal_curve(X, h, iters=10, grid=200)` — Fit a principal curve from the first principal component line.
- `principal_surface(X, h, iters=8, grid=41)` — Fit a two-dimensional principal surface from the first two principal components.
- `linear_smoother_directions(X, v0, iters)` — (14.62) with a least-squares straight-line smoother on centred X: the direction after each pass.
- `batch_som_1d(X, K, passes=30, r0=None, seed=0)` — A one-dimensional batch SOM (ESL 14.48): K prototypes on a chain, Gaussian neighbourhood shrinking to 0.5.

## `reading`

Readings: the source behind a lesson, for the games in ~/games.

- `KIND` — constant
- `FILE` — constant
- `FIGURES` — constant
- `SCHEMA` — constant
- `EXPORT_SUBDIR` — constant
- `NAME` — constant
- `REF_ID` — constant
- `STORY` — constant
- `IMAGE` — constant
- `LINK` — constant
- `FIGURE_TYPES` — constant
- `FIGURE_MAX` — constant
- `WORDS_SOFT` — constant
- `UA` — constant
- `write_reading(package: str | Path, *, name: str, story: str, work: dict, passages: list[dict], references: dict[str, dict], tags: Iterable[str]=()) -> dict` — Write reading.json and manifest.json; returns the manifest.
- `read(package: str | Path) -> dict` — The package's reading.json.
- `passage_ids(package: str | Path) -> list[str]` — The ids of a reading's passages, in order: what a story's beats may name.
- `math_problems(md: str) -> list[str]` — Unbalanced maths in a passage: a $$ or a single $ left open.
- `validate_reading(package: str | Path) -> dict` — {ok, errors, warnings}: a reading the game can show, every citation resolvable.
- `check_links(package: str | Path, *, timeout: float=15.0) -> list[dict]` — Fetch every reference URL (and the work's): [{id, url, status, ok, final}].
- `pdf_page_png(pdf: str | Path, page: int, out: str | Path, *, dpi: int=150) -> Path` — One PDF page (1-based, the PDF's own numbering) rendered to a PNG, to look at or crop.
- `pdf_text(pdf: str | Path, page: int) -> str` — A PDF page's text, to find the page a passage or figure is on.
- `pdf_pages_text(pdf: str | Path, first: int, last: int, *, drop_lines: Iterable[str]=()) -> str` — PDF pages first..last (the PDF's numbering) as one NFC-normalised line of prose, page-number lines and `drop_lines` (running heads) removed.
- `span(text: str, start: str, end: str) -> str` — The verbatim text from marker `start` through the first `end` after it; ValueError if `start` is not found exactly once or `end` is missing.
- `numbered_equations(md: str) -> dict[str, str]` — Display equations `$$… (9.4)$$` in a markdown text, by number ('9.4' -> the LaTeX inside the $$); the first wins.
- `pdf_figure(pdf: str | Path, page: int, out: str | Path, *, crop: tuple[float, float, float, float] | None=None, dpi: int=150, max_width: int=1400, trim: bool=True) -> Path` — A figure cut from a PDF page into a PNG at `out` (figures/<name>.png).
- `outline(package: str | Path) -> str` — The reading as one markdown document: each passage, then its references. Write it to previews/outline.md.
- `export_reading(package: str | Path, game_root: str | Path) -> dict` — Install a reading into `<game_root>/public/assets/reading/<name>/` and rebuild the index.

## `reading_overlap`

Prose of a retold reading passage that does occur in the source text: what an own-words check must look at.

- `words(text: str) -> list[str]` — Lowercased a–z words of `text`, with end-of-line hyphenation joined.
- `prose(markdown: str) -> str` — A passage's markdown with display and inline maths and images taken out.
- `shared_runs(markdown: str, source: str, *, n: int=7) -> list[str]` — Every run of `n` consecutive prose words of `markdown` also found in `source` (also matched with spaces removed, for PDF text that splits words).

## `reading_verbatim`

Prose of a reading passage that does not occur in the source text: what a verbatim check must look at.

- `unquoted_runs(markdown: str, source: str, *, min_chars: int=25, phrase_chars: int=12) -> list[str]` — Prose runs of a passage (between formulas, figures, citations and […] cuts) not found in `source`; a run that crosses a page break is checked phrase by phrase.

## `reduced_rank_lda`

Fisher's reduced-rank LDA (ESL Section 4.3.3), numpy only: canonical variates from the sphered centroids, and nearest-centroid scores in the first L of them.

- `inv_sqrt(S)` — The symmetric inverse square root S^(-1/2) of a symmetric positive-definite matrix, from its eigen-decomposition.
- `canonical(M, W, pis)` — ESL 4.3.3's steps for centroids M (K, p), within-class covariance W and priors pis: (V with columns v_l = W^-1/2 v*_l, eigenvalues D_B of the pis-weighted covariance B* of M* = M W^-1/2, V*), largest first.
- `rr_scores(X, V, M, pis, L)` — Reduced-rank LDA scores of the rows of X, one column per class: -1/2 squared distance to each centroid in the first L discriminant coordinates (columns of V), plus log pi_k; argmax classifies.

## `region_paint`

Two-class decision regions on a chart.Panel: shade where a classifier's probability is above or below a level, and its boundary polylines.

- `paint_regions(panel, proba, xlim, ylim, colors, *, n=181, alpha=0.22, level=0.5)` — Shade an n x n grid over xlim x ylim on `panel` with colors[0] where proba(points (M, 2)) <= level, colors[1] above; returns the level boundary as polylines.

## `regression_tree`

A least-squares regression tree (CART growing, ESL 9.2.2), numpy only, whose leaves keep their training responses.

- `tree_fit(X, y, depth=3, min_leaf=5)` — Grow a tree on rows X, responses y by exhaustive squared-error splits: nested dicts {j, s, l, r}, leaves {leaf: y values}.
- `tree_leaf(tree, x)` — The training responses in the terminal node row x falls into (x[j] <= s goes left): take .mean() to predict, sample to draw.

## `ridge_df`

Ridge effective degrees of freedom (ESL 3.50) inverted to a penalty, and the one-standard-error rule (ESL 7.10), numpy only.

- `lambda_for_df(d, df, iters=300)` — The ridge penalty lambda with sum d_j^2 / (d_j^2 + lambda) = df (ESL 3.50), by bisection on the singular values d; 0 for df >= len(d), inf for df <= 0.
- `one_se_index(cv, se)` — Index of the first (least complex) grid point whose CV error is within one standard error of the minimum (ESL 7.10); grids must run simple to complex.

## `simplex_ls`

Least squares with weights on the simplex (nonnegative, summing to 1): the quadratic program of constrained stacking (ESL Section 8.8).

- `simplex_weights(F, y)` — Weights w >= 0 with sum 1 minimizing ||y - F w||^2, for F (N x M) and y (N,); scipy SLSQP from equal weights.

## `single_layer_net`

Single-hidden-layer neural networks of ESL (11.5) with weight decay (11.16) or weight elimination (11.17): back-propagation gradients, BFGS fits and epoch-by-epoch batch gradient descent, numpy + scipy.

- `sigmoid(v)` — 1 / (1 + e^-v), elementwise, with v clipped to [-60, 60] so it never overflows.
- `unpack(theta, p, M, K)` — The weight vector theta as (alpha (p+1, M), beta (M+1, K)), row 0 of each the intercepts.
- `start(rng, p, M, K, scale=0.7)` — Random starting weights, uniform on [-scale, scale] (ESL 11.5.3), as one theta vector.
- `forward(theta, X, M, K)` — Hidden activations Z (N, M) and output scores T (N, K) of the rows of X, as in (11.5).
- `predict(theta, X, M, K, out='linear')` — The network's outputs at the rows of X: scores T for out='linear', softmax probabilities (11.6) for out='softmax'.
- `loss_grad(theta, X, Y, M, K, lam, out='linear', penalty='decay')` — R(theta) + lam J(theta) and its gradient by back-propagation: R squared error (out='linear') or cross-entropy with one-hot Y (out='softmax'); J (11.16) for penalty='decay', (11.17) for 'elimination'.
- `fit(X, Y, M, lam, rng, out='linear', maxiter=3000, penalty='decay', theta0=None)` — Minimise R + lam J with L-BFGS from theta0 or random starting weights; Y is (N, K); returns theta.
- `gd_path(X, Y, M, lam, rng, epochs, gamma, out='linear', every=1, monitor=None, scale=0.7)` — Batch back-propagation (11.13), theta <- theta - gamma * grad, for `epochs` epochs: (monitored epochs, monitor(theta) at each, final theta), monitoring at epoch 0 and every `every`.

## `som`

Self-organizing maps as ESL Section 14.4 describes them, numpy only: online (14.46), weighted (14.47) and batch (14.48) SOMs, and K-means to compare.

- `grid_coords(q1, q2)` — Integer coordinates l_j in Q1 x Q2 (1-based) of a q1 x q2 grid, row by row: (K, 2).
- `pc_init(X, q1, q2, span=1.0)` — Prototypes sewn on the first two principal component plane in a regular q1 x q2 pattern,
- `closest(X, M)` — Index of the closest prototype (Euclidean distance in R^p) for every row of X.
- `recon_error(X, M)` — Reconstruction error: the sum over observations of ||x - m_j||^2 to the closest prototype.
- `rectangular(r)` — The simplest neighbourhood: weight 1 for grid distance at most r, else 0.
- `gaussian(sigma)` — A smooth neighbourhood function h: exp(-d^2 / (2 sigma^2)) of the grid distance d.
- `online_som(X, M0, L, passes, R, r_end=1.0, alpha0=1.0, h=None, seed=0, track=False)` — Online SOM: each x_i in turn moves its closest prototype's grid neighbours by (14.46);
- `batch_som(X, M0, L, iters, weight, tol=0.0)` — Batch SOM (14.48): every m_j becomes the weighted mean of the points that mapped to its
- `lloyd(X, M0, iters=200)` — K-means (Lloyd's iterations) from the centres M0: assign to the closest, move to the means.
- `kmeans_best(X, K, starts=20, seed=0)` — The lowest-error K-means solution over `starts` random starts (K distinct observations).
- `half_sphere(sd=0.6, n=30, seed=0)` — Exercise 14.5's data: three classes of n points near the unit sphere, noise standard deviation sd.
- `helix(n=200, noise=0.1, seed=0)` — Exercise 14.13's data: X = (cos s, sin s, s) + 0.1 Z at n equally spaced s in [0, 2 pi]. Returns (s, X).

## `sparse_pca`

Sparse principal components by ESL (14.71), numpy only: the elastic-net V-step, the Procrustes Theta-step, and the alternation.

- `soft(z, g)` — Soft thresholding: sign(z) (|z| - g)_+.
- `elastic_net(S, theta, lam, lam1, v0=None, sweeps=500, tol=1e-12)` — argmin_v (theta - v)^T S (theta - v) + lam ||v||_2^2 + lam1 ||v||_1, by coordinate descent.
- `elastic_net_block(S, Theta, lam, lam1, V0=None, iters=20000, tol=1e-10)` — All K elastic nets of the V-step at once, by accelerated proximal gradient (FISTA); for large p.
- `theta_step(S, V)` — argmax_Theta tr(Theta^T M) s.t. Theta^T Theta = I_K with M = S V (14.116): Theta = U Q^T from M = U D Q^T.
- `criterion(S, Theta, V, lam, lam1)` — The value of (14.71), computed from S = X^T X.
- `spca(S, K, lam, lam1, Theta0=None, iters=200, tol=1e-10, trace=False)` — Alternate the V-step (K elastic nets) and the Theta-step (Procrustes by SVD) on (14.71).

## `spectral_graph`

Similarity graphs for spectral methods, numpy only: ESL's three-ring toy data, radial similarities, k-nearest-neighbour graphs and the graph Laplacian.

- `three_rings(seed=7, n=150, radii=(1.0, 2.8, 5.0), sd=0.25)` — ESL Figure 14.29's toy data: n points per ring, uniform angle, radius from `radii`, Gaussian noise sd; returns (X (N, 2), ring index per point).
- `sqdist(X)` — Squared Euclidean distances between the rows of X, as an N x N matrix.
- `similarity(X, c=2.0)` — Radial-kernel gram matrix s_ii' = exp(-d_ii'^2 / c) (ESL 14.67).
- `knn_pairs(X, k)` — Boolean N x N: True where i is among the k nearest neighbours of i' or vice versa (ESL's symmetric set N_K).
- `knn_weights(X, k=10, c=2.0)` — Adjacency matrix W: radial similarities kept on the k-nearest-neighbour pairs, zero elsewhere.
- `laplacian(W)` — Unnormalized graph Laplacian L = G - W, G the diagonal of degrees (ESL 14.63).

## `surface3d`

Perspective plots of a surface z = f(X1, X2) with pycairo (no matplotlib): a shaded mesh in a wire box, painted back to front.

- `TOP` — constant
- `UNDER` — constant
- `EDGE` — constant
- `HZ` — constant
- `projector(az_deg: float, el_deg: float)` — Orthographic view: (proj(x, y, z) -> (screen right, screen up, depth towards viewer), unit vector towards the viewer); az = 45 puts the corner of smallest x and y nearest.
- `surface(c, box, f, xr=(0.0, 1.0), yr=(0.0, 1.0), zr=None, n=40, az=45.0, el=28.0, xlabel='X1', ylabel='X2', zlabel='g(V)', font_size=22, top=TOP, under=UNDER)` — Draw z = f(X1, X2) (f takes meshgrid arrays) over xr x yr, heights zr (default the data's range), in the pixel box (left, top, right, bottom) of cairo context c; axis names ending in a digit get it as a subscript.

## `survival_curves`

Survival-data estimates for reading figures, numpy only: Kaplan-Meier with Greenwood standard errors and a one-covariate Cox fit.

- `km_with_se(time, event)` — Kaplan-Meier steps with Greenwood standard errors (event 1 = death, 0 = censored): arrays (times, S, se), starting at (0, 1, 0).
- `cox_1d(x, time, event, iters=50)` — Cox partial-likelihood fit of one covariate x by Newton's method (Breslow ties): (beta, likelihood-ratio statistic against beta = 0).

## `tangent_distance`

Tangent distance for small grey-scale images (ESL 13.3.3, Ex. 13.4), numpy only: kernel-smoothed derivatives, affine tangent images, two-sided distance.

- `grid(side=16)` — Paper coordinates (x1 right, x2 up) of a side x side image's pixel centres, row-major from the top, origin at the image centre.
- `smoother(image, lam=0.9)` — A Gaussian Nadaraya-Watson smoother of a square image's pixels (bandwidth lam pixels): F(z) -> (value, dF/dx1, dF/dx2) at points z (M, 2).
- `affine_image(image, theta=0.0, s1=1.0, s2=1.0, h=0.0, c=(0.0, 0.0), lam=0.9)` — The smoothed image F(c + x0 + A(x - x0)), A = Q(theta) [[s1, h], [0, s2]], sampled at the pixel centres (identity = smoothed image).
- `tangents(image, lam=0.9)` — The six tangent images of ESL Ex. 13.4 at the identity: {rotation, scale 1, scale 2, shear, translation 1, translation 2} -> pixel vectors.
- `tangent_distance(x, Tx, y, Ty)` — Two-sided tangent distance min over a, b of ||(x + Tx a) - (y + Ty b)|| (tangents as columns), by least squares: (distance, a, b).

## `thin_plate`

Thin-plate splines in the plane (ESL 5.37-5.39, Ex. 5.14), numpy only: the r^2 log r radial function, design and penalty on any knots.

- `eta(r)` — The thin-plate radial function r^2 log r (0 at r = 0), on arrays.
- `distances(A, B)` — Euclidean distances between the rows of A and the rows of B: (len(A), len(B)).
- `null_basis(T)` — An orthonormal basis Z (columns) of {a : T^T a = 0}, from a complete QR of T.
- `thin_plate_system(X, knots)` — (A, Omega, Z) for f = b0 + b^T x + sum_k alpha_k eta(|x - knot_k|) with alpha = Z c: design A = [1, X, E Z], penalty 8 pi Z^T E Z on c.

## `tree_boost`

Small gradient tree boosting (ESL Algorithms 10.3 and 10.4) with partial dependence by weighted tree traversal (Ex. 10.11) and by averaging (10.48), numpy only.

- `bin_edges(X, nbins=64)` — Per column, the candidate split values: up to nbins - 1 distinct quantiles; x <= edge goes left.
- `binned(X, edges)` — Bin index of every value: the number of edges strictly below it, so bin <= k iff x <= edges[k].
- `grow_tree(B, r, edges, J, leaf_value, min_leaf=10)` — A least-squares regression tree on residuals r grown best-first to J terminal nodes (as MART does).
- `tree_predict(tree, X)` — The tree's value at every row of X (raw values; x <= thr goes left).
- `huber_boost(X, y, *, J=6, nu=0.1, M=800, alpha=0.9, edges=None, X_test=None, y_test=None)` — Gradient boosting with Huber loss (Algorithm 10.3; delta the alpha-quantile of |residual| each step).
- `softmax(F)` — Row-wise class probabilities (10.21) from an (N, K) array of f_k.
- `multiclass_boost(X, g, K, *, J=6, nu=0.1, M=300, X_test=None, g_test=None)` — K-class gradient boosting with multinomial deviance (Algorithm 10.4); classes g in 0..K-1.
- `predict(model, X, M=None)` — f_M(x) = f0 + the sum of the first M trees (all by default) at every row of X.
- `tree_partial_dependence(tree, S, G)` — Ex. 10.11: one tree's partial dependence on the columns S at the rows of G (len(S) columns), by weighted traversal.
- `partial_dependence(model, S, G)` — A boosted model's partial dependence on S at the rows of G: f0 plus the sum of its trees' (Ex. 10.11).
- `average_dependence(fun, X, S, G)` — (10.48): at each row of G, the average of fun over the rows of X with the columns S set to that row.
- `importance(trees, p)` — (10.42)-(10.43): relative importance of each of p inputs over the trees, square-rooted, largest 100.

## `two_class_fits`

Two-class LDA and logistic regression fits (ESL Sections 4.3 and 4.4.1), numpy only, returning boundary coefficients.

- `lda_fit(X, g)` — Two-class LDA on rows X, labels g in {0, 1}: (alpha, alpha0, (mu0, mu1, Sigma)) of (4.33), class 1 vs 0, Sigma pooled over N - 2.
- `logistic_fit(X, g, iters=60, tol=1e-10)` — Two-class logistic regression by Newton-Raphson (IRLS) on rows X, labels g in {0, 1}: (beta, beta0); stops after `iters` steps on separable data.

## `verbatim`

Check a reading passage's prose against the source text it was copied from, letters only.

- `letters(text: str) -> str` — The text NFKC-normalised, lowercased, with everything but a–z removed (spacing, hyphenation, punctuation).
- `prose_sentences(markdown: str) -> list[str]` — A passage's prose sentences, with maths, figures, tables, footnote marks and bold subheadings taken out; citation links keep their text.
- `unmatched_prose(markdown: str, source_text: str, *, min_letters: int=12) -> list[str]` — The passage's prose sentences (of at least `min_letters` letters) not found letter-for-letter in `source_text`.

## `wavelets`

Orthonormal discrete wavelet transforms (ESL Section 5.9), numpy only: Haar and symmlet-8 pyramids, soft thresholding, SureShrink.

- `SQ2` — constant
- `HAAR` — constant
- `SYM8` — constant
- `highpass(h)` — The wavelet (high-pass) filter g(n) = (-1)^n h(L-1-n) of a scaling filter h.
- `analysis_step(a, h)` — One pyramid step, periodic: (smooth, detail), each half the length of a.
- `synthesis_step(s, d, h)` — The inverse of analysis_step: the length-2m signal whose step gives (s, d).
- `dwt(y, h, j0=0)` — Wavelet transform of y (length 2^J) down to V_j0: [V_j0 coefficients, W_j0, ..., W_(J-1)].
- `idwt(coeffs, h)` — Inverse of dwt: the signal from [V_j0, W_j0, ..., W_(J-1)].
- `flat(coeffs)` — The coefficient list as one vector y* (coarse first).
- `unflat(v, j0, J)` — A vector y* back into the list [V_j0, W_j0, ..., W_(J-1)].
- `basis_matrix(N, h, j0=0)` — The N x N matrix W whose columns are the basis vectors, so that dwt(y) = W^T y.
- `basis_function(N, h, level, k, j0=0, kind='psi')` — Basis vector of length N: psi_{level,k} (detail) or phi_{level,k} (smooth, level = j0).
- `haar_pyramid(y)` — Exercise 5.19: the Haar transform by pairwise sums and differences, counting operations.
- `haar_inverse(coeffs)` — The inverse Haar pyramid, counting operations: (signal, ops).
- `soft(x, lam)` — Soft thresholding (5.69): sign(x)(|x| - lam)_+.
- `mad_sigma(d)` — Noise standard deviation from the finest-level coefficients: median |d| / 0.6745.
- `sure_threshold(x)` — SureShrink threshold for coefficients x of unit noise (Donoho and Johnstone's hybrid rule).
- `sureshrink(y, h, j0)` — Wavelet smoothing as in Figure 5.17: (fit, coefficients, shrunk coefficients, sigma).
- `spline_cv(x, y, K, lams)` — Smoothing spline (5.17) with lambda chosen by leave-one-out CV (5.26): (fit, lambda, df).