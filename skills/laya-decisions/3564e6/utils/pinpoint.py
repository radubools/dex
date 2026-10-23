"""Find the sentence of a chunk that carries a criterion, by asking.

A chunk sized to Laya's budget is a few hundred words. Knowing that a question
answered 0.95 on chunk 15 does not tell a reader *which line* of chunk 15, and
that is the line a report has to quote. Picking it by keyword overlap looks
right often enough to be dangerous: a criterion about a contingency picks a
sentence about site reconstruction that never mentions one.

So ask. `strongest` puts the same question to each sentence of the chunk, one
at a time, and hands back the sentence that answered highest together with the
number it answered — which is a second, finer measurement of the same thing,
not a heuristic. A criterion that answers 0.9 on the chunk and 0.9 on one of
its sentences is pinned to that sentence. One that answers 0.9 on the chunk
and 0.25 on every sentence in it is carried by the chunk as a whole, and a
reader deciding whether to act on it should know that.

    quotes = pinpoint.strongest(agent, chunk_text, {"permit": question})

**`cite.py` is the other half of this, and which one you want depends on
whether you already know the sentence.** `cite.holders` and `cite.window` take
a phrase the caller has written down and find it; they are how you check that
a criterion peaked where a person would have pointed. This module has no such
phrase and derives one, which is what you need when nobody has read the
document and written the phrases out — the same split as `sweep.binary_scores`
against `unlabelled`. Use `cite` when you have an oracle, this when you do not.

Everything here returns data and takes its thresholds as keyword arguments.
Nothing here decides what counts as pinned — that is the caller's bar to set,
and it should be: on one lender's report, 9 of 19 criteria that scored well on
a whole chunk had a sentence answering above 0.50, and the other 10 did not.
Both groups were worth reporting; they were not worth reporting as the same
thing.
"""

from __future__ import annotations

import re
from typing import Any, Pattern


def _sibling(name: str) -> Any:
    """Import a module sitting beside this one, package-relative or flat."""
    try:
        return __import__(f"{__package__}.{name}", fromlist=[name]) if __package__ \
            else __import__(name)
    except ImportError:
        return __import__(name)

#: A run of text shorter than this is layout debris rather than a sentence —
#: a stray phone number, a table cell, a heading the extractor stranded.
MIN_WORDS = 8

#: ...and one longer than this is usually two sentences the extractor ran
#: together, which makes a quote that is too long to be worth reading.
MAX_WORDS = 60

#: Split on sentence ends, but never on the full stop of an initial: a
#: document full of "Leonard R. Izbicki" otherwise yields quotes that stop
#: dead at "Leonard R." and read as though a line were dropped.
SENTENCE = re.compile(r"(?<=[.?!])(?<![A-Z]\.)\s+")


def sentences(
    text: str,
    *,
    min_words: int = MIN_WORDS,
    max_words: int = MAX_WORDS,
    drop: Pattern[str] | None = None,
) -> list[str]:
    """`text` as quotable sentences, debris and anything matching `drop` removed.

    `drop` is for the page furniture of a particular corpus — a running
    header, a letterhead, a footer. Those pass a word count and carry a date
    and an address, which is enough for a decision model to answer almost
    anything about them, so a caller that knows the pattern should pass it.
    """
    flat = " ".join(text.split())
    out = []
    for part in SENTENCE.split(flat):
        part = part.strip(" •-")
        if min_words <= len(part.split()) <= max_words and not (
                drop and drop.search(part)):
            out.append(part)
    return out


def rank(
    agent: Any,
    text: str,
    questions: dict,
    *,
    ask: Any = None,
    detail: Any = None,
    **kwargs: Any,
) -> dict[str, list[tuple[float, float, str]]]:
    """Every sentence of `text` scored by each question, strongest first.

    Returns `{question id: [(verdict, confidence, sentence), ...]}`. `ask` and
    `detail` default to `laya.ask` and `laya.detail`; they are arguments so
    this module can be exercised without the 850 MB checkpoint, and so a
    caller driving a different checkpoint is not locked out.
    """
    if ask is None or detail is None:
        laya = _sibling("laya")
        ask = ask or laya.ask
        detail = detail or laya.detail

    parts = sentences(text, **kwargs)
    scored: dict[str, list[tuple[float, float, str]]] = {q: [] for q in questions}
    for part in parts:
        for qid, answer in detail(ask(agent, part, questions)).items():
            scored[qid].append(
                (float(answer["verdict"]), answer.get("confidence", 0.0), part))
    for qid in scored:
        scored[qid].sort(key=lambda t: -t[0])
    return scored


def strongest(
    agent: Any,
    text: str,
    questions: dict,
    **kwargs: Any,
) -> dict[str, dict]:
    """The sentence of `text` each question answers highest on, with its numbers.

    One record per question: `quote`, the `verdict` and `confidence` it earned,
    how many sentences were tried, and the `runner_up` verdict — which is what
    shows whether the winning sentence won by a nose or outright. A question
    whose chunk yields no quotable sentence is absent rather than guessed at.
    """
    out = {}
    for qid, ranked in rank(agent, text, questions, **kwargs).items():
        if not ranked:
            continue
        verdict, confidence, sentence = ranked[0]
        out[qid] = {
            "quote": sentence,
            "verdict": round(verdict, 4),
            "confidence": round(confidence, 4),
            "sentences_tried": len(ranked),
            "runner_up": round(ranked[1][0], 4) if len(ranked) > 1 else None,
        }
    return out
