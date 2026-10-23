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
