"""Exercise ledger.py: what a run publishes, and what the pooled history says.

    /path/to/python skills/laya-decisions/<version>/test/test_ledger.py

Run it by path, not `-m test.test_ledger`: `test` is a standard library package
and shadows this directory.

The pure half — which questions a run publishes, and the records it builds —
runs first and needs no database. The rest works in a schema called
`ledger_selftest`, created at the start and dropped at the end, so a green run
never touches the shared `experiments` schema.

Runs are built with `sweep.build_run` rather than written out by hand: the
carried-forward behaviour this module has to respect is sweep's, and a
hand-made dict would let it drift.

It needs `postgres-experiments-state` as well as this skill. In a project with
both enabled they materialise into one `utils/` and `experiments` is simply
importable; run straight out of the skills tree it is not, so the sibling skill
is located below.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))


def _add_experiments() -> None:
    """Put `experiments` on sys.path, from wherever this machine keeps it."""
    try:
        import experiments  # noqa: F401
        return
    except ImportError:
        pass
    skills = HERE.parent.parent.parent
    versions = sorted((skills / "postgres-experiments-state").glob("*/utils"),
                      key=lambda p: p.stat().st_mtime)
    if not versions:
        raise SystemExit(
            "test_ledger needs the postgres-experiments-state skill: "
            f"no version found under {skills / 'postgres-experiments-state'}")
    sys.path.insert(0, str(versions[-1]))


_add_experiments()

import experiments  # noqa: E402
import sweep  # noqa: E402

import ledger  # noqa: E402

SANDBOX = "ledger_selftest"
PASSED = 0


def ok(label: str, condition: bool, note: str = "") -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAILED: {label}{'  — ' + note if note else ''}")
    PASSED += 1
    print(f"ok   {label}{'  — ' + note if note else ''}")


def raises(exc, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception as other:  # noqa: BLE001
        print(f"     (raised {type(other).__name__}: {other})")
        return False
    return False


QUESTIONS = {
    "is_draft": {"type": "noul", "instructions": "Is this an unfinished draft?"},
    "topic": {"type": "choice", "instructions": "What is this about?",
              "criteria": ["cost", "schedule", "safety"]},
}


def a_run(version, qids, *, scores=None, note="", previous=None):
    """One sweep run over `qids`, scored with J."""
    questions = {q: QUESTIONS[q] for q in qids}
    answers = {q: {"chunk1": 0.9, "chunk2": 0.2} for q in qids}
    metrics = {q: {"J": (scores or {}).get(q, 0.5), "n": 2} for q in qids}
    return sweep.build_run(version, questions, answers,
                           metrics=metrics, previous=previous, note=note)


# -- no database needed ----------------------------------------------------

def test_asked() -> None:
    first = a_run("v1", ["is_draft", "topic"])
    ok("a run publishes what it asked", ledger.asked(first) == ["is_draft", "topic"])

    second = a_run("v2", ["is_draft"], previous=first)
    ok("the carried-forward question is not republished",
       ledger.asked(second) == ["is_draft"],
       "topic is in the run file but v2 never re-ran it")
    ok("sweep really did carry it forward",
       set(second["questions"]) == {"is_draft", "topic"})

    handmade = {"questions": QUESTIONS, "carried_forward": ["topic"]}
    ok("without asked_this_run, carried_forward is still honoured",
       ledger.asked(handmade) == ["is_draft"])
    ok("a question id with no definition is dropped",
       ledger.asked({"asked_this_run": ["ghost"], "questions": {}}) == [])


def test_entries() -> None:
    run = a_run("v1", ["is_draft"], scores={"is_draft": 0.4}, note="first pass")
    [record] = ledger.entries(run, task="pkg/iterate.py")

    ok("the record is named for the question", record["name"] == "is_draft")
    ok("sweep's bookkeeping is stripped from the question",
       "last_run" not in record["question"] and "last_run" in run["questions"]["is_draft"])
    ok("what is left is the question Laya was given",
       record["question"] == QUESTIONS["is_draft"])
    ok("the metrics come across", record["metrics"] == {"J": 0.4, "n": 2})
    ok("the detail carries the version", record["detail"]["version"] == "v1")
    ok("and the wording that scored it", record["detail"]["question"] == QUESTIONS["is_draft"])
    ok("and the answers behind it", record["detail"]["answers"] == {"chunk1": 0.9, "chunk2": 0.2})
    ok("the note comes across", record["note"] == "first pass")

    [lean] = ledger.entries(run, task="pkg", include_answers=False)
    ok("include_answers=False drops the per-chunk answers", "answers" not in lean["detail"])
    ok("but keeps the wording", lean["detail"]["question"] == QUESTIONS["is_draft"])

    ok("an empty note becomes None, not an empty string",
       ledger.entries(a_run("v1", ["topic"]), task="pkg")[0]["note"] is None)


# -- against the database --------------------------------------------------

def test_publish(conn) -> None:
    first = a_run("v1", ["is_draft", "topic"], scores={"is_draft": 0.40, "topic": 0.10})
    written = ledger.publish(conn, first, task="pkg-a/iterate.py")
    ok("publishing a run writes one entry per question asked", len(written) == 2)
    ok("both criteria are registered",
       [r["name"] for r in experiments.criteria(conn)] == ["is_draft", "topic"])

    second = a_run("v2", ["is_draft"], scores={"is_draft": 0.62}, previous=first)
    ledger.publish(conn, second, task="pkg-a/iterate.py")
    ok("the second iteration appends to the same criterion",
       len(ledger.history(conn, "is_draft")) == 2)
    ok("and does not invent a run for the carried-forward one",
       len(ledger.history(conn, "topic")) == 1,
       "topic was in the v2 run file but v2 never asked it")

    entry = ledger.history(conn, "is_draft")[1]
    ok("the entry records who ran it", entry["task"] == "pkg-a/iterate.py")
    ok("the metric is stored", entry["metrics"]["J"] == 0.62)
    ok("the wording is stored beside the score",
       entry["detail"]["question"]["instructions"] == QUESTIONS["is_draft"]["instructions"])
    ok("the criterion row tracks the latest version",
       experiments.criterion(conn, "is_draft")["body"]["last_run"] == "v2")

    # One bad question in the middle must take the whole iteration down with it,
    # or the run is counted as an execution that scored only half its questions.
    broken = a_run("v3", ["is_draft", "topic"])
    broken["questions"]["topic"] = {"type": "choice", "instructions": "no options"}
    before = len(ledger.history(conn, "is_draft"))
    ok("a malformed question aborts the publish",
       raises(ValueError, ledger.publish, conn, broken, task="pkg-a/iterate.py"))
    ok("and the transaction rolled the rest back",
       len(ledger.history(conn, "is_draft")) == before,
       "is_draft would otherwise have been written before topic failed")


def test_standing(conn) -> None:
    # A second package, scoring the same criterion — this is the whole point.
    ledger.publish(conn, a_run("v3", ["is_draft"], scores={"is_draft": 0.63}),
                   task="pkg-b/summarise.py")
    ledger.publish(conn, a_run("v4", ["is_draft"], scores={"is_draft": 0.635}),
                   task="pkg-b/summarise.py")

    record = ledger.standing(conn)
    ok("every registered criterion is reported", set(record) == {"is_draft", "topic"})

    draft = record["is_draft"]
    ok("runs are pooled across tasks", draft["runs"] == 4)
    ok("and the tasks are named",
       draft["tasks"] == ["pkg-a/iterate.py", "pkg-b/summarise.py"])
    ok("the best version is labelled task:version",
       draft["best_version"] == "pkg-a/iterate.py:v2", draft["best_version"])
    ok("the best score is the one that cleared min_gain", draft["best"] == 0.62)
    ok("the latest score is the latest, not the best", draft["latest"] == 0.635)
    ok("two later rounds that did not clear it count as converged",
       draft["converged"] is True and draft["rounds_since_best"] == 2)
    ok("the question is reported with it",
       draft["question"]["instructions"] == QUESTIONS["is_draft"]["instructions"])
    ok("last_run_at is set", draft["last_run_at"] is not None)

    topic = record["topic"]
    ok("a criterion with one round has not converged", topic["converged"] is False)
    ok("its one run is counted", topic["runs"] == 1 and topic["scored"] == 1)

    ok("patience is adjustable",
       ledger.standing(conn, patience=3)["is_draft"]["converged"] is False)
    ok("min_gain is adjustable",
       ledger.standing(conn, min_gain=0.001)["is_draft"]["best_version"]
       == "pkg-b/summarise.py:v4")
    ok("names= narrows it", set(ledger.standing(conn, names=["topic"])) == {"topic"})

    other = ledger.standing(conn, metric="n")
    ok("metric= reads a different key", other["is_draft"]["best"] == 2.0)
    missing = ledger.standing(conn, metric="recall")
    ok("a metric nobody recorded leaves best None",
       missing["is_draft"]["best"] is None and missing["is_draft"]["scored"] == 0)
    ok("and unmeasured does not count as converged",
       missing["is_draft"]["converged"] is False)
    ok("the runs are still counted though", missing["is_draft"]["runs"] == 4)


def test_worth_improving(conn) -> None:
    record = ledger.standing(conn)
    ok("only the unconverged come back", ledger.worth_improving(record) == ["topic"])
    ok("raising patience puts a criterion back on the list",
       ledger.worth_improving(ledger.standing(conn, patience=3))
       == ["is_draft", "topic"])
    ok("an empty standing is an empty list", ledger.worth_improving({}) == [])


def test_unscored_criterion(conn) -> None:
    # Registered by hand, never run: it must not read as finished.
    experiments.upsert_criterion(conn, "never_run", QUESTIONS["topic"])
    record = ledger.standing(conn)["never_run"]
    ok("a criterion with no history reports zero runs", record["runs"] == 0)
    ok("it has no best score", record["best"] is None)
    ok("it is not converged", record["converged"] is False)
    ok("and it shows up as worth improving",
       "never_run" in ledger.worth_improving(ledger.standing(conn)))


def main() -> None:
    test_asked()
    test_entries()

    url = experiments.dsn()
    print(f"\n--- against {url}, in schema {SANDBOX} ---")
    with experiments.connect(url, schema=SANDBOX) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SANDBOX}" CASCADE')
        try:
            experiments.ensure_schema(conn)
            test_publish(conn)
            test_standing(conn)
            test_worth_improving(conn)
            test_unscored_criterion(conn)
        finally:
            conn.execute(f'DROP SCHEMA IF EXISTS "{SANDBOX}" CASCADE')
            ok("the sandbox schema was dropped",
               conn.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname = $1",
                             SANDBOX) == 0)
    print(f"\n{PASSED} checks passed")


if __name__ == "__main__":
    main()
