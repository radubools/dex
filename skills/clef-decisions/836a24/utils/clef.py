"""Ask Clef-Flash — Cloudflare's 9B decision model, run locally on MLX — typed questions about text.

Clef-Flash is a SystemOne-compatible decision model (the same request and answer
shapes as Jev): one forward pass turns *state + questions* into a probability for
every allowed option of every question. No text is generated, nothing is parsed.

    from utils import clef

    questions = {
        "pending_co": clef.yes_no("Does this passage say a change order is awaiting approval?"),
        "who": clef.choice("Who raised the issue?", {"lender": "", "contractor": "", "inspector": ""}),
        "risk": clef.score("How much cost risk does this passage describe?", ["none", "some", "serious"]),
    }
    result = clef.ask(text, questions)
    clef.answers(result)          # {'pending_co': 0.97, 'who': 'lender', 'risk': 1.6}
    result["answers"]["who"]      # confidence and per-label probabilities
    for cid, result in clef.ask_each([(id, text), ...], questions):
        ...

The builders, `answers`, `scale` and result shapes match `jev-decisions`, so code
written against Jev ports by changing the import.

## Throughput is set by tokens, and questions cost more than text

Measured on this M4 Max (40-core GPU, 128 GB), steady state, 3,000-token
records: **~650 tokens/s with the 8-bit weights, ~860 with them dequantized to
bf16** (`load(dequantize=True)`, the default — same numbers, 17 GB resident
instead of 11). It is compute-bound: **batching does not help** — 1, 2, 4 and 8
records per forward pass all ran at 0.16 chunks/s with 30 questions — and
**neither do more instances**: two bf16 instances together made 681 tok/s, three
761, four 553; two 8-bit 624, three 544. RAM is not the constraint. The model
card's 0.34 s per 1k tokens is an M5 Max figure.

Every yes/no question adds ~100 schema tokens to the record (its instructions
plus the two option descriptions), and the schema comes *after* the state, so it
cannot be cached across chunks. A 400-token chunk asked 30 questions is ~3,400
tokens, ~5 s. Budget a sweep with `estimate()` before starting one, and spend
tokens where they inform — see the `criteria-loop` skill's probe-and-walk.

Clef decides a record's questions jointly, so very large sets interact: see
`MAX_QUESTIONS`. Up to ~30 per record the answer barely depends on its company.

## Pinned code

The MLX port ships its loader (`clef_mlx.py`) beside the weights. It is loaded
from the snapshot at `REVISION`, which was read before use; a new upstream
revision is not picked up until someone reads it and moves the pin.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

MODEL = "mlx-community/clef-flash-8bit"
REVISION = "5a195dc147926b63b1d139691ed4480ca9f92c0e"
MAX_LENGTH = 16384
#: Measured with bf16-dequantized weights on the M4 Max; use for estimates only.
TOKENS_PER_SECOND = 850
#: Questions per record. Clef decides a record's questions *jointly*: 140 seed
#: criteria in one record against the same 140 in records of 10 moved P by 0.06
#: on average and up to 0.45, and flipped 1 verdict in 140. At 30 the movement
#: was under 0.01. Capping the set keeps an answer from depending on which other
#: criteria happened to share its record, at ~12% extra for repeating the text.
MAX_QUESTIONS = 32
#: Model instances `Pool` runs by default. One: more made it slower (see above).
WORKERS = 1

_MODELS: dict[tuple, Any] = {}


class ContextTooLong(ValueError):
    """A record would not fit `MAX_LENGTH`; nothing is truncated silently."""


@lru_cache(maxsize=2)
def snapshot(repo: str = MODEL, revision: str = REVISION) -> Path:
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo, revision=revision))


@lru_cache(maxsize=2)
def _clef_mlx(repo: str = MODEL, revision: str = REVISION):
    path = snapshot(repo, revision) / "clef_mlx.py"
    spec = importlib.util.spec_from_file_location("clef_mlx", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["clef_mlx"] = module
    spec.loader.exec_module(module)
    return module


def _dequantize(module) -> None:
    import mlx.core as mx
    import mlx.nn as nn

    for name, child in module.children().items():
        items = child if isinstance(child, list) else [child]
        for i, c in enumerate(items):
            if isinstance(c, nn.QuantizedLinear):
                w = mx.dequantize(c.weight, c.scales, c.get("biases"),
                                  group_size=c.group_size, bits=c.bits)
                lin = nn.Linear(w.shape[1], w.shape[0], bias="bias" in c)
                lin.weight = w.astype(mx.bfloat16)
                if "bias" in c:
                    lin.bias = c.bias
                if isinstance(child, list):
                    child[i] = lin
                else:
                    setattr(module, name, lin)
            elif isinstance(c, nn.Module):
                _dequantize(c)


def load(repo: str = MODEL, revision: str = REVISION, *, dequantize: bool = True,
         vision: bool = False):
    """The loaded model, cached per process.

    `vision=False` (the default) loads the text-only backbone through mlx-lm;
    `vision=True` loads backbone and vision tower through mlx-vlm, which
    `ask_image` needs. They are separate instances — load the one you use.
    """
    key = (repo, revision, dequantize, vision)
    if key not in _MODELS:
        import mlx.core as mx

        m = _clef_mlx(repo, revision).load(str(snapshot(repo, revision)),
                                           backend="vlm" if vision else "lm")
        if dequantize:
            _dequantize(m._text_model)
            mx.eval(m._text_model.parameters())
            mx.clear_cache()
        _MODELS[key] = m
    return _MODELS[key]


def release() -> None:
    """Drop every loaded model and give the GPU memory back."""
    import mlx.core as mx

    _MODELS.clear()
    mx.clear_cache()


# ------------------------------------------------------------------ builders


def yes_no(instructions: str, *, true: str | None = None, false: str | None = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def choice(instructions: str, options: Mapping[str, str] | Iterable[str]) -> dict:
    criteria = dict(options) if isinstance(options, Mapping) else {str(o): "" for o in options}
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def validate(questions: Mapping[str, dict]) -> dict:
    if not questions:
        raise ValueError("at least one question is required")
    for qid, q in questions.items():
        t = q.get("type")
        if t not in ("noul", "choice", "score"):
            raise ValueError(f"{qid}: type must be noul, choice or score")
        if t == "choice" and len(q.get("criteria") or {}) < 2:
            raise ValueError(f"{qid}: a choice needs at least two options")
        if t == "score" and not 2 <= len(q.get("criteria") or []) <= 10:
            raise ValueError(f"{qid}: a score needs 2-10 levels")
    return dict(questions)


# ------------------------------------------------------------------ asking


def record_tokens(text: str, questions: Mapping[str, dict], *, model=None) -> int:
    m = model or load()
    enc = _clef_mlx().encode_record(m.tokenizer, {"state": text, "questions": dict(questions)},
                                    max_length=10**9)
    return len(enc.input_ids)


def groups(text: str, questions: Mapping[str, dict], *, max_length: int = MAX_LENGTH,
           max_questions: int = MAX_QUESTIONS, model=None) -> list[dict]:
    """Split `questions` into sets of at most `max_questions` that each fit one record beside `text`."""
    items = list(questions.items())
    chunks = [dict(items[i:i + max_questions]) for i in range(0, len(items), max_questions)]
    out: list[dict] = []
    for qs in chunks:
        if record_tokens(text, qs, model=model) <= max_length:
            out.append(qs)
            continue
        current: dict = {}
        for qid, q in qs.items():
            trial = {**current, qid: q}
            if current and record_tokens(text, trial, model=model) > max_length:
                out.append(current)
                current = {qid: q}
            else:
                current = trial
        if record_tokens(text, current, model=model) > max_length:
            raise ContextTooLong(f"the text alone plus one question exceeds {max_length} tokens")
        out.append(current)
    return out


def ask(text: str, questions: Mapping[str, dict], *, max_length: int = MAX_LENGTH,
        max_questions: int = MAX_QUESTIONS, model=None) -> dict:
    """Answer every question about `text`, in as few records as fit `max_length`.

    Returns `{"answers": {qid: answer}, "usage": {"input_tokens", "records", "elapsed_ms"}}`,
    each answer in SystemOne shape (`noul` / `choice`+`confidence`+`probabilities` /
    `score`+`confidence`+`probabilities`+`legend`).
    """
    m = model or load()
    validate(questions)
    started = time.perf_counter()
    answers: dict[str, Any] = {}
    tokens = 0
    sets = groups(text, questions, max_length=max_length, max_questions=max_questions, model=m)
    for qs in sets:
        try:
            out = m.systemone({"model": "clef-flash", "state": text, "questions": qs},
                              max_length=max_length, truncate=False)
        except _clef_mlx().ContextTooLong as exc:
            raise ContextTooLong(str(exc)) from exc
        answers.update(out["answers"])
        tokens += out["usage"]["input_tokens"]
    return {"answers": answers,
            "usage": {"input_tokens": tokens, "records": len(sets),
                      "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}}


def ask_each(items: Iterable[tuple[str, str]],
             questions: Mapping[str, dict] | Mapping[str, Mapping[str, dict]], *,
             per_item: bool = False, model=None) -> Iterator[tuple[str, dict]]:
    """`(id, result)` for each `(id, text)`, in order, as each one finishes.

    With `per_item=True`, `questions` maps each id to its own question set, so a
    walk can ask a chunk only the criteria that fired on its neighbour.
    """
    m = model or load()
    for cid, text in items:
        qs = questions[cid] if per_item else questions
        if not qs:
            continue
        yield cid, ask(text, qs, model=m)


#: Longest side an image is scaled down to before it is asked about. Image tokens
#: grow with area (16 px patches, merged 2x2): 1,280 x 960 is ~1,200 tokens.
MAX_IMAGE_SIDE = 1280
IMAGE_STATE = "Decide each field from the attached image."


def _image(image):
    import io

    from PIL import Image

    if isinstance(image, (bytes, bytearray, memoryview)):
        img = Image.open(io.BytesIO(bytes(image)))
    elif isinstance(image, (str, Path)):
        img = Image.open(image)
    else:
        img = image
    img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    return img


def ask_image(image, questions: Mapping[str, dict], *, text: str | None = None,
              max_questions: int = MAX_QUESTIONS, max_length: int = MAX_LENGTH, model=None) -> dict:
    """Answer every question about an image — PNG/JPEG bytes, a path, or a PIL image.

    `text` is optional context that goes in the state beside the image (a caption,
    the page's text); without it the state only says to decide from the image.
    Same result shape as `ask`. Loads the vision backbone (`load(vision=True)`)
    unless a model is passed.
    """
    m = model or load(vision=True)
    validate(questions)
    img = _image(image)
    state = text or IMAGE_STATE
    started = time.perf_counter()
    answers: dict[str, Any] = {}
    tokens = 0
    items = list(questions.items())
    sets = [dict(items[i:i + max_questions]) for i in range(0, len(items), max_questions)]
    for qs in sets:
        try:
            out = m.systemone({"model": "clef-flash", "state": state, "images": [img], "questions": qs},
                              max_length=max_length, truncate=False)
        except _clef_mlx().ContextTooLong as exc:
            raise ContextTooLong(str(exc)) from exc
        answers.update(out["answers"])
        tokens += out["usage"]["input_tokens"]
    return {"answers": answers,
            "usage": {"input_tokens": tokens, "records": len(sets), "image_size": list(img.size),
                      "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}}


# ------------------------------------------------------------------ several instances

_WORKER_MODEL = None


def _worker_init(repo: str, revision: str, dequantize: bool) -> None:
    global _WORKER_MODEL
    _WORKER_MODEL = load(repo, revision, dequantize=dequantize)


def _worker_ask(cid: str, text: str, questions: dict, max_length: int) -> tuple[str, dict]:
    return cid, ask(text, questions, max_length=max_length, model=_WORKER_MODEL)


class Pool:
    """`workers` processes, each holding its own copy of the model on the one GPU.

    The default is one worker: on the M4 Max every extra instance *lowered* the
    total (1 bf16: 857 tok/s, 2: 681, 4: 553). The pool is still the way to run
    Clef from async code — the model lives in its own process, off the event
    loop's threads — and `workers=` is there to re-measure on other hardware.

        with clef.Pool() as pool:
            for cid, result in pool.ask_each(items, questions):
                ...

    Results arrive as they finish, not in input order. Each worker costs ~17 GB
    with `dequantize=True` (11 GB without).
    """

    def __init__(self, workers: int | None = None, *, repo: str = MODEL, revision: str = REVISION,
                 dequantize: bool = True, max_length: int = MAX_LENGTH):
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor

        self.workers = workers or WORKERS
        self.max_length = max_length
        snapshot(repo, revision)  # download once, before the workers race for it
        self._pool = ProcessPoolExecutor(
            max_workers=self.workers, mp_context=mp.get_context("spawn"),
            initializer=_worker_init, initargs=(repo, revision, dequantize),
        )

    def ask_each(self, items: Iterable[tuple[str, str]],
                 questions: Mapping[str, dict] | Mapping[str, Mapping[str, dict]], *,
                 per_item: bool = False, in_flight: int | None = None,
                 errors: str = "raise") -> Iterator[tuple[str, dict]]:
        """`errors="yield"` hands back `(id, exception)` for an item that failed instead of
        raising, so a sweep can note it (`corpus.errors`) and carry on."""
        from concurrent.futures import FIRST_COMPLETED, wait

        ids: dict = {}

        def result(f):
            try:
                return f.result()
            except Exception as exc:  # noqa: BLE001
                if errors != "yield":
                    raise
                return ids[f], exc

        limit = in_flight or self.workers * 2
        pending = set()
        for cid, text in items:
            qs = dict(questions[cid] if per_item else questions)
            if not qs:
                continue
            f = self._pool.submit(_worker_ask, cid, text, qs, self.max_length)
            ids[f] = cid
            pending.add(f)
            if len(pending) >= limit:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for f in done:
                    yield result(f)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in done:
                yield result(f)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> "Pool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ------------------------------------------------------------------ results


def answers(result: dict) -> dict:
    """The verdict alone per question: P(true), the winning label, or the expected level."""
    out = {}
    for qid, a in result["answers"].items():
        t = a.get("type")
        out[qid] = a["noul"] if t == "noul" else a["choice"] if t == "choice" else a["score"]
    return out


def scale(answer: dict) -> float:
    """Any answer on one 0-1 axis: P(true), the choice's confidence, or score/(levels-1)."""
    t = answer.get("type")
    if t == "noul":
        return float(answer["noul"])
    if t == "choice":
        return float(answer["confidence"])
    levels = len(answer["probabilities"])
    return float(answer["score"]) / max(levels - 1, 1)


def estimate(texts: Iterable[str], questions: Mapping[str, dict], *, model=None,
             tokens_per_second: float = TOKENS_PER_SECOND) -> dict:
    """Tokens and wall-clock a sweep of `questions` over `texts` will take."""
    texts = list(texts)
    if not texts:
        return {"records": 0, "tokens": 0, "seconds": 0.0, "hours": 0.0}
    sample = texts if len(texts) <= 50 else texts[:: math.ceil(len(texts) / 50)]
    mean = sum(record_tokens(t, questions, model=model) for t in sample) / len(sample)
    tokens = mean * len(texts)
    seconds = tokens / tokens_per_second
    return {"records": len(texts), "tokens": int(tokens), "seconds": round(seconds),
            "hours": round(seconds / 3600, 2)}
