"""Measure what a Laya question scores against questions whose answer is known.

A P(true) has no scale of its own. This package's round 1 ranked
`billed_versus_observed` at 0.881 and called it the strongest criterion in the
document — on a chunk where *"does this text report the result of a political
election?"* scored 0.978. The number was real and it meant nothing, because
nothing said how high a question can get on that chunk without being true of it.

So ask some questions you already know the answer to, alongside the ones you
don't, and report every score net of theirs **on the same chunk**. Two kinds
earn their place:

- **controls** — trivially true or trivially false of any text. *"Does this text
  contain written words?"* finds a chunk that says yes to anything.
- **probes** — plausible for the corpus and absent from this document, checked
  by string search before being asked. *"Does this text mention a soils or
  geotechnical report?"* reached 0.854 on a construction report containing no
  such phrase, which is the level word overlap alone can reach.

Everything here returns numbers. What floor to demand, and whether a criterion
that clears it is worth acting on, stays with the caller.
"""

from __future__ import annotations

from collections.abc import Iterable


def floors(
    answers: dict, units: Iterable[str], questions: Iterable[str], *, exclude: str = ""
) -> dict:
    """Per unit, the highest verdict any of `questions` returned on it.

    `answers` is `{question_id: {unit_id: {"verdict": float, ...}}}` — the shape
    a `sweep` run stores. A unit no listed question was asked about gets 0.0.
    `exclude` drops one question, for scoring a control against the others.
    """
    wanted = [q for q in questions if q != exclude]
    out = {}
    for unit in units:
        scored = [
            answers[q][unit]["verdict"]
            for q in wanted
            if q in answers and unit in answers[q]
        ]
        out[unit] = max(scored) if scored else 0.0
    return out


def leave_one_out(answers: dict, units: Iterable[str], questions: Iterable[str]) -> dict:
    """`floors` once per question in `questions`, each computed without itself.

    A control scored against a floor it helps define reads at or below zero, which
    hides the one number it exists to report. Use `out[qid]` as the floor for
    `qid` and `floors(...)` for everything else.
    """
    units = list(units)
    return {q: floors(answers, units, questions, exclude=q) for q in questions}


def net(verdicts: dict, floor: dict) -> dict:
    """Per unit, a verdict minus that unit's floor. Negative means below the floor."""
    return {u: v - floor.get(u, 0.0) for u, v in verdicts.items()}


def verdicts(answers: dict, qid: str, units: Iterable[str] | None = None) -> dict:
    """`{unit_id: verdict}` for one question, optionally restricted to `units`."""
    per = {u: d["verdict"] for u, d in answers.get(qid, {}).items()}
    return per if units is None else {u: per[u] for u in units if u in per}


def underbudget(questions: dict, budget: int, state_budget) -> dict:
    """The questions that would leave fewer than `budget` tokens for the text.

    Laya's 512 tokens cover the whole sequence, and in a call asking several
    questions the most expensive one sets the text budget for all of them — so a
    single wordy question, a choice with twenty described options, silently
    shrinks the text every other question in the round gets to see. A corpus
    split to one round's budget then no longer fits the next round's, and the
    chunk hashes change, and no two rounds can be compared.

    Pass `laya.state_budget`. There is nothing to split here: `state_budget` over
    a set is the minimum over its members, so a set fits exactly when each of its
    questions fits. This returns `{id: tokens_left}` for the ones that do not, and
    the caller decides whether to reword them, drop them or re-split the corpus.
    """
    return {
        qid: state_budget({qid: q})
        for qid, q in questions.items()
        if state_budget({qid: q}) < budget
    }


def ceiling(answers: dict, units: Iterable[str], questions: Iterable[str]) -> tuple:
    """The highest any of `questions` gets net of the others: `(score, question_id)`.

    The top of the usable scale. A criterion scoring below this is not
    distinguishable from a question about something the document does not
    contain — which is the claim a reader of a ranked list will otherwise make
    for it.
    """
    units = list(units)
    loo = leave_one_out(answers, units, questions)
    best = []
    for q in questions:
        if q not in answers:
            continue
        scored = net(verdicts(answers, q, units), loo[q])
        if scored:
            best.append((max(scored.values()), q))
    return max(best) if best else (0.0, "")
