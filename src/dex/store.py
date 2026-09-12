"""Reads and writes for threads, messages, and tasks.

Everything the UI needs after a restart lives here. The in-memory dataclasses
are views over these rows, never the source of truth.
"""

from __future__ import annotations

import json
import os
import uuid
import socket  # noqa: F401  (hostname is supplied by the caller)
import time
from pathlib import Path
from typing import Any

import asyncpg

from .config import DEFAULT_CONCURRENCY, MAX_WORKERS
from .db import Database
from .models import Task, TaskState
from .threads import Thread, ThreadMessage

TASK_COLUMNS = """
    id, thread_id, title, slug, problem, state, activity,
    extract(epoch from created_at)::float8 AS created_at,
    extract(epoch from started_at)::float8 AS started_at,
    extract(epoch from finished_at)::float8 AS finished_at,
    error, cost_usd, turns, session_id, attempt, parent_id, resumed_from, output_slug,
    model, cost_is_estimate, project, scope
"""


def task_from_row(row: asyncpg.Record) -> Task:
    task = Task(
        problem=row["problem"],
        title=row["title"],
        slug=row["slug"],
        id=row["id"],
        state=TaskState(row["state"]),
        created_at=row["created_at"] or time.time(),
    )
    task.thread_id = row["thread_id"]
    task.activity = row["activity"]
    task.started_at = row["started_at"]
    task.finished_at = row["finished_at"]
    task.error = row["error"]
    task.cost_usd = row["cost_usd"]
    task.turns = row["turns"]
    task.session_id = row["session_id"]
    task.attempt = row["attempt"]
    task.parent_id = row["parent_id"]
    task.resumed_from = row["resumed_from"]
    task.output_slug = row["output_slug"]
    task.model = row["model"]
    task.cost_is_estimate = row["cost_is_estimate"]
    task.project = row["project"]
    task.scope = row["scope"]
    return task


class ThreadStore:
    """Threads and their messages."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self, title: str | None = None, project: str | None = None, kind: str = "chat"
    ) -> Thread:
        default = "Project design" if kind == "project_design" else "New thread"
        thread = Thread(title=(title or default)[:80], project=project, kind=kind)
        await self.db.pool.execute(
            """INSERT INTO threads (id, title, created_at, updated_at, project, kind)
               VALUES ($1, $2, to_timestamp($3), to_timestamp($4), $5, $6)""",
            thread.id, thread.title, thread.created_at, thread.updated_at, project, kind,
        )
        return thread

    async def list(self, project: str | None = None, include_hidden: bool = False) -> list[Thread]:
        rows = await self.db.pool.fetch(
            """SELECT t.id, t.title, t.project, t.kind,
                      extract(epoch from t.created_at)::float8 AS created_at,
                      extract(epoch from t.updated_at)::float8 AS updated_at,
                      (SELECT count(*) FROM messages m WHERE m.thread_id = t.id) AS message_count,
                      COALESCE(
                          (SELECT array_agg(k.id ORDER BY k.created_at)
                           FROM tasks k WHERE k.thread_id = t.id), '{}'
                      ) AS task_ids
               FROM threads t
               WHERE ($1::text IS NULL OR t.project = $1)
                 AND ($2::boolean OR NOT t.hidden)
               -- Design threads lead: they are the project's own conversation.
               ORDER BY (t.kind = 'project_design') DESC, t.updated_at DESC""",
            project, include_hidden,
        )
        return [
            Thread(
                id=row["id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                task_ids=list(row["task_ids"] or []),
                messages=[],
                message_count=row["message_count"],
                project=row["project"],
                kind=row["kind"],
            )
            for row in rows
        ]

    async def get(self, thread_id: str) -> Thread | None:
        row = await self.db.pool.fetchrow(
            """SELECT id, title, project, kind,
                      extract(epoch from created_at)::float8 AS created_at,
                      extract(epoch from updated_at)::float8 AS updated_at
               FROM threads WHERE id = $1""",
            thread_id,
        )
        if row is None:
            return None

        messages = await self.db.pool.fetch(
            """SELECT id, role, kind, body, data, extract(epoch from ts)::float8 AS ts
               FROM messages WHERE thread_id = $1 ORDER BY ts, id""",
            thread_id,
        )
        task_ids = await self.db.pool.fetch(
            "SELECT id FROM tasks WHERE thread_id = $1 ORDER BY created_at", thread_id
        )
        thread = Thread(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            project=row["project"],
            kind=row["kind"],
            task_ids=[r["id"] for r in task_ids],
            messages=[
                ThreadMessage(
                    id=m["id"], role=m["role"], kind=m["kind"],
                    text=m["body"], data=Database.load(m["data"]), ts=m["ts"],
                )
                for m in messages
            ],
        )
        thread.message_count = len(thread.messages)
        return thread

    async def costs_by_thread(self) -> dict[str, float]:
        rows = await self.db.pool.fetch(
            """SELECT thread_id, COALESCE(sum(cost_usd), 0)::float8 AS cost
               FROM tasks WHERE thread_id IS NOT NULL GROUP BY thread_id"""
        )
        return {row["thread_id"]: row["cost"] for row in rows}

    async def append(self, thread_id: str, message: ThreadMessage) -> ThreadMessage | None:
        async with self.db.pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval("SELECT title FROM threads WHERE id = $1", thread_id)
            if exists is None:
                return None
            await conn.execute(
                """INSERT INTO messages (id, thread_id, role, kind, body, data, ts)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb, to_timestamp($7))""",
                message.id, thread_id, message.role, message.kind,
                message.text, Database.dump(message.data), message.ts,
            )
            # The first thing the operator says names the thread.
            if exists == "New thread" and message.role == "user" and message.text.strip():
                await conn.execute(
                    "UPDATE threads SET title = $2, updated_at = now() WHERE id = $1",
                    thread_id, message.text.strip().splitlines()[0][:80],
                )
            else:
                await conn.execute(
                    "UPDATE threads SET updated_at = now() WHERE id = $1", thread_id
                )
        return message

    async def hide(self, thread_id: str) -> bool:
        """Take a thread out of the list without destroying anything.

        Deleting cascaded a thread's messages away and set its tasks' thread_id
        to NULL, so one mis-tap lost the conversation around a month of work
        and left the tasks with no home. Nothing is removed now.
        """
        result = await self.db.pool.execute(
            "UPDATE threads SET hidden = true WHERE id = $1", thread_id
        )
        return result.endswith(" 1")

    async def unhide(self, thread_id: str) -> bool:
        result = await self.db.pool.execute(
            "UPDATE threads SET hidden = false WHERE id = $1", thread_id
        )
        return result.endswith(" 1")

    async def touch(self, thread_id: str) -> None:
        await self.db.pool.execute("UPDATE threads SET updated_at = now() WHERE id = $1", thread_id)


class TaskStore:
    """Task rows, including the claim/heartbeat bookkeeping the queue needs."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, task: Task) -> Task:
        await self.db.pool.execute(
            """INSERT INTO tasks (id, thread_id, title, slug, problem, state, created_at,
                                  attempt, parent_id, session_id, output_slug, resumed_from,
                                  model, project, scope)
               VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7), $8, $9, $10, $11, $12, $13, $14,
                       $15)""",
            task.id, task.thread_id, task.title, task.slug, task.problem,
            task.state.value, task.created_at, task.attempt, task.parent_id, task.session_id,
            task.output_slug, task.resumed_from, task.model, task.project, task.scope,
        )
        return task

    async def get(self, task_id: str) -> Task | None:
        row = await self.db.pool.fetchrow(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = $1", task_id)
        return task_from_row(row) if row else None

    async def list(
        self,
        thread_id: str | None = None,
        limit: int = 200,
        include_archived: bool = False,
    ) -> list[Task]:
        """Tasks, newest first, or a thread's own in the order they started.

        Archived ones are left out by default. Archiving is how an operator
        says "stop showing me this", so a listing that still carried them would
        be the one place the decision did not take.
        """
        archived = "" if include_archived else " AND state <> 'archived'"
        if thread_id:
            rows = await self.db.pool.fetch(
                f"""SELECT {TASK_COLUMNS} FROM tasks
                     WHERE thread_id = $1{archived}
                     ORDER BY created_at LIMIT $2""",
                thread_id, limit,
            )
        else:
            where = " WHERE state <> 'archived'" if not include_archived else ""
            rows = await self.db.pool.fetch(
                f"SELECT {TASK_COLUMNS} FROM tasks{where} ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [task_from_row(row) for row in rows]

    async def taken_slugs(self, project: str | None = None) -> set[str]:
        """Slugs already in use.

        Unscoped for uniqueness — a slug is unique across the whole table, so a
        new one must avoid every existing slug. Scoped to a project when the
        question is "what does this project already have", which is a different
        question: showing the planner every project's packages had it plan yoga
        poses when asked to regenerate the algorithms.
        """
        if project is None:
            rows = await self.db.pool.fetch("SELECT slug FROM tasks")
        else:
            rows = await self.db.pool.fetch(
                "SELECT slug, output_slug FROM tasks WHERE project = $1", project
            )
            return {
                slug
                for row in rows
                for slug in (row["slug"], row["output_slug"])
                if slug
            }
        return {row["slug"] for row in rows}

    async def set_state(self, task_id: str, state: TaskState, **fields: Any) -> None:
        assignments = ["state = $2"]
        values: list[Any] = [task_id, state.value]
        for column, value in fields.items():
            if column in {"started_at", "finished_at"}:
                values.append(value)
                assignments.append(f"{column} = to_timestamp(${len(values)})")
            else:
                values.append(value)
                assignments.append(f"{column} = ${len(values)}")
        await self.db.pool.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = $1", *values
        )

    async def set_cost(self, task_id: str, cost: float, *, estimate: bool) -> None:
        """Record spend. An estimate never overwrites an authoritative figure."""
        await self.db.pool.execute(
            """UPDATE tasks SET cost_usd = $2, cost_is_estimate = $3
               WHERE id = $1 AND ($3 = false OR cost_is_estimate = true OR cost_usd IS NULL)""",
            task_id, cost, estimate,
        )

    async def set_session(self, task_id: str, session_id: str) -> None:
        await self.db.pool.execute(
            "UPDATE tasks SET session_id = $2 WHERE id = $1", task_id, session_id
        )

    async def claim(self, worker: str) -> Task | None:
        """Take the oldest queued task, skipping rows another worker holds."""
        async with self.db.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                # Only queued work is claimable. A paused task is deliberately
                # held back until `resume_paused` decides there is room for it.
                # Work that has run before goes first: a task parked by a
                # pause has already cost tokens and holds an agent session
                # worth continuing, so it is finished before anything untouched
                # is begun. `seq` alone made that true only by coincidence,
                # because parked tasks usually happen to be the older ones.
                """SELECT id FROM tasks WHERE state = 'queued'
                   ORDER BY (started_at IS NOT NULL) DESC, seq
                   FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            if row is None:
                return None
            claimed = await conn.fetchrow(
                f"""UPDATE tasks
                    SET state = 'running', claimed_by = $2, claimed_at = now(),
                        started_at = now(), activity = 'starting'
                    WHERE id = $1
                    RETURNING {TASK_COLUMNS}""",
                row["id"], worker,
            )
        return task_from_row(claimed) if claimed else None

    async def heartbeat(self, task_id: str, activity: str | None = None) -> None:
        """Refresh the claim, and carry the current activity into the row.

        State changes emit events, but what the agent is *doing* changes far
        more often than its state; without this a REST read shows whatever
        activity was current at the last transition.
        """
        await self.db.pool.execute(
            "UPDATE tasks SET claimed_at = now(), activity = COALESCE($2, activity) WHERE id = $1",
            task_id, activity,
        )

    async def paused(self, limit: int = 10) -> list[Task]:
        """Tasks waiting to go again, oldest first — the order they resume in.

        Only ones that actually started. A row can be paused without ever
        having run — the tasks imported from the old file-backed store are
        marked that way — and starting those would build directories for
        packages that have since been superseded.
        """
        rows = await self.db.pool.fetch(
            f"""SELECT {TASK_COLUMNS} FROM tasks
                WHERE state = 'paused' AND started_at IS NOT NULL
                ORDER BY seq LIMIT $1""",
            limit,
        )
        return [task_from_row(row) for row in rows]

    async def release_orphans(
        self, stale_after_seconds: int = 90, hostname: str | None = None
    ) -> list[str]:
        """Mark tasks whose worker is gone as paused, so they go again.

        A server killed mid-run leaves rows in `running` with a fresh claim, so
        waiting for the heartbeat to go stale would strand them for a minute or
        more after a restart. When the claim names a process on this host, its
        liveness can be checked directly and the row recovered immediately.
        """
        candidates = await self.db.pool.fetch(
            """SELECT id, claimed_by,
                      extract(epoch from (now() - claimed_at))::float8 AS age
               FROM tasks
               WHERE state IN ('running', 'awaiting_input')"""
        )

        doomed = [
            row["id"]
            for row in candidates
            if row["age"] is None
            or row["age"] > stale_after_seconds
            or _claimed_by_dead_local_process(row["claimed_by"], hostname)
        ]
        if not doomed:
            return []

        rows = await self.db.pool.fetch(
            """UPDATE tasks
               SET state = 'paused',
                   activity = NULL,
                   claimed_by = NULL,
                   finished_at = COALESCE(finished_at, now()),
                   error = COALESCE(error, 'The server stopped while this task was running.')
               WHERE id = ANY($1::text[])
               RETURNING id""",
            doomed,
        )
        return [row["id"] for row in rows]


def _claimed_by_dead_local_process(claimed_by: str | None, hostname: str | None) -> bool:
    """True when the claim names a process on this machine that no longer exists."""
    if not claimed_by or not hostname or ":" not in claimed_by:
        return False
    host, _, pid_text = claimed_by.rpartition(":")
    if host != hostname or not pid_text.isdigit():
        return False
    try:
        os.kill(int(pid_text), 0)  # signal 0 only checks for existence
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # alive, owned by someone else
    return False


class TaskMessageStore:
    """Follow-up notes queued against a running task.

    Delivery is deliberately deferred: a message typed while the agent is
    working is held until the task stops, then becomes the brief for its next
    attempt. Interrupting a run mid-flight would throw away the work in
    progress, which is rarely what someone typing a note wants.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, task_id: str, body: str) -> dict[str, Any]:
        message_id = uuid.uuid4().hex[:12]
        row = await self.db.pool.fetchrow(
            """INSERT INTO task_messages (id, task_id, body)
               VALUES ($1, $2, $3)
               RETURNING id, body, extract(epoch from created_at)::float8 AS created_at""",
            message_id, task_id, body,
        )
        return {"id": row["id"], "body": row["body"], "createdAt": row["created_at"],
                "delivered": False}

    async def pending(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self.db.pool.fetch(
            """SELECT id, body, extract(epoch from created_at)::float8 AS created_at
               FROM task_messages
               WHERE task_id = $1 AND delivered_at IS NULL
               ORDER BY created_at""",
            task_id,
        )
        return [
            {"id": r["id"], "body": r["body"], "createdAt": r["created_at"], "delivered": False}
            for r in rows
        ]

    async def take(self, task_id: str) -> list[str]:
        """Claim every undelivered message for a task, marking them delivered."""
        rows = await self.db.pool.fetch(
            """UPDATE task_messages SET delivered_at = now()
               WHERE task_id = $1 AND delivered_at IS NULL
               RETURNING body, created_at""",
            task_id,
        )
        return [r["body"] for r in sorted(rows, key=lambda r: r["created_at"])]


class CostStore:
    """Spend rollups for the dashboard and the per-thread totals."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def totals(self) -> dict[str, float]:
        """Spend over the usual windows, for the header menu."""
        row = await self.db.pool.fetchrow(
            """SELECT
                 COALESCE(sum(cost_usd) FILTER (WHERE created_at > now() - interval '1 day'), 0) AS day,
                 COALESCE(sum(cost_usd) FILTER (WHERE created_at > now() - interval '7 days'), 0) AS week,
                 COALESCE(sum(cost_usd) FILTER (WHERE created_at > now() - interval '30 days'), 0) AS month,
                 COALESCE(sum(cost_usd), 0) AS all_time
               FROM tasks"""
        )
        return {k: float(v) for k, v in dict(row).items()}

    async def by_thread(self, thread_id: str) -> float:
        """A thread costs what the tasks it started cost."""
        value = await self.db.pool.fetchval(
            "SELECT COALESCE(sum(cost_usd), 0) FROM tasks WHERE thread_id = $1", thread_id
        )
        return float(value or 0)

    async def series(
        self, since: str = "7 days", bucket: str = "day", group_by: str = "model"
    ) -> list[dict[str, Any]]:
        """Cost per time bucket, split by model or by project.

        `group_by` is validated against a fixed set rather than interpolated
        freely — it names a SQL expression.
        """
        expression = {
            "model": "COALESCE(model, 'unknown')",
            # A project is the assets subdirectory, which for now is one per
            # deployment; kept as a column so the breakdown is ready for more.
            "project": "'algorithms'",
            "state": "state",
        }.get(group_by, "COALESCE(model, 'unknown')")
        interval = _INTERVALS.get(since, "7 days")
        width = _BUCKETS.get(bucket, "day")

        rows = await self.db.pool.fetch(
            f"""SELECT date_trunc('{width}', created_at) AS bucket,
                       {expression} AS series,
                       COALESCE(sum(cost_usd), 0)::float8 AS cost,
                       count(*) AS tasks
                FROM tasks
                WHERE created_at > now() - interval '{interval}'
                GROUP BY 1, 2
                ORDER BY 1""",
        )
        return [
            {
                "bucket": row["bucket"].isoformat(),
                "series": row["series"],
                "cost": row["cost"],
                "tasks": row["tasks"],
            }
            for row in rows
        ]


#: Whitelists, because these land in SQL text rather than as parameters.
_INTERVALS = {"day": "1 day", "week": "7 days", "month": "30 days", "all": "100 years"}
_BUCKETS = {"hour": "hour", "day": "day", "week": "week", "month": "month"}


class SettingsStore:
    """Global operator settings, small and read often.

    `auto_approve` is read at the moment a permission decision is made rather
    than when a task starts, so flipping it takes effect on runs already in
    flight.
    """

    AUTO_APPROVE = "auto_approve"
    PAUSED = "paused"
    #: Set by the usage watcher when a Claude limit is nearly spent. Separate
    #: from PAUSED so that releasing one does not silently release the other:
    #: the operator's pause and dex's own are different decisions.
    LIMIT_PAUSED = "limit_paused"
    #: Last rate-limit reading per window, as the CLI reported it.
    LIMIT_SNAPSHOT = "limit_snapshot"
    #: An operator's override of LIMIT_PAUSED, and the window it applies to.
    LIMIT_OVERRIDE = "limit_override"
    #: When the last usage probe ran and what it cost.
    LIMIT_PROBE = "limit_probe"
    TASK_CONCURRENCY = "task_concurrency"
    CHAT_CONCURRENCY = "chat_concurrency"
    MODEL = "model"
    ANIMATION_SPEED = "animation_speed"

    #: Applied when nothing is stored yet.
    DEFAULTS: dict[str, Any] = {
        AUTO_APPROVE: False,
        PAUSED: False,
        LIMIT_PAUSED: False,
        TASK_CONCURRENCY: DEFAULT_CONCURRENCY,
        CHAT_CONCURRENCY: DEFAULT_CONCURRENCY,
        MODEL: None,
        ANIMATION_SPEED: 1.0,
    }

    @staticmethod
    def project_model_key(project: str) -> str:
        """Per-project override, e.g. `model:algorithms`."""
        return f"model:{project}"

    def __init__(self, db: Database) -> None:
        self.db = db

    async def all(self) -> dict[str, Any]:
        rows = await self.db.pool.fetch("SELECT key, value FROM settings")
        settings = {row["key"]: Database.load(row["value"]).get("v") for row in rows}
        for key, value in self.DEFAULTS.items():
            settings.setdefault(key, value)
        return settings

    async def get(self, key: str, default: Any = None) -> Any:
        row = await self.db.pool.fetchval("SELECT value FROM settings WHERE key = $1", key)
        if row is None:
            return default
        return Database.load(row).get("v", default)

    async def set(self, key: str, value: Any) -> None:
        await self.db.pool.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES ($1, $2::jsonb, now())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            key, Database.dump({"v": value}),
        )

    async def auto_approve(self) -> bool:
        return bool(await self.get(self.AUTO_APPROVE, False))

    async def paused(self) -> bool:
        """Whether generation work is held. Chats and planning still run."""
        return bool(await self.get(self.PAUSED, False))

    async def limit_paused(self) -> bool:
        """Whether dex held work because a Claude limit is nearly spent."""
        return bool(await self.get(self.LIMIT_PAUSED, False))

    async def work_held(self) -> bool:
        """Whether generation work runs at all right now.

        Two independent reasons to stop, and work only runs when neither
        applies: the operator has not paused, *and* there is limit left. Every
        caller asks this rather than either flag, so a new reason to hold work
        needs one change here and none anywhere else.
        """
        return await self.paused() or await self.limit_paused()

    async def limit_snapshot(self) -> dict[str, Any]:
        """Last reading per limit window: {type: {utilization, status, ...}}."""
        stored = await self.get(self.LIMIT_SNAPSHOT, {})
        return stored if isinstance(stored, dict) else {}

    async def record_limit(self, info: dict[str, Any]) -> dict[str, Any]:
        """Fold one rate-limit reading into the snapshot.

        Keyed by window, because the five-hour and seven-day limits are spent
        at different rates and it is the nearest one that matters.

        A reading carries `unifiedWindows` — every window with its own figure —
        alongside the single window it is nominally about. Reading all of them
        costs nothing extra and is the difference between knowing the five-hour
        limit is nearly gone and knowing the seven-day one is only half spent.
        """
        seen = time.time()
        snapshot = await self.limit_snapshot()
        status = info.get("status")

        windows = info.get("unifiedWindows")
        if isinstance(windows, dict) and windows:
            for name, reading in windows.items():
                if not isinstance(reading, dict):
                    continue
                snapshot[str(name)] = {
                    "utilization": reading.get("utilization"),
                    # Only the window this reading is about carries the status;
                    # for the others the number is the whole story.
                    "status": status if name == info.get("rateLimitType") else None,
                    "resetsAt": reading.get("resetsAt"),
                    "seenAt": seen,
                }
        else:
            snapshot[str(info.get("rateLimitType") or "unknown")] = {
                "utilization": info.get("utilization"),
                "status": status,
                "resetsAt": info.get("resetsAt"),
                "seenAt": seen,
            }
        await self.set(self.LIMIT_SNAPSHOT, snapshot)
        return snapshot

    async def concurrency(self, key: str) -> int:
        """A limit, clamped to something a machine can actually run."""
        raw = await self.get(key, self.DEFAULTS.get(key, 3))
        try:
            return max(1, min(MAX_WORKERS, int(raw)))
        except (TypeError, ValueError):
            return DEFAULT_CONCURRENCY

    async def animation_speed(self) -> float:
        """Playback multiplier applied when no per-animation choice is made."""
        try:
            return max(0.1, min(8.0, float(await self.get(self.ANIMATION_SPEED, 1.0))))
        except (TypeError, ValueError):
            return 1.0

    async def model_for(self, project: str | None, fallback: str) -> str:
        """Per-project override first, then the global setting, then the config."""
        if project:
            chosen = await self.get(self.project_model_key(project))
            if chosen:
                return str(chosen)
        return str(await self.get(self.MODEL) or fallback)


def canonical_tag(raw: str) -> str:
    """One tag in the spelling the Library groups by: `group:value`.

    The guides ask for lowercase kebab-case, but the filters are built from
    whatever the manifests actually say — so a tag spelled `Topic: Two Pointers`
    would sit beside `topic:two-pointers` as a second pill for the same thing.
    Absorbing the trivial variants here is cheaper than a fragmented library,
    and it costs nothing that a deliberate tag would want.

    Returns "" for anything left empty, which the caller drops.
    """
    text = raw.strip().lower()
    group, colon, value = text.partition(":")
    # Only the first colon separates; a value may contain more.
    group, value = group.strip(), value.strip()
    if colon and not (group and value):
        return ""  # "topic:" or ":arrays" names nothing
    # Whitespace and underscores where the vocabulary uses hyphens: one tag
    # spelled three ways is three pills.
    normalise = lambda part: "-".join(part.replace("_", " ").split())  # noqa: E731
    group, value = normalise(group), normalise(value)
    return f"{group}:{value}" if colon else group


def normalise_tags(raw: Any) -> list[str]:
    """The usable tags out of whatever a manifest happens to hold.

    Tolerant on purpose. A package written before tags existed, one caught
    mid-write, or one where the agent put an object under `tags` should drop
    out of the filters rather than break the Library for every other package.
    Order is kept — a manifest lists its tags in a deliberate order — but
    duplicates and blanks are dropped.
    """
    if not isinstance(raw, list):
        return []
    seen: dict[str, None] = {}
    for entry in raw:
        if isinstance(entry, str):
            tag = canonical_tag(entry)
            if tag:
                seen.setdefault(tag, None)
    return list(seen)


class PackageTagStore:
    """What tags each package carries, mirrored from its manifest.

    The manifests are the truth; this is an index over them, maintained by the
    watcher as they change and rebuilt from disk at startup so a manifest
    edited while the server was down is not missed.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def set(self, project: str, slug: str, tags: list[str]) -> None:
        await self.db.pool.execute(
            """INSERT INTO package_tags (project, slug, tags, updated_at)
               VALUES ($1, $2, $3::jsonb, now())
               ON CONFLICT (project, slug)
               DO UPDATE SET tags = EXCLUDED.tags, updated_at = now()""",
            project, slug, json.dumps(tags),
        )

    async def forget(self, project: str, slug: str) -> None:
        await self.db.pool.execute(
            "DELETE FROM package_tags WHERE project = $1 AND slug = $2", project, slug
        )

    async def for_project(self, project: str) -> dict[str, list[str]]:
        rows = await self.db.pool.fetch(
            "SELECT slug, tags FROM package_tags WHERE project = $1", project
        )
        return {row["slug"]: json.loads(row["tags"]) for row in rows}

    async def reconcile(self, assets_dir: Path, project: str) -> int:
        """Rebuild one project's index from the manifests on disk.

        Returns how many packages changed, so a startup pass can say whether it
        actually found drift rather than claiming work it did not do.
        """
        root = (assets_dir / project).resolve()
        if not root.is_dir():
            return 0
        known = await self.for_project(project)
        changed = 0
        on_disk: set[str] = set()
        for directory in (p for p in root.iterdir() if p.is_dir()):
            on_disk.add(directory.name)
            tags = read_manifest_tags(directory / "manifest.json")
            if known.get(directory.name) != tags:
                await self.set(project, directory.name, tags)
                changed += 1
        # A package removed from disk should stop filtering the library.
        for slug in known.keys() - on_disk:
            await self.forget(project, slug)
            changed += 1
        return changed


def read_manifest_tags(manifest: Path) -> list[str]:
    """The `tags` list from a package manifest, or nothing."""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    return normalise_tags(payload.get("tags"))
