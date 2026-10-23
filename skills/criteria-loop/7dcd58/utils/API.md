## `coverage`

Judge criteria on broad coverage: drop only the non-confident, the flat, and the redundant.

- `CORPUS` — constant
- `CRIT` — constant
- `UNCERTAIN_BAND` — constant
- `class Rule` — 
- `DDL` — constant
- `ensure_schema(conn) -> None` — 
- `coverage_set(conn, *, share: float=0.75, salt: str='cover-v1') -> list[str]` — `share` of every document's usable, embedded chunks, chosen by hash (rounded up).
- `usable_total(conn) -> int` — 
- `profile(conn, criterion_ids: list[int], cover: list[str], *, threshold: float=0.5, band: tuple[float, float]=UNCERTAIN_BAND, table: str='decisions', item: str='chunk_id') -> dict[int, dict]` — Per criterion: coverage of the usable corpus, spread and confidence of its answers, and
- `judge(profiles: dict, rule: Rule=Rule(), *, order: list[int] | None=None) -> list[dict]` — Verdicts for every profiled criterion: kept, dropped (with the reason), or pending.
- `record(conn, round_id: int, verdicts: list[dict]) -> None` — Write the judgements and move every eligible criterion to kept/dropped.
- `rule_dict(rule: Rule) -> dict` — 

## `criteria_loop`

Stage 3: generate criteria, evaluate them with Clef-Flash over probes and neighbour walks, keep the useful ones.

- `TOKENS_PER_QUESTION` — constant
- `DEFAULTS` — constant
- `evaluate(conn, plan: dict[str, set[int]], questions: dict[int, dict], *, how: str, round_id: int, budget: dict, log=_log) -> int` — Ask each planned chunk its criteria; write as it goes. Returns tokens spent.
- `run_round(conn, *, generate: int=0, note: str | None=None, rule: usefulness.Rule | None=None, log=_log, **params) -> dict` — 
- `screen_leakage(proposals: list[dict], log=_log) -> list[dict]` — Refuse proposals whose meaning sits too close to the change-order target (BGE-M3 cosine).
- `run_coverage_round(conn, *, generate: int=0, share: float=0.75, rule: coverage.Rule | None=None, budget_tokens: int=40000000, workers: int | None=None, note: str | None=None, log=_log) -> dict` — One coverage round: (generate) -> ask every live criterion about the coverage set -> judge.
- `reopen(conn, *, log=_log) -> int` — Put every dropped criterion back to candidate, so the coverage rule judges it afresh.
- `pick_seeds(conn, n: int=40, *, top: int=15, log=_log) -> list[int]` — Round-0 candidates: the `top` best-scoring seeds, then a diverse spread of the rest.
- `status(conn) -> dict` — 

## `criteria_store`

The `criteria` schema: criteria, rounds, per-chunk decisions, and per-round usefulness.

- `SCHEMA` — constant
- `DDL` — constant
- `ensure_schema(conn) -> None` — 
- `slug(text: str, *, limit: int=60) -> str` — 
- `import_seeds(conn, rows: list[dict], *, source: str) -> int` — Load the backup's `criteria-seed.json` rows (`kind, name, question, best_j, runs`).
- `add_criteria(conn, items: list[dict], *, origin: str, round_id: int | None, modality: str='text') -> list[int]` — Insert criteria (`{"question", "name"?, "parent_id"?}`), skipping wordings already present.
- `start_round(conn, *, evaluator: str, generator: str | None, params: dict, note: str | None=None) -> int` — 
- `finish_round(conn, round_id: int, stats: dict) -> None` — 
- `criteria(conn, *, status: str | list[str] | None=None, ids: list[int] | None=None, modality: str | None=None) -> list[dict]` — Criteria by status, id and modality (`text` — asked of chunks — or `image` — of pictures).
- `decided(conn, criterion_ids: list[int]) -> set[tuple[int, str]]` — 
- `write_decisions(conn, rows: list[tuple[int, str, float, str]], *, round_id: int, evaluator: str) -> None` — `rows` are `(criterion_id, chunk_id, p, how)`.
- `judge(conn, round_id: int, verdicts: list[dict]) -> None` — Record a round's usefulness rows and move each criterion to kept/dropped.

## `generator`

Write the next round's criteria with a local LLM served by Ollama.

- `HOST` — constant
- `MODEL` — constant
- `SCHEMA` — constant
- `RISKS` — constant
- `LEAKAGE` — constant
- `TARGET_DESCRIPTIONS` — constant
- `LEAKAGE_SIMILARITY` — constant
- `implies_target(wording: str) -> bool` — Whether a criterion's wording names change orders, amendments, overruns or budget changes.
- `SYSTEM` — constant
- `SYSTEM_VISUAL` — constant
- `propose(kept: list[dict], dropped: list[dict], uncovered: list[str], *, n: int=20, model: str=MODEL, temperature: float=0.7) -> list[dict]` — New criteria as SystemOne questions, deduplicated against `kept` and each other.
- `propose_visual(kept: list[dict], dropped: list[dict], examples: list[dict], *, n: int=20, model: str=MODEL, temperature: float=0.7) -> list[dict]` — Image criteria (`Does this image show ...?`) from the picture classes and captions on file.

## `image_sweep`

Ask criteria about the picture regions OCR indexed (`corpus.images`) with Clef-Flash's image input.

- `FURNITURE` — constant
- `run(conn, *, criterion_ids: list[int] | None=None, building_type: str | None=None, project_folder: str | None=None, min_page_share: float=0.0, caption: bool=False, all_kinds: bool=False, log=print) -> dict` — 
- `run_round(conn, *, generate: int=0, caption: bool=False, rule: coverage.Rule | None=None, note: str | None=None, log=print) -> dict` — An image round: (generate visual criteria) -> ask them of every content picture -> judge.
- `top(conn, n: int=20, threshold: float=0.5) -> list[dict]` — 

## `pipeline`

Run the stages in sequence, outside dex: ingest -> OCR -> embed -> criteria rounds.

- `backfill(args) -> int` — 

## `usefulness`

Judge criteria by walking the chunk-neighbour graph: how far and how widely does each one reach?

- `CORPUS` — constant
- `CRIT` — constant
- `class Rule` — 
- `probe_set(conn, *, per_document: int=10, salt: str='probe-v1') -> list[str]` — `per_document` usable, embedded chunks from every document, chosen by hash.
- `walk_plan(conn, criterion_ids: list[int], *, threshold: float, k: int, frontier: set[tuple[int, str]]) -> dict[str, set[int]]` — `{chunk_id: {criterion_id, ...}}` still to ask: undecided usable neighbours of `frontier` hits.
- `neighbor_hits(conn, frontier: set[tuple[int, str]], *, threshold: float, k: int) -> set[tuple[int, str]]` — Neighbours of `frontier` chunks where the same criterion has already fired.
- `hits_among(conn, pairs: list[tuple[int, str]], threshold: float) -> set[tuple[int, str]]` — 
- `measure(conn, criterion_ids: list[int], probes: list[str], *, threshold: float=0.5, k: int=5, max_depth: int=4) -> dict[int, dict]` — The usefulness numbers for each criterion, from everything decided so far.
- `verdict(m: dict, rule: Rule=Rule()) -> dict` — `m` with `keep` and a one-line `reason` added.