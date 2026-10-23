## `evidence`

Pick the chunks worth asking about — prose, not a repeated form, not a grid of figures.

- `FUNCTION_WORDS` — constant
- `PROSE_RATIO` — constant
- `DIGIT_SHARE` — constant
- `DUPLICATE_OVERLAP` — constant
- `DUPLICATE_GROUP` — constant
- `prose_ratio(text: str) -> float` — The share of `text`'s words that are English function words, 0.0 to 1.0.
- `is_prose(text: str, ratio: float=PROSE_RATIO) -> bool` — True when `text` reads as sentences rather than as a table of values.
- `digit_share(text: str) -> float` — The share of `text`'s non-space characters that are digits or money marks.
- `overlap(a: str, b: str) -> float` — Jaccard overlap of two chunks' distinctive words, 0.0 to 1.0.
- `duplicates(chunks: dict, *, threshold: float=DUPLICATE_OVERLAP, group: int=DUPLICATE_GROUP) -> set` — Ids of chunks belonging to a repeated set of `group` or more.
- `core(chunks: dict, *, ratio: float=PROSE_RATIO, threshold: float=DUPLICATE_OVERLAP, group: int=DUPLICATE_GROUP, digits: float=DIGIT_SHARE, drop_decoded: bool=True) -> set` — Ids of the chunks a quote can honestly come from: the four filters composed.
- `rank(answers: dict, ids: set) -> list` — (verdict, chunk id) for `ids`, strongest first; ids with no answer dropped.

## `laya`

Ask Laya typed questions — yes/no, multiple choice, rubric score — about a chunk of text.

- `MODEL` — constant
- `MAX_LEN` — constant
- `HEAD_MAX_LEN` — constant
- `BATCH` — constant
- `CACHE_LIMIT` — constant
- `class TooLong` — Raised when text would be silently truncated to fit the questions asked of it.
- `checkpoint(model: str=MODEL) -> tuple[Any, dict]` — The tokenizer and config for a checkpoint, fetched without its weights.
- `yes_no(instructions: str, *, false: str='', true: str='') -> dict` — A yes/no question, answered with P(true). Upstream names this type `noul`.
- `choice(instructions: str, options: list[str] | dict) -> dict` — A multiple-choice question over `options`: labels alone, or {label: description}.
- `score(instructions: str, levels: list) -> dict` — A rubric question over ordered `levels`, answered with the expected level.
- `validate(questions: dict) -> list[dict]` — Check a question set against Laya's own rules, returning the internal forms.
- `state_budget(questions: dict, model: str=MODEL) -> int` — How many tokens of text fit alongside `questions`, the most expensive one deciding.
- `token_count(text: str, model: str=MODEL) -> int` — How many tokens the checkpoint makes of `text`, counted as `predict` would count it.
- `split(text: str, budget: int, model: str=MODEL) -> list[str]` — Cut `text` into chunks of at most `budget` tokens, breaking at paragraphs then sentences.
- `ask(agent: Any, text: str, questions: dict) -> dict` — Answer `questions` about one chunk, refusing to let the chunk be truncated.
- `ask_batches(agent: Any, text: str, questions: dict, batch: int=BATCH) -> Iterator[list[tuple[str, dict]]]` — Split `text` to fit `questions` and answer it `batch` chunks at a time.
- `ask_chunks(agent: Any, text: str, questions: dict, batch: int=BATCH) -> Iterator[tuple[str, dict]]` — Split `text` to fit `questions` and answer them about each chunk, in order.
- `answers(result: dict) -> dict` — One plain value per question: P(true), the chosen label, or the expected level.
- `describe(questions: dict) -> list[dict]` — Each question as a record — `id`, `type`, `prompt`, `options` — ready to serialise.
- `detail(result: dict) -> dict` — Each answer as its `answers()` verdict plus the whole record Laya returned.
- `cache_limit(limit: int | None=CACHE_LIMIT) -> int` — Cap MLX's buffer cache at `limit` bytes, returning the limit it replaced.
- `release() -> None` — Hand every cached MLX buffer back, so the memory returns to the system.
- `memory() -> dict` — MLX's `active`, `cache` and `peak` bytes right now, for a progress line or a log.
- `load(model: str=MODEL, *, cache: int | None=CACHE_LIMIT, **kwargs: Any) -> Any` — Load a checkpoint onto MLX; pass `dtype="float32"` for closer parity with upstream.

## `ledger`

Publish each run's scores to the shared experiments database, and read back what every task has scored.

- `DEFAULT_METRIC` — constant
- `asked(run: dict) -> list[str]` — The question ids a run actually asked, never the ones it carried forward.
- `entries(run: dict, *, task: str, include_answers: bool=True) -> list[dict]` — What `publish` would write for a run — one record per question asked, no database.
- `publish(conn: Any, run: dict, *, task: str, include_answers: bool=True) -> list[dict]` — Register each criterion this run asked and append one history entry to each.
- `history(conn: Any, qid: str) -> list[dict]` — Every history entry recorded against one criterion, oldest first, all tasks.
- `standing(conn: Any, *, metric: str=DEFAULT_METRIC, names: list[str] | None=None, min_gain: float=0.02, patience: int=2) -> dict` — Per criterion, what the accumulated history says: runs, tasks, best score, converged.
- `worth_improving(standing: dict) -> list[str]` — The criteria from `standing` that have not converged, by name.

## `sweep`

Store chunks once by hash, then iterate versioned question sets against them.

- `ID_LENGTH` — constant
- `chunk_id(text: str, length: int=ID_LENGTH) -> str` — A stable short hash of `text`, used as its key in the chunk store.
- `save_chunks(path: Path | str, chunks: dict, **meta: Any) -> dict` — Write the chunk store — `{id: {...}}` plus whatever `meta` you pass.
- `load_chunks(path: Path | str) -> dict` — Read a chunk store written by `save_chunks`.
- `build_run(version: str, questions: dict, answers: dict, *, metrics: dict | None=None, previous: dict | None=None, chunks_file: str='chunks.json', note: str='') -> dict` — One run: what was asked, what came back, and what carried over unchanged.
- `save_run(path: Path | str, run: dict) -> dict` — Write one run file.
- `load_run(path: Path | str) -> dict` — Read a run file written by `save_run`.
- `missing_chunks(run: dict, chunks: dict) -> dict` — Chunk ids a run answers that the store does not hold, per question.
- `binary_scores(truth: dict, verdicts: dict, *, at: float=0.5) -> dict` — Recall, specificity, Youden's J and agreement for one yes/no question.
- `convergence(history: dict, *, min_gain: float=0.02, patience: int=2) -> dict` — Per question: its best version, and whether iterating has stopped paying.

## `unlabelled`

Score a Laya question set over one document when there is no oracle.

- `FIRE` — constant
- `percentile(values: list[float], q: float) -> float` — The `q`-th percentile of `values`, nearest-rank.
- `numbers(values: list[float], *, at: float=FIRE) -> dict[str, Any]` — Peak, spread, support and lift for one yes/no criterion's answers.
- `shape(scored: dict, *, peak: float=0.7, lift: float=0.3, spread: float=0.45) -> str` — Label a scored yes/no criterion by the shape of its answers.
- `categorical(verdicts: list[Any], confidences: list[float]) -> dict[str, Any]` — How a choice or score question came out, across a document's chunks.
- `score_round(questions: dict, answers: dict, *, core: list[str] | None=None, form: list[str] | None=None, at: float=FIRE) -> dict[str, dict]` — Score a whole question set from `{qid: {chunk_id: laya.detail row}}`.
- `polarity(claim: dict, denial: dict, ids: list[str] | None=None) -> dict[str, Any]` — How far a criterion separates from its own denial, on its peak chunk.
- `polarities(pairs: dict, answers: dict, ids: list[str] | None=None) -> dict[str, dict]` — `polarity` for every claim/denial pair both halves of which were answered.
- `top_chunks(answers_for_q: dict, n: int=3) -> list[tuple[str, float]]` — The `n` chunks that answered one yes/no criterion most strongly.