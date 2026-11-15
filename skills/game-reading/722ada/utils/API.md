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

## `chart`

Figures without matplotlib: a small pycairo chart kit (canvas, axes panels, ticks, lines, bands, rugs, points, legends) writing PNGs.

- `INK` — constant
- `GREY` — constant
- `FONT` — constant
- `hexrgb(h)` — '#RRGGBB' to an (r, g, b) tuple of floats in [0, 1], the colour form every drawing call takes.
- `class Canvas` — A white canvas of w x h points, written at `scale` pixels per point.
- `class Panel` — Axes in a box (left, top, right, bottom) of the canvas, mapping data coordinates to points.

## `coef_paths`

Coefficient paths of linear-model fitting procedures (LAR, forward stepwise, forward stagewise, incremental stagewise) and their L1 arc length, numpy only; the lasso path is lasso_path.lasso_path.

- `standardize(X, y)` — Centre the columns of X and scale them to unit norm (ESL Algorithm 3.2, step 1), centre y; returns (X, y, column norms).
- `lar_path(X, y, tol=1e-09)` — Breakpoints of least angle regression (ESL Algorithm 3.2) on unit-norm X: (betas (steps+1, p), entered variable per breakpoint, None at the end).
- `forward_stepwise_path(X, y, steps=None)` — Forward stepwise selection (ESL Section 3.3.2): least-squares fits on a growing active set; returns (betas (steps+1, p), entry order).
- `forward_stagewise_path(X, y, max_steps=4000, tol=1e-06)` — Forward stagewise (ESL Section 3.3.3) on unit-norm X: each step adds <x_j, r> to the most correlated variable's coefficient; betas per step.
- `incremental_stagewise_path(X, y, eps=0.01, max_steps=60000, tol=None, keep_every=20)` — Incremental forward stagewise FS_eps (ESL Algorithm 3.4) on unit-norm X: steps of eps toward the top correlation's sign; every keep_every-th beta.
- `arc_length(betas)` — Cumulative L1 arc length at each row of a piecewise-linear coefficient path (ESL footnote 2, p. 74).
- `densify(betas, per_segment=60)` — Points along a piecewise-linear path, per_segment per segment: (arc lengths, coefficient rows).
- `at_fraction(betas, fracs)` — Coefficients at the given fractions of a path's total L1 arc length, linear between rows: array (len(fracs), p).

## `contours`

Contour lines without matplotlib: zero-contour segments of a gridded field (marching squares) and their chaining into polylines.

- `marching_segments(F, g)` — Zero-contour segments of F (F[i, j] at x = g[j], y = g[i]) as ((x0, y0), (x1, y1)) pairs, linearly interpolated.
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

## `verbatim`

Check a reading passage's prose against the source text it was copied from, letters only.

- `letters(text: str) -> str` — The text NFKC-normalised, lowercased, with everything but a–z removed (spacing, hyphenation, punctuation).
- `prose_sentences(markdown: str) -> list[str]` — A passage's prose sentences, with maths, figures, tables, footnote marks and bold subheadings taken out; citation links keep their text.
- `unmatched_prose(markdown: str, source_text: str, *, min_letters: int=12) -> list[str]` — The passage's prose sentences (of at least `min_letters` letters) not found letter-for-letter in `source_text`.