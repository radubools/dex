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