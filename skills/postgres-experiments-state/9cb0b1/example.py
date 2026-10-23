"""The SKILL.md walkthrough, runnable: schema, criterion, run, track record.

    /Users/raduparadovschi/dex/.venv/bin/python3 <this skill>/example.py          # tidies up
    /Users/raduparadovschi/dex/.venv/bin/python3 <this skill>/example.py --keep   # keeps the row

Unlike `test/test_experiments.py`, which works in a throwaway schema, this runs
against the real `experiments` schema in the dex database — creating it if it is
not there yet, which is safe to repeat. The demo criterion it writes is deleted
on the way out unless you pass `--keep`, so the shared schema keeps no junk.

The question and the answer detail below are literal copies of the shapes
`laya.yes_no()` and `laya.detail(result)[qid]` produce. Nothing here imports
laya, and no model is loaded.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "utils"))

import experiments  # noqa: E402

NAME = "example_is_draft"

# What `laya.yes_no("Is this an unfinished draft?")` builds.
QUESTION = {
    "type": "noul",
    "instructions": "Is this an unfinished draft?",
}

# What `laya.detail(result)["is_draft"]` returns for one chunk: the verdict,
# with the confidence and the distribution that produced it.
DETAIL = {
    "verdict": 0.88,
    "type": "noul",
    "noul": 0.88,
    "confidence": 0.77,
    "probabilities": {"false": 0.12, "true": 0.88},
}


def main() -> None:
    keep = "--keep" in sys.argv
    url = experiments.dsn()
    print(f"connecting to {url}\n")

    with experiments.connect(url) as conn:
        # 1. The schema. Idempotent — run it at the top of every task.
        experiments.ensure_schema(conn)
        print(f"schema {conn.schema!r} is ready")

        # 2. Register the criterion: its name and the question schema itself,
        #    so a later task can ask exactly what this one asked.
        row = experiments.upsert_criterion(
            conn, NAME, QUESTION,
            owner="postgres-experiments-state",
            corpus="eDIL PACS monthly updates",
        )
        print(f"criterion {row['name']!r} is row {row['id']}, kind {row['kind']!r}")

        # 3. Log one execution of it. `metrics` is whatever this task scored;
        #    `detail` is the full answer, distribution included.
        entry = experiments.record_run(
            conn, NAME,
            task="postgres-experiments-state/example.py",
            metrics={"youden_j": 0.62, "chunks": 240},
            detail=DETAIL,
            note="illustrative numbers, not a real run",
        )
        print(f"logged run {entry['id']} at {entry['recorded_at']:%Y-%m-%d %H:%M:%S %Z}")

        # 4. Read the track record back — this is the call that answers
        #    "is this criterion worth more work?", with data rather than a verdict.
        record = experiments.track_record(conn, NAME)
        print(f"\n{record['name']}: {record['runs']} run(s) by {', '.join(record['tasks'])}")
        for metric, series in record["metrics"].items():
            points = ", ".join(f"{p['value']}" for p in series)
            print(f"  {metric}: {points}")

        # 5. And the same thing in free-form SQL, for anything the helpers
        #    above do not cover. Values go in `params`, never in the string.
        rows = experiments.query(
            conn,
            """SELECT s.name,
                      s.body->'question'->>'instructions' AS asked,
                      count(h.id)                          AS runs,
                      max((h.metrics->>'youden_j')::float) AS best_j
                 FROM experiments.state s
                 LEFT JOIN experiments.history h ON h.state_id = s.id
                WHERE s.kind = $1
             GROUP BY s.name, asked
             ORDER BY best_j DESC NULLS LAST""",
            [experiments.KIND_CRITERION],
        )
        print("\nevery registered criterion:")
        for r in rows:
            print(f"  {r['name']:<24} {r['runs']} run(s), best J {r['best_j']}  — {r['asked']}")

        if keep:
            print(f"\n--keep: {NAME!r} left in place")
        else:
            experiments.delete_row(conn, experiments.KIND_CRITERION, NAME)
            print(f"\nremoved the demo row {NAME!r}; the schema stays")


if __name__ == "__main__":
    main()
