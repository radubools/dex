"""Ask Laya typed questions — yes/no, multiple choice, rubric score — about a chunk of text.

Laya is a non-autoregressive decision model: one forward pass turns a piece of
text plus a set of typed questions into calibrated probabilities. It generates
no tokens, so there is no JSON to parse back and nothing to hallucinate. This
module drives the MLX build on Apple silicon through `laya_mlx`.

What it adds over calling `laya_mlx.load(...).predict(...)` yourself:

*   **The real token budget.** Laya's 512-token limit covers the whole
    sequence — the question's instructions, its rendered options *and* the
    text. `laya_mlx` silently drops whatever does not fit, so an over-long
    chunk comes back with a confident answer about its first half and no
    indication that the rest was never read. `state_budget` works out what is
    actually left for the text, `split` cuts text to it, and `ask` raises
    `TooLong` rather than answering about a truncated chunk.
*   **Question builders** that name the boolean type `yes_no`. Upstream calls
    it `noul`, which is what has to go on the wire.
*   **`answers`**, flattening a result to one plain Python value per question.

    agent = laya.load()
    questions = {
        "refund": laya.yes_no("Does the customer ask for money back?"),
        "team": laya.choice("Who should handle this?", ["billing", "technical"]),
        "urgency": laya.score("How urgent is this?", ["not urgent", "soon", "critical"]),
    }
    for chunk, result in laya.ask_chunks(agent, long_text, questions):
        print(laya.answers(result))

Everything but `load` and `ask` works from the tokenizer and config alone, so
budgeting and splitting a corpus costs a few kilobytes of download rather than
the checkpoint's weights.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

#: The MLX FP16 conversion of Laya's English checkpoint (ModernBERT-large, 421M).
MODEL = "aac6fef/laya-mlx"

#: Fallbacks used only if a checkpoint's config omits them; every published one has both.
MAX_LEN = 512
HEAD_MAX_LEN = 192

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")

_CHECKPOINTS: dict[str, tuple[Any, dict]] = {}


class TooLong(ValueError):
    """Raised when text would be silently truncated to fit the questions asked of it."""


def checkpoint(model: str = MODEL) -> tuple[Any, dict]:
    """The tokenizer and config for a checkpoint, fetched without its weights.

    Returns `(tokenizer, config)`. A local directory is read in place; anything
    else is treated as a Hugging Face repo id and only the three small files
    are downloaded. Cached per model id for the life of the process.
    """
    if model not in _CHECKPOINTS:
        from laya_mlx.tokenizer import Tokenizer

        local = Path(model)
        if local.is_dir():
            tok_dir, cfg_path = local / "tokenizer", local / "rl_agent_config.json"
        else:
            from huggingface_hub import hf_hub_download

            tok_dir = Path(hf_hub_download(model, "tokenizer/tokenizer.json")).parent
            hf_hub_download(model, "tokenizer/tokenizer_config.json")
            cfg_path = Path(hf_hub_download(model, "rl_agent_config.json"))
        _CHECKPOINTS[model] = (
            Tokenizer(tok_dir),
            json.loads(cfg_path.read_text(encoding="utf-8")),
        )
    return _CHECKPOINTS[model]


def yes_no(instructions: str, *, false: str = "", true: str = "") -> dict:
    """A yes/no question, answered with P(true). Upstream names this type `noul`.

    `false` and `true` describe what each verdict means; leaving them empty
    uses Laya's own wording.
    """
    question = {"type": "noul", "instructions": instructions}
    if false or true:
        question["criteria"] = {"false": false, "true": true}
    return question


def choice(instructions: str, options: list[str] | dict) -> dict:
    """A multiple-choice question over `options`: labels alone, or {label: description}.

    Descriptions are rendered into the prompt as `label: description` and are
    usually worth the tokens — they are what the model matches the text
    against.
    """
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list) -> dict:
    """A rubric question over ordered `levels`, answered with the expected level.

    The answer is continuous: a probability-weighted mean over `0 .. len(levels) - 1`,
    so three levels give a number between 0.0 and 2.0 rather than a bare index.
    """
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def validate(questions: dict) -> list[dict]:
    """Check a question set against Laya's own rules, returning the internal forms.

    Raises `ValueError` for an unknown type, a missing `instructions`, duplicate
    choice labels or an empty rubric — the same checks `predict` would make,
    but before anything is loaded or downloaded.
    """
    if not isinstance(questions, dict):
        raise ValueError("questions must be a dictionary keyed by question id")
    from laya_mlx.agent import Agent

    to_internal = getattr(Agent, "_to_internal", None)
    if to_internal is None:  # pragma: no cover - guards an upstream rename
        raise RuntimeError(
            "laya_mlx.Agent._to_internal is gone; this module validated and "
            "budgeted questions through it. See SKILL.md."
        )
    return [to_internal(q) for q in questions.values()]


def state_budget(questions: dict, model: str = MODEL) -> int:
    """How many tokens of text fit alongside `questions`, the most expensive one deciding.

    Laya spends its 512 tokens on `[CLS] type instructions [SEP] options [SEP]`
    first and gives the text whatever is left, so a long instruction or a
    twenty-option choice is paid for out of how much text you may ask about.
    Every question in a call sees the same text, so the tightest one sets the
    limit for all of them.
    """
    from laya_mlx.common import build_prefix

    tok, cfg = checkpoint(model)
    max_len = cfg.get("max_len", MAX_LEN)
    head_max_len = cfg.get("head_max_len", HEAD_MAX_LEN)
    longest = max(
        (len(build_prefix(tok, q, head_max_len)[0]) for q in validate(questions)),
        default=0,
    )
    # One token is held back for the [SEP] that closes the sequence.
    return max(0, max_len - longest - 1)


def token_count(text: str, model: str = MODEL) -> int:
    """How many tokens the checkpoint makes of `text`, counted as `predict` would count it."""
    from laya_mlx.common import serialize_state

    tok, _ = checkpoint(model)
    rendered = serialize_state(text).replace(tok.mask_token, " ")
    return len(tok(rendered, add_special_tokens=False)["input_ids"])


def split(text: str, budget: int, model: str = MODEL) -> list[str]:
    """Cut `text` into chunks of at most `budget` tokens, breaking at paragraphs then sentences.

    Chunks are packed greedily, so a short document comes back as one chunk.
    Raises `TooLong` if a single word exceeds the budget, which means the
    questions have left too little room to ask about anything.
    """
    if budget < 1:
        raise TooLong(f"no room for text: budget is {budget} tokens")
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    def extend(piece: str, separator: str) -> bool:
        nonlocal current
        candidate = piece if not current else current + separator + piece
        if token_count(candidate, model) > budget:
            return False
        current = candidate
        return True

    for paragraph in _PARAGRAPH.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if extend(paragraph, "\n\n"):
            continue
        flush()
        if extend(paragraph, "\n\n"):
            continue
        for atom in _atoms(paragraph, budget, model):
            if not extend(atom, " "):
                flush()
                current = atom
    flush()
    return chunks


def ask(agent: Any, text: str, questions: dict) -> dict:
    """Answer `questions` about one chunk, refusing to let the chunk be truncated.

    Returns Laya's own result: `{"model", "answers", "usage"}`, where each
    answer carries `confidence` and the full `probabilities` as well as its
    value. Raises `TooLong` if `text` does not fit — `split` it first, or use
    `ask_chunks`.
    """
    model = getattr(agent, "model_id", MODEL)
    budget = state_budget(questions, model)
    used = token_count(text, model)
    if used > budget:
        raise TooLong(
            f"chunk is {used} tokens but these questions leave room for {budget}; "
            "laya_mlx would drop the rest without saying so. Split it first."
        )
    return agent.predict(text, questions)


def ask_chunks(agent: Any, text: str, questions: dict) -> list[tuple[str, dict]]:
    """Split `text` to fit `questions` and answer them about each chunk, in order.

    Returns `(chunk, result)` pairs so an answer can be traced back to the text
    it was given. Each chunk is a separate forward pass: Laya sees no context
    beyond the chunk in hand.
    """
    model = getattr(agent, "model_id", MODEL)
    budget = state_budget(questions, model)
    return [(chunk, ask(agent, chunk, questions)) for chunk in split(text, budget, model)]


def answers(result: dict) -> dict:
    """One plain value per question: P(true), the chosen label, or the expected level.

    The confidences and per-option probabilities stay in `result["answers"]`;
    this is for the common case of wanting the verdict alone.
    """
    flat = {}
    for qid, answer in result["answers"].items():
        kind = answer["type"]
        flat[qid] = answer["choice"] if kind == "choice" else (
            answer["score"] if kind == "score" else answer["noul"]
        )
    return flat


def load(model: str = MODEL, **kwargs: Any) -> Any:
    """Load a checkpoint onto MLX; pass `dtype="float32"` for closer parity with upstream.

    Downloads the weights on first use and keeps them in the Hugging Face
    cache. Accepts every `laya_mlx.load` keyword — `batch_size`, `device`,
    `revision`, `compile`.
    """
    import laya_mlx

    return laya_mlx.load(model, **kwargs)


def _atoms(paragraph: str, budget: int, model: str) -> Iterator[str]:
    """Break one over-long paragraph into sentences, and any over-long sentence into word runs."""
    for sentence in _SENTENCE.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        if token_count(sentence, model) <= budget:
            yield sentence
            continue
        run = ""
        for word in sentence.split():
            candidate = word if not run else run + " " + word
            if token_count(candidate, model) <= budget:
                run = candidate
                continue
            if run:
                yield run
            if token_count(word, model) > budget:
                raise TooLong(
                    f"a single word is {token_count(word, model)} tokens, over the "
                    f"{budget}-token budget these questions leave"
                )
            run = word
        if run:
            yield run
