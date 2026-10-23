"""Score a Laya question set over one document when there is no oracle.

`sweep.binary_scores` needs a truth set.  Often there is not one: nobody has
labelled which paragraphs of a report imply the thing you are asking about,
and labelling them is the work you were trying to avoid.  What the answers can
still tell you is how a criterion is failing, and the two failures are visible
without any labels at all.

**It never fires.**  The strongest chunk in the document sits at 0.2.  The
document does not support the criterion, whatever the mean says.

**It fires everywhere.**  Every chunk comes back at 0.9, the cover page and
the number grids included.  That is a question about the English language, or
about the genre, not about this document — and on a corpus of forms it is the
*common* failure, because a loose question scores 1.00 on the boilerplate that
makes up most of every page.

`numbers` measures both at once, `shape` labels the result, and `score_round`
applies them to a whole question set.

The third function is the one worth reading the module for.  **A high
probability from a decision model means "this passage is about this topic",
not "the claim is true."**  A control question asking whether a building was
more than one storey scored 0.89 — on the very paragraph that called it
single-storey.  The model found the passage that settles the question and then
answered it the wrong way round, and every ranking built on peak alone was
measuring topicality.  `polarity` is the test: ask a criterion beside its flat
denial and subtract.  A claim the model is reading separates; one it is only
locating does not.

Calibrate it.  Ask a pair of questions that genuinely contradict each other
about the same text, and whatever *they* separate by is the most this model
can do on this document — nothing below that number means anything.  On one
lender's construction report the contradictory pair separated by 0.08, which
cut twenty-one candidate criteria to the thirteen the model was actually
reading, and the pattern in which ones survived was the useful part: claims
about something the text puts in words separated, claims requiring an
inference about the subject did not.

Everything here returns data.  Which support is high enough to keep, and which
polarity clears your bar, are the caller's decisions; the thresholds are
keyword defaults so that moving one does not mean editing this file.
"""

from __future__ import annotations

from statistics import mean, median
from typing import Any

FIRE = 0.5  # P(true) at or above which a yes/no answer counts as "fired"


def percentile(values: list[float], q: float) -> float:
    """The `q`-th percentile of `values`, nearest-rank."""
    ordered = sorted(values)
    i = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[i]


def numbers(values: list[float], *, at: float = FIRE) -> dict[str, Any]:
    """Peak, spread, support and lift for one yes/no criterion's answers.

    `support` is `peak * (1 - spread)` — evidence and localisation at once. A
    question answering 0.95 on two chunks of thirty-nine scores 0.90; the same
    0.95 on all thirty-nine scores 0.00. It is a ranking device for choosing
    the next round's question set, not a probability of anything.

    `lift` is `peak` minus the 75th percentile, and it is what `support`
    cannot express. A criterion may answer 0.44 on one chunk and 0.15 or less
    on every other: unimpressive in absolute terms, but the model put it on
    exactly one chunk, and that is worth reading before throwing it away. Low
    peak with low lift is noise; low peak with high lift is a weak answer in
    the right place.
    """
    if not values:
        return {"chunks": 0, "peak": 0.0, "spread": 0.0, "support": 0.0, "lift": 0.0}
    fired = [v for v in values if v >= at]
    peak = max(values)
    spread = len(fired) / len(values)
    return {
        "chunks": len(values),
        "peak": round(peak, 4),
        "mean": round(mean(values), 4),
        "median": round(median(values), 4),
        "p75": round(percentile(values, 0.75), 4),
        "floor": round(min(values), 4),
        "fired": len(fired),
        "spread": round(spread, 4),
        "support": round(peak * (1.0 - spread), 4),
        "lift": round(peak - percentile(values, 0.75), 4),
        "at": at,
    }


def shape(
    scored: dict, *, peak: float = 0.70, lift: float = 0.30, spread: float = 0.45
) -> str:
    """Label a scored yes/no criterion by the shape of its answers.

    ``localised``  a strong peak on a few chunks — the useful case
    ``pervasive``  a strong peak nearly everywhere; either true of the whole
                   document or true of the genre, and those need telling apart
    ``faint``      a weak peak concentrated on one chunk: worth a look
    ``absent``     weak and flat

    Anything that is not a `noul` question comes back ``n/a``.
    """
    if scored.get("type") != "noul":
        return "n/a"
    if scored["peak"] >= peak:
        return "pervasive" if scored["spread"] >= spread else "localised"
    return "faint" if scored["lift"] >= lift else "absent"


def categorical(verdicts: list[Any], confidences: list[float]) -> dict[str, Any]:
    """How a choice or score question came out, across a document's chunks.

    A choice question answering the same label on every chunk is as
    uninformative as a yes/no firing on every chunk, so the dominant label's
    share is reported for the same reason `spread` is. A score question gets
    peak, mean and median of its levels instead.
    """
    if not verdicts:
        return {"chunks": 0}
    numeric = all(isinstance(v, (int, float)) for v in verdicts)
    out: dict[str, Any] = {
        "chunks": len(verdicts),
        "mean_confidence": round(mean(confidences), 4) if confidences else None,
    }
    if numeric:
        out |= {
            "peak": round(max(verdicts), 4),
            "mean": round(mean(verdicts), 4),
            "median": round(median(verdicts), 4),
        }
    else:
        counts: dict[str, int] = {}
        for v in verdicts:
            counts[str(v)] = counts.get(str(v), 0) + 1
        top = max(counts.items(), key=lambda kv: kv[1])
        out |= {
            "labels": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "dominant": top[0],
            "dominant_share": round(top[1] / len(verdicts), 4),
        }
    return out


def score_round(
    questions: dict,
    answers: dict,
    *,
    core: list[str] | None = None,
    form: list[str] | None = None,
    at: float = FIRE,
) -> dict[str, dict]:
    """Score a whole question set from `{qid: {chunk_id: laya.detail row}}`.

    `core` restricts the score to the chunks that count as evidence — the
    prose, say, rather than the tables; `utils/evidence.py` picks them.  `form`
    names the rest, whose peak comes back as `form_peak` beside each score, so
    a criterion firing on the shape of a table stays visible instead of being
    silently dropped.  Pass neither and every chunk is scored together.
    """
    out = {}
    for qid, question in questions.items():
        per_chunk = answers.get(qid, {})
        ids = [c for c in (core or per_chunk) if c in per_chunk]
        verdicts = [per_chunk[c]["verdict"] for c in ids]
        confs = [
            per_chunk[c].get("confidence")
            for c in ids
            if isinstance(per_chunk[c].get("confidence"), (int, float))
        ]
        if question["type"] == "noul":
            out[qid] = numbers([float(v) for v in verdicts], at=at)
            if confs:
                out[qid]["mean_confidence"] = round(mean(confs), 4)
            if form:
                other = [float(per_chunk[c]["verdict"]) for c in form if c in per_chunk]
                if other:
                    out[qid]["form_peak"] = round(max(other), 4)
                    out[qid]["form_spread"] = round(
                        len([v for v in other if v >= at]) / len(other), 4
                    )
        else:
            out[qid] = categorical(verdicts, confs)
        out[qid]["type"] = question["type"]
        out[qid]["shape"] = shape(out[qid])
    return out


def polarity(claim: dict, denial: dict, ids: list[str] | None = None) -> dict[str, Any]:
    """How far a criterion separates from its own denial, on its peak chunk.

    `claim` and `denial` are one question's answers keyed by chunk id, as
    `laya.detail` returns them; `ids` restricts the comparison to a subset.

    Ask a criterion together with its flat denial — worded as closely to it as
    English allows — and subtract on the chunk where the *claim* peaks. A
    criterion the model is genuinely reading answers the claim high and the
    denial low. One it is only locating by topic answers both high and lands
    near zero.

    Calibrate before believing any of it: run a pair of questions that
    contradict each other outright over the same text, and treat whatever they
    separate by as the floor.

    Returns the chunk it peaked on, both probabilities there, the difference,
    and the same difference averaged over every chunk — which catches a pair
    that separated on one chunk by luck.
    """
    keys = [c for c in (ids or claim) if c in claim and c in denial]
    if not keys:
        return {
            "chunk": None, "claim": 0.0, "denial": 0.0,
            "polarity": 0.0, "polarity_mean": 0.0, "chunks": 0,
        }
    at = max(keys, key=lambda c: float(claim[c]["verdict"]))
    here, there = float(claim[at]["verdict"]), float(denial[at]["verdict"])
    return {
        "chunk": at,
        "claim": round(here, 4),
        "denial": round(there, 4),
        "polarity": round(here - there, 4),
        "polarity_mean": round(
            sum(
                float(claim[c]["verdict"]) - float(denial[c]["verdict"])
                for c in keys
            )
            / len(keys),
            4,
        ),
        "chunks": len(keys),
    }


def polarities(
    pairs: dict, answers: dict, ids: list[str] | None = None
) -> dict[str, dict]:
    """`polarity` for every claim/denial pair both halves of which were answered.

    `pairs` maps a claim's question id to its denial's; `answers` is
    `{qid: {chunk id: laya.detail row}}`, and `ids` restricts the comparison
    the same way `polarity`'s does.

    **Pass only the answers the round actually executed.**  A pair with a half
    missing is skipped rather than guessed at, which is the point: polarity
    compares two questions on identical chunks, so a denial answered in some
    earlier round against some other question set is not that comparison.  A
    run file that carries unasked criteria forward holds both halves of pairs
    the round never ran, and handing the whole file to this function invents
    separations nobody measured.
    """
    return {
        claim: polarity(answers[claim], answers[denial], ids=ids)
        for claim, denial in pairs.items()
        if claim in answers and denial in answers
    }


def top_chunks(answers_for_q: dict, n: int = 3) -> list[tuple[str, float]]:
    """The `n` chunks that answered one yes/no criterion most strongly."""
    pairs = [
        (cid, float(d["verdict"]))
        for cid, d in answers_for_q.items()
        if isinstance(d["verdict"], (int, float))
    ]
    return sorted(pairs, key=lambda kv: -kv[1])[:n]
