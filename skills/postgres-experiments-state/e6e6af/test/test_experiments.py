"""Exercise utils/experiments.py against a real Postgres, in a throwaway schema.

    /Users/raduparadovschi/dex/.venv/bin/python3 test/test_experiments.py

Run it by path, not `-m test.test_experiments`: `test` is a standard library
package and shadows this directory.

Everything that touches the database happens in `experiments_selftest`, created
at the start and dropped at the end, so a green run proves the DDL and the
queries without putting a single row in the shared `experiments` schema. The
checks that need no database — connection-string resolution, the identifier
guard, question validation — run first and would catch most mistakes even if
the server were down.
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "utils"))

import asyncpg  # noqa: E402

import experiments  # noqa: E402

SANDBOX = "experiments_selftest"
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
    except Exception as other:  # noqa: BLE001 — the wrong exception is a failure
        print(f"     (raised {type(other).__name__}: {other})")
        return False
    return False


# -- no database needed ----------------------------------------------------

def test_dsn() -> None:
    saved = {k: os.environ.pop(k, None)
             for k in ("EXPERIMENTS_DATABASE_URL", "DEX_DATABASE_URL")}
    try:
        ok("an explicit url wins", experiments.dsn("postgresql://x/y") == "postgresql://x/y")
        ok("nothing set falls back to local dex", experiments.dsn() == experiments.DEFAULT_DSN)
        os.environ["DEX_DATABASE_URL"] = "postgresql://dex/one"
        ok("DEX_DATABASE_URL is read", experiments.dsn() == "postgresql://dex/one")
        os.environ["EXPERIMENTS_DATABASE_URL"] = "postgresql://dex/two"
        ok("EXPERIMENTS_DATABASE_URL overrides it", experiments.dsn() == "postgresql://dex/two")
        ok("an argument still overrides both",
           experiments.dsn("postgresql://x/y") == "postgresql://x/y")
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value


def test_identifier_guard() -> None:
    ok("the default schema is accepted",
       any("CREATE SCHEMA IF NOT EXISTS" in s for s in experiments.schema_sql()))
    statements = experiments.schema_sql()
    guarded = [s for s in statements
               if ("IF NOT EXISTS" in s) or ("OR REPLACE" in s)
               or s.startswith(("DROP TRIGGER IF EXISTS", "COMMENT ON"))]
    triggers = [i for i, s in enumerate(statements) if s.lstrip().startswith("CREATE TRIGGER")]
    ok("every statement is guarded, bar the CREATE TRIGGERs",
       len(guarded) + len(triggers) == len(statements),
       f"{len(statements)} statements, {len(triggers)} triggers")
    ok("and each of those is preceded by its own DROP TRIGGER IF EXISTS",
       all(statements[i - 1].startswith("DROP TRIGGER IF EXISTS") for i in triggers),
       "which is what makes them re-runnable")
    ok("a schema name with a dash is refused",
       raises(ValueError, experiments.schema_sql, "not-an-ident"))
    ok("a quoted injection is refused",
       raises(ValueError, experiments.schema_sql, 'x"; DROP SCHEMA public; --'))
    ok("the schema name is quoted where it is used",
       f'"{SANDBOX}".state' in experiments.schema_sql(SANDBOX)[2])


def test_question_shape() -> None:
    yes_no = {"type": "noul", "instructions": "Is this a draft?"}
    choice = {"type": "choice", "instructions": "About?", "criteria": ["cost", "safety"]}
    ok("a yes/no question passes", experiments.check_question(yes_no) is yes_no)
    ok("a choice with options passes", experiments.check_question(choice) is choice)
    ok("a missing instructions is caught",
       raises(ValueError, experiments.check_question, {"type": "noul"}))
    ok("a choice with no options is caught",
       raises(ValueError, experiments.check_question,
              {"type": "choice", "instructions": "?"}))
    ok("an answer passed where a question belongs is caught",
       raises(ValueError, experiments.check_question, {"verdict": 0.9}))
    ok("a non-dict is caught", raises(TypeError, experiments.check_question, "draft?"))


def test_task_pattern() -> None:
    # The four forms `history.task` really takes, and the two near-misses the
    # shortcuts get wrong: a `LIKE 'pkg/%'` prefix drops the first and third,
    # an unbounded substring match accepts the last two.
    pattern = experiments.task_pattern("doc-1")
    for task in ("doc-1", "doc-1/r4", "construction/doc-1/rescore.py",
                 "construction/doc-1#v5"):
        ok(f"{task!r} is that package", bool(pattern.search(task)))
    for task in ("doc-13", "doc-1-2", "xdoc-1", "construction/doc-10"):
        ok(f"{task!r} is not", not pattern.search(task))
    ok("a name with regex characters is a literal",
       bool(experiments.task_pattern("doc+1").search("construction/doc+1/r2"))
       and not experiments.task_pattern("doc+1").search("doc1"))


def test_connect_failures() -> None:
    # Nothing listens on this port; the retry loop should try `retries` times
    # and then give up with the transport error rather than swallowing it.
    started = time.monotonic()
    ok("a dead server is retried, then raised",
       raises(OSError, experiments.connect, "postgresql://127.0.0.1:1/dex",
              retries=3, backoff=0.01, timeout=1))
    ok("the retries were quick", time.monotonic() - started < 5.0,
       f"{time.monotonic() - started:.2f}s for 3 attempts")

    # The module blocks on its own loop, so a caller already inside one would
    # deadlock. It has to say that instead.
    async def from_inside_a_loop():
        return raises(RuntimeError, experiments.connect, experiments.dsn())

    ok("connecting from inside a running loop raises rather than hanging",
       asyncio.run(from_inside_a_loop()))

    started = time.monotonic()
    ok("a missing database fails at once, not after a wait",
       raises(asyncpg.InvalidCatalogNameError, experiments.connect,
              experiments.dsn().rsplit("/", 1)[0] + "/no_such_database_here",
              retries=3, backoff=5.0))
    ok("fail-fast really did not sleep", time.monotonic() - started < 2.0,
       f"{time.monotonic() - started:.2f}s")


# -- against the database --------------------------------------------------

def test_schema(conn) -> None:
    first = experiments.ensure_schema(conn)
    ok("ensure_schema ran every statement", len(first) == len(experiments.schema_sql()))
    ok("the schema exists",
       conn.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname = $1", SANDBOX) == 1)
    tables = conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY 1", SANDBOX)
    ok("both tables exist", [t["tablename"] for t in tables] == ["history", "state"])
    ok("the view exists",
       conn.fetchval("SELECT count(*) FROM pg_views WHERE schemaname = $1 AND viewname = $2",
                     SANDBOX, "state_with_history") == 1)

    experiments.ensure_schema(conn)
    ok("running it twice is a no-op", True, "no error, tables unchanged")
    ok("nothing was dropped and recreated",
       conn.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = $1", SANDBOX) == 2)


def test_rows(conn) -> None:
    row = experiments.upsert_row(conn, "note", "alpha", {"a": 1})
    ok("an insert returns the row", row["name"] == "alpha" and row["body"] == {"a": 1})
    ok("jsonb comes back as a dict, not a string", isinstance(row["body"], dict))

    replaced = experiments.upsert_row(conn, "note", "alpha", {"b": 2})
    ok("a second upsert replaces the body by default", replaced["body"] == {"b": 2})
    ok("and keeps the same row", replaced["id"] == row["id"])

    merged = experiments.upsert_row(conn, "note", "alpha", {"c": 3}, merge=True)
    ok("merge=True keeps what was there", merged["body"] == {"b": 2, "c": 3})
    ok("updated_at moved with the update", merged["updated_at"] > merged["created_at"])

    ok("a name with an apostrophe is just a value",
       experiments.upsert_row(conn, "note", "O'Brien & Sons #3", {})["name"]
       == "O'Brien & Sons #3")

    ok("get_row finds it", experiments.get_row(conn, "note", "alpha")["id"] == row["id"])
    ok("get_row on a missing name is None",
       experiments.get_row(conn, "note", "nothing here") is None)
    ok("kind separates namespaces",
       experiments.upsert_row(conn, "other", "alpha", {})["id"] != row["id"])

    ok("list_rows filters by kind", len(experiments.list_rows(conn, "note")) == 2)
    ok("list_rows with no kind returns everything",
       len(experiments.list_rows(conn)) == 3)
    ok("limit is honoured", len(experiments.list_rows(conn, limit=1)) == 1)

    ok("a non-dict body is refused",
       raises(TypeError, experiments.upsert_row, conn, "note", "alpha", ["not", "a", "dict"]))


def test_history(conn) -> None:
    ok("appending to a missing row raises NoSuchRow",
       raises(experiments.NoSuchRow, experiments.append_history, conn, "note", "ghost"))

    experiments.upsert_row(conn, "note", "beta", {})
    entry = experiments.append_history(
        conn, "note", "beta", task="pkg-one",
        metrics={"j": 0.4}, detail={"verdict": 0.81, "confidence": 0.9},
        note="first pass")
    ok("an entry comes back with its id", entry["id"] > 0)
    ok("metrics round-trip as a dict", entry["metrics"] == {"j": 0.4})
    ok("the timestamp is timezone-aware", entry["recorded_at"].tzinfo is not None)

    experiments.append_history(conn, "note", "beta", task="pkg-two", metrics={"j": 0.62})
    entries = experiments.history(conn, "note", "beta")
    ok("history is oldest first", [e["task"] for e in entries] == ["pkg-one", "pkg-two"])
    ok("newest_first reverses it",
       [e["task"] for e in experiments.history(conn, "note", "beta", newest_first=True)]
       == ["pkg-two", "pkg-one"])
    ok("limit with newest_first gives the latest entry",
       experiments.history(conn, "note", "beta", limit=1, newest_first=True)[0]["task"]
       == "pkg-two")

    past = datetime.now(timezone.utc) - timedelta(days=30)
    experiments.append_history(conn, "note", "beta", task="pkg-old",
                               metrics={"j": 0.1}, recorded_at=past)
    ok("an explicit recorded_at sorts into place",
       [e["task"] for e in experiments.history(conn, "note", "beta")][0] == "pkg-old")

    ok("history of another row is separate",
       experiments.history(conn, "note", "alpha") == [])

    entry_id = entries[0]["id"]
    ok("an entry cannot be updated",
       raises(asyncpg.RaiseError, experiments.execute, conn,
              f'UPDATE "{SANDBOX}".history SET metrics = $1::jsonb WHERE id = $2',
              [{"j": 9.9}, entry_id]))
    ok("the entry is unchanged after the refusal",
       experiments.history(conn, "note", "beta")[1]["metrics"] == {"j": 0.4})


def test_view(conn) -> None:
    rows = experiments.query(
        conn,
        f'SELECT name, runs, history FROM "{SANDBOX}".state_with_history'
        " WHERE kind = $1 AND name = $2",
        ["note", "beta"])
    ok("the view counts the runs", rows[0]["runs"] == 3)
    ok("the view hands back the history as an array", isinstance(rows[0]["history"], list))
    ok("the array is in time order",
       [e["task"] for e in rows[0]["history"]] == ["pkg-old", "pkg-one", "pkg-two"])
    ok("a row with no history gets an empty array",
       experiments.query(conn, f'SELECT history FROM "{SANDBOX}".state_with_history'
                               " WHERE kind = $1 AND name = $2", ["note", "alpha"]
                         )[0]["history"] == [])


def test_criteria(conn) -> None:
    question = {"type": "noul", "instructions": "Is this an unfinished draft?"}
    row = experiments.upsert_criterion(conn, "is_draft", question, owner="pdf-qa", version="v3")
    ok("a criterion stores its question", row["body"]["question"] == question)
    ok("and whatever metadata came with it", row["body"]["owner"] == "pdf-qa")
    ok("it lands under the criterion kind", row["kind"] == experiments.KIND_CRITERION)
    ok("an answer cannot be registered as a question",
       raises(ValueError, experiments.upsert_criterion, conn, "bad", {"verdict": 0.2}))

    ok("criterion() reads it back",
       experiments.criterion(conn, "is_draft")["body"]["question"]["instructions"]
       == question["instructions"])
    ok("criteria() lists only criteria",
       [c["name"] for c in experiments.criteria(conn)] == ["is_draft"])

    experiments.record_run(conn, "is_draft", task="pdf-qa",
                           metrics={"youden_j": 0.51, "n": 240},
                           detail={"verdict": 0.88, "type": "noul", "confidence": 0.77,
                                   "probabilities": {"false": 0.12, "true": 0.88}})
    experiments.record_run(conn, "is_draft", task="pdf-qa-v4",
                           metrics={"youden_j": 0.62, "n": 240},
                           detail={"verdict": 0.91, "type": "noul", "confidence": 0.81,
                                   "probabilities": {"false": 0.09, "true": 0.91}})

    record = experiments.track_record(conn, "is_draft")
    ok("track_record counts the runs", record["runs"] == 2)
    ok("it names the tasks", record["tasks"] == ["pdf-qa", "pdf-qa-v4"])
    ok("it series-ifies each metric",
       [p["value"] for p in record["metrics"]["youden_j"]] == [0.51, 0.62])
    ok("the series carries the task that produced each point",
       record["metrics"]["youden_j"][1]["task"] == "pdf-qa-v4")
    ok("first and last run are set",
       record["first_run"] <= record["last_run"])

    ok("the per-option probabilities survive the round trip",
       experiments.history(conn, experiments.KIND_CRITERION, "is_draft"
                           )[1]["detail"]["probabilities"]["true"] == 0.91)

    empty = experiments.track_record(conn, "is_draft", kind="note")
    ok("a row with no history reports zero runs, not an error",
       empty["runs"] == 0 and empty["first_run"] is None and empty["metrics"] == {})


def test_free_form_sql(conn) -> None:
    rows = experiments.query(
        conn,
        f"""SELECT s.name, count(h.id) AS runs
            FROM "{SANDBOX}".state s LEFT JOIN "{SANDBOX}".history h ON h.state_id = s.id
            WHERE s.kind = $1 GROUP BY s.name ORDER BY runs DESC""",
        [experiments.KIND_CRITERION])
    ok("a free-form join works", rows[0]["name"] == "is_draft" and rows[0]["runs"] == 2)

    ok("a parameter is never SQL",
       experiments.query(conn, f'SELECT * FROM "{SANDBOX}".state WHERE name = $1',
                         ["alpha'; DROP TABLE state; --"]) == [])
    ok("the table is still there",
       conn.fetchval(f'SELECT count(*) FROM "{SANDBOX}".state') == 5,
       "3 notes, 1 other, 1 criterion")

    tag = experiments.execute(
        conn, f'UPDATE "{SANDBOX}".state SET body = body || $1::jsonb WHERE kind = $2',
        [{"reviewed": True}, "note"])
    ok("execute returns the command tag", tag == "UPDATE 3", tag)
    ok("the touch trigger fired on a raw update",
       experiments.get_row(conn, "note", "alpha")["body"]["reviewed"] is True)


def test_transaction_and_delete(conn) -> None:
    try:
        with conn.transaction():
            experiments.upsert_row(conn, "note", "rolled-back", {})
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    ok("a failed transaction leaves nothing behind",
       experiments.get_row(conn, "note", "rolled-back") is None)

    ok("delete_row reports what it did",
       experiments.delete_row(conn, "note", "beta") is True)
    ok("deleting again reports False",
       experiments.delete_row(conn, "note", "beta") is False)
    ok("the history went with it",
       conn.fetchval(f'SELECT count(*) FROM "{SANDBOX}".history h'
                     f' JOIN "{SANDBOX}".state s ON s.id = h.state_id'
                     " WHERE s.name = $1", "beta") == 0)


def test_entries_for_chunks(conn) -> None:
    """Runs last, and on kinds of its own: the tests above count rows and kinds."""
    experiments.upsert_row(conn, "chunked", "alpha", {})
    experiments.upsert_row(conn, "chunked_other", "beta", {})
    first = experiments.append_history(
        conn, "chunked", "alpha", task="pkg-one",
        detail={"version": "r1", "answers": {"c1": {"verdict": 0.9},
                                             "c2": {"verdict": 0.1}}})
    second = experiments.append_history(
        conn, "chunked_other", "beta", task="pkg-two",
        detail={"version": "r2", "answers": {"c2": {"verdict": 0.5},
                                             "c3": {"verdict": 0.4}}})
    # The two shapes that must be skipped rather than raise: no `answers` at all,
    # and an `answers` that is not an object. `jsonb_object_keys` errors on both.
    experiments.append_history(conn, "chunked", "alpha", task="pkg-three",
                               detail={"verdict": 0.7})
    experiments.append_history(conn, "chunked", "alpha", task="pkg-four",
                               detail={"answers": 0.99})

    hits = experiments.entries_for_chunks(conn, ["c1"], schema=SANDBOX)
    ok("a chunk id finds the entry that names it",
       [h["entry"] for h in hits] == [first["id"]])
    ok("the row carries kind, name, task and version",
       (hits[0]["kind"], hits[0]["name"], hits[0]["task"], hits[0]["version"])
       == ("chunked", "alpha", "pkg-one", "r1"))
    ok("chunks counts how many of the ids asked for that entry names",
       hits[0]["chunks"] == 1)

    shared = experiments.entries_for_chunks(conn, ["c2"], schema=SANDBOX)
    ok("a chunk two entries name finds both, oldest first",
       [h["entry"] for h in shared] == [first["id"], second["id"]])
    ok("the search crosses kinds by default",
       {h["kind"] for h in shared} == {"chunked", "chunked_other"})
    ok("kind restricts it to one namespace",
       [h["entry"] for h in
        experiments.entries_for_chunks(conn, ["c2"], kind="chunked_other",
                                       schema=SANDBOX)] == [second["id"]])
    ok("an entry with no answers object never appears",
       {h["task"] for h in experiments.entries_for_chunks(
           conn, ["c1", "c2", "c3"], schema=SANDBOX)} == {"pkg-one", "pkg-two"})
    ok("several ids are counted per entry",
       experiments.entries_for_chunks(conn, ["c2", "c3"],
                                      schema=SANDBOX)[-1]["chunks"] == 2)
    ok("a repeated id is not counted twice",
       experiments.entries_for_chunks(conn, ["c1", "c1"],
                                      schema=SANDBOX)[0]["chunks"] == 1)
    ok("no ids finds nothing",
       experiments.entries_for_chunks(conn, [], schema=SANDBOX) == [])
    ok("an unknown id finds nothing",
       experiments.entries_for_chunks(conn, ["nope"], schema=SANDBOX) == [])
    ok("any iterable of ids will do",
       len(experiments.entries_for_chunks(conn, {"c1": 1}.keys(),
                                          schema=SANDBOX)) == 1)


def test_connection_lifecycle(conn_url: str) -> None:
    with experiments.connect(conn_url, schema=SANDBOX) as c:
        ok("a connection works as a context manager", c.fetchval("SELECT 1") == 1)
        ok("it reports itself open", c.closed is False)
    ok("and is closed on the way out", c.closed is True)
    ok("a closed connection refuses to run",
       raises(RuntimeError, c.fetchval, "SELECT 1"))
    ok("closing twice is harmless", c.close() is None)


def main() -> None:
    test_dsn()
    test_identifier_guard()
    test_question_shape()
    test_task_pattern()
    test_connect_failures()

    url = experiments.dsn()
    print(f"\n--- against {url}, in schema {SANDBOX} ---")
    with experiments.connect(url, schema=SANDBOX) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SANDBOX}" CASCADE')
        try:
            test_schema(conn)
            test_rows(conn)
            test_history(conn)
            test_view(conn)
            test_criteria(conn)
            test_free_form_sql(conn)
            test_transaction_and_delete(conn)
            test_entries_for_chunks(conn)
        finally:
            conn.execute(f'DROP SCHEMA IF EXISTS "{SANDBOX}" CASCADE')
            ok("the sandbox schema was dropped",
               conn.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname = $1",
                             SANDBOX) == 0)
    test_connection_lifecycle(url)
    print(f"\n{PASSED} checks passed")


if __name__ == "__main__":
    main()
