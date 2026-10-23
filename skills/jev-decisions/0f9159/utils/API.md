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
- `groups(questions: dict, size: int=MAX_QUESTIONS) -> list[dict]` — A question set cut into request-sized groups, in the order it was written.
- `tokenizer(name: str=TOKENIZER) -> Any` — The tokenizer `token_count` counts with, downloaded once and cached per name.
- `token_count(text: str, model: str=TOKENIZER) -> int` — How many tokens `TOKENIZER` makes of `text`.
- `split(text: str, budget: int=CHUNK_TOKENS, model: str=TOKENIZER) -> list[str]` — Cut `text` into chunks of at most `budget` tokens, breaking at paragraphs then sentences.
- `ask(client: Client, state: Any, questions: dict) -> dict` — Answer every question in `questions` about one state, in as few requests as the endpoint allows.
- `ask_each(client: Client, chunks: Iterable[str], questions: dict, workers: int=WORKERS) -> Iterator[tuple[str, dict]]` — Answer `questions` about each of `chunks`, `workers` requests in flight, yielding in order.
- `ask_chunks(client: Client, text: str, questions: dict, workers: int=WORKERS, budget: int=CHUNK_TOKENS) -> Iterator[tuple[str, dict]]` — Split `text` to `budget` tokens and answer `questions` about each chunk, in order.
- `answers(result: dict) -> dict` — One plain value per question: P(true), the chosen label, or the expected level.
- `describe(questions: dict) -> list[dict]` — Each question as a record — `id`, `type`, `prompt`, `options` — ready to serialise.
- `detail(result: dict) -> dict` — Each answer as its `answers()` verdict plus the whole record Jev returned.
- `scale(question: dict, answer: dict) -> float` — One answer on a 0-1 scale, whatever the question's type.
- `cost(results: Iterable[dict]) -> dict` — Credits, tokens, requests and chunks over a run's results, for a progress line or a log.