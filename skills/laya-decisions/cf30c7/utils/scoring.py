"""Score a question across a corpus of chunks, and spot the chunks that answer everything.

`laya.ask_chunks` gives you one answer per chunk per question. This turns a
document's worth of them into numbers you can rank on, and answers the two
questions that come up as soon as you do.

**Which number ranks a question?** Not its top chunk. Laya answers the spirit of
a question, so one broad enough to be about the corpus at all comes back strong
on every chunk of it — asking "could weather affect this?" of a construction
report gets a yes everywhere, and `max` says so while nothing has been located.
`summarise` reports **lift**, the best chunk minus the mean across all of them,
which only moves when a question separates part of the corpus from the rest.

**Which chunks are worth believing?** `evidence.core` answers half of that from
the text alone — is this prose, or a table, or a repeated form. The other half
needs the answers: a chunk that says yes to twenty-five unrelated questions has
not reported twenty-five findings, it has reported that it cannot be read.
`credulity` measures that per chunk, and `magnets` finds the chunks that are the
strongest evidence for far more questions than they can really hold. The two
filters catch different things and compose: on the document this was written
for, `core` dropped the cost tables and kept the site plan, credulity dropped
the site plan and kept the tables, and only their intersection took the floor
control from 0.852 down to 0.098.

Everything here returns data. Whether a lift of 0.4 is good, and how many peaks
make a chunk a magnet, are judgements for the caller; the defaults are a place
to start, not a rule.
"""

from statistics import mean
from typing import Any, Iterable

# A verdict at or above this counts as the question firing on that chunk.
FIRES_AT = 0.5

# A chunk that is the strongest evidence for more than this many questions is
# answering the shape of the question rather than its content.
MAGNET_PEAKS = 5


def strength(question: dict, answer: dict) -> float:
    """One answer as a number from 0 to 1, whatever the question's type.

    P(true) for a yes/no, the rubric level over its range for a score, and the
    winning label's confidence for a choice — how sure it is of the label it
    picked, not which label that was. Folding the three onto one scale is what
    lets a mixed question set be ranked at all.
    """
    kind = question["type"]
    if kind == "noul":
        return float(answer["verdict"])
    if kind == "score":
        levels = len(question["criteria"])
        return float(answer["verdict"]) / (levels - 1) if levels > 1 else 0.0
    if kind == "choice":
        return float(answer.get("confidence") or 0.0)
    raise ValueError(f"unknown question type {kind!r}")


def summarise(
    question: dict,
    answers: dict,
    *,
    at: float = FIRES_AT,
    pool: Iterable[str] | None = None,
) -> dict:
    """What one question did across a corpus: its best chunk, its spread, its lift.

    `answers` is keyed by chunk id, each value an answer from `laya.detail`.
    `pool` restricts the scoring to some of the chunks without discarding the
    rest, so a stored run keeps every answer and the filter stays a decision the
    caller can move. Returns `segments`, `max`, `mean`, `lift`, `fired`,
    `share` and `top_chunk`, plus `labels` for a choice.
    """
    if pool is not None:
        keep = set(pool)
        answers = {cid: a for cid, a in answers.items() if cid in keep}
    if not answers:
        return {"segments": 0}
    scored = {cid: strength(question, a) for cid, a in answers.items()}
    best = max(scored, key=lambda c: scored[c])
    values = list(scored.values())
    top, avg = scored[best], mean(values)
    fired = [c for c, v in scored.items() if v >= at]
    out = {
        "segments": len(values),
        "max": round(top, 4),
        "mean": round(avg, 4),
        "lift": round(top - avg, 4),
        "fired": len(fired),
        "share": round(len(fired) / len(values), 4),
        "top_chunk": best,
    }
    if question["type"] == "choice":
        counts: dict[str, int] = {}
        for answer in answers.values():
            counts[answer["verdict"]] = counts.get(answer["verdict"], 0) + 1
        out["labels"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return out


def ranked(
    question: dict,
    answers: dict,
    limit: int = 5,
    *,
    pool: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    """The strongest chunks for one question, `(chunk id, strength)`, strongest first."""
    if pool is not None:
        keep = set(pool)
        answers = {cid: a for cid, a in answers.items() if cid in keep}
    scored = [(cid, strength(question, a)) for cid, a in answers.items()]
    scored.sort(key=lambda pair: -pair[1])
    return scored[:limit]


def credulity(questions: dict, answers: dict) -> dict[str, float]:
    """Per chunk, its mean strength across `questions` — how readily it says yes.

    `answers` is keyed by question id then chunk id, as `summarise` takes one
    question's worth. Pass only questions whose subjects are unrelated: the
    measure works because a chunk cannot honestly be strong evidence for all of
    them, so a high score means the chunk is being read as a bag of words. Drop
    controls and any classifier questions before calling.
    """
    scores: dict[str, list[float]] = {}
    for qid, question in questions.items():
        for cid, answer in answers.get(qid, {}).items():
            scores.setdefault(cid, []).append(strength(question, answer))
    return {cid: mean(values) for cid, values in scores.items() if values}


def peaks(rounds: Iterable[tuple[dict, dict]], *, pool: Iterable[str] | None = None
          ) -> dict[str, int]:
    """How many questions, over every round given, have each chunk as their best.

    `rounds` is an iterable of `(questions, answers)` pairs — one per round, or
    one in total for a single pass. Counting across rounds rather than within
    one is what makes the result stable: a chunk that wins for a dozen different
    questions asked in different words is not winning on the wording.
    """
    counts: dict[str, int] = {}
    for questions, answers in rounds:
        for qid, question in questions.items():
            top = ranked(question, answers.get(qid, {}), 1, pool=pool)
            if top:
                counts[top[0][0]] = counts.get(top[0][0], 0) + 1
    return counts


def magnets(counts: dict[str, int], *, threshold: int = MAGNET_PEAKS) -> set[str]:
    """Chunk ids that are the best evidence for more than `threshold` questions.

    Takes `peaks`' output rather than recomputing it, so the counts can be
    reported beside the verdict and the threshold moved without rescoring.
    Look at the distribution before trusting the default: a real cut usually
    shows up as a gap, not a round number.
    """
    return {cid for cid, n in counts.items() if n > threshold}
