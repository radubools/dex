## `jev`

Ask Jev typed questions — yes/no, multiple choice, rubric score — about a chunk of text.

- `URL` — constant
- `MODEL` — constant
- `CHUNK_TOKENS` — constant
- `MAX_QUESTIONS` — constant
- `MAX_CHOICE_OPTIONS` — constant
- `WORKERS` — constant
- `MAX_BODY` — constant
- `RETRY_STATUS` — constant
- `RETRIES` — constant
- `BACKOFF` — constant
- `TOKENIZER` — constant
- `class JevError` — Anything the endpoint refused. Carries `status` and the server's `message`.
- `class AuthError` — 401. The key in `JEV_API_KEY` is missing, malformed or revoked.
- `class InvalidRequest` — 400, 413 or 422 — the request itself is wrong and will stay wrong.
- `class ProviderError` — 502 `The decision provider rejected the request.`, after `RETRIES` attempts.
- `class RateLimited` — 429, still refused after `RETRIES` attempts.
- `class Overloaded` — 529, still refused after `RETRIES` attempts.
- `class TooLong` — Raised when a state is too large to post at all. `split` it first.
- `class Client` — Key, model and HTTP settings for a run. Cheap; safe to share across threads.
- `client(key: str | None=None, **kwargs: Any) -> Client` — A `Client` from `JEV_API_KEY`, or from `key` if you pass one.
- `yes_no(instructions: str, *, false: str='', true: str='') -> dict` — A yes/no question, answered with P(true). Jev names this type `noul`.
- `choice(instructions: str, options: list[str] | dict) -> dict` — A multiple-choice question over `options`: labels alone, or {label: description}.
- `score(instructions: str, levels: list) -> dict` — A rubric question over 2 to 10 ordered `levels`, answered with the expected level.
- `validate(questions: dict) -> dict` — Check a question set question by question, returning it unchanged.
- `rebuild(question: dict, *, ignore: Iterable[str]=()) -> dict` — One stored question remade by the builder for its type; raises if it differs.
- `groups(questions: dict, size: int=MAX_QUESTIONS) -> list[dict]` — A question set cut into request-sized groups, in the order it was written.
- `blocks(need: dict) -> list[tuple[tuple[str, ...], list[str]]]` — Chunks grouped by the exact set of questions each one still needs, largest group first.
- `tokenizer(name: str=TOKENIZER) -> Any` — The tokenizer `token_count` counts with, downloaded once and cached per name.
- `token_count(text: str, model: str=TOKENIZER) -> int` — How many tokens `TOKENIZER` makes of `text`.
- `split(text: str, budget: int=CHUNK_TOKENS, model: str=TOKENIZER) -> list[str]` — Cut `text` into chunks of at most `budget` tokens, breaking at paragraphs then sentences.
- `postable(questions: dict) -> dict` — A question set in the shape the endpoint accepts, otherwise unchanged.
- `ask(client: Client, state: Any, questions: dict) -> dict` — Answer every question in `questions` about one state, in as few requests as the endpoint allows.
- `ask_each(client: Client, chunks: Iterable[str], questions: dict, workers: int=WORKERS) -> Iterator[tuple[str, dict]]` — Answer `questions` about each of `chunks`, `workers` requests in flight, yielding in order.
- `ask_chunks(client: Client, text: str, questions: dict, workers: int=WORKERS, budget: int=CHUNK_TOKENS) -> Iterator[tuple[str, dict]]` — Split `text` to `budget` tokens and answer `questions` about each chunk, in order.
- `answers(result: dict) -> dict` — One plain value per question: P(true), the chosen label, or the expected level.
- `describe(questions: dict) -> list[dict]` — Each question as a record — `id`, `type`, `prompt`, `options` — ready to serialise.
- `detail(result: dict) -> dict` — Each answer as its `answers()` verdict plus the whole record Jev returned.
- `with_confidence(record: dict) -> dict` — One `detail()` record with a yes/no's missing `confidence` filled in.
- `scale(question: dict, answer: dict) -> float` — One answer on a 0-1 scale, whatever the question's type.
- `cost(results: Iterable[dict]) -> dict` — Credits, tokens, requests and chunks over a run's results, for a progress line or a log.
- `estimate(chunks: Any, questions: Any, *, size: int=MAX_QUESTIONS) -> dict` — What a sweep will cost before it runs: `{"chunks", "requests", "credits"}`.

## `jev_ledger`

Publish Jev's scores to the shared experiments database, in rows of their own.

- `KIND` — constant
- `KIND_LAYA` — constant
- `DEFAULT_METRIC` — constant
- `model_name(model: Any=None) -> str` — The model name to stamp: a string, a `jev.Client`'s `model`, or `jev.MODEL`.
- `fingerprint(question: dict) -> str` — One question as a canonical string, so two records of it compare equal or do not.
- `pairs(name: str, entries: Iterable[dict]) -> set[tuple[str, str, str]]` — The `(criterion, question fingerprint, chunk)` triples `entries` already cover.
- `pair_index(records: Iterable[dict]) -> dict[tuple[str, str, str], list[dict]]` — The `(criterion, wording, chunk)` triples in `coverage()` records, each mapped to the records holding it.
- `definitions(rounds: dict[str, dict], *, prefix: str='') -> list[dict]` — The distinct `(criterion, wording)` questions across several rounds of asks.
- `remaining(definitions: Iterable[dict], covered: Any, *, chunks: Iterable[str] | None=None) -> dict[str, list[dict]]` — The plan's `(criterion, wording, chunk)` pairs that `covered` does not hold, grouped by chunk.
- `passes(todo: dict[str, list[dict]]) -> list[tuple[tuple[str, ...], list[str]]]` — A `remaining()` plan regrouped as `(wording keys, the chunks wanting exactly those)`.
- `price(todo: dict[str, list[dict]], *, size: int | None=None) -> dict` — What a `remaining()` plan will cost: `{"chunks", "requests", "credits", "pairs"}`.
- `asked(run: dict) -> list[str]` — The question ids a run actually asked, never the ones it carried forward.
- `entries(run: dict, *, task: str, model: Any=None, include_answers: bool=True) -> list[dict]` — What `publish` would write for a run — one record per question asked, no database.
- `triples(run: dict, *, task: str, model: Any=None) -> set[tuple[str, str, str]]` — The `(criterion, question fingerprint, chunk)` triples `publish` would record.
- `register(conn: Any, name: str, question: dict, *, model: Any=None, merge: bool=True, schema: str | None=None, **meta: Any) -> dict` — Register one criterion under `KIND`, storing its question and the model asked.
- `criterion(conn: Any, name: str, *, schema: str | None=None) -> dict | None` — One registered Jev criterion by name, or None.
- `criteria(conn: Any, *, model: str | None=None, schema: str | None=None) -> list[dict]` — Every criterion registered under `KIND`, oldest first; one model's if `model` is given.
- `record(conn: Any, name: str, *, task: str, model: Any=None, metrics: dict | None=None, detail: dict | None=None, note: str | None=None, schema: str | None=None) -> dict` — Append one history entry for a Jev run of `name`, stamped with the model.
- `publish(conn: Any, run: dict, *, task: str, model: Any=None, include_answers: bool=True, schema: str | None=None) -> list[dict]` — Register each criterion this run asked and append one history entry to each.
- `history(conn: Any, name: str, *, model: str | None=None, schema: str | None=None) -> list[dict]` — Every entry recorded against one Jev criterion, oldest first, all tasks.
- `coverage(conn: Any, names: Any=None, *, answers: bool=False, schema: str | None=None) -> list[dict]` — Per published entry, which chunks it already scored — the read side of `publish`.
- `track(conn: Any, name: str, *, schema: str | None=None) -> dict` — `experiments.track_record` for the Jev kind: runs, tasks, each metric's series.
- `standing(conn: Any, *, metric: str=DEFAULT_METRIC, names: list[str] | None=None, model: str | None=None, min_gain: float=0.02, patience: int=2, schema: str | None=None) -> dict` — Per Jev criterion: runs, tasks, best score, whether iterating has stopped paying.
- `worth_improving(standing: dict) -> list[str]` — The criteria from `standing` that have not converged, by name.
- `compare(conn: Any, name: str | None=None, *, metric: str=DEFAULT_METRIC, kinds: tuple[str, ...]=(KIND_LAYA, KIND), schema: str | None=None) -> dict` — One criterion name, what each model's rows say about it — the point of separate rows.