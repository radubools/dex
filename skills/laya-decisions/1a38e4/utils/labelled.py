"""Score a Laya question set when some answers are already known — controls, or an oracle.

The mirror of `unlabelled.py`, which scores a round when nothing about the
document is known. These two functions are for the cases where something is, and
both turn out to be cheap to arrange:

* **`control_scores`** — you have questions whose answer is *no everywhere*. A
  proposition the document verifiably does not contain costs one line to write and
  makes every yes a counted false positive, so specificity falls out over the whole
  set at once, with no corpus to label.
* **`oracle_scores`** — you know, per question, which chunks do evidence it, and
  nothing about the rest. That is what reading a document gives you, and it yields
  recall; pair it with the specificity above for Youden's J.

Neither is `sweep.binary_scores`, which needs a full per-chunk truth column for one
question. Here the truth is uniformly false across many questions, or known only on
a handful of chunks per question — and in the second case a chunk not listed is
*unlabelled*, not negative, which is why recall and specificity come from different
functions and the caller adds them rather than one function returning J.

Why bother, when `unlabelled.shape` and `evidence.sink_scores` already flag a
question that is reading nothing: because they cannot tell you the rate. On a
47-page lender's construction analysis, six controls written in the document's own
register — delivered steel, an overdue submittal, a change order for unforeseen
excavation, a failed inspection, a request for extension of time, the fire marshal
rejecting sprinkler drawings, every one of them absent from all 47 pages — measured
specificity 0.899, and **four of the six outscored two thirds of the real criteria**
on every separation and margin statistic tried against them. Nothing that judges a
question by its own answers alone surfaces that, and it is the difference between a
filter a reader confirms and a ranker whose top hit is trusted.

Two things worth knowing before relying on either.

**Controls have to look like the document.** An earlier set on the same report asked
about asbestos, a strike, litigation and insolvency, and measured specificity 0.964
— which read as good news and was the controls failing. Nothing about asbestos
resembles a page of conduit and switchgear, so they never tested the passages that
were actually producing false positives. A control in the corpus's own register
dropped the figure by six points and found them.

**Recall wants every passage that evidences a question, not one of them.** A report
states the same fact in more than one place, and crediting a question only for the
passage you happened to note first understates it: on the document above, recall was
0.854 pinning each criterion to a single passage and 0.958 allowing the several it
really had. `positives` takes a collection per question for that reason.

Both return data. Thresholds are keyword arguments with defaults, no file is read,
and neither decides whether a question is good enough — that judgement belongs to
the caller, as it does everywhere else in this skill.
"""

from statistics import mean
from typing import Any, Iterable, Mapping

#: A yes/no verdict at or above this counts as the model having said yes.
AT = 0.5


def _verdict(answer: Any) -> float | None:
    """A verdict out of either a bare number or an answer record."""
    if isinstance(answer, bool):
        return float(answer)
    if isinstance(answer, (int, float)):
        return float(answer)
    if isinstance(answer, Mapping):
        value = answer.get("verdict")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def control_scores(
    answers: Mapping[str, Mapping[str, Any]],
    controls: Iterable[str],
    *,
    at: float = AT,
) -> dict:
    """Specificity and per-chunk credulity from controls known to be false everywhere.

    `answers` maps question id to {chunk id: verdict or answer record}; `controls`
    names the questions whose correct answer is no on every chunk. Every verdict at
    or above `at` is therefore a false positive.

    Returns `specificity` over all control decisions, the same per control, and
    per chunk the mean and maximum control verdict — a chunk's `credulity`, which
    says how readily that passage agrees with a proposition regardless of its
    truth. Raises KeyError if a named control is not in `answers`.
    """
    named = list(controls)
    for qid in named:
        if qid not in answers:
            raise KeyError(f"control {qid!r} is not in answers")

    per_control, false_positives, decisions = {}, 0, 0
    for qid in named:
        values = [v for v in map(_verdict, answers[qid].values()) if v is not None]
        hits = sum(1 for v in values if v >= at)
        false_positives += hits
        decisions += len(values)
        per_control[qid] = {
            "n": len(values),
            "false_positives": hits,
            "specificity": 1 - hits / len(values) if values else None,
            "mean": mean(values) if values else None,
            "max": max(values) if values else None,
        }

    chunk_ids: set[str] = set()
    for qid in named:
        chunk_ids.update(answers[qid])

    per_chunk = {}
    for cid in chunk_ids:
        values = [
            v
            for v in (_verdict(answers[q].get(cid)) for q in named)
            if v is not None
        ]
        if not values:
            continue
        per_chunk[cid] = {
            "credulity": mean(values),
            "max": max(values),
            "false_positives": sum(1 for v in values if v >= at),
            "which": [
                q
                for q in named
                if (_verdict(answers[q].get(cid)) or 0.0) >= at
            ],
        }

    return {
        "controls": named,
        "at": at,
        "decisions": decisions,
        "false_positives": false_positives,
        "specificity": 1 - false_positives / decisions if decisions else None,
        "per_control": per_control,
        "per_chunk": per_chunk,
    }


def oracle_scores(
    answers: Mapping[str, Mapping[str, Any]],
    positives: Mapping[str, Iterable[str]],
    *,
    at: float = AT,
) -> dict:
    """Recall and retrieval precision@1 against known-positive chunks per question.

    `positives` maps question id to the chunks that genuinely evidence it; chunks
    not listed are unlabelled, not negative, so nothing here computes specificity
    — pair this with `control_scores` for that, and add the two rates minus one for
    Youden's J.

    Two different questions get answered, and they are the point of having both:

    * **recall** — did the question reach `at` on any chunk that evidences it?
      That is *does this passage say X?*, which is what a decision model is for.
    * **precision@1** — is the question's highest-scoring chunk overall one that
      evidences it? That is *which passage says X?*, which is retrieval, and a
      model can be good at the first and poor at the second.

    Per question it also returns the best verdict among the evidencing chunks and
    where the global argmax landed, so a caller can go and read the passage.
    """
    per_question, fired, correct_argmax, scored = {}, 0, 0, 0
    for qid, wanted in positives.items():
        if qid not in answers:
            raise KeyError(f"question {qid!r} is not in answers")
        chunks = answers[qid]
        evidencing = [c for c in wanted if c in chunks]
        if not evidencing:
            per_question[qid] = {"scored": False, "reason": "no evidencing chunk present"}
            continue

        at_evidence = {c: _verdict(chunks[c]) for c in evidencing}
        at_evidence = {c: v for c, v in at_evidence.items() if v is not None}
        if not at_evidence:
            per_question[qid] = {"scored": False, "reason": "no numeric verdict"}
            continue

        best_chunk = max(at_evidence, key=at_evidence.get)
        best = at_evidence[best_chunk]
        everything = {
            c: v for c, v in ((c, _verdict(a)) for c, a in chunks.items()) if v is not None
        }
        argmax = max(everything, key=everything.get) if everything else None

        scored += 1
        hit = best >= at
        on_target = argmax in set(evidencing)
        fired += hit
        correct_argmax += on_target
        per_question[qid] = {
            "scored": True,
            "verdict_at_evidence": best,
            "evidence_chunk": best_chunk,
            "fired": hit,
            "argmax_chunk": argmax,
            "argmax_verdict": everything.get(argmax),
            "argmax_is_evidence": on_target,
        }

    return {
        "at": at,
        "scored": scored,
        "fired": fired,
        "recall": fired / scored if scored else None,
        "argmax_is_evidence": correct_argmax,
        "precision_at_1": correct_argmax / scored if scored else None,
        "per_question": per_question,
    }
