"""Keep shared project state in the dex Postgres, in an `experiments` schema.

A task's findings die with its package unless something outlives it. This is
that something: a schema in the dex database that any task can write a row to
and any later task can read back, plus an append-only history beside each row
so a thing that is run repeatedly accumulates a track record instead of a
single latest number.

The case it was built for is a Laya criterion — a name, the question schema
`laya.yes_no`/`choice`/`score` built, and one history entry per task execution
that asked it, carrying the metric and the full answer detail:

    with experiments.connect() as conn:
        experiments.ensure_schema(conn)
        experiments.upsert_criterion(conn, "is_draft", question, owner="pdf-qa")
        experiments.record_run(conn, "is_draft", task="pdf-qa",
                               metrics={"youden_j": 0.62},
                               detail=laya.detail(result))
        past = experiments.track_record(conn, "is_draft")

Nothing here is specific to Laya — `upsert_row` and `append_history` take any
`kind` — and this module never imports `laya`. A criterion is validated by
shape alone, so the two skills stay independent.

Three things to know before using it:

*   **It is synchronous, over asyncpg.** `asyncpg` is the only driver in the
    project's environment. Each `Connection` owns a private event loop and
    blocks on it, so callers write ordinary straight-line code. That also means
    it cannot be called from inside a running event loop; `connect` says so
    rather than deadlocking.
*   **Every value goes in as a parameter.** `$1`-style placeholders, always.
    The single identifier that cannot be a parameter is the schema name, and
    that one is checked against `[a-z_][a-z0-9_]*` and quoted before it is
    interpolated.
*   **`connect` retries; statements do not.** A connection that drops
    mid-statement leaves no way to tell a write that never arrived from one
    that arrived and lost its acknowledgement, so re-running it is not this
    module's decision to make. Reconnect and decide.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Sequence

import asyncpg

#: The schema every table here lives in. Pass `schema=` to work in another one.
SCHEMA = "experiments"

#: Used when neither an argument nor the environment names a database.
DEFAULT_DSN = "postgresql://127.0.0.1/dex"

#: `kind` for a row holding a Laya criterion. Any other string is equally valid.
KIND_CRITERION = "laya_criterion"

#: Connection attempts, and the first gap between them — it doubles each retry.
CONNECT_RETRIES = 3
CONNECT_BACKOFF = 0.5
CONNECT_TIMEOUT = 10.0

# Deliberately narrow: a database that is starting up, a socket that is not
# listening yet, a server at its connection limit. An authentication failure or
# a missing database is a mistake, not a hiccup, and waiting will not fix it.
_TRANSIENT = (
    OSError,
    asyncio.TimeoutError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class NoSuchRow(LookupError):
    """Raised when a row is addressed by (kind, name) and no such row exists."""


def dsn(url: str | None = None) -> str:
    """The database to talk to: the argument, then the environment, then local dex.

    `EXPERIMENTS_DATABASE_URL` wins over `DEX_DATABASE_URL`, so a task can be
    pointed at a scratch database without touching dex's own configuration.
    """
    return (
        url
        or os.environ.get("EXPERIMENTS_DATABASE_URL")
        or os.environ.get("DEX_DATABASE_URL")
        or DEFAULT_DSN
    )


def _ident(schema: str | None) -> str:
    """Check a schema name and return it double-quoted, ready to interpolate."""
    name = schema or SCHEMA
    if not _IDENT.match(name):
        raise ValueError(
            f"{name!r} is not a plain lower-case identifier; "
            "a schema name is interpolated into SQL and will not be quoted for you"
        )
    return f'"{name}"'


class Connection:
    """One database connection, driven synchronously from its own event loop.

    Use it as a context manager — `with experiments.connect() as conn:` — or
    call `close()` yourself. `fetch`, `fetchone`, `fetchval` and `execute` take
    `$1`-style placeholders and a value per placeholder.
    """

    def __init__(self, raw: asyncpg.Connection, loop: asyncio.AbstractEventLoop,
                 *, schema: str | None = None) -> None:
        self._raw = raw
        self._loop = loop
        self.schema = schema or SCHEMA

    # -- plumbing ---------------------------------------------------------

    @property
    def _live(self) -> asyncpg.Connection:
        # Reached before the coroutine is built: `self._raw.fetch(...)` would
        # already have raised AttributeError by the time `_run` saw it.
        if self._raw is None:
            raise RuntimeError("this connection is closed; call experiments.connect() again")
        return self._raw

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    @property
    def closed(self) -> bool:
        """True once `close()` has run, or the server has hung up."""
        return self._raw is None or self._raw.is_closed()

    def close(self) -> None:
        """Close the connection and the loop that drove it. Safe to call twice."""
        if self._raw is not None:
            try:
                self._loop.run_until_complete(self._raw.close())
            finally:
                self._raw = None
                self._loop.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- statements -------------------------------------------------------

    def fetch(self, sql: str, *params: Any) -> list[dict]:
        """Run a query and return every row as a plain dict."""
        rows = self._run(self._live.fetch(sql, *params))
        return [dict(r) for r in rows]

    def fetchone(self, sql: str, *params: Any) -> dict | None:
        """Run a query and return its first row as a dict, or None if there is none."""
        row = self._run(self._live.fetchrow(sql, *params))
        return dict(row) if row is not None else None

    def fetchval(self, sql: str, *params: Any) -> Any:
        """Run a query and return the first column of its first row."""
        return self._run(self._live.fetchval(sql, *params))

    def execute(self, sql: str, *params: Any) -> str:
        """Run a statement and return the command tag, e.g. `INSERT 0 1`."""
        return self._run(self._live.execute(sql, *params))

    @contextmanager
    def transaction(self) -> Iterator["Connection"]:
        """Wrap a block in one transaction, rolled back if it raises."""
        tx = self._live.transaction()
        self._run(tx.start())
        try:
            yield self
        except BaseException:
            self._run(tx.rollback())
            raise
        else:
            self._run(tx.commit())


async def _open(url: str, timeout: float) -> asyncpg.Connection:
    raw = await asyncpg.connect(url, timeout=timeout)
    # Without these, jsonb columns arrive as strings and dicts cannot be passed
    # as parameters — every caller would end up json.dumps-ing at both ends.
    for kind in ("json", "jsonb"):
        await raw.set_type_codec(
            kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
    return raw


def connect(url: str | None = None, *, schema: str | None = None,
            retries: int = CONNECT_RETRIES, backoff: float = CONNECT_BACKOFF,
            timeout: float = CONNECT_TIMEOUT) -> Connection:
    """Connect to the dex database, retrying a server that is not ready yet.

    `retries` counts attempts in total, `backoff` is the first gap in seconds
    and doubles after each failure. Only transient failures are retried: a bad
    password or a missing database raises immediately.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "experiments.connect() is synchronous and needs a thread with no "
            "running event loop; call it before starting one, or in a worker thread"
        )

    url = dsn(url)
    loop = asyncio.new_event_loop()
    delay = backoff
    for attempt in range(1, max(1, retries) + 1):
        try:
            raw = loop.run_until_complete(_open(url, timeout))
        except _TRANSIENT:
            if attempt == max(1, retries):
                loop.close()
                raise
            time.sleep(delay)
            delay *= 2
        except BaseException:
            loop.close()
            raise
        else:
            return Connection(raw, loop, schema=schema)
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------

def schema_sql(schema: str | None = None) -> list[str]:
    """The statements `ensure_schema` runs, in order — every one of them idempotent.

    Exposed so a caller can print, review or ship the DDL without running it.
    """
    s = _ident(schema)
    return [
        f"CREATE SCHEMA IF NOT EXISTS {s}",
        f"""COMMENT ON SCHEMA {s} IS
            'Shared state written by dex tasks: one row per named thing, with an append-only history.'""",
        f"""CREATE TABLE IF NOT EXISTS {s}.state (
                id         bigserial PRIMARY KEY,
                kind       text NOT NULL,
                name       text NOT NULL,
                body       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (kind, name)
            )""",
        f"""COMMENT ON COLUMN {s}.state.kind IS
            'What sort of thing this row is; laya_criterion for a Laya question.'""",
        f"""COMMENT ON COLUMN {s}.state.body IS
            'Free-form JSON. For a criterion: {{"question": <laya question>, ...}}.'""",
        f"""CREATE TABLE IF NOT EXISTS {s}.history (
                id          bigserial PRIMARY KEY,
                state_id    bigint NOT NULL REFERENCES {s}.state(id) ON DELETE CASCADE,
                recorded_at timestamptz NOT NULL DEFAULT now(),
                task        text,
                metrics     jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                detail      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                note        text
            )""",
        f"""COMMENT ON TABLE {s}.history IS
            'One entry per task execution. Append-only: UPDATE is refused by trigger.'""",
        f"""COMMENT ON COLUMN {s}.history.detail IS
            'The full answer record — verdict, confidence, per-option probabilities.'""",
        f"CREATE INDEX IF NOT EXISTS state_kind_idx ON {s}.state (kind)",
        f"CREATE INDEX IF NOT EXISTS state_body_idx ON {s}.state USING gin (body)",
        f"""CREATE INDEX IF NOT EXISTS history_state_recorded_idx
                ON {s}.history (state_id, recorded_at)""",
        # An entry records what happened; editing one would rewrite history in
        # the literal sense. Deletes are left alone so a row can still be
        # dropped with its parent.
        f"""CREATE OR REPLACE FUNCTION {s}.refuse_update() RETURNS trigger
            LANGUAGE plpgsql AS $fn$
            BEGIN
                RAISE EXCEPTION '%.% is append-only: % on row % refused',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, OLD.id;
            END;
            $fn$""",
        f"DROP TRIGGER IF EXISTS history_append_only ON {s}.history",
        f"""CREATE TRIGGER history_append_only BEFORE UPDATE ON {s}.history
                FOR EACH ROW EXECUTE FUNCTION {s}.refuse_update()""",
        f"""CREATE OR REPLACE FUNCTION {s}.touch_updated_at() RETURNS trigger
            LANGUAGE plpgsql AS $fn$
            BEGIN NEW.updated_at := now(); RETURN NEW; END;
            $fn$""",
        f"DROP TRIGGER IF EXISTS state_touch ON {s}.state",
        f"""CREATE TRIGGER state_touch BEFORE UPDATE ON {s}.state
                FOR EACH ROW EXECUTE FUNCTION {s}.touch_updated_at()""",
        # For anyone reading with psql: the row and its history in one place,
        # the history as the JSON array it reads as from Python.
        f"""CREATE OR REPLACE VIEW {s}.state_with_history AS
            SELECT s.id, s.kind, s.name, s.body, s.created_at, s.updated_at,
                   (SELECT count(*) FROM {s}.history h WHERE h.state_id = s.id) AS runs,
                   COALESCE((SELECT jsonb_agg(to_jsonb(h) - 'state_id'
                                              ORDER BY h.recorded_at, h.id)
                             FROM {s}.history h WHERE h.state_id = s.id),
                            '[]'::jsonb) AS history
            FROM {s}.state s""",
    ]


def ensure_schema(conn: Connection, *, schema: str | None = None) -> list[str]:
    """Create the schema, its two tables, indexes, triggers and view if absent.

    Idempotent — running it against an up-to-date database changes nothing.
    Returns the command tags, one per statement, for a caller that wants to see
    what the server did.
    """
    tags = []
    with conn.transaction():
        for statement in schema_sql(schema or conn.schema):
            tags.append(conn.execute(statement))
    return tags


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def upsert_row(conn: Connection, kind: str, name: str, body: dict | None = None,
               *, merge: bool = False, schema: str | None = None) -> dict:
    """Insert a row, or update the one already at (kind, name). Returns the row.

    `body` replaces what was there. With `merge=True` it is shallow-merged into
    it instead, so a task can add a key without knowing the rest.
    """
    s = _ident(schema or conn.schema)
    body = body if body is not None else {}
    if not isinstance(body, dict):
        raise TypeError(f"body must be a dict, not {type(body).__name__}")
    assignment = f"{s}.state.body || EXCLUDED.body" if merge else "EXCLUDED.body"
    return conn.fetchone(
        f"""INSERT INTO {s}.state (kind, name, body) VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (kind, name) DO UPDATE SET body = {assignment}
            RETURNING *""",
        kind, name, body,
    )


def get_row(conn: Connection, kind: str, name: str,
            *, schema: str | None = None) -> dict | None:
    """One row by (kind, name), or None."""
    s = _ident(schema or conn.schema)
    return conn.fetchone(
        f"SELECT * FROM {s}.state WHERE kind = $1 AND name = $2", kind, name
    )


def list_rows(conn: Connection, kind: str | None = None, *, limit: int | None = None,
              schema: str | None = None) -> list[dict]:
    """Every row, or every row of one `kind`, oldest first."""
    s = _ident(schema or conn.schema)
    return conn.fetch(
        f"""SELECT * FROM {s}.state
            WHERE ($1::text IS NULL OR kind = $1) ORDER BY kind, name
            LIMIT $2::bigint""",
        kind, limit,
    )


def delete_row(conn: Connection, kind: str, name: str,
               *, schema: str | None = None) -> bool:
    """Delete a row and its history. True if there was one to delete."""
    s = _ident(schema or conn.schema)
    tag = conn.execute(
        f"DELETE FROM {s}.state WHERE kind = $1 AND name = $2", kind, name
    )
    return tag != "DELETE 0"


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------

def append_history(conn: Connection, kind: str, name: str, *, task: str | None = None,
                   metrics: dict | None = None, detail: dict | None = None,
                   note: str | None = None, recorded_at: datetime | None = None,
                   schema: str | None = None) -> dict:
    """Add one entry to a row's history. Raises `NoSuchRow` if the row is missing.

    `recorded_at` defaults to the server's clock, which is the one to prefer:
    entries written from different machines stay comparable.
    """
    s = _ident(schema or conn.schema)
    row = conn.fetchone(
        f"""INSERT INTO {s}.history (state_id, task, metrics, detail, note, recorded_at)
            SELECT s.id, $3, $4::jsonb, $5::jsonb, $6, COALESCE($7::timestamptz, now())
            FROM {s}.state s WHERE s.kind = $1 AND s.name = $2
            RETURNING *""",
        kind, name, task, metrics or {}, detail or {}, note, recorded_at,
    )
    if row is None:
        raise NoSuchRow(f"no {kind!r} row named {name!r}; upsert it before logging a run")
    return row


def history(conn: Connection, kind: str, name: str, *, limit: int | None = None,
            newest_first: bool = False, schema: str | None = None) -> list[dict]:
    """A row's history entries, oldest first unless `newest_first`.

    With `limit` and `newest_first` together you get the last N entries, still
    returned newest first — reverse them if you want a series to plot.
    """
    s = _ident(schema or conn.schema)
    order = "DESC" if newest_first else "ASC"
    return conn.fetch(
        f"""SELECT h.* FROM {s}.history h
            JOIN {s}.state s ON s.id = h.state_id
            WHERE s.kind = $1 AND s.name = $2
            ORDER BY h.recorded_at {order}, h.id {order}
            LIMIT $3::bigint""",
        kind, name, limit,
    )


def track_record(conn: Connection, name: str, *, kind: str = KIND_CRITERION,
                 schema: str | None = None) -> dict:
    """What a row's history adds up to: how many runs, when, and each metric's series.

    Returns data, not a verdict — `runs`, `first_run`, `last_run`, the `tasks`
    that contributed, and `metrics` as `{key: [{at, task, value}, ...]}` in
    time order. Whether that track record justifies more work is the caller's
    call, and it needs the series to make it.
    """
    entries = history(conn, kind, name, schema=schema)
    metrics: dict[str, list[dict]] = {}
    for entry in entries:
        for key, value in (entry["metrics"] or {}).items():
            metrics.setdefault(key, []).append(
                {"at": entry["recorded_at"], "task": entry["task"], "value": value}
            )
    return {
        "kind": kind,
        "name": name,
        "runs": len(entries),
        "first_run": entries[0]["recorded_at"] if entries else None,
        "last_run": entries[-1]["recorded_at"] if entries else None,
        "tasks": sorted({e["task"] for e in entries if e["task"]}),
        "metrics": metrics,
    }


# --------------------------------------------------------------------------
# Laya criteria — the same two tables, with `kind` fixed and the body checked
# --------------------------------------------------------------------------

def check_question(question: dict) -> dict:
    """Check a value looks like a question `laya.yes_no`/`choice`/`score` built.

    Shape only — `type` and `instructions`, plus `criteria` for the types that
    carry options. Laya itself is not imported here, so the two skills stay
    independent and this one needs no model to run.
    """
    if not isinstance(question, dict):
        raise TypeError(f"a question is a dict, not {type(question).__name__}")
    missing = [k for k in ("type", "instructions") if k not in question]
    if missing:
        raise ValueError(
            f"question is missing {', '.join(missing)}; "
            "pass what laya.yes_no/choice/score returned, not the answer"
        )
    if question["type"] in ("choice", "score") and not question.get("criteria"):
        raise ValueError(f"a {question['type']} question needs its `criteria` options")
    return question


def upsert_criterion(conn: Connection, name: str, question: dict, *,
                     merge: bool = False, schema: str | None = None,
                     **meta: Any) -> dict:
    """Register a Laya criterion: its name, its question schema, and any `meta` you pass.

    Stored as `{"question": ..., **meta}` in `body`, so `criterion(...)["body"]
    ["question"]` hands straight back to `laya.ask_chunks`.
    """
    body = {"question": check_question(question), **meta}
    return upsert_row(conn, KIND_CRITERION, name, body, merge=merge, schema=schema)


def criterion(conn: Connection, name: str, *, schema: str | None = None) -> dict | None:
    """One registered criterion by name, or None."""
    return get_row(conn, KIND_CRITERION, name, schema=schema)


def criteria(conn: Connection, *, schema: str | None = None) -> list[dict]:
    """Every registered criterion."""
    return list_rows(conn, KIND_CRITERION, schema=schema)


def record_run(conn: Connection, name: str, *, task: str, metrics: dict | None = None,
               detail: dict | None = None, note: str | None = None,
               schema: str | None = None) -> dict:
    """Log one execution of a criterion: which task, what it scored, what Laya answered.

    `detail` is meant to be `laya.detail(result)[name]` — the verdict with the
    confidence and per-option probabilities behind it. Pass the whole thing;
    a verdict without its distribution cannot be re-judged later.
    """
    return append_history(conn, KIND_CRITERION, name, task=task, metrics=metrics,
                          detail=detail, note=note, schema=schema)


# --------------------------------------------------------------------------
# Free-form SQL
# --------------------------------------------------------------------------

def query(conn: Connection, sql: str, params: Sequence[Any] = ()) -> list[dict]:
    """Run any SELECT and get dicts back. Placeholders are `$1`, `$2`, ...

        experiments.query(conn,
            "SELECT name, body->'question'->>'type' AS t FROM experiments.state"
            " WHERE kind = $1", ["laya_criterion"])

    Values belong in `params`, never in `sql` — an f-string here is how a name
    with an apostrophe becomes a syntax error and a name from outside becomes
    an injection.
    """
    return conn.fetch(sql, *params)


def execute(conn: Connection, sql: str, params: Sequence[Any] = ()) -> str:
    """Run any statement that is not a query, returning its command tag."""
    return conn.execute(sql, *params)
