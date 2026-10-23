"""Ask Jev typed questions — yes/no, multiple choice, rubric score — about a chunk of text.

Jev is a hosted System One model: one request turns a piece of text plus a set
of typed questions into calibrated probabilities. It generates no prose, so
there is no JSON to parse back and nothing to hallucinate. This module drives
`POST https://thejevai.com/v1/systemone` with the key in `JEV_API_KEY`.

It is the network sibling of the `laya-decisions` skill: the question builders,
`split`, `answers`, `detail`, `describe` and `scale` have the same names and
the same behaviour, and `split` at the same budget cuts a document into exactly
the same chunks, so a Jev run and a Laya run over one document line up
chunk-for-chunk. What differs is everything the network imposes.

What it adds over posting the JSON yourself, each point measured against the
live endpoint:

*   **A `User-Agent`.** Without one the endpoint's CDN answers `403` with
    `error code: 1010` before the API sees the request — no mention of the key,
    nothing in the docs. `_post` always sends one.
*   **The envelope.** The documented `{"model", "answers", "usage"}` is not what
    comes back. The body is `{"code", "message", "data": {"result": {...},
    "creditsUsed": n}}`, `result` has no `model`, and `elapsed` is `elapsedMs`.
    `ask` returns the unwrapped result with `credits` and `elapsed_ms` folded
    in, so callers never see the envelope.
*   **One request per state, however many questions.** Asking every question
    about a chunk at once is the point of the API and the default here. But the
    endpoint takes at most `MAX_QUESTIONS` (8) of them in a request — nine is
    `400 Questions must contain 1-8 items.`, measured at every size from 9 to
    256 — so `ask` cuts a larger set into groups of 8, asks each group about the
    *same* state, and merges the answers into one result. 255 is the cap on a
    single choice question's *options*, not on questions. `validate` catches the
    malformed sets that otherwise come back as an unhelpful `502 The decision
    provider rejected the request.` before anything is sent.
*   **Chunking to `CHUNK_TOKENS`.** The endpoint does not truncate — it read
    9,382 tokens without complaint — but the answer decays as the text around
    it grows: one fact at the end of the state scored P(true) 0.97, 0.91, 0.77,
    0.63 under 50, 400, 1,500 and 3,000 words of filler. Chunks keep each
    answer about a passage the model actually attends to, and keep every
    request inside one credit.
*   **Retries and concurrency.** `429`, `502` and `529` are retried with
    exponential backoff; `400`, `401` and `413` are not, because those are
    reproducible and yours to fix. `502` is in the retry list the hard way: the
    gateway returns it both for a malformed request and for the upstream model
    being unavailable, with the same message, and a request that answered in
    0.5 s one morning answered `502` to every attempt the next while `400` and
    `401` still came back correctly. Requests are ~0.55 s each and the endpoint
    took sixteen at once without a `429`, so `ask_chunks` runs `workers` in
    flight and still yields in document order.

Unlike Laya, the endpoint is **not deterministic**. Over three runs of the same
twenty-four chunks, P(true) moved by up to 0.06 and a rubric score by up to
0.11 — median movement 0.00 — and the chosen label never moved. So record each
run's numbers and compare them with a tolerance; `==` between two runs of the
same questions fails on most chunks and tells you nothing.

    client = jev.client()
    questions = {
        "refund": jev.yes_no("Does the customer ask for money back?"),
        "team": jev.choice("Who should handle this?", ["billing", "technical"]),
        "urgency": jev.score("How urgent is this?", ["not urgent", "soon", "critical"]),
    }
    for chunk, result in jev.ask_chunks(client, long_text, questions):
        print(jev.answers(result))

Only `ask` and anything built on it touches the network. `token_count` and
`split` need the tokenizer file alone (a 2 MB download, no key), so budgeting
and splitting a corpus costs nothing and can be done offline.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Iterator

#: The evaluation endpoint. One state and up to `MAX_QUESTIONS` per request.
URL = "https://thejevai.com/v1/systemone"

#: The flagship model alias. `usage` comes back stamped with the concrete
#: version it resolved to (`jev-1.13.0` at the time of writing).
MODEL = "jev-latest"

#: Tokens of text per chunk. Not a limit the API imposes — see the module
#: docstring — but the size at which an answer is still about the passage.
CHUNK_TOKENS = 512

#: Questions the endpoint accepts in one request. Nine is `400 Questions must
#: contain 1-8 items.`, and so is every size from 9 to 256 — measured, twice, a
#: day apart. `ask` groups a larger set to this size rather than refusing it.
MAX_QUESTIONS = 8

#: Options a single choice question may list. This is the 255 in the docs; it
#: has nothing to do with how many questions a request takes.
MAX_CHOICE_OPTIONS = 255

#: Requests in flight in `ask_chunks` / `ask_each`.
WORKERS = 8

#: Request bodies larger than this come back `413 The playground request is too
#: large.` Measured between 31,316 and 37,616 bytes of state; this leaves room.
MAX_BODY = 30_000

#: Statuses worth sending again. `502` is in here because the gateway returns it
#: both for a malformed request *and* for the upstream model being unavailable,
#: with the same message either way: a request that answered fine one morning
#: answered `502` to every attempt the next, while `400` and `401` still came
#: back correctly from the same gateway. `400`, `401` and `413` are never
#: retried — those are reproducible and yours to fix.
RETRY_STATUS = (429, 502, 529)

#: Attempts per request, and the first backoff in seconds (doubled each time).
RETRIES = 4
BACKOFF = 1.0

#: The tokenizer `token_count` counts with: plain ModernBERT, the same
#: vocabulary the `laya-decisions` skill uses. Verified to give identical
#: counts to `laya.token_count` on this project's reports. Jev's own tokenizer
#: is not published; it counted 7-23% more than this one on OCR'd report text,
#: so treat a chunk budget as approximate — it is a policy, not a hard limit.
TOKENIZER = "answerdotai/ModernBERT-base"

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")

_TOKENIZERS: dict[str, Any] = {}
_DONE = object()


class JevError(RuntimeError):
    """Anything the endpoint refused. Carries `status` and the server's `message`."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class AuthError(JevError):
    """401. The key in `JEV_API_KEY` is missing, malformed or revoked."""


class InvalidRequest(JevError):
    """400, 413 or 422 — the request itself is wrong and will stay wrong."""


class ProviderError(JevError):
    """502 `The decision provider rejected the request.`, after `RETRIES` attempts.

    Ambiguous by design on the gateway's side: the same status and message cover
    a malformed request and the upstream model being unavailable. If `validate`
    passed and a request that worked before now fails, it is the service, not
    you — wait and run again rather than rewriting the questions.
    """


class RateLimited(JevError):
    """429, still refused after `RETRIES` attempts."""


class Overloaded(JevError):
    """529, still refused after `RETRIES` attempts."""


class TooLong(ValueError):
    """Raised when a state is too large to post at all. `split` it first."""


class Client:
    """Key, model and HTTP settings for a run. Cheap; safe to share across threads.

    `jev.client()` builds one from `JEV_API_KEY`. Nothing is opened until the
    first `ask`, and each request makes its own connection, so one client can
    serve every worker in `ask_chunks`.
    """

    def __init__(
        self,
        key: str,
        *,
        model: str = MODEL,
        url: str = URL,
        timeout: float = 60.0,
        retries: int = RETRIES,
        user_agent: str = "jev-decisions/1.0",
    ) -> None:
        self.key = key
        self.model = model
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent


def client(key: str | None = None, **kwargs: Any) -> Client:
    """A `Client` from `JEV_API_KEY`, or from `key` if you pass one.

    Raises `AuthError` when the variable is unset rather than letting the first
    request fail four hundred chunks into a run.
    """
    key = key or os.environ.get("JEV_API_KEY", "")
    if not key:
        raise AuthError(401, "JEV_API_KEY is not set in the environment")
    return Client(key, **kwargs)


def yes_no(instructions: str, *, false: str = "", true: str = "") -> dict:
    """A yes/no question, answered with P(true). Jev names this type `noul`.

    `false` and `true` describe what each verdict means; leaving them empty
    uses Jev's own wording.
    """
    question = {"type": "noul", "instructions": instructions}
    if false or true:
        question["criteria"] = {"false": false, "true": true}
    return question


def choice(instructions: str, options: list[str] | dict) -> dict:
    """A multiple-choice question over `options`: labels alone, or {label: description}.

    Descriptions are rendered into the prompt as `label: description` and are
    usually worth the tokens — they are what the model matches the text
    against. Up to 255 options.
    """
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list) -> dict:
    """A rubric question over 2 to 10 ordered `levels`, answered with the expected level.

    The answer is continuous: a probability-weighted mean over `0 .. len(levels) - 1`,
    so three levels give a number between 0.0 and 2.0 rather than a bare index.
    """
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def validate(questions: dict) -> dict:
    """Check a question set question by question, returning it unchanged.

    Raises `ValueError` for an unknown type, a missing `instructions`, a choice
    without options or with more than `MAX_CHOICE_OPTIONS`, and a rubric outside
    2-10 levels. Worth doing locally: the endpoint answers a choice with no
    `criteria`, an unknown model and a missing state all with the same `502 The
    decision provider rejected the request.`

    A set larger than `MAX_QUESTIONS` passes — `ask` groups it rather than
    refusing it. Use `groups()` to see how it will be cut.
    """
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a non-empty dictionary keyed by question id")
    for qid, question in questions.items():
        if not isinstance(question, dict):
            raise ValueError(f"question {qid!r} is not a dictionary")
        kind = question.get("type")
        if kind not in ("noul", "choice", "score"):
            raise ValueError(f"question {qid!r} has unknown type {kind!r}")
        if not question.get("instructions"):
            raise ValueError(f"question {qid!r} has no instructions")
        criteria = question.get("criteria")
        if kind == "choice":
            if not criteria:
                raise ValueError(f"choice {qid!r} has no options")
            if len(criteria) > MAX_CHOICE_OPTIONS:
                raise ValueError(
                    f"choice {qid!r} has {len(criteria)} options; "
                    f"{MAX_CHOICE_OPTIONS} is the cap"
                )
        if kind == "score":
            if not isinstance(criteria, (list, tuple)) or not 2 <= len(criteria) <= 10:
                raise ValueError(
                    f"score {qid!r} needs an ordered list of 2-10 levels; the endpoint "
                    "accepts one level and answers 0.0 with confidence 1.0, which is "
                    "not an answer"
                )
    return questions


def groups(questions: dict, size: int = MAX_QUESTIONS) -> list[dict]:
    """A question set cut into request-sized groups, in the order it was written.

    One group is one request about one state. A set of eight or fewer is a
    single group, which is the case worth keeping fast: asking every question
    about a chunk together is both cheaper and the API's whole point.
    """
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValueError("size must be a positive number of questions")
    ids = list(questions)
    return [
        {qid: questions[qid] for qid in ids[start : start + size]}
        for start in range(0, len(ids), size)
    ]


def tokenizer(name: str = TOKENIZER) -> Any:
    """The tokenizer `token_count` counts with, downloaded once and cached per name.

    A local directory holding a `tokenizer.json` is read in place; anything else
    is a Hugging Face repo id and only that one file is fetched. No key, no
    weights, about 2 MB.
    """
    if name not in _TOKENIZERS:
        from tokenizers import Tokenizer

        path = os.path.join(name, "tokenizer.json")
        if not os.path.isfile(path):
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(name, "tokenizer.json")
        tok = Tokenizer.from_file(path)
        tok.no_truncation()
        tok.no_padding()
        _TOKENIZERS[name] = tok
    return _TOKENIZERS[name]


def token_count(text: str, model: str = TOKENIZER) -> int:
    """How many tokens `TOKENIZER` makes of `text`.

    An estimate of what Jev will charge for: on this project's OCR'd reports the
    endpoint's own `usage.input_tokens` ran 7-23% above this count. Since
    nothing is truncated the gap costs nothing but a slightly larger request.
    """
    return len(tokenizer(model).encode(text, add_special_tokens=False).ids)


def split(text: str, budget: int = CHUNK_TOKENS, model: str = TOKENIZER) -> list[str]:
    """Cut `text` into chunks of at most `budget` tokens, breaking at paragraphs then sentences.

    Chunks are packed greedily, so a short document comes back as one chunk.
    Raises `TooLong` if a single word exceeds the budget. Identical to
    `laya.split` given the same budget, so chunk ids from a Laya package still
    name the same passages here.
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


def ask(client: Client, state: Any, questions: dict) -> dict:
    """Answer every question in `questions` about one state, in as few requests as the endpoint allows.

    Eight or fewer questions is one request — the cheap, normal case, and the
    reason to build a question set rather than a loop. A larger set is cut into
    groups of `MAX_QUESTIONS` by `groups()` and asked group by group about the
    *same* state, because the endpoint refuses nine in a request; the answers
    are merged back into one result keyed by your question ids, and `usage`,
    `credits` and `elapsed_ms` are the totals over the groups it took.
    `requests` says how many that was.

    `state` is text, or the JSON object or array of strings the endpoint also
    accepts. Returns `{"answers", "usage", "credits", "elapsed_ms", "requests"}`,
    where each answer carries `confidence` and the full `probabilities` as well
    as its value. Raises `AuthError`, `InvalidRequest`, `RateLimited` or
    `Overloaded`; raises `TooLong` before sending if a body is over `MAX_BODY`.
    """
    validate(questions)
    merged: dict = {
        "answers": {},
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "credits": 0,
        "elapsed_ms": 0,
        "requests": 0,
    }
    for group in groups(questions):
        body = json.dumps(
            {"model": client.model, "state": state, "questions": group}
        ).encode()
        if len(body) > MAX_BODY:
            raise TooLong(
                f"request body is {len(body)} bytes; the endpoint answers 413 above "
                f"about {MAX_BODY}. Split the state first — see split() and "
                "ask_chunks()."
            )
        result = _post(client, body)["data"]
        merged["answers"].update(result["result"].get("answers", {}))
        usage = result["result"].get("usage") or {}
        merged["usage"]["input_tokens"] += usage.get("input_tokens", 0)
        merged["usage"]["output_tokens"] += usage.get("output_tokens", 0)
        merged["credits"] += result.get("creditsUsed") or 0
        merged["elapsed_ms"] += result["result"].get("elapsedMs") or 0
        merged["requests"] += 1
    missing = set(questions) - set(merged["answers"])
    if missing:
        raise JevError(
            0, f"the endpoint answered {len(merged['answers'])} of {len(questions)} "
            f"questions; missing {sorted(missing)}"
        )
    return merged


def ask_each(
    client: Client,
    chunks: Iterable[str],
    questions: dict,
    workers: int = WORKERS,
) -> Iterator[tuple[str, dict]]:
    """Answer `questions` about each of `chunks`, `workers` requests in flight, yielding in order.

    For chunks you already have — a Laya package's `chunks.json`, a page split,
    one row per file. Only `workers` chunks are ever outstanding and results are
    yielded as they line up, so a corpus of any length holds a fixed amount of
    memory. Jev sees no context beyond the chunk in hand.

    The unit in flight is a *chunk*, not a request: a chunk carrying more than
    `MAX_QUESTIONS` questions costs `ceil(len(questions) / MAX_QUESTIONS)`
    requests, run one after another inside its worker. That keeps concurrency
    at `workers` however large the question set grows.
    """
    validate(questions)
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError("workers must be a positive number of requests in flight")
    source = iter(chunks)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        for chunk in source:
            pending.append((chunk, pool.submit(ask, client, chunk, questions)))
            if len(pending) == workers:
                break
        while pending:
            chunk, future = pending.popleft()
            yield chunk, future.result()
            nxt = next(source, _DONE)
            if nxt is not _DONE:
                pending.append((nxt, pool.submit(ask, client, nxt, questions)))


def ask_chunks(
    client: Client,
    text: str,
    questions: dict,
    workers: int = WORKERS,
    budget: int = CHUNK_TOKENS,
) -> Iterator[tuple[str, dict]]:
    """Split `text` to `budget` tokens and answer `questions` about each chunk, in order.

    Yields `(chunk, result)` pairs so an answer can be traced back to the text
    it was given. This is a generator over a bounded window of requests: write
    each result out as it arrives rather than collecting the document, and wrap
    it in `list()` only when the whole document's results genuinely have to be
    live at once.
    """
    yield from ask_each(client, split(text, budget), questions, workers)


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


def describe(questions: dict) -> list[dict]:
    """Each question as a record — `id`, `type`, `prompt`, `options` — ready to serialise.

    For saving *what was asked* beside what came back. `options` is the
    question's `criteria`: the labels or {label: description} of a choice, the
    ordered levels of a score, and absent for a yes/no with Jev's own wording.
    """
    described = []
    for qid, question in questions.items():
        row = {
            "id": qid,
            "type": question["type"],
            "prompt": question["instructions"],
        }
        if "criteria" in question:
            row["options"] = question["criteria"]
        described.append(row)
    return described


def detail(result: dict) -> dict:
    """Each answer as its `answers()` verdict plus the whole record Jev returned.

    The one thing added is `probabilities` for a yes/no: the endpoint ships only
    the scalar P(true), so `noul` is the single type whose distribution a saved
    file would otherwise be missing. Everything else is passed through untouched.
    """
    verdicts = answers(result)
    detailed = {}
    for qid, answer in result["answers"].items():
        row = {"verdict": verdicts[qid], **answer}
        if answer["type"] == "noul":
            true = answer["noul"]
            row["probabilities"] = {"false": round(1.0 - true, 4), "true": true}
        detailed[qid] = row
    return detailed


def scale(question: dict, answer: dict) -> float:
    """One answer on a 0-1 scale, whatever the question's type.

    `answers()` hands back three incompatible scales — P(true) for a yes/no, a
    *label* for a choice, an expected level between 0 and `len(levels) - 1` for
    a score — because that is what each type means on its own. The moment a
    caller mixes types the scales collide: sorting a column of answers raises on
    the choice's string, and one threshold cannot be right for a 0-1 probability
    and a 0-2 rubric at once. Both of those are easy to write and silent when
    wrong; a rubric answer of 1.4 compared against 0.5 reads as a confident yes
    for every chunk in the corpus.

    So: a yes/no passes through, a choice becomes the probability it gave the
    label it picked, and a score is divided by its top level. `answer` is one
    entry from `detail()` — it needs the `probabilities` that adds.
    """
    verdict = answer["verdict"]
    kind = question.get("type")
    if kind == "score":
        levels = question.get("criteria") or []
        return float(verdict) / max(len(levels) - 1, 1)
    if isinstance(verdict, str):
        probabilities = answer.get("probabilities") or {}
        return float(probabilities.get(verdict, answer.get("confidence", 0.0)))
    return float(verdict)


def cost(results: Iterable[dict]) -> dict:
    """Credits, tokens, requests and chunks over a run's results, for a progress line or a log.

    Credits are charged per request and grow with its size: every request a
    `CHUNK_TOKENS` chunk produces was one credit, while a 3,000-word state was
    three. Within one request the question count is free — eight cost what one
    costs — so the cheapest sweep asks up to `MAX_QUESTIONS` about each chunk at
    a time. Past eight the groups are separate requests and the bill is
    `chunks × ceil(questions / MAX_QUESTIONS)` credits, which is the number to
    do in your head before starting a corpus.
    """
    totals = {
        "chunks": 0,
        "requests": 0,
        "credits": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    for result in results:
        totals["chunks"] += 1
        totals["requests"] += result.get("requests") or 1
        totals["credits"] += result.get("credits") or 0
        usage = result.get("usage") or {}
        totals["input_tokens"] += usage.get("input_tokens", 0)
        totals["output_tokens"] += usage.get("output_tokens", 0)
    return totals


def _post(client: Client, body: bytes) -> dict:
    """POST `body`, retrying `RETRY_STATUS` with exponential backoff, and raise the rest."""
    request = urllib.request.Request(
        client.url,
        data=body,
        headers={
            "Authorization": f"Bearer {client.key}",
            "Content-Type": "application/json",
            # Without this the CDN answers 403 `error code: 1010` and the API
            # never sees the request.
            "User-Agent": client.user_agent,
            "Accept": "application/json",
        },
    )
    delay = BACKOFF
    for attempt in range(1, max(1, client.retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=client.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            status = error.code
            message = _message(error.read())
            if status in RETRY_STATUS and attempt < client.retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise _error(status, message) from None
        except urllib.error.URLError as error:
            if attempt < client.retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise JevError(0, f"could not reach {client.url}: {error.reason}") from None
    raise JevError(0, "unreachable")  # pragma: no cover


def _message(raw: bytes) -> str:
    """The server's `message` if the body is the JSON envelope, else the body itself."""
    text = raw.decode("utf-8", "replace").strip()
    try:
        return json.loads(text).get("message") or text
    except (ValueError, AttributeError):
        return text


def _error(status: int, message: str) -> JevError:
    if status == 401:
        return AuthError(status, message)
    if status == 429:
        return RateLimited(status, message)
    if status == 502:
        return ProviderError(status, message)
    if status == 529:
        return Overloaded(status, message)
    return InvalidRequest(status, message)


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
                    f"{budget}-token budget"
                )
            run = word
        if run:
            yield run
