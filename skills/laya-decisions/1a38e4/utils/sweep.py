"""Store chunks once by hash, then iterate versioned question sets against them.

Asking a model the same questions about the same corpus twice is cheap to get
wrong: the text gets copied into every result file, a run that only re-asks two
of fifteen questions loses the other thirteen, and nothing says which answers
came from which wording. This keeps the text in one content-addressed file and
each run in its own, holding question definitions, summary metrics, and answers
keyed by chunk hash.

    chunks = {sweep.chunk_id(t): {"text": t, "tokens": n} for t in texts}
    sweep.save_chunks(path, chunks, document="report.pdf")

    run = sweep.build_run("v3", questions, answers, metrics=m, previous=prior)
    sweep.save_run(runs / "v3.json", run)

A run that re-asks only some questions carries the rest forward from
`previous`, each marked with the version it was actually last asked in, so
every run file is a complete picture of the question set at that point.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Long enough that a collision across a corpus is not a practical concern,
# short enough to read in a diff.
ID_LENGTH = 12


def chunk_id(text: str, length: int = ID_LENGTH) -> str:
    """A stable short hash of `text`, used as its key in the chunk store."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def save_chunks(path: Path | str, chunks: dict, **meta: Any) -> dict:
    """Write the chunk store — `{id: {...}}` plus whatever `meta` you pass."""
    store = {"meta": meta, "chunks": chunks}
    Path(path).write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n")
    return store


def load_chunks(path: Path | str) -> dict:
    """Read a chunk store written by `save_chunks`."""
    return json.loads(Path(path).read_text())


def build_run(
    version: str,
    questions: dict,
    answers: dict,
    *,
    metrics: dict | None = None,
    previous: dict | None = None,
    chunks_file: str = "chunks.json",
    note: str = "",
) -> dict:
    """One run: what was asked, what came back, and what carried over unchanged.

    `questions` and `answers` are keyed by question id; `answers[qid]` is keyed
    by chunk id. Every question in `previous` that this run did not ask is
    copied across with `last_run` set to the version that did ask it, so the
    run file describes the whole question set rather than only the part that
    moved.
    """
    metrics = metrics or {}
    out_questions, out_metrics, out_answers = {}, {}, {}

    for qid, definition in questions.items():
        out_questions[qid] = {**definition, "last_run": version}
        if qid in metrics:
            out_metrics[qid] = metrics[qid]
        if qid in answers:
            out_answers[qid] = answers[qid]

    if previous:
        for qid, definition in previous.get("questions", {}).items():
            if qid in out_questions:
                continue
            # Whatever version last actually asked it, not this one.
            out_questions[qid] = {
                **definition,
                "last_run": definition.get("last_run", previous.get("version")),
            }
            if qid in previous.get("metrics", {}):
                out_metrics[qid] = previous["metrics"][qid]
            if qid in previous.get("answers", {}):
                out_answers[qid] = previous["answers"][qid]

    return {
        "version": version,
        "note": note,
        "chunks_file": chunks_file,
        "asked_this_run": sorted(questions),
        "carried_forward": sorted(set(out_questions) - set(questions)),
        "questions": out_questions,
        "metrics": out_metrics,
        "answers": out_answers,
    }


def save_run(path: Path | str, run: dict) -> dict:
    """Write one run file."""
    Path(path).write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    return run


def load_run(path: Path | str) -> dict:
    """Read a run file written by `save_run`."""
    return json.loads(Path(path).read_text())


def missing_chunks(run: dict, chunks: dict) -> dict:
    """Chunk ids a run answers that the store does not hold, per question.

    An empty dict means every foreign key resolves.
    """
    known = set(chunks["chunks"])
    gaps = {}
    for qid, by_chunk in run.get("answers", {}).items():
        unknown = sorted(set(by_chunk) - known)
        if unknown:
            gaps[qid] = unknown
    return gaps


def collapsed(text: str) -> str:
    """`text` with every run of whitespace reduced to one space, and trimmed."""
    return re.sub(r"\s+", " ", text).strip()


def locate(phrase: str, chunks: dict, order: list | None = None) -> list:
    """Ids of the chunks containing `phrase`, matched on collapsed whitespace.

    For pointing at the chunk that states a known fact — so a criterion can be
    read where its evidence is rather than where its probability happens to
    peak. `unlabelled.polarity` takes `ids=`, so the two compose:
    `polarity(claim, denial, ids=locate(phrase, chunks))`.

    **Match on collapsed whitespace, not with `in` or `str.index`.**
    `laya.split` normalises whitespace as it segments, so a chunk is not a
    literal substring of the text it came from: a phrase that spanned a line
    break in the source is one space in the chunk, and a plain containment test
    misses it silently. Seven of twenty-nine anchor phrases on one lender's
    report failed that way — all of them present, none of them found.

    `chunks` is a store's inner mapping, `load_chunks(path)["chunks"]`; `order`
    lists the ids to search and fixes the order of the result, defaulting to
    the store's own.

    Returns every match, which may be none. A report repeats itself — one
    sentence about a start date conditional on a neighbour's approval appeared
    in three chunks — so whether to average across the matches, take the first,
    or treat an empty result as an error is the caller's decision, and the last
    of those is worth making deliberately: a mistyped phrase and a fact the
    document does not contain look identical here.
    """
    needle = collapsed(phrase)
    return [
        cid
        for cid in (order if order is not None else chunks)
        if cid in chunks and needle in collapsed(chunks[cid]["text"])
    ]


def summarise(verdicts: dict, ids: Iterable | None = None, *, at: float = 0.5) -> dict:
    """Best, runner-up, mean, lift and hit count for one question's answers over `ids`.

    `verdicts` is `{chunk_id: P(true)}` — what a round of `ask`/`ask_chunks`
    produced for one yes/no question. `ids` restricts it to a subset of the
    chunks; None means all of them. `at` is the threshold a hit counts at.

    This is the counterpart to `binary_scores` for when there is **no oracle**.
    `binary_scores` needs labels somebody supplied, and on an arbitrary
    document nobody has any; these are the numbers readable off the answers
    alone.

    `lift` — best minus mean — is the one worth having. A question that
    answers `yes` to every paragraph has a high `best` and no `lift`, so a
    ranking by `best` puts it at the top while it distinguishes nothing.

    Passing `ids` is how a caller compares one group of chunks against another
    — prose against tables, say, or a document's body against its appendices.
    An empty subset returns `n: 0` with None scores rather than raising, so
    several groups can be summarised without checking each one first.
    """
    values = ([verdicts[i] for i in ids if i in verdicts] if ids is not None
              else list(verdicts.values()))
    n = len(values)
    if not n:
        return {"best": None, "runner_up": None, "mean": None, "lift": None,
                "hits": 0, "n": 0}
    ranked = sorted(values, reverse=True)
    mean = sum(values) / n
    return {
        "best": round(ranked[0], 4),
        "runner_up": round(ranked[1], 4) if n > 1 else None,
        "mean": round(mean, 4),
        "lift": round(ranked[0] - mean, 4),
        "hits": sum(1 for v in values if v >= at),
        "n": n,
    }


def binary_scores(truth: dict, verdicts: dict, *, at: float = 0.5) -> dict:
    """Recall, specificity, Youden's J and agreement for one yes/no question.

    `truth` and `verdicts` are both keyed by chunk id — the evidence actually
    in the text, and what the model said. J (recall + specificity - 1) is the
    number worth optimising: agreement flatters a question that never fires,
    and mean-probability separation flatters one that fires at everything.
    """
    ids = [i for i in truth if i in verdicts]
    tp = fp = fn = tn = 0
    for i in ids:
        said = verdicts[i] > at if isinstance(verdicts[i], (int, float)) else bool(verdicts[i])
        if truth[i] and said:
            tp += 1
        elif truth[i]:
            fn += 1
        elif said:
            fp += 1
        else:
            tn += 1
    recall = tp / (tp + fn) if tp + fn else float("nan")
    specificity = tn / (tn + fp) if tn + fp else float("nan")
    return {
        "n": len(ids),
        "in_text": tp + fn,
        "fires": tp + fp,
        "recall": recall,
        "specificity": specificity,
        "J": recall + specificity - 1,
        "agreement": (tp + tn) / len(ids) if ids else float("nan"),
    }


def convergence(
    history: dict, *, min_gain: float = 0.02, patience: int = 2
) -> dict:
    """Per question: its best version, and whether iterating has stopped paying.

    `history` maps version -> {question id: score}, in the order the versions
    ran. A question counts as converged once `patience` later versions have all
    failed to beat its best score by more than `min_gain`. Both are defaults,
    not rules — pass your own.
    """
    order = list(history)
    out = {}
    for qid in {q for scores in history.values() for q in scores}:
        # NaN never equals itself; that is how an unscored question is skipped.
        seen = [(v, history[v][qid]) for v in order
                if qid in history[v] and history[v][qid] == history[v][qid]]
        if not seen:
            continue
        # A later version only takes the crown by beating the standing best by
        # more than min_gain. Plain argmax would let a 0.005 wobble reset the
        # clock, and then nothing ever converges.
        best_v, best = seen[0]
        for version, value in seen[1:]:
            if value > best + min_gain:
                best_v, best = version, value
        after = len(seen) - 1 - [v for v, _ in seen].index(best_v)
        out[qid] = {
            "best_version": best_v,
            "best": best,
            "latest": seen[-1][1],
            "rounds_since_best": after,
            "converged": after >= patience,
        }
    return out
