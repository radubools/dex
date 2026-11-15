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

## `chart_shapes`

Shapes for chart.Panel figures: filled polygons, edged dots, and a canvas capped at the reading figure width.

- `canvas(w, h, max_px=1400)` — A chart.Canvas of w x h points at up to 2 pixels per point, never wider than max_px pixels.
- `fill_poly(p, pts, color, alpha=0.35, edge=None, width=1.6)` — A polygon through data points `pts` on chart.Panel `p`, filled at `alpha` and outlined in `edge` if given.
- `dot(p, x, y, color, r=4.5, edge=INK)` — A filled dot of radius r points with an `edge` outline at data point (x, y), with no line joining it to earlier drawing.

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

## `equation_check`

Compare a reading's numbered display equations with a reference text's (the study notes), ignoring spacing and sizing.

- `normalize(tex: str) -> str` — LaTeX without spacing commands, \big/\Big sizing, whitespace, commas and full stops, for comparing formulas.
- `equation_diffs(markdown: str, reference_markdown: str) -> list[str]` — Numbers of the display equations in `markdown` whose LaTeX differs from (or is missing in) `reference_markdown`, normalized.

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

## `natural_splines`

Natural cubic spline bases (ESL 5.4-5.5) evaluated at given points, plain and orthonormalised, numpy only.

- `ns_basis(x, knots)` — Natural cubic spline basis at points x for the given sorted knots (boundary knots first and last): (len(x), len(knots)).
- `orthonormal_ns_basis(x, knots)` — An orthonormal basis (Q of a QR decomposition) of the same column space as ns_basis(x, knots).

## `panel_marks`

Marks for a figure_panel.Panel: lines clipped to the axes box, dashed segments, Gaussian contour ellipses, shape markers.

- `INK` — constant
- `clip_line(p, v, xlim, ylim)` — The part of the line p + t v inside the box xlim x ylim, as two points (arrays), or None if it misses.
- `segment(panel, a, b, color, width=2.0, dash=None)` — A straight segment between data points a and b on `panel`, dashed when dash = (on, off) in final pixels.
- `gaussian_contour(mu, sigma, c2, n=200)` — n points (rows) on the ellipse (x - mu)^T sigma^-1 (x - mu) = c2; c2 = 5.991 encloses 95% of a 2-D Gaussian.
- `marker(panel, x, y, shape, color, r=5.0, edge=INK)` — A filled class marker at data point (x, y): shape 'circle', 'square' or 'triangle', radius r in final pixels.
- `plus(panel, x, y, color=INK, r=7.0, width=2.5)` — A '+' (a centroid) at data point (x, y), arm length r and stroke width in final pixels.

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

## `regression_tree`

A least-squares regression tree (CART growing, ESL 9.2.2), numpy only, whose leaves keep their training responses.

- `tree_fit(X, y, depth=3, min_leaf=5)` — Grow a tree on rows X, responses y by exhaustive squared-error splits: nested dicts {j, s, l, r}, leaves {leaf: y values}.
- `tree_leaf(tree, x)` — The training responses in the terminal node row x falls into (x[j] <= s goes left): take .mean() to predict, sample to draw.

## `thin_plate`

Thin-plate splines in the plane (ESL 5.37-5.39, Ex. 5.14), numpy only: the r^2 log r radial function, design and penalty on any knots.

- `eta(r)` — The thin-plate radial function r^2 log r (0 at r = 0), on arrays.
- `distances(A, B)` — Euclidean distances between the rows of A and the rows of B: (len(A), len(B)).
- `null_basis(T)` — An orthonormal basis Z (columns) of {a : T^T a = 0}, from a complete QR of T.
- `thin_plate_system(X, knots)` — (A, Omega, Z) for f = b0 + b^T x + sum_k alpha_k eta(|x - knot_k|) with alpha = Z c: design A = [1, X, E Z], penalty 8 pi Z^T E Z on c.

## `two_class_fits`

Two-class LDA and logistic regression fits (ESL Sections 4.3 and 4.4.1), numpy only, returning boundary coefficients.

- `lda_fit(X, g)` — Two-class LDA on rows X, labels g in {0, 1}: (alpha, alpha0, (mu0, mu1, Sigma)) of (4.33), class 1 vs 0, Sigma pooled over N - 2.
- `logistic_fit(X, g, iters=60, tol=1e-10)` — Two-class logistic regression by Newton-Raphson (IRLS) on rows X, labels g in {0, 1}: (beta, beta0); stops after `iters` steps on separable data.

## `verbatim`

Check a reading passage's prose against the source text it was copied from, letters only.

- `letters(text: str) -> str` — The text NFKC-normalised, lowercased, with everything but a–z removed (spacing, hyphenation, punctuation).
- `prose_sentences(markdown: str) -> list[str]` — A passage's prose sentences, with maths, figures, tables, footnote marks and bold subheadings taken out; citation links keep their text.
- `unmatched_prose(markdown: str, source_text: str, *, min_letters: int=12) -> list[str]` — The passage's prose sentences (of at least `min_letters` letters) not found letter-for-letter in `source_text`.