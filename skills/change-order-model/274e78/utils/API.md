## `baseline`

Does the report text add anything beyond who and where? Metadata baselines against the criteria.

- `CRIT` — constant
- `LENDERS` — constant
- `normalise_lender(raw: str | None) -> str` — 
- `client_field(path) -> str | None` — The `Client:` value on a monthly workbook's `Main` sheet.
- `state_of(folder: str) -> str` — 
- `category_of(building_type: str) -> str` — 
- `metadata(conn, rows: list[dict], *, min_count: int=3) -> tuple[np.ndarray, list[str], np.ndarray, list[str]]` — `(M1, M1 names, exposure, exposure names)` for the labelled rows, in order.
- `run(conn, *, target: str='had_change_orders', material: float=0.05, repeats: int=10, shuffles: int=30, seed: int=0, log=print) -> dict` — 

## `co_labels`

Change-order labels per project, from the contractor's draw summary in the monthly reports.

- `SCHEMA` — constant
- `WORKBOOK` — constant
- `PR` — constant
- `FIELDS` — constant
- `DDL` — constant
- `read_g702(path: Path) -> dict` — The G702 fields of one monthly workbook's `Contractor` sheet; `error` if unreadable.
- `monthly_workbooks(project: edil_sources.Project) -> list[Path]` — 
- `label(project: edil_sources.Project) -> dict | None` — The label row for one project, or None if it has no readable monthly workbook.
- `ensure_schema(conn) -> None` — 
- `run(conn, *, log=print) -> dict` — Label every project that has an ingested document. Returns counts.
- `labels(conn, *, min_billed: float | None=None, min_reports: int=1, drop_flagged: bool=True) -> list[dict]` — Stored labels, optionally only projects far enough along to count a negative.

## `lasso`

Fit the kept criteria to the change-order label with an L1 (lasso) model, and say how far to trust it.

- `CRIT` — constant
- `DDL` — constant
- `features(conn, folders: list[str], *, threshold: float=0.5, modalities: tuple[str, ...]=('text', 'image'), status: str | None='kept', ids: list[int] | None=None) -> tuple[np.ndarray, list[dict]]` — `(X, columns)`: one row per project folder, one column per criterion of `status`.
- `CLASSIFY` — constant
- `target_vector(rows: list[dict], target: str, material: float=0.05) -> tuple[list[dict], np.ndarray]` — `(rows used, y)` for a target. `material_change_orders`: net COs ≥ `material` of the initial contract.
- `fit(X: np.ndarray, y: np.ndarray, *, target: str='had_change_orders', outer_folds: int=5, repeats: int=10, inner_folds: int=4, bootstraps: int=200, seed: int=0) -> dict` — Outer-CV score, the final fit's coefficients, and bootstrap selection frequencies.
- `permutation_baseline(X: np.ndarray, y: np.ndarray, *, target: str, n: int, seed: int=0) -> list[float]` — The outer-CV score of the same pipeline on `n` label shuffles: what chance scores.
- `run(conn, *, target: str='had_change_orders', min_billed: float | None=None, min_reports: int=1, modalities: tuple[str, ...]=('text', 'image'), seed: int=0, status: str='kept', permutations: int=0, material: float=0.05, log=print) -> dict` — 

## `window`

Tune which criteria to accept: search contiguous windows of the confidence ranking against the label.

- `CRIT` — constant
- `DDL` — constant
- `ranking(conn, *, rank_by: str='mean_confidence') -> list[dict]` — Every text criterion with a judgement, most confident first (its latest judgement's numbers).
- `windows(n: int, sizes: list[int], step: int) -> list[tuple[int, int]]` — 
- `scan(X: np.ndarray, y: np.ndarray, wins: list[tuple[int, int]], *, target: str, repeats: int, seed: int) -> list[float]` — 
- `run(conn, *, target: str='material_change_orders', material: float=0.05, rank_by: str='mean_confidence', sizes: list[int]=(10, 20, 40), step: int=5, shuffles: int=20, repeats: int=3, min_billed: float | None=None, seed: int=0, log=print) -> dict` — 