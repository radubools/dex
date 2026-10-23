"""Publish each run's scores to the shared experiments database, and read back what every task has scored.

`sweep.py` keeps one project's iterations in run files. That is enough while a
single package is tuning a single question set, and not enough for the question
the second package asks: *has anyone already tried this criterion, and did
iterating on it still pay?* Run files cannot answer it — they live in whichever
package wrote them.

This is the bridge to `postgres-experiments-state`, which can. Two directions:

    ledger.publish(conn, run, task="pdf-qa/iterate.py")   # after each iteration
    standing = ledger.standing(conn)                      # before the next one
    ledger.worth_improving(standing)                      # ['is_draft', 'topic']

`publish` registers every criterion the run actually asked and appends one
history entry per criterion — the metrics it scored, the wording that scored
them, and the answers behind it. `standing` reads every entry back, **from all
tasks, not just yours**, and runs `sweep.convergence` over the combined
sequence, so a criterion three other packages have already ground down shows up
as converged before you spend a round on it.

Two things to know:

*   **Only what the run actually asked is published.** A run carries its whole
    question set forward, including questions it did not re-ask; publishing
    those would invent executions that never happened and tilt every track
    record built on them. `asked_this_run` is what gets written.
*   **It needs the `postgres-experiments-state` skill enabled.** Nothing else
    in `laya-decisions` does, and nothing here is imported until you call it,
    so the rest of the skill still works without a database.
"""

from __future__ import annotations

from typing import Any

#: Scores are read from this key of a history entry's metrics — what
#: `sweep.binary_scores` calls Youden's J. Pass `metric=` for anything else.
DEFAULT_METRIC = "J"


def _sibling(name: str) -> Any:
    """Import a module sitting beside this one, package-relative or flat."""
    try:
        # Imported as `utils.ledger`, the way a package imports it.
        return __import__(f"{__package__}.{name}", fromlist=[name]) if __package__ \
            else __import__(name)
    except ImportError:
        # Imported flat, the way the tests put `utils/` on sys.path.
        return __import__(name)


def _experiments() -> Any:
    try:
        return _sibling("experiments")
    except ImportError as exc:  # pragma: no cover - depends on what is enabled
        raise RuntimeError(
            "ledger needs the `postgres-experiments-state` skill enabled: "
            "no `experiments` module beside this one"
        ) from exc


def _score(entry: dict, metric: str) -> float | None:
    """The numeric value of `metric` in a history entry, or None if it has none."""
    value = (entry.get("metrics") or {}).get(metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # NaN is how binary_scores reports a degenerate split; it is not a score.
    return float(value) if value == value else None


def asked(run: dict) -> list[str]:
    """The question ids a run actually asked, never the ones it carried forward."""
    if "asked_this_run" in run:
        wanted = list(run["asked_this_run"])
    else:
        carried = set(run.get("carried_forward", []))
        wanted = [q for q in run.get("questions", {}) if q not in carried]
    return [q for q in wanted if q in run.get("questions", {})]


def namespaced(run: dict, prefix: str) -> dict:
    """A copy of `run` with `prefix` on every question id, for the shared table.

    `experiments.state` is one table for every project with the skill enabled, so
    a criterion called `weather` collides with any other package's `weather`.
    Prefixing before `publish` is how several packages studying the same kind of
    document pool their runs of one criterion instead of overwriting each other.

    It renames **five** keys, and the two easy to forget are the reason this is
    here rather than in each caller: `asked_this_run` and `carried_forward` hold
    bare ids too, and `asked()` intersects `asked_this_run` with `questions`. Miss
    them and the intersection is empty, so `publish` writes nothing at all,
    returns `[]` and raises no error — a silent no-op that looks like success.

    The original is not modified; nested values are shared, not copied.
    """
    out = dict(run)
    for key in ("questions", "answers", "metrics"):
        if key in run:
            out[key] = {prefix + qid: value for qid, value in run[key].items()}
    for key in ("asked_this_run", "carried_forward"):
        if key in run:
            out[key] = [prefix + qid for qid in run[key]]
    return out


def entries(run: dict, *, task: str, include_answers: bool = True) -> list[dict]:
    """What `publish` would write for a run — one record per question asked, no database.

    Each record is `{name, question, task, metrics, detail, note}`. Useful on
    its own to see what a run will contribute before it contributes it.
    """
    records = []
    for qid in asked(run):
        # `last_run` is sweep's bookkeeping, not part of the question Laya was
        # given; the rest is exactly what `laya.yes_no`/`choice`/`score` built.
        question = {k: v for k, v in run["questions"][qid].items() if k != "last_run"}
        detail: dict[str, Any] = {"version": run.get("version"), "question": question}
        if include_answers and qid in run.get("answers", {}):
            detail["answers"] = run["answers"][qid]
        records.append({
            "name": qid,
            "question": question,
            "task": task,
            "metrics": run.get("metrics", {}).get(qid, {}),
            "detail": detail,
            "note": run.get("note") or None,
        })
    return records


def publish(conn: Any, run: dict, *, task: str, include_answers: bool = True) -> list[dict]:
    """Register each criterion this run asked and append one history entry to each.

    `task` says who ran it — a package and script, so a track record can be read
    back to its source. The whole run lands in one transaction: a half-published
    iteration would be counted as an execution that only scored some of its
    questions.
    """
    ex = _experiments()
    written = []
    with conn.transaction():
        for record in entries(run, task=task, include_answers=include_answers):
            ex.upsert_criterion(conn, record["name"], record["question"],
                                merge=True, last_run=run.get("version"))
            written.append(ex.record_run(
                conn, record["name"], task=task,
                metrics=record["metrics"], detail=record["detail"], note=record["note"]))
    return written


def history(conn: Any, qid: str) -> list[dict]:
    """Every history entry recorded against one criterion, oldest first, all tasks."""
    ex = _experiments()
    return ex.history(conn, ex.KIND_CRITERION, qid)


def asked_by(conn: Any, task: str, *, schema: str | None = None) -> list[dict]:
    """What one task's entries record it having asked, oldest first — `publish` read back.

    One record per history entry, as `{"name", "version", "question", "chunks",
    "entry", "recorded_at", "metrics", "note"}`: the question as it was put, the
    round that put it, and the chunk ids it was put of, taken from
    `detail["question"]`, `detail["version"]` and the keys of
    `detail["answers"]`. An entry carrying no question or no per-chunk answers
    says nothing about what was asked and is left out.

    The inverse of the lookup `history` offers. `history(conn, name)` answers
    *who has scored this criterion*; this answers *what did this task ask, and of
    what text* — which is the question a replay has to answer before it can ask
    anything, whether the replay is of this criterion set against a second model
    or of an interrupted sweep against itself.

    **It reads the entries, never the row.** `experiments.state` is unique on
    `(kind, name)`, so in a project where several packages share a criterion
    vocabulary the last one to register a name owns its `body["question"]` and an
    earlier package's wording is simply gone. A history entry is append-only and
    belongs to the task that wrote it, so it is the only place the question a
    given task actually asked survives.

    Returns data and judges nothing: whether the entries found are complete,
    whether a criterion appearing twice under one version is a re-run or a
    mistake, and what to do about either belong to the caller, who is the only
    one who knows what it is about to ask.
    """
    ex = _experiments()
    # The schema name is the one identifier that cannot be a parameter;
    # `_ident` is the guard `experiments` applies to it everywhere else.
    s = ex._ident(schema or getattr(conn, "schema", None))
    found = []
    for entry in conn.fetch(
        f"""SELECT s.name, h.id, h.recorded_at, h.metrics, h.detail, h.note
              FROM {s}.state s JOIN {s}.history h ON h.state_id = s.id
             WHERE s.kind = $1 AND h.task = $2
          ORDER BY h.recorded_at, h.id""", ex.KIND_CRITERION, task,
    ):
        detail = entry["detail"] or {}
        chunks = sorted(detail.get("answers") or {})
        if not detail.get("question") or not chunks:
            continue
        found.append({
            "name": entry["name"],
            "version": detail.get("version"),
            "question": detail["question"],
            "chunks": chunks,
            "entry": entry["id"],
            "recorded_at": entry["recorded_at"],
            "metrics": entry["metrics"],
            "note": entry["note"],
        })
    return found


def asked_by(conn: Any, task: str, *, schema: str | None = None) -> list[dict]:
    """What one task's entries record it having asked, oldest first — `publish` read back.

    One record per history entry, as `{"name", "version", "question", "chunks",
    "entry", "recorded_at", "metrics", "note"}`: the question as it was put, the
    round that put it, and the chunk ids it was put of, taken from
    `detail["question"]`, `detail["version"]` and the keys of
    `detail["answers"]`. An entry carrying no question or no per-chunk answers
    says nothing about what was asked and is left out.

    The inverse of the lookup `history` offers. `history(conn, name)` answers
    *who has scored this criterion*; this answers *what did this task ask, and of
    what text* — which is the question a replay has to answer before it can ask
    anything, whether the replay is of this criterion set against a second model
    or of an interrupted sweep against itself.

    **It reads the entries, never the row.** `experiments.state` is unique on
    `(kind, name)`, so in a project where several packages share a criterion
    vocabulary the last one to register a name owns its `body["question"]` and an
    earlier package's wording is simply gone. A history entry is append-only and
    belongs to the task that wrote it, so it is the only place the question a
    given task actually asked survives.

    Returns data and judges nothing: whether the entries found are complete,
    whether a criterion appearing twice under one version is a re-run or a
    mistake, and what to do about either belong to the caller, who is the only
    one who knows what it is about to ask.
    """
    ex = _experiments()
    # The schema name is the one identifier that cannot be a parameter;
    # `_ident` is the guard `experiments` applies to it everywhere else.
    s = ex._ident(schema or getattr(conn, "schema", None))
    found = []
    for entry in conn.fetch(
        f"""SELECT s.name, h.id, h.recorded_at, h.metrics, h.detail, h.note
              FROM {s}.state s JOIN {s}.history h ON h.state_id = s.id
             WHERE s.kind = $1 AND h.task = $2
          ORDER BY h.recorded_at, h.id""", ex.KIND_CRITERION, task,
    ):
        detail = entry["detail"] or {}
        chunks = sorted(detail.get("answers") or {})
        if not detail.get("question") or not chunks:
            continue
        found.append({
            "name": entry["name"],
            "version": detail.get("version"),
            "question": detail["question"],
            "chunks": chunks,
            "entry": entry["id"],
            "recorded_at": entry["recorded_at"],
            "metrics": entry["metrics"],
            "note": entry["note"],
        })
    return found


def standing(conn: Any, *, metric: str = DEFAULT_METRIC, names: list[str] | None = None,
             min_gain: float = 0.02, patience: int = 2) -> dict:
    """Per criterion, what the accumulated history says: runs, tasks, best score, converged.

    Every task's entries are pooled and ordered by time, so the sequence
    `sweep.convergence` sees is the real one across packages rather than one
    package's view of it. Versions are labelled `task:version` because two
    packages both calling a round `v3` are two different rounds.

    `min_gain` and `patience` are passed straight to `sweep.convergence` — its
    defaults, not rules. A criterion nobody has scored on `metric` comes back
    with `converged` False and `best` None: unmeasured is not the same as
    finished, and it is the caller's business which one to act on.
    """
    ex = _experiments()
    sweep = _sibling("sweep")

    rows = ex.list_rows(conn, ex.KIND_CRITERION)
    if names is not None:
        wanted = set(names)
        rows = [r for r in rows if r["name"] in wanted]

    collected = []
    for row in rows:
        for entry in ex.history(conn, ex.KIND_CRITERION, row["name"]):
            collected.append((entry["recorded_at"], entry["id"], row["name"], entry))
    collected.sort(key=lambda item: (item[0], item[1]))

    scores: dict[str, dict] = {}
    summary: dict[str, dict] = {
        row["name"]: {
            "metric": metric,
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
        record = summary[name]
        record["runs"] += 1
        record["last_run_at"] = recorded_at
        if entry["task"] and entry["task"] not in seen_tasks[name]:
            seen_tasks[name].append(entry["task"])
        value = _score(entry, metric)
        if value is None:
            continue
        record["scored"] += 1
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
    """The criteria from `standing` that have not converged, by name.

    A convenience filter over data you already have — the thresholds that decide
    it were the ones you passed to `standing`, and the record it read is still
    there to argue with.
    """
    return sorted(name for name, record in standing.items() if not record["converged"])
