"""Exercise utils/sweep.py: hashing, carry-forward, foreign keys, scoring.

    /path/to/python skills/laya-decisions/<version>/test/test_sweep.py

Run it by path, not `-m test.test_sweep`: `test` is a standard library package
and shadows this directory.

Needs neither Laya nor a corpus — the storage rules are what is being tested,
so a handful of literal chunks makes every failure obvious and the whole file
runs in under a second.
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))

import sweep  # noqa: E402

PASSED = 0


def ok(label: str, condition: bool, note: str = "") -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAILED: {label}{'  — ' + note if note else ''}")
    PASSED += 1
    print(f"ok   {label}{'  — ' + note if note else ''}")


def test_ids() -> None:
    a = sweep.chunk_id("the same text")
    ok("the same text hashes the same twice", a == sweep.chunk_id("the same text"))
    ok("different text hashes differently", a != sweep.chunk_id("other text"))
    ok("ids are the configured length", len(a) == sweep.ID_LENGTH, a)
    ok("length is adjustable", len(sweep.chunk_id("x", length=8)) == 8)


def test_store(tmp: Path) -> None:
    chunks = {sweep.chunk_id(t): {"text": t, "tokens": len(t.split())}
              for t in ["alpha beta", "gamma delta epsilon"]}
    sweep.save_chunks(tmp / "chunks.json", chunks, document="demo.pdf")
    back = sweep.load_chunks(tmp / "chunks.json")
    ok("the store round-trips", back["chunks"] == chunks)
    ok("metadata survives", back["meta"]["document"] == "demo.pdf")


def test_carry_forward() -> None:
    q = {"a": {"type": "noul", "prompt": "A?"},
         "b": {"type": "noul", "prompt": "B?"}}
    first = sweep.build_run(
        "v1", q, {"a": {"c1": 1}, "b": {"c1": 2}},
        metrics={"a": {"J": 0.5}, "b": {"J": 0.1}},
    )
    ok("a first run asks everything", first["asked_this_run"] == ["a", "b"])
    ok("and carries nothing", first["carried_forward"] == [])
    ok("every question is stamped with the run",
       all(d["last_run"] == "v1" for d in first["questions"].values()))

    # Second run re-asks only b, with a new wording.
    second = sweep.build_run(
        "v2", {"b": {"type": "noul", "prompt": "B, reworded?"}},
        {"b": {"c1": 9}}, metrics={"b": {"J": 0.7}}, previous=first,
    )
    ok("a subset run reports what it asked", second["asked_this_run"] == ["b"])
    ok("and what it carried", second["carried_forward"] == ["a"])
    ok("the whole set is still described", set(second["questions"]) == {"a", "b"})
    ok("the re-asked question is stamped with this run",
       second["questions"]["b"]["last_run"] == "v2")
    ok("the carried question keeps the run that asked it",
       second["questions"]["a"]["last_run"] == "v1")
    ok("the re-asked answer is the new one", second["answers"]["b"]["c1"] == 9)
    ok("the carried answer is preserved", second["answers"]["a"]["c1"] == 1)
    ok("the carried metric is preserved", second["metrics"]["a"]["J"] == 0.5)
    ok("the new wording replaced the old",
       second["questions"]["b"]["prompt"] == "B, reworded?")

    # Third run touches neither: both must carry, keeping their own versions.
    third = sweep.build_run("v3", {}, {}, previous=second)
    ok("a run that asks nothing carries everything",
       third["carried_forward"] == ["a", "b"])
    ok("each keeps its own last_run across two hops",
       (third["questions"]["a"]["last_run"], third["questions"]["b"]["last_run"])
       == ("v1", "v2"))


def test_foreign_keys(tmp: Path) -> None:
    chunks = {sweep.chunk_id("held"): {"text": "held"}}
    store = sweep.save_chunks(tmp / "fk.json", chunks)
    good = sweep.build_run("v1", {"a": {}}, {"a": {sweep.chunk_id("held"): 1}})
    ok("a run whose ids resolve reports no gaps",
       sweep.missing_chunks(good, store) == {})
    bad = sweep.build_run("v1", {"a": {}}, {"a": {"deadbeef0000": 1}})
    ok("a dangling id is reported",
       sweep.missing_chunks(bad, store) == {"a": ["deadbeef0000"]})


def test_scores() -> None:
    truth = {"1": True, "2": True, "3": False, "4": False}
    perfect = sweep.binary_scores(truth, {"1": 0.9, "2": 0.8, "3": 0.1, "4": 0.2})
    ok("a perfect question scores J = 1", perfect["J"] == 1.0)
    ok("and counts what is in the text", perfect["in_text"] == 2)

    never = sweep.binary_scores(truth, dict.fromkeys(truth, 0.0))
    ok("a question that never fires scores J = 0", never["J"] == 0.0)
    ok("even though agreement looks respectable", never["agreement"] == 0.5)

    always = sweep.binary_scores(truth, dict.fromkeys(truth, 1.0))
    ok("a question that always fires also scores J = 0", always["J"] == 0.0)
    ok("booleans work as well as probabilities",
       sweep.binary_scores(truth, {"1": True, "2": True, "3": False, "4": False})["J"]
       == 1.0)


def test_convergence() -> None:
    history = {
        "v1": {"a": 0.10, "b": 0.50},
        "v2": {"a": 0.60, "b": 0.51},  # a jumps; b's +0.01 is not material
        "v3": {"a": 0.90, "b": 0.20},  # a jumps again; b falls away
        "v4": {"a": 0.91, "b": 0.30},  # a's +0.01 is not material either
    }
    out = sweep.convergence(history)
    ok("the version that last really gained is the best",
       out["a"]["best_version"] == "v3", f"a best {out['a']['best']}")
    ok("a later wobble does not take the crown", out["a"]["latest"] == 0.91)
    ok("one round after the best is not yet converged",
       out["a"]["converged"] is False,
       f"{out['a']['rounds_since_best']} round since")
    ok("a question that peaked early and fell is converged",
       out["b"]["converged"] is True, f"best {out['b']['best_version']}")
    ok("an immaterial gain never became the best",
       out["b"]["best_version"] == "v1")
    ok("patience is adjustable",
       sweep.convergence(history, patience=1)["a"]["converged"] is True)
    ok("a gain under min_gain does not reset the clock",
       sweep.convergence({"v1": {"c": 0.5}, "v2": {"c": 0.505},
                          "v3": {"c": 0.5}})["c"]["converged"] is True)
    ok("raising min_gain can make a real gain immaterial",
       sweep.convergence(history, min_gain=0.95)["a"]["best_version"] == "v1")


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_ids()
        test_store(tmp)
        test_carry_forward()
        test_foreign_keys(tmp)
        test_scores()
        test_convergence()
    print(f"\n{PASSED} checks passed")


if __name__ == "__main__":
    main()
