"""Exercise utils/jev_ledger.py against the real database. Run it; it prints and asserts.

    /path/to/python skills/jev-decisions/<version>/test/test_jev_ledger.py

Run it by path, not `-m test.test_jev_ledger`: `test` is a standard library
package and shadows this directory.

Needs `experiments` from the `postgres-experiments-state` skill, `sweep` from
`laya-decisions` (for the `standing` checks only), and a reachable Postgres. It
spends **no credits** — Jev is never called; what is under test is what gets
written, and the answers are canned.

Everything that touches the database happens in a schema called
`jev_ledger_selftest`, created at the start and dropped at the end, so a green
run proves the writes without putting a row in the shared `experiments.state` —
which holds thousands of `laya_criterion` rows that other packages read.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))
# `experiments` and `sweep` come from the sibling skills, the way a project's
# merged utils/ directory presents them.
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "skills" / "postgres-experiments-state" / "097a8b" / "utils"))
sys.path.insert(0, str(REPO / "skills" / "laya-decisions" / "a29d3c" / "utils"))

import jev  # noqa: E402
import jev_ledger  # noqa: E402

SCHEMA = "jev_ledger_selftest"


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"  ok  {label}{' — ' + detail if detail else ''}")


QUESTIONS = {
    "change_order": jev.yes_no("Does this passage discuss a change order?"),
    "topic": jev.choice("What is this mainly about?", {
        "cost": "money", "schedule": "time", "other": "anything else"}),
}


def run(version, metrics, *, asked=None, note=None):
    """A sweep-shaped run dict, the same shape `ledger.publish` takes."""
    return {
        "version": version,
        "questions": dict(QUESTIONS),
        "asked_this_run": list(asked if asked is not None else QUESTIONS),
        "carried_forward": [q for q in QUESTIONS
                            if asked is not None and q not in asked],
        "answers": {qid: {"verdict": 0.9, "type": "noul", "confidence": 0.8}
                    for qid in QUESTIONS},
        "metrics": metrics,
        "note": note,
    }


print("no database needed")
check("the Jev kind is its own", jev_ledger.KIND == "jev_criterion")
check("and it is not Laya's", jev_ledger.KIND != jev_ledger.KIND_LAYA
      and jev_ledger.KIND_LAYA == "laya_criterion")
check("the default model is the one jev.py sends",
      jev_ledger.model_name() == jev.MODEL, jev_ledger.model_name())
check("a Client's model is read off it",
      jev_ledger.model_name(jev.Client("sk_x", model="jev-9")) == "jev-9")
check("a plain string passes through", jev_ledger.model_name("jev-1.13.0") == "jev-1.13.0")

only_one = run("v2", {"change_order": {"J": 0.5}}, asked=["change_order"])
check("asked() ignores what a run carried forward",
      jev_ledger.asked(only_one) == ["change_order"])
records = jev_ledger.entries(only_one, task="selftest", model="jev-latest")
check("entries() writes one record per question asked", len(records) == 1)
check("each record carries the model", records[0]["model"] == "jev-latest"
      and records[0]["detail"]["model"] == "jev-latest")
check("the question travels with it, minus sweep's bookkeeping",
      records[0]["question"] == QUESTIONS["change_order"])

print("\ndatabase, in a throwaway schema")
import experiments as ex  # noqa: E402


def shared_counts():
    """Rows per kind in the real `experiments.state`, to prove none were added."""
    with ex.connect() as live:
        return {r["kind"]: r["n"] for r in ex.query(
            live, "SELECT kind, count(*) AS n FROM experiments.state GROUP BY kind")}


before = shared_counts()
print(f"  ..  shared schema before: {before}")

conn = ex.connect(schema=SCHEMA)
try:
    ex.execute(conn, f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    ex.ensure_schema(conn, schema=SCHEMA)
    print(f"  ..  writing into {SCHEMA}, not the shared schema")

    first = jev_ledger.publish(conn, run("v1", {
        "change_order": {"J": 0.40}, "topic": {"J": 0.55}}),
        task="selftest/round1", schema=SCHEMA)
    check("publish writes one entry per criterion asked", len(first) == 2)

    rows = jev_ledger.criteria(conn, schema=SCHEMA)
    check("both criteria are registered", sorted(r["name"] for r in rows) ==
          ["change_order", "topic"])
    check("every row is the Jev kind", {r["kind"] for r in rows} == {jev_ledger.KIND},
          str({r["kind"] for r in rows}))
    check("every row records the model",
          {(r["body"] or {}).get("model") for r in rows} == {jev.MODEL})
    check("the question round-trips as a question",
          ex.check_question(jev_ledger.criterion(
              conn, "topic", schema=SCHEMA)["body"]["question"])["type"] == "choice")

    # The whole point of the separate kind: a Laya row of the same name must be
    # a different row, and neither may see the other's history.
    ex.upsert_criterion(conn, "change_order", QUESTIONS["change_order"],
                        schema=SCHEMA)
    ex.record_run(conn, "change_order", task="selftest/laya",
                  metrics={"J": 0.91}, detail={"verdict": 0.4}, schema=SCHEMA)
    both = ex.query(conn, f'SELECT kind, name FROM "{SCHEMA}".state '
                          "WHERE name = $1 ORDER BY kind", ["change_order"])
    check("one name, two rows, distinguished by kind",
          [r["kind"] for r in both] == ["jev_criterion", "laya_criterion"], str(both))
    check("the Jev history does not see Laya's entry",
          [e["task"] for e in jev_ledger.history(conn, "change_order", schema=SCHEMA)]
          == ["selftest/round1"])
    check("and Laya's does not see Jev's",
          [e["task"] for e in ex.history(conn, ex.KIND_CRITERION, "change_order",
                                         schema=SCHEMA)] == ["selftest/laya"])

    jev_ledger.publish(conn, run("v2", {
        "change_order": {"J": 0.62}, "topic": {"J": 0.56}}),
        task="selftest/round2", schema=SCHEMA)
    jev_ledger.publish(conn, run("v3", {
        "change_order": {"J": 0.61}, "topic": {"J": 0.57}}),
        task="selftest/round2", schema=SCHEMA)

    record = jev_ledger.track(conn, "change_order", schema=SCHEMA)
    check("track() reports the Jev runs only", record["runs"] == 3, str(record["runs"]))
    check("track() keeps the metric as a series",
          [point["value"] for point in record["metrics"]["J"]] == [0.40, 0.62, 0.61],
          str(record["metrics"]["J"]))

    stand = jev_ledger.standing(conn, schema=SCHEMA)
    check("standing() covers both criteria", sorted(stand) == ["change_order", "topic"])
    check("standing() names the model", stand["change_order"]["model"] == jev.MODEL)
    check("standing() finds the best round", stand["change_order"]["best"] == 0.62
          and stand["change_order"]["best_version"] == "selftest/round2:v2",
          str(stand["change_order"]["best_version"]))
    check("standing() pools every task", stand["change_order"]["tasks"] ==
          ["selftest/round1", "selftest/round2"], str(stand["change_order"]["tasks"]))
    check("standing() ignores Laya's 0.91 in the same-named row",
          stand["change_order"]["best"] != 0.91)
    check("a criterion that stopped paying is flagged",
          stand["topic"]["converged"] is True, str(stand["topic"]))
    check("worth_improving filters on that",
          jev_ledger.worth_improving(stand) == ["change_order"],
          str(jev_ledger.worth_improving(stand)))

    print("\nmodel as the distinguishing value")
    jev_ledger.register(conn, "topic", QUESTIONS["topic"], model="jev-next",
                        schema=SCHEMA)
    jev_ledger.record(conn, "topic", task="selftest/next", model="jev-next",
                      metrics={"J": 0.80}, detail={"verdict": "cost"}, schema=SCHEMA)
    check("one model's entries can be read alone",
          [e["detail"]["model"] for e in
           jev_ledger.history(conn, "topic", model="jev-next", schema=SCHEMA)]
          == ["jev-next"])
    check("standing(model=) restricts the sequence",
          jev_ledger.standing(conn, model="jev-next", schema=SCHEMA)["topic"]["runs"] == 1)
    by_model = ex.query(
        conn, f"""SELECT h.detail->>'model' AS model, count(*) AS entries
                    FROM "{SCHEMA}".history h JOIN "{SCHEMA}".state s ON s.id = h.state_id
                   WHERE s.kind = $1 GROUP BY 1 ORDER BY 1""", [jev_ledger.KIND])
    check("the model is queryable in SQL",
          [(r["model"], r["entries"]) for r in by_model] ==
          [("jev-latest", 6), ("jev-next", 1)], str(by_model))

    print("\ncomparing the two models on one criterion")
    side = jev_ledger.compare(conn, "change_order", schema=SCHEMA)
    check("compare() reports both kinds",
          sorted(side["change_order"]) == ["jev_criterion", "laya_criterion"])
    check("compare() reads Laya's best", side["change_order"]["laya_criterion"]["best"] == 0.91)
    check("compare() reads Jev's best", side["change_order"]["jev_criterion"]["best"] == 0.62)
    check("compare() over every name finds the ones only one model has",
          jev_ledger.compare(conn, schema=SCHEMA)["topic"]["laya_criterion"] is None)

    print("\nreading back which chunks were already scored")
    # A run whose answers are keyed by chunk id, which is what a sweep over a
    # document publishes and what a resumed sweep has to read back.
    per_chunk = run("v4", {"change_order": {"J": 0.6}}, asked=["change_order"])
    per_chunk["answers"] = {"change_order": {
        "c1": {"verdict": 0.9, "type": "noul"},
        "c2": {"verdict": 0.1, "type": "noul"}}}
    jev_ledger.publish(conn, per_chunk, task="selftest/sweep", schema=SCHEMA)

    seen = jev_ledger.coverage(conn, schema=SCHEMA)
    check("coverage() returns one record per entry",
          len(seen) == len(jev_ledger.history(conn, "change_order", schema=SCHEMA))
          + len(jev_ledger.history(conn, "topic", schema=SCHEMA)), str(len(seen)))
    check("oldest first", [rec["entry"] for rec in seen] ==
          sorted(rec["entry"] for rec in seen))
    swept = [rec for rec in seen if rec["task"] == "selftest/sweep"]
    check("the chunks a run scored come back as a set",
          len(swept) == 1 and swept[0]["chunks"] == {"c1", "c2"}, str(swept))
    check("with the run version, the model and the question beside them",
          swept[0]["version"] == "v4" and swept[0]["model"] == jev.MODEL
          and swept[0]["question"] == QUESTIONS["change_order"], str(swept[0]))
    check("names= bounds it to the criteria asked for",
          {rec["name"] for rec in
           jev_ledger.coverage(conn, ["topic"], schema=SCHEMA)} == {"topic"})
    check("an entry whose answers are not per chunk reports no chunks",
          all(not rec["chunks"] for rec in seen if rec["task"] == "selftest/next"))
    check("Laya's entry under the same name is not in it",
          not [rec for rec in seen if rec["task"] == "selftest/laya"])
    check("by default the verdicts are left out",
          all("answers" not in rec for rec in seen))

    withal = jev_ledger.coverage(conn, schema=SCHEMA, answers=True)
    check("answers=True changes nothing else",
          [{k: v for k, v in rec.items() if k != "answers"} for rec in withal] == seen,
          str(withal[:1]))
    scored = [rec for rec in withal if rec["task"] == "selftest/sweep"][0]
    check("and hands back the verdict per chunk, for a resume that reuses it",
          scored["answers"] == per_chunk["answers"]["change_order"], str(scored["answers"]))
    check("pair_index over them keys the same triples either way",
          set(jev_ledger.pair_index(withal)) == set(jev_ledger.pair_index(seen)))
finally:
    ex.execute(conn, f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    conn.close()
    print(f"\n  ..  dropped {SCHEMA}")

print("\nthe shared schema is untouched")
after = shared_counts()
check("not one row was added to the shared experiments.state",
      after == before, f"before {before} / after {after}")

print("\nall checks passed")
