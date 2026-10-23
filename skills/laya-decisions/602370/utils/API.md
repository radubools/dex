## `laya`

Ask Laya typed questions — yes/no, multiple choice, rubric score — about a chunk of text.

- `MODEL` — constant
- `MAX_LEN` — constant
- `HEAD_MAX_LEN` — constant
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
- `ask_chunks(agent: Any, text: str, questions: dict) -> list[tuple[str, dict]]` — Split `text` to fit `questions` and answer them about each chunk, in order.
- `answers(result: dict) -> dict` — One plain value per question: P(true), the chosen label, or the expected level.
- `load(model: str=MODEL, **kwargs: Any) -> Any` — Load a checkpoint onto MLX; pass `dtype="float32"` for closer parity with upstream.