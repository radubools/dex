"""Read-only SQL over HTTP: a query in, rows of dictionaries out.

Destination: `src/dex/sql.py`. Nothing here imports anything else from dex, so
moving the file in is a copy and two lines in `api.py` — see `install.py`.

    POST /api/sql   {"sql": "SELECT ...", "params": [...], "limit": 1000}
    GET  /api/sql?sql=SELECT+...&params=%5B...%5D

    -> {"rows": [{...}, ...], "columns": [...], "rowcount": 12,
        "truncated": false, "elapsed_ms": 3.1}

Why this exists: a project widget runs in an iframe with `sandbox="allow-scripts"`
and no `allow-same-origin`, so it has an opaque origin, no session cookie, and
no way to call `/api/*` as the signed-in user. It can still make a *cross-origin*
request, and a `null` origin is allowed to read a response that says
`Access-Control-Allow-Origin: *`. That is the one door into this frame, and this
router is it.

Three layers stand between that open door and the database, in decreasing order
of how much they matter:

1.  **The `dex_readonly` role** (`role.sql`). `default_transaction_read_only`,
    `pg_read_all_data`, a 5 s `statement_timeout`, no TEMP, no CREATE. This is
    the real boundary, enforced by Postgres under every clever spelling of a
    write. Everything else here is a courtesy.
2.  **Loopback only**, by default. The endpoint is unauthenticated on purpose —
    a null-origin frame has no credentials to send — so `allow_remote=False`
    keeps it reachable from a browser on this machine and from nowhere else.
    Turning that off publishes every row in the database to the network.
3.  **A statement guard**, which refuses anything that is not a single read.
    A regex over SQL is the weakest of the three and is here to turn a mistake
    into a 400 rather than a rollback. Never rely on it alone.

What it deliberately does not do: no writes, no DDL, no multiple statements, no
`Access-Control-Allow-Credentials` (which `*` forbids anyway, and which would
turn this into a cross-site read of the signed-in user's dex).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import ipaddress
import json
import logging
import re
import time
import uuid
from typing import Any, Sequence
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

log = logging.getLogger("dex.sql")

#: The role `role.sql` creates. Queries run as this and nothing else.
ROLE = "dex_readonly"

#: Rows returned unless the caller asks for fewer, and the ceiling on asking for
#: more. A dashboard that needs 50,000 rows in one response wants an aggregate.
DEFAULT_LIMIT = 1_000
MAX_LIMIT = 20_000

#: Seconds a query may run. The role carries the same limit as a server-side
#: default; this one covers the wait for a connection as well.
TIMEOUT_S = 5.0

#: Connections held for this router. Small: it serves dashboards, not traffic.
POOL_MIN = 0
POOL_MAX = 4

#: A statement may begin with one of these and nothing else.
READ_PREFIXES = ("select", "with", "table", "values", "explain", "show")

#: Cheap things worth refusing by name even though the role already refuses
#: them, because the error a caller gets back is then about what they wrote.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|"
    r"comment|reindex|vacuum|analyze|cluster|copy|call|do|set|reset|"
    r"listen|notify|lock|prepare|execute|deallocate|refresh|security)\b",
    re.IGNORECASE,
)

#: `pg_read_all_data` can read these, and a dashboard has no business in them.
FORBIDDEN_TABLES = re.compile(
    r"\b(pg_authid|pg_shadow|pg_user_mapping|identities|sessions|api_keys)\b",
    re.IGNORECASE,
)


class QueryRequest(BaseModel):
    """A query, its parameters, and how many rows the caller wants back."""

    sql: str = Field(min_length=1, max_length=20_000)
    params: list[Any] = Field(default_factory=list, max_length=64)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class Refused(ValueError):
    """The statement is not a single read. Becomes a 400, never a 500."""


def readonly_dsn(url: str) -> str:
    """`url` with its user replaced by `dex_readonly` and any password dropped.

    Local connections on this machine authenticate by `trust`, so no password is
    needed or wanted. Set `DEX_SQL_READONLY_URL` to override the whole thing —
    that is the hook for a deployment where the role does need a password.
    """
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{ROLE}@{host}{port}", parts.path, "", ""))


def _scan(sql: str, *, blank_literals: bool) -> str:
    """`sql` with comments blanked, and its string literals blanked too if asked.

    One walker, because every one of these has to nest correctly: a quote inside
    a comment, a comment inside a quote, a doubled quote escaping itself, and a
    dollar-quoted body that may contain all three. Regexes get each case right
    in isolation and the combination wrong.

    Literal contents are what the *guard* must not read. `SELECT '-- drop'`
    contains no comment and `SELECT 'a; b'` is one statement — but scanning text
    that still holds them sees the word DROP and a second statement, and refuses
    a perfectly good query. Keeping the quote characters means the shape of the
    statement survives; only the payload goes.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == quote:
                    # A doubled quote is an escaped one, not the end.
                    if i + 1 < n and sql[i + 1] == quote:
                        out.append("" if blank_literals else sql[i:i + 2])
                        i += 2
                        continue
                    out.append(quote)
                    i += 1
                    break
                out.append("" if blank_literals else sql[i])
                i += 1
            continue
        if ch == "$" and (m := re.match(r"\$([A-Za-z_]\w*)?\$", sql[i:])):
            tag = m.group(0)
            end = sql.find(tag, i + len(tag))
            end = n if end == -1 else end + len(tag)
            out.append(f"{tag}{tag}" if blank_literals else sql[i:end])
            i = end
            continue
        if sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_comments(sql: str) -> str:
    """`sql` with `--` line comments and `/* */` blocks blanked, strings intact."""
    return _scan(sql, blank_literals=False)


def redact(sql: str) -> str:
    """`sql` with comments *and* the contents of every literal blanked — what the guard reads."""
    return _scan(sql, blank_literals=True)


def check(sql: str) -> str:
    """The statement to run, or `Refused` saying why it will not be.

    Two forms of the same text, and keeping them apart is the whole correctness
    of this function:

    *   **`guardable`** — comments and literal *contents* blanked. Everything is
        judged on this, so `SELECT 1; /*`…`*/ DELETE FROM tasks` cannot smuggle a
        second statement past the scan and `SELECT 'a; b'` is not mistaken for
        one.
    *   **`runnable`** — comments blanked, literals intact. This is what goes to
        Postgres. Judging the redacted form and running the real one is the
        point; running the redacted one would quietly drop every string in the
        query.
    """
    text = sql.strip()
    if not text:
        raise Refused("empty statement")

    guardable = redact(text).strip().rstrip(";").strip()
    if not guardable:
        raise Refused("no statement, only comments")
    if ";" in guardable:
        raise Refused("one statement per request; found a `;` inside this one")

    first = guardable.split(None, 1)[0].lower().lstrip("(")
    if first not in READ_PREFIXES:
        raise Refused(
            f"only {', '.join(p.upper() for p in READ_PREFIXES)} are allowed; "
            f"this starts with {first.upper()!r}"
        )
    if hit := FORBIDDEN.search(guardable):
        raise Refused(f"{hit.group(0).upper()!r} is not allowed in a read-only query")
    if hit := FORBIDDEN_TABLES.search(guardable):
        raise Refused(f"{hit.group(0)!r} is not readable through this endpoint")

    # Prefer the caller's own text, minus a trailing semicolon, so what runs is
    # what they wrote. The exception is a query whose semicolon is followed by a
    # comment (`SELECT 1; -- x`), where trimming leaves the `;` in the middle and
    # asyncpg refuses the lot; there the comment-free form is what gets run.
    runnable = text.rstrip().rstrip(";").strip()
    if not runnable or ";" in redact(runnable):
        runnable = strip_comments(text).strip().rstrip(";").strip()
    return runnable


def jsonable(value: Any) -> Any:
    """A value asyncpg produced, as something `json.dumps` accepts.

    jsonb already arrives as a dict through the pool's codec. What is left is
    the scalar types with no JSON spelling — and the long tail (`Range`,
    `Box`, a domain type) which becomes its `str()`, because a dashboard showing
    `[1,10)` is better than a 500.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, decimal.Decimal):
        # float(), not str(): every consumer of this is a chart.
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


class ReadOnlyPool:
    """A lazily-opened asyncpg pool that connects as `dex_readonly`.

    Lazy on purpose: a dex that starts before `role.sql` has been run should
    boot normally and fail only on the first request to this endpoint, with an
    error that names the missing role. A pool opened during `lifespan` would
    take the whole server down instead.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    async def _init(conn: asyncpg.Connection) -> None:
        # Without these, jsonb arrives as a string and every consumer parses it
        # a second time. `experiments.py` sets the same pair.
        for kind in ("json", "jsonb"):
            await conn.set_type_codec(
                kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )

    async def pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        self.dsn,
                        min_size=POOL_MIN,
                        max_size=POOL_MAX,
                        command_timeout=TIMEOUT_S,
                        init=self._init,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            f"cannot connect as {ROLE}: {exc}. Run "
                            "`assets/construction/dex-sql-api/apply_role.py`, or set "
                            "DEX_SQL_READONLY_URL."
                        ),
                    ) from exc
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def run(self, sql: str, params: Sequence[Any], limit: int) -> dict:
        """Run one read and return `{rows, columns, rowcount, truncated, elapsed_ms}`.

        Fetches through a cursor rather than `fetch()`: a mistyped join against
        `experiments.history` is 13,000 rows today and the cursor stops at
        `limit + 1`, so the response is capped without the server first
        materialising the whole result in memory.
        """
        pool = await self.pool()
        started = time.perf_counter()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                # Belt and braces over the role's own default, and it also
                # covers a DSN someone pointed at a different role.
                await conn.execute(f"SET LOCAL statement_timeout = {int(TIMEOUT_S * 1000)}")
                cursor = await conn.cursor(sql, *params)
                records = await cursor.fetch(limit + 1)
        truncated = len(records) > limit
        records = records[:limit]
        columns = list(records[0].keys()) if records else []
        return {
            "rows": [{k: jsonable(v) for k, v in r.items()} for r in records],
            "columns": columns,
            "rowcount": len(records),
            "truncated": truncated,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }


#: Sent on every response from this router, including its errors — a fetch that
#: fails CORS gives the caller no status code at all, which is a miserable thing
#: to debug from inside a sandboxed frame.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "600",
    # No Access-Control-Allow-Credentials. With `*` it is invalid, and with a
    # specific origin it would make this a cross-site read of a signed-in dex.
    "Vary": "Origin",
}


def is_loopback(request: Request) -> bool:
    """True if the request came from this machine."""
    host = request.client.host if request.client else ""
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A unix socket has no address; uvicorn reports "". Treat anything
        # unparseable as remote rather than guessing in the permissive direction.
        return False


def build_sql_router(
    database_url: str,
    *,
    readonly_url: str | None = None,
    allow_remote: bool = False,
    enabled: bool = True,
) -> APIRouter:
    """The `/api/sql` router.

    `database_url` is dex's own DSN; the role is swapped into it.
    `readonly_url` overrides that entirely. `allow_remote=True` drops the
    loopback check and publishes the database to the network — do not.
    """
    router = APIRouter(tags=["sql"])
    pool = ReadOnlyPool(readonly_url or readonly_dsn(database_url))
    router.state_pool = pool  # type: ignore[attr-defined]  # for tests and shutdown

    def reply(body: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(body, status_code=status, headers=CORS_HEADERS)

    async def answer(request: Request, sql: str, params: Sequence[Any], limit: int) -> JSONResponse:
        if not enabled:
            return reply({"error": "/api/sql is disabled"}, 404)
        if not allow_remote and not is_loopback(request):
            host = request.client.host if request.client else "?"
            log.warning("refused /api/sql from %s", host)
            return reply({"error": "this endpoint answers only on loopback"}, 403)
        try:
            statement = check(sql)
        except Refused as exc:
            return reply({"error": str(exc)}, 400)
        try:
            result = await pool.run(statement, params, limit)
        except HTTPException as exc:
            return reply({"error": exc.detail}, exc.status_code)
        except asyncio.TimeoutError:
            return reply({"error": f"query took longer than {TIMEOUT_S}s"}, 504)
        except asyncpg.PostgresError as exc:
            # The database's own complaint, which is what a dashboard author
            # needs. It is a read-only role: there is nothing here to leak that
            # a SELECT would not already return.
            return reply({"error": f"{type(exc).__name__}: {exc}"}, 400)
        return reply(result)

    @router.options("/api/sql")
    async def preflight() -> JSONResponse:
        return reply({})

    @router.post("/api/sql")
    async def run_post(request: Request, body: QueryRequest) -> JSONResponse:
        return await answer(request, body.sql, body.params, body.limit)

    @router.get("/api/sql")
    async def run_get(
        request: Request,
        sql: str = Query(min_length=1, max_length=20_000),
        params: str = Query("[]", description="a JSON array"),
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    ) -> JSONResponse:
        # GET exists so a sandboxed frame can ask without a preflight: no custom
        # header means the browser treats it as a simple request.
        try:
            values = json.loads(params)
        except json.JSONDecodeError as exc:
            return reply({"error": f"params is not JSON: {exc}"}, 400)
        if not isinstance(values, list):
            return reply({"error": "params must be a JSON array"}, 400)
        if len(values) > 64:
            return reply({"error": "at most 64 params"}, 400)
        return await answer(request, sql, values, limit)

    return router
