"""Export one task's scored criteria out of the experiments tables into a file the criteria-dashboard widget opens.

A widget runs in a sandboxed frame with an opaque origin: no network, no
database, no session. It gets the bytes of the file it was opened on and
nothing else. So a viewer over `experiments.state` cannot query
`experiments.state` — something has to read the tables and write a file first,
and this is that something.

    from utils import dashboard, experiments

    with experiments.connect() as conn:
        dashboard.write(conn, "change-order-risk-criteria-doc-21-3/run_round.py",
                        "doc21-3.criteria.json", chunks="chunks.json")

The file it writes is one **task's** view: every criterion that task scored,
each one's latest run, the metrics that run recorded, and the per-chunk verdict
behind them. `dashboard.tasks(conn)` lists what there is to export.

Two things this module does **not** do:

*   **It does not compute a metric.** Whatever `history.metrics` holds is copied
    across verbatim. There is no ground truth anywhere in these tables — no gold
    answer per chunk — so an accuracy figure cannot be derived here, and one
    invented from the verdicts would read like a measurement. The widget shows
    the recorded numbers and says which run recorded them.
*   **It does not hold the chunk text.** The tables key every answer by a
    12-hex chunk id and store no text; the text lives in the package's
    `chunks.json`. Pass it as `chunks=` and the export embeds it once, shared by
    every criterion. Leave it out and the listing shows ids, which is legible to
    nobody — so pass it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

#: The two kinds a decision model writes. `laya_criterion` comes from
#: `ledger.py`, `jev_criterion` from `jev_ledger.py`; one criterion name scored
#: by both models is two rows, told apart only by this column.
KINDS = ("laya_criterion", "jev_criterion")

#: Which model wrote a row, from its kind. `detail["model"]` overrides it when
#: the entry carries one — Jev returns no version, so what is stored there is
#: the alias the request used, and that is more specific than "jev".
MODEL_OF_KIND = {"laya_criterion": "laya", "jev_criterion": "jev"}

#: The fraction of P(true) a chunk must reach to be listed. The widget's slider
#: starts here and the number is written into the file so the export says what
#: it was exported for.
DEFAULT_THRESHOLD = 0.30

#: What the widget reads to know this is one of its files rather than some other
#: JSON that happens to end `.criteria.json`.
FORMAT = "experiments.criteria"
FORMAT_VERSION = 1


def _experiments() -> Any:
    """The `experiments` module beside this one, however this one was imported."""
    try:
        return __import__(f"{__package__}.experiments", fromlist=["experiments"]) \
            if __package__ else __import__("experiments")
    except ImportError:
        return __import__("experiments")


def tasks(conn: Any, *, kinds: Iterable[str] = KINDS, schema: str | None = None) -> list[dict]:
    """Every task that has scored a criterion, most criteria first.

    `{task, criteria, entries, last_run}` per row — enough to pick one to
    export without guessing at a task string.
    """
    ex = _experiments()
    s = ex._ident(schema or getattr(conn, "schema", None))
    return ex.query(
        conn,
        f"""
        SELECT h.task,
               count(DISTINCT s.id) AS criteria,
               count(*)             AS entries,
               max(h.recorded_at)   AS last_run
          FROM {s}.history h
          JOIN {s}.state   s ON s.id = h.state_id
         WHERE h.task IS NOT NULL AND s.kind = ANY($1)
      GROUP BY h.task
      ORDER BY criteria DESC, entries DESC, h.task
        """,
        [list(kinds)],
    )


def load_chunks(source: Any) -> dict[str, dict]:
    """Chunk id → `{text, ...}`, from a `chunks.json` path, its parsed dict, or a plain map.

    Accepts the three shapes that actually turn up: the store a package writes
    (`{"meta": ..., "chunks": {id: {"text": ...}}}`), the inner `chunks` dict on
    its own, and `{id: "text"}`. `meta["order"]` is honoured when present, so a
    chunk carries the position it had in the document.
    """
    raw = source
    if isinstance(source, (str, Path)):
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TypeError(f"chunks must be a path or a mapping, not {type(raw).__name__}")

    order: list[str] = []
    if "chunks" in raw and isinstance(raw.get("chunks"), Mapping):
        order = [c for c in (raw.get("meta") or {}).get("order") or [] if isinstance(c, str)]
        raw = raw["chunks"]

    out: dict[str, dict] = {}
    for cid, value in raw.items():
        if isinstance(value, str):
            out[str(cid)] = {"text": value}
        elif isinstance(value, Mapping):
            entry = {"text": str(value.get("text") or "")}
            for extra in ("page", "pages", "kind", "n"):
                if value.get(extra) is not None:
                    entry[extra] = value[extra]
            out[str(cid)] = entry
        else:
            raise TypeError(f"chunk {cid!r} is neither text nor an object")
    for position, cid in enumerate(order, start=1):
        if cid in out:
            out[cid].setdefault("n", position)
    return out


def _normalise(answer: Any) -> dict | None:
    """One stored answer as `{verdict, confidence, probabilities}`, whatever shape it was written in.

    `detail["answers"]` is written by each round script and three forms are in
    the live schema:

    * a **mapping** — `{"verdict": 0.81, "confidence": 0.81, "probabilities": …}`;
      149 tasks, 7,616 criteria.
    * a **bare number** — `0.99`; one task, but its 243 criteria make it the
      largest package in the project.
    * a **two-element list** — `[0.32, 0.68]`, verdict then confidence; 2 tasks,
      188 criteria.

    Handling only the mapping fails silently rather than loudly: the export keeps
    the criterion, drops every answer, and writes a file whose chunk listing is
    empty for no stated reason. Returns None only for a shape carrying no verdict.
    """
    if isinstance(answer, Mapping):
        verdict = answer.get("verdict")
        if verdict is None:
            for key in ("noul", "choice", "score"):
                if key in answer:
                    verdict = answer[key]
                    break
        return {
            "verdict": verdict,
            "confidence": answer.get("confidence"),
            "probabilities": answer.get("probabilities"),
        }
    if isinstance(answer, bool):
        return None
    if isinstance(answer, (int, float)):
        return {"verdict": float(answer), "confidence": None, "probabilities": None}
    if isinstance(answer, (list, tuple)) and answer:
        confidence = answer[1] if len(answer) > 1 else None
        return {
            "verdict": answer[0],
            "confidence": float(confidence) if isinstance(confidence, (int, float))
            and not isinstance(confidence, bool) else None,
            "probabilities": None,
        }
    return None


def _answers(entry: Mapping) -> dict[str, dict]:
    """A history entry's per-chunk answers, or `{}` if it was written without them."""
    detail = entry.get("detail")
    if not isinstance(detail, Mapping):
        return {}
    answers = detail.get("answers")
    return dict(answers) if isinstance(answers, Mapping) else {}


def _rows(conn: Any, task: str, kinds: Iterable[str], schema: str | None) -> list[dict]:
    """One row per criterion this task scored: its latest run that has answers.

    `DISTINCT ON` picks per criterion, and the ordering prefers an entry
    carrying answers over a newer one written without them — a run with no
    answers can still fill the cards but leaves the listing empty, and an empty
    listing is the thing this view exists to show.
    """
    ex = _experiments()
    s = ex._ident(schema or getattr(conn, "schema", None))
    return ex.query(
        conn,
        f"""
        SELECT DISTINCT ON (s.id)
               s.id, s.kind, s.name, s.body,
               h.recorded_at, h.metrics, h.detail, h.note,
               (SELECT count(*) FROM {s}.history t
                 WHERE t.state_id = s.id AND t.task = $1) AS runs,
               (SELECT count(*) FROM {s}.history t
                 WHERE t.state_id = s.id)                 AS runs_all_tasks
          FROM {s}.state   s
          JOIN {s}.history h ON h.state_id = s.id
         WHERE h.task = $1 AND s.kind = ANY($2)
      ORDER BY s.id,
               (h.detail -> 'answers' IS NOT NULL
                AND h.detail -> 'answers' <> '{{}}'::jsonb) DESC,
               h.recorded_at DESC, h.id DESC
        """,
        [task, list(kinds)],
    )


def export(
    conn: Any,
    task: str,
    *,
    chunks: Any = None,
    threshold: float = DEFAULT_THRESHOLD,
    kinds: Iterable[str] = KINDS,
    schema: str | None = None,
) -> dict:
    """The whole export document for one task, ready to serialise.

    Criteria come out ordered by the `hits` their run recorded, descending;
    a criterion whose task never recorded a `hits` metric sorts after every one
    that did, by name. Nothing is recomputed — `metrics` is copied across as the
    run wrote it, `hits` included.

    Raises `LookupError` if no criterion was ever scored under `task`, rather
    than writing an empty file that looks like a task with nothing in it.
    """
    store = load_chunks(chunks) if chunks is not None else {}
    rows = _rows(conn, task, kinds, schema)
    if not rows:
        raise LookupError(
            f"no criterion in {tuple(kinds)} was scored by task {task!r} — "
            "dashboard.tasks(conn) lists the tasks that have any"
        )

    criteria: list[dict] = []
    used: set[str] = set()
    for row in rows:
        metrics = dict(row.get("metrics") or {})
        detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
        body = row.get("body") if isinstance(row.get("body"), Mapping) else {}
        question = (detail or {}).get("question") or (body or {}).get("question") or {}

        answers = []
        for cid, answer in sorted(_answers(row).items()):
            normalised = _normalise(answer)
            if normalised is None:
                continue
            used.add(cid)
            answers.append({"chunk": cid, **normalised})

        criteria.append({
            "name": row["name"],
            "kind": row["kind"],
            "model": (detail or {}).get("model") or MODEL_OF_KIND.get(row["kind"], row["kind"]),
            "question": question,
            "recorded_at": _stamp(row.get("recorded_at")),
            "runs": row.get("runs"),
            "runs_all_tasks": row.get("runs_all_tasks"),
            "note": row.get("note"),
            "metrics": metrics,
            "answers": answers,
        })

    criteria.sort(key=lambda c: (-_hits(c), c["name"]))

    # Only the chunks some answer refers to: a store may cover a whole document
    # while the run asked about a pool drawn from it, and the untouched text is
    # dead weight in a file the browser has to parse in one go.
    embedded = {cid: store[cid] for cid in sorted(used) if cid in store}
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "task": task,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": float(threshold),
        "chunks": embedded,
        "chunks_missing": sorted(cid for cid in used if cid not in store),
        "criteria": criteria,
    }


def _hits(criterion: Mapping) -> float:
    """The `hits` its run recorded, or -1 so the criteria that recorded none sort last."""
    value = (criterion.get("metrics") or {}).get("hits")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return -1.0
    return float(value)


def _stamp(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def write(
    conn: Any,
    task: str,
    path: str | Path,
    *,
    chunks: Any = None,
    threshold: float = DEFAULT_THRESHOLD,
    kinds: Iterable[str] = KINDS,
    schema: str | None = None,
) -> Path:
    """`export` written to `path`, which must end `.criteria.json` for a widget to open it."""
    out = Path(path)
    if not out.name.endswith(".criteria.json"):
        raise ValueError(
            f"{out.name!r} will open in the code viewer: the widget rule matches "
            "*.criteria.json"
        )
    document = export(conn, task, chunks=chunks, threshold=threshold, kinds=kinds, schema=schema)
    out.write_text(json.dumps(document, indent=1, default=str) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    """`python3 .../utils/dashboard.py --task ... --chunks chunks.json --out x.criteria.json`"""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", help="the history.task to export; omitted, lists them")
    parser.add_argument("--chunks", help="the package's chunks.json, for the chunk text")
    parser.add_argument("--out", help="where to write it; must end .criteria.json")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)

    ex = _experiments()
    with ex.connect() as conn:
        if not args.task:
            for row in tasks(conn)[:40]:
                print(f"{row['criteria']:>5} criteria  {row['entries']:>5} entries  {row['task']}")
            return 0
        out = args.out or f"{args.task.split('/')[0]}.criteria.json"
        path = write(conn, args.task, out, chunks=args.chunks, threshold=args.threshold)
        document = json.loads(path.read_text(encoding="utf-8"))
        print(f"{path}: {len(document['criteria'])} criteria, {len(document['chunks'])} chunks")
        if document["chunks_missing"]:
            print(f"  {len(document['chunks_missing'])} chunk ids have no text — wrong --chunks?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
