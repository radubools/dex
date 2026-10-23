## `clef`

Ask Clef-Flash — Cloudflare's 9B decision model, run locally on MLX — typed questions about text.

- `MODEL` — constant
- `REVISION` — constant
- `MAX_LENGTH` — constant
- `TOKENS_PER_SECOND` — constant
- `MAX_QUESTIONS` — constant
- `WORKERS` — constant
- `class ContextTooLong` — A record would not fit `MAX_LENGTH`; nothing is truncated silently.
- `snapshot(repo: str=MODEL, revision: str=REVISION) -> Path` — 
- `load(repo: str=MODEL, revision: str=REVISION, *, dequantize: bool=True, vision: bool=False)` — The loaded model, cached per process.
- `release() -> None` — Drop every loaded model and give the GPU memory back.
- `yes_no(instructions: str, *, true: str | None=None, false: str | None=None) -> dict` — 
- `choice(instructions: str, options: Mapping[str, str] | Iterable[str]) -> dict` — 
- `score(instructions: str, levels: list[str]) -> dict` — 
- `validate(questions: Mapping[str, dict]) -> dict` — 
- `record_tokens(text: str, questions: Mapping[str, dict], *, model=None) -> int` — 
- `groups(text: str, questions: Mapping[str, dict], *, max_length: int=MAX_LENGTH, max_questions: int=MAX_QUESTIONS, model=None) -> list[dict]` — Split `questions` into sets of at most `max_questions` that each fit one record beside `text`.
- `ask(text: str, questions: Mapping[str, dict], *, max_length: int=MAX_LENGTH, max_questions: int=MAX_QUESTIONS, model=None) -> dict` — Answer every question about `text`, in as few records as fit `max_length`.
- `ask_each(items: Iterable[tuple[str, str]], questions: Mapping[str, dict] | Mapping[str, Mapping[str, dict]], *, per_item: bool=False, model=None) -> Iterator[tuple[str, dict]]` — `(id, result)` for each `(id, text)`, in order, as each one finishes.
- `MAX_IMAGE_SIDE` — constant
- `IMAGE_STATE` — constant
- `ask_image(image, questions: Mapping[str, dict], *, text: str | None=None, max_questions: int=MAX_QUESTIONS, max_length: int=MAX_LENGTH, model=None) -> dict` — Answer every question about an image — PNG/JPEG bytes, a path, or a PIL image.
- `class Pool` — `workers` processes, each holding its own copy of the model on the one GPU.
- `answers(result: dict) -> dict` — The verdict alone per question: P(true), the winning label, or the expected level.
- `scale(answer: dict) -> float` — Any answer on one 0-1 axis: P(true), the choice's confidence, or score/(levels-1).
- `estimate(texts: Iterable[str], questions: Mapping[str, dict], *, model=None, tokens_per_second: float=TOKENS_PER_SECOND) -> dict` — Tokens and wall-clock a sweep of `questions` over `texts` will take.