"""Publish Jev's scores to the shared experiments database, in rows of their own.

`ledger.py` (in `laya-decisions`) does this for Laya, writing rows of kind
`laya_criterion`. This is the same bridge for Jev, and it writes kind
`jev_criterion` — a **different row per criterion per model**, never a shared
one:

    jev_ledger.publish(conn, run, task="pdf-qa/iterate.py")   # after each round
    standing = jev_ledger.standing(conn)                      # before the next
    jev_ledger.compare(conn, "is_draft")                      # Laya vs Jev

`experiments.state` is unique on `(kind, name)`, and `kind` is the column that
keeps them apart. Why that matters rather than being tidiness: `standing()`
pools every task's history entries for one criterion into **one time-ordered
sequence** and runs `sweep.convergence` over it. Two models' scores interleaved
in that sequence is not a convergence curve — it is two curves shuffled
together, and a criterion Laya had ground down reads as still improving the
first time Jev scores it higher. The database already holds 6,842
`laya_criterion` rows and 12,807 history entries; keeping Jev out of them is
what makes both readable.

Three things to know:

*   **The model name is recorded, not inferred.** Jev's response carries no
    model version — not in the body, not in a header, whatever the docs show —
    so what gets stamped is what the request *asked for*: `client.model`,
    `jev-latest` by default. It lands on the row as `body["model"]` and on every
    history entry as `detail["model"]`.

    Of those two, **`detail["model"]` is the record and `body["model"]` is only
    the last registrant.** A row is unique on `(kind, name)`, so a second alias
    scoring the same criterion overwrites the row's stamp while its entries keep
    their own — which is what you want, since the history is the thing a track
    record is built from. `standing(model=...)` and `history(model=...)` filter
    on the entries, and are the supported way to keep two aliases' sequences
    apart.
*   **It needs the `postgres-experiments-state` skill enabled**, and `standing()`
    additionally needs `sweep` from `laya-decisions`. Nothing is imported until
    you call it, so `jev.py` works with neither.
*   **Only what a run actually asked is published**, exactly as in `ledger.py`:
    a run carries its whole question set forward, and publishing the questions
    it did not re-ask would invent executions that never happened.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

#: `kind` for a row holding a criterion scored by Jev. `laya-decisions` writes
#: `laya_criterion`; the two never share a row.
KIND = "jev_criterion"

#: The kind `laya-decisions` writes, for `compare` and for read-only queries.
KIND_LAYA = "laya_criterion"

#: Scores are read from this key of a history entry's metrics — what
#: `sweep.binary_scores` calls Youden's J. Pass `metric=` for anything else.
DEFAULT_METRIC = "J"


def _sibling(name: str) -> Any:
    """Import a module sitting beside this one, package-relative or flat."""
    try:
        return __import__(f"{__package__}.{name}", fromlist=[name]) if __package__ \
            else __import__(name)
    except ImportError:
        return __import__(name)


def _experiments() -> Any:
    try:
        return _sibling("experiments")
    except ImportError as exc:  # pragma: no cover - depends on what is enabled
        raise RuntimeError(
            "jev_ledger needs the `postgres-experiments-state` skill enabled: "
            "no `experiments` module beside this one"
        ) from exc


def _sweep() -> Any:
    try:
        return _sibling("sweep")
    except ImportError as exc:  # pragma: no cover - depends on what is enabled
        raise RuntimeError(
            "jev_ledger.standing needs `sweep` from the `laya-decisions` skill, "
            "which holds the convergence rule both ledgers use. Read the raw "
            "entries with history() or track() instead."
        ) from exc


def model_name(model: Any = None) -> str:
    """The model name to stamp: a string, a `jev.Client`'s `model`, or `jev.MODEL`.

    Jev returns no model version of its own, so this is the alias the request
    was sent with. Recording the alias rather than nothing is the point: it is
    what a later reader needs to know two rows were not scored by the same
    thing.
    """
    if isinstance(model, str) and model:
        return model
    if model is not None and getattr(model, "model", None):
        return str(model.model)
    return str(_sibling("jev").MODEL)


def _score(entry: dict, metric: str) -> float | None:
    """The numeric value of `metric` in a history entry, or None if it has none."""
    value = (entry.get("metrics") or {}).get(metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # NaN is how binary_scores reports a degenerate split; it is not a score.
    return float(value) if value == value else None


def fingerprint(question: dict) -> str:
    """One question as a canonical string, so two records of it compare equal or do not.

    `last_run` is `sweep`'s bookkeeping about which round last asked a question,
    not part of what was asked, so it is dropped: a criterion carried forward
    and the round that asked it fingerprint the same, while a rewording under
    the same id fingerprints differently. That is the distinction a resume needs
    — a reworded criterion is a question nobody has put to this chunk yet.
    """
    return json.dumps(
        {k: v for k, v in (question or {}).items() if k != "last_run"}, sort_keys=True
    )


def pairs(name: str, entries: Iterable[dict]) -> set[tuple[str, str, str]]:
    """The `(criterion, question fingerprint, chunk)` triples `entries` already cover.

    What a resumed sweep subtracts from its plan before it spends a credit. An
    entry `publish` wrote carries the question it asked and, in
    `detail["answers"]`, the chunks it asked it of; one recorded without either
    contributes nothing, because nothing in it says which chunks it saw.

    `entries` is whatever carries a `detail` — `history(conn, name)`, or the rows
    of a query of your own — and `name` is the criterion they belong to, since a
    history entry does not repeat it.
    """
    out: set[tuple[str, str, str]] = set()
    for entry in entries:
        detail = entry.get("detail") or {}
        question = detail.get("question")
        if not question:
            continue
        mark = fingerprint(question)
        for chunk in detail.get("answers") or {}:
            out.add((name, mark, chunk))
    return out


def pair_index(records: Iterable[dict]) -> dict[tuple[str, str, str], list[dict]]:
    """The `(criterion, wording, chunk)` triples in `coverage()` records, each mapped to the records holding it.

    `pairs()` answers "has this been asked?" for one criterion and flattens to a
    set. This answers "asked by which entry?", over as many criteria as you hand
    it: `coverage(conn)` straight in, and out comes what a resumed sweep
    subtracts its plan against, keyed exactly as `pairs()` keys it.

    Two things the set shape cannot give you. A triple mapping to more than one
    record is a pair recorded **twice** — one question, one chunk, two
    executions, one credit spent for nothing and two points in a sequence that
    saw one run. And each record is the whole `coverage()` record, so the task,
    round and model that already covered a pair are there to judge rather than
    just its existence.

    A record with no question contributes nothing, for the same reason it
    contributes nothing to `pairs()`: nothing in it says which wording those
    chunks saw. Reports and does not decide — which duplicates matter, and whose
    entries count, are the caller's.
    """
    out: dict[tuple[str, str, str], list[dict]] = {}
    for record in records:
        question = record.get("question")
        if not question:
            continue
        mark = fingerprint(question)
        for chunk in record.get("chunks") or ():
            out.setdefault((record["name"], mark, chunk), []).append(record)
    return out


def definitions(rounds: dict[str, dict], *, prefix: str = "") -> list[dict]:
    """The distinct `(criterion, wording)` questions across several rounds of asks.

    `pairs()` and `pair_index()` say what has already been asked; this builds the
    other side of that subtraction, the plan. `rounds` is
    `{round: {criterion: question}}` — what each round put to a model, as
    `sweep`'s run files record it — and what comes back is one record per
    *distinct* question, with the rounds that asked it:

        {"key": "open_rfi#2", "qid": "open_rfi", "name": "<prefix>open_rfi",
         "question": <as it will be posted>, "original": <as it was given>,
         "rewritten": False, "fingerprint": ..., "versions": ["r4", "r5"]}

    The unit is the *wording*, because that is the unit `fingerprint` and so the
    whole resume path uses: a criterion re-asked unchanged in a later round is
    one question and costs one ask, while the same name reworded mid-sweep is two
    questions and a pair recorded for the first does not excuse skipping the
    second. On the package this was written for that is 125 definitions behind
    166 `(round, criterion)` asks — 960 credits rather than 1,380.

    `key` is `qid#n`, numbered by order of first use as `rounds` is ordered,
    because a dict cannot hold two wordings under one name and a cache of
    answers needs one key per thing asked. `name` is `prefix + qid`: pass the
    prefix a package puts on its criteria for the shared table, so the record
    lines up with what `pairs()` returns.

    `question` is the **posted** form — `jev.postable` applied, which turns a
    choice's list of labels into `{label: ""}` the endpoint will accept — and
    `fingerprint` is taken from it, so a pair already in the database is
    recognised by the question Jev was really given. `original` keeps what was
    handed in beside it and `rewritten` says whether they differ, since what a
    row should record is the question that was posted.
    """
    jev = _sibling("jev")
    seen: dict[tuple[str, str], dict] = {}
    for version, questions in rounds.items():
        for qid, original in questions.items():
            question = jev.postable({qid: original})[qid]
            mark = fingerprint(question)
            key = (qid, mark)
            if key not in seen:
                n = sum(1 for other, _ in seen if other == qid) + 1
                seen[key] = {
                    "key": f"{qid}#{n}",
                    "qid": qid,
                    "name": prefix + qid,
                    "question": question,
                    "original": original,
                    "rewritten": mark != fingerprint(original),
                    "fingerprint": mark,
                    "versions": [],
                }
            seen[key]["versions"].append(version)
    return list(seen.values())


def remaining(definitions: Iterable[dict], covered: Any, *,
              chunks: Iterable[str] | None = None) -> dict[str, list[dict]]:
    """The plan's `(criterion, wording, chunk)` pairs that `covered` does not hold, grouped by chunk.

    `definitions()` builds the plan and `pair_index()` (or `pairs()`) says what
    the database already covers; this is the subtraction between them, which is
    the step a resumed sweep has to get right to avoid paying twice for one
    answer. `covered` is anything a triple can be tested against with `in` — a
    `pair_index()` mapping, a `pairs()` set, or a union of several.

    Grouped **by chunk** because that is the unit a request is charged for:
    `{chunk_id: [definition, ...]}` hands one chunk's outstanding wordings
    straight to a single `jev.ask_chunks` call, where up to `MAX_QUESTIONS` of
    them ride on one credit. A chunk with nothing left is left out entirely, so
    an exhausted plan comes back `{}` and the caller spends nothing.

    Each definition names its own chunks in a `"chunks"` key if it has one — a
    wording two rounds asked may span more segments than one a single round
    asked — and falls back to `chunks` if not. Order is preserved on both axes:
    chunks in the order they are first named, wordings in the order
    `definitions` gives them, so a resumed run is reproducible and two plans
    diff readably.

    Reports and does not decide. Whether a pair covered by *another* task, or
    under another model alias, counts as done is the caller's call, and the way
    to say so is to hand in a `covered` filtered to the entries that count —
    `pair_index()` keeps the whole record behind each triple for exactly that.
    """
    fallback = None if chunks is None else list(chunks)
    out: dict[str, list[dict]] = {}
    for record in definitions:
        where = record["chunks"] if record.get("chunks") is not None else fallback
        if where is None:
            raise ValueError(
                f"{record.get('key') or record.get('name')!r} names no chunks and "
                "no `chunks=` fallback was given"
            )
        for chunk in where:
            if (record["name"], record["fingerprint"], chunk) in covered:
                continue
            out.setdefault(chunk, []).append(record)
    return out


def passes(todo: dict[str, list[dict]]) -> list[tuple[tuple[str, ...], list[str]]]:
    """A `remaining()` plan regrouped as `(wording keys, the chunks wanting exactly those)`.

    `remaining()` groups by chunk because a chunk is what a credit is charged
    for. `jev.ask_each` wants the transpose — one question set, many chunks —
    and it is the only thing that runs `jev.WORKERS` requests in flight.
    Executing a plan literally therefore means one serial call per chunk, with
    seven workers idle for the length of the sweep; this puts the chunks that
    want the same set back together, so each group is one parallel pass:

        for keys, chunks in jev_ledger.passes(todo):
            questions = {key: by_key[key]["question"] for key in keys}
            for chunk, result in jev.ask_each(client, [text[c] for c in chunks],
                                              questions):
                ...

    A plan is usually only a handful of groups wide, because what differs
    between chunks is which *scope* asked about them. On the replay this was
    written for, 165 chunks came out as three passes — the form pages, the core
    segments a third round reached, and the ones it did not — and 165 serial
    calls became 90 seconds.

    Groups come out in the order their first chunk appears and each group's
    chunks in the order `todo` gives them, so a resumed run is reproducible.
    The key tuple is the group's identity, not a set: `remaining()` preserves
    the definition order within a chunk, and two chunks whose outstanding
    wordings differ only in order are genuinely two different requests.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for chunk, records in todo.items():
        groups.setdefault(tuple(r["key"] for r in records), []).append(chunk)
    return list(groups.items())


def price(todo: dict[str, list[dict]], *, size: int | None = None) -> dict:
    """What a `remaining()` plan will cost: `{"chunks", "requests", "credits", "pairs"}`.

    `jev.estimate` prices a sweep where every chunk carries the same questions.
    A resume is exactly the case where they do not — a chunk an earlier round
    already covered has fewer wordings left than one it never reached — so the
    bill is `estimate` summed per chunk, which is this. Exact for the same
    reason `estimate` is: one request per credit while each state stays inside
    `jev.CHUNK_TOKENS`. Measured on an 11,905-pair replay spanning three
    scopes, it predicted 1,560 credits and the endpoint charged 1,560.

    `pairs` is how many answers the plan will produce, which is the number to
    put beside the credits when deciding whether to run it. `size` is the
    questions a request carries, defaulting to `jev.MAX_QUESTIONS`.
    """
    jev = _sibling("jev")
    per_request = jev.MAX_QUESTIONS if size is None else size
    total = {"chunks": 0, "requests": 0, "credits": 0, "pairs": 0}
    for records in todo.values():
        one = jev.estimate(1, len(records), size=per_request)
        total["chunks"] += 1
        total["requests"] += one["requests"]
        total["credits"] += one["credits"]
        total["pairs"] += len(records)
    return total


def asked(run: dict) -> list[str]:
    """The question ids a run actually asked, never the ones it carried forward."""
    if "asked_this_run" in run:
        wanted = list(run["asked_this_run"])
    else:
        carried = set(run.get("carried_forward", []))
        wanted = [q for q in run.get("questions", {}) if q not in carried]
    return [q for q in wanted if q in run.get("questions", {})]


def entries(run: dict, *, task: str, model: Any = None,
            include_answers: bool = True) -> list[dict]:
    """What `publish` would write for a run — one record per question asked, no database.

    Each record is `{name, question, model, task, metrics, detail, note}`. Useful
    on its own to see what a run will contribute before it contributes it.
    """
    name = model_name(model)
    records = []
    for qid in asked(run):
        # `last_run` is sweep's bookkeeping, not part of the question Jev was
        # given; the rest is exactly what `jev.yes_no`/`choice`/`score` built.
        question = {k: v for k, v in run["questions"][qid].items() if k != "last_run"}
        detail: dict[str, Any] = {
            "model": name,
            "version": run.get("version"),
            "question": question,
        }
        if include_answers and qid in run.get("answers", {}):
            detail["answers"] = run["answers"][qid]
        records.append({
            "name": qid,
            "question": question,
            "model": name,
            "task": task,
            "metrics": run.get("metrics", {}).get(qid, {}),
            "detail": detail,
            "note": run.get("note") or None,
        })
    return records


def triples(run: dict, *, task: str, model: Any = None) -> set[tuple[str, str, str]]:
    """The `(criterion, question fingerprint, chunk)` triples `publish` would record.

    `pairs()` keys entries already stored and `pair_index()` keys `coverage()`
    records; this keys a run that has not been written yet, in the same shape, so
    the plan and the database can be compared with `&` and `-` rather than by
    hand:

        clash = triples(run, task=task) & set(pair_index(coverage(conn)))

    That intersection is the last gate before a write, and the one failure it
    catches is the one nothing else does. `remaining()` subtracts what the
    database holds from the *plan*; between the plan and `publish` sits whatever
    the caller does to group answers into runs, and a mistake there records one
    execution as two history entries — two points in a sequence that saw one
    run, with no error raised about it.

    Read through `entries()`, so what it reports is what `publish` would write
    and not a second opinion about it: same `asked()` filter, so a question
    carried forward is absent here too, and same `detail["answers"]` keys, so the
    chunks are the ones that would actually be recorded. Reports and does not
    decide — whether a clash means skip the pair, drop the run or accept a second
    reading is the caller's.
    """
    out: set[tuple[str, str, str]] = set()
    for record in entries(run, task=task, model=model):
        mark = fingerprint(record["question"])
        for chunk in (record["detail"].get("answers") or {}):
            out.add((record["name"], mark, chunk))
    return out


def register(conn: Any, name: str, question: dict, *, model: Any = None,
             merge: bool = True, schema: str | None = None, **meta: Any) -> dict:
    """Register one criterion under `KIND`, storing its question and the model asked.

    The Laya-side `experiments.upsert_criterion` writes kind `laya_criterion`
    and cannot be pointed elsewhere, so this goes through `upsert_row` with the
    same `check_question` validation. `body` is `{"question": ..., "model": ...,
    **meta}`, which `criterion()` hands straight back to `jev.ask_chunks`.
    """
    ex = _experiments()
    body = {
        "question": ex.check_question(question),
        "model": model_name(model),
        **meta,
    }
    return ex.upsert_row(conn, KIND, name, body, merge=merge, schema=schema)


def criterion(conn: Any, name: str, *, schema: str | None = None) -> dict | None:
    """One registered Jev criterion by name, or None."""
    return _experiments().get_row(conn, KIND, name, schema=schema)


def criteria(conn: Any, *, model: str | None = None,
             schema: str | None = None) -> list[dict]:
    """Every criterion registered under `KIND`, oldest first; one model's if `model` is given."""
    rows = _experiments().list_rows(conn, KIND, schema=schema)
    if model is None:
        return rows
    return [row for row in rows if (row["body"] or {}).get("model") == model]


def record(conn: Any, name: str, *, task: str, model: Any = None,
           metrics: dict | None = None, detail: dict | None = None,
           note: str | None = None, schema: str | None = None) -> dict:
    """Append one history entry for a Jev run of `name`, stamped with the model.

    `detail` is meant to be `jev.detail(result)[name]` — the verdict with the
    confidence and per-option probabilities behind it. The model is folded into
    `detail["model"]` whatever else it holds, because a stored verdict with no
    model beside it cannot be compared with anything later.
    """
    ex = _experiments()
    # The stamp goes last, so a `detail` that already carries a `model` from
    # `entries()` agrees with it rather than overriding it with a stale one.
    stamped = {**(detail or {}), "model": model_name(model)}
    return ex.append_history(conn, KIND, name, task=task, metrics=metrics,
                             detail=stamped, note=note, schema=schema)


def publish(conn: Any, run: dict, *, task: str, model: Any = None,
            include_answers: bool = True, schema: str | None = None) -> list[dict]:
    """Register each criterion this run asked and append one history entry to each.

    Mirrors `ledger.publish` — same run shape, same one-transaction rule, same
    `task` convention of package and script — but every row and entry lands
    under `KIND` with the model stamped. The whole run is one transaction: a
    half-published iteration would count as an execution that only scored some
    of its questions.
    """
    written = []
    with conn.transaction():
        for rec in entries(run, task=task, model=model,
                           include_answers=include_answers):
            register(conn, rec["name"], rec["question"], model=rec["model"],
                     merge=True, schema=schema, last_run=run.get("version"))
            written.append(record(
                conn, rec["name"], task=task, model=rec["model"],
                metrics=rec["metrics"], detail=rec["detail"], note=rec["note"],
                schema=schema))
    return written


def history(conn: Any, name: str, *, model: str | None = None,
            schema: str | None = None) -> list[dict]:
    """Every entry recorded against one Jev criterion, oldest first, all tasks.

    `model` filters on the stamp in `detail`, for a criterion that has been
    scored under more than one alias.
    """
    rows = _experiments().history(conn, KIND, name, schema=schema)
    if model is None:
        return rows
    return [r for r in rows if (r.get("detail") or {}).get("model") == model]


def coverage(conn: Any, names: Any = None, *, answers: bool = False,
             schema: str | None = None) -> list[dict]:
    """Per published entry, which chunks it already scored — the read side of `publish`.

    `entries()` stores a run's per-chunk answers under `detail["answers"]`, keyed
    by chunk id, and this reads them back: one record per history entry, oldest
    first, as `{"name", "version", "task", "model", "question", "chunks",
    "entry"}`, over the criteria in `names` or over every one the kind holds.

    What it is for is resuming. A sweep that was interrupted — or a second pass
    over a corpus a previous task already scored — can ask the database which
    `(criterion, chunk)` pairs exist before spending a credit on one that does,
    which is the difference between a resumable sweep and one that starts again
    from nothing.

    **`answers=True` adds the verdicts beside the chunk ids**, as
    `record["answers"]` — `detail["answers"]` verbatim, `{chunk: record}`, the
    same rows `jev.detail` produced. Knowing a pair exists is enough to skip
    asking it; publishing the round it belongs to needs the answer as well, and
    without this the only way back to it is a second pass over `history()`
    re-deriving each entry's fingerprint. The default is off because the maps are
    the bulk of an entry and a caller deciding what to ask does not need them:
    `pair_index(coverage(conn, names, answers=True))` is the resume that reuses
    what was already scored, `pair_index(coverage(conn, names))` the one that
    only counts it.

    Deliberately unfiltered and undecided: whose entries count, which question
    they had to have asked, and what to do about a partial one are the caller's
    to judge, because only the caller knows what it is about to ask.
    """
    wanted = None if names is None else set(names)
    records = []
    for row in criteria(conn, schema=schema):
        if wanted is not None and row["name"] not in wanted:
            continue
        for entry in history(conn, row["name"], schema=schema):
            detail = entry.get("detail") or {}
            replies = detail.get("answers") or {}
            record = {
                "name": row["name"],
                "version": detail.get("version"),
                "task": entry.get("task"),
                "model": detail.get("model"),
                "question": detail.get("question"),
                "chunks": set(replies),
                "entry": entry["id"],
            }
            if answers:
                record["answers"] = replies
            records.append(record)
    records.sort(key=lambda rec: rec["entry"])
    return records


def track(conn: Any, name: str, *, schema: str | None = None) -> dict:
    """`experiments.track_record` for the Jev kind: runs, tasks, each metric's series."""
    return _experiments().track_record(conn, name, kind=KIND, schema=schema)


def standing(conn: Any, *, metric: str = DEFAULT_METRIC,
             names: list[str] | None = None, model: str | None = None,
             min_gain: float = 0.02, patience: int = 2,
             schema: str | None = None) -> dict:
    """Per Jev criterion: runs, tasks, best score, whether iterating has stopped paying.

    The Jev-kind twin of `ledger.standing`. Every task's entries are pooled and
    ordered by time, so the sequence `sweep.convergence` sees is the real one
    across packages. Versions are labelled `task:version` because two packages
    both calling a round `v3` are two different rounds.

    `model` restricts both the rows and their entries to one model alias — worth
    passing the day a second alias starts writing here, since a sequence mixing
    two models is the thing this kind exists to prevent.

    `min_gain` and `patience` go straight to `sweep.convergence`: its defaults,
    not rules. A criterion nobody has scored on `metric` comes back `converged`
    False with `best` None — unmeasured is not finished.
    """
    ex = _experiments()
    sweep = _sweep()

    rows = criteria(conn, model=model, schema=schema)
    if names is not None:
        wanted = set(names)
        rows = [r for r in rows if r["name"] in wanted]

    collected = []
    for row in rows:
        for entry in history(conn, row["name"], model=model, schema=schema):
            collected.append((entry["recorded_at"], entry["id"], row["name"], entry))
    collected.sort(key=lambda item: (item[0], item[1]))

    scores: dict[str, dict] = {}
    summary: dict[str, dict] = {
        row["name"]: {
            "metric": metric,
            "model": (row["body"] or {}).get("model"),
            "runs": 0,
            "scored": 0,
            "tasks": [],
            "question": (row["body"] or {}).get("question"),
            "last_run_at": None,
            "best": None,
            "best_version": None,
            "latest": None,
            "rounds_since_best": 0,
            "converged": False,
        }
        for row in rows
    }

    seen_tasks: dict[str, list] = {name: [] for name in summary}
    for recorded_at, entry_id, name, entry in collected:
        row_summary = summary[name]
        row_summary["runs"] += 1
        row_summary["last_run_at"] = recorded_at
        if entry["task"] and entry["task"] not in seen_tasks[name]:
            seen_tasks[name].append(entry["task"])
        value = _score(entry, metric)
        if value is None:
            continue
        row_summary["scored"] += 1
        version = (entry["detail"] or {}).get("version")
        label = f"{entry['task'] or 'unknown'}:{version}" if version else f"#{entry_id}"
        # Two entries can legitimately share a task and a version — a re-run of
        # the same round. They are still separate points in the sequence.
        if label in scores and name in scores[label]:
            label = f"{label}#{entry_id}"
        scores.setdefault(label, {})[name] = value

    for name, tasks in seen_tasks.items():
        summary[name]["tasks"] = tasks

    for name, verdict in sweep.convergence(scores, min_gain=min_gain,
                                           patience=patience).items():
        summary[name].update(verdict)
    return summary


def worth_improving(standing: dict) -> list[str]:
    """The criteria from `standing` that have not converged, by name."""
    return sorted(name for name, rec in standing.items() if not rec["converged"])


def compare(conn: Any, name: str | None = None, *, metric: str = DEFAULT_METRIC,
            kinds: tuple[str, ...] = (KIND_LAYA, KIND),
            schema: str | None = None) -> dict:
    """One criterion name, what each model's rows say about it — the point of separate rows.

    Returns `{name: {kind: {"model", "runs", "scored", "best", "latest",
    "tasks", "last_run_at"}}}`, one inner entry per kind that has a row of that
    name and `None` for a kind that has not scored it. Pass `name` for one
    criterion or nothing for every name either kind holds.

    Read-only and deliberately dumb: it reports what each model scored and does
    not tell you which is better. Two models' numbers on one criterion are
    comparable only if they were scored against the same labels, and that is
    something the caller knows and this function cannot.
    """
    ex = _experiments()
    out: dict[str, dict] = {}
    for kind in kinds:
        for row in ex.list_rows(conn, kind, schema=schema):
            if name is not None and row["name"] != name:
                continue
            record_for = out.setdefault(row["name"], {k: None for k in kinds})
            past = ex.history(conn, kind, row["name"], schema=schema)
            values, tasks, scored, last = [], [], 0, None
            for entry in past:
                last = entry["recorded_at"]
                if entry["task"] and entry["task"] not in tasks:
                    tasks.append(entry["task"])
                value = _score(entry, metric)
                if value is not None:
                    scored += 1
                    values.append(value)
            record_for[kind] = {
                "model": (row["body"] or {}).get("model"),
                "metric": metric,
                "runs": len(past),
                "scored": scored,
                "best": max(values) if values else None,
                "latest": values[-1] if values else None,
                "tasks": tasks,
                "last_run_at": last,
            }
    return out
