## `dashboard`

Export one task's scored criteria out of the experiments tables into a file the criteria-dashboard widget opens.

- `KINDS` — constant
- `MODEL_OF_KIND` — constant
- `DEFAULT_THRESHOLD` — constant
- `FORMAT` — constant
- `FORMAT_VERSION` — constant
- `tasks(conn: Any, *, kinds: Iterable[str]=KINDS, schema: str | None=None) -> list[dict]` — Every task that has scored a criterion, most criteria first.
- `load_chunks(source: Any) -> dict[str, dict]` — Chunk id → `{text, ...}`, from a `chunks.json` path, its parsed dict, or a plain map.
- `export(conn: Any, task: str, *, chunks: Any=None, threshold: float=DEFAULT_THRESHOLD, kinds: Iterable[str]=KINDS, schema: str | None=None) -> dict` — The whole export document for one task, ready to serialise.
- `write(conn: Any, task: str, path: str | Path, *, chunks: Any=None, threshold: float=DEFAULT_THRESHOLD, kinds: Iterable[str]=KINDS, schema: str | None=None) -> Path` — `export` written to `path`, which must end `.criteria.json` for a widget to open it.
- `main(argv: list[str] | None=None) -> int` — `python3 .../utils/dashboard.py --task ... --chunks chunks.json --out x.criteria.json`

## `experiments`

Keep shared project state in the dex Postgres, in an `experiments` schema.

- `SCHEMA` — constant
- `DEFAULT_DSN` — constant
- `KIND_CRITERION` — constant
- `CONNECT_RETRIES` — constant
- `CONNECT_BACKOFF` — constant
- `CONNECT_TIMEOUT` — constant
- `class NoSuchRow` — Raised when a row is addressed by (kind, name) and no such row exists.
- `dsn(url: str | None=None) -> str` — The database to talk to: the argument, then the environment, then local dex.
- `class Connection` — One database connection, driven synchronously from its own event loop.
- `connect(url: str | None=None, *, schema: str | None=None, retries: int=CONNECT_RETRIES, backoff: float=CONNECT_BACKOFF, timeout: float=CONNECT_TIMEOUT) -> Connection` — Connect to the dex database, retrying a server that is not ready yet.
- `schema_sql(schema: str | None=None) -> list[str]` — The statements `ensure_schema` runs, in order — every one of them idempotent.
- `ensure_schema(conn: Connection, *, schema: str | None=None) -> list[str]` — Create the schema, its two tables, indexes, triggers and view if absent.
- `upsert_row(conn: Connection, kind: str, name: str, body: dict | None=None, *, merge: bool=False, schema: str | None=None) -> dict` — Insert a row, or update the one already at (kind, name). Returns the row.
- `get_row(conn: Connection, kind: str, name: str, *, schema: str | None=None) -> dict | None` — One row by (kind, name), or None.
- `list_rows(conn: Connection, kind: str | None=None, *, limit: int | None=None, schema: str | None=None) -> list[dict]` — Every row, or every row of one `kind`, oldest first.
- `delete_row(conn: Connection, kind: str, name: str, *, schema: str | None=None) -> bool` — Delete a row and its history. True if there was one to delete.
- `append_history(conn: Connection, kind: str, name: str, *, task: str | None=None, metrics: dict | None=None, detail: dict | None=None, note: str | None=None, recorded_at: datetime | None=None, schema: str | None=None) -> dict` — Add one entry to a row's history. Raises `NoSuchRow` if the row is missing.
- `history(conn: Connection, kind: str, name: str, *, limit: int | None=None, newest_first: bool=False, schema: str | None=None) -> list[dict]` — A row's history entries, oldest first unless `newest_first`.
- `entries_for_chunks(conn: Connection, chunk_ids: Iterable[str], *, kind: str | None=None, schema: str | None=None) -> list[dict]` — Every history entry whose `detail["answers"]` names one of `chunk_ids`.
- `task_pattern(package: str) -> re.Pattern` — The regex matching every way `history.task` names one package.
- `track_record(conn: Connection, name: str, *, kind: str=KIND_CRITERION, schema: str | None=None) -> dict` — What a row's history adds up to: how many runs, when, and each metric's series.
- `check_question(question: dict) -> dict` — Check a value looks like a question `laya.yes_no`/`choice`/`score` built.
- `upsert_criterion(conn: Connection, name: str, question: dict, *, merge: bool=False, schema: str | None=None, **meta: Any) -> dict` — Register a Laya criterion: its name, its question schema, and any `meta` you pass.
- `criterion(conn: Connection, name: str, *, schema: str | None=None) -> dict | None` — One registered criterion by name, or None.
- `criteria(conn: Connection, *, schema: str | None=None) -> list[dict]` — Every registered criterion.
- `record_run(conn: Connection, name: str, *, task: str, metrics: dict | None=None, detail: dict | None=None, note: str | None=None, schema: str | None=None) -> dict` — Log one execution of a criterion: which task, what it scored, what Laya answered.
- `query(conn: Connection, sql: str, params: Sequence[Any]=()) -> list[dict]` — Run any SELECT and get dicts back. Placeholders are `$1`, `$2`, ...
- `execute(conn: Connection, sql: str, params: Sequence[Any]=()) -> str` — Run any statement that is not a query, returning its command tag.