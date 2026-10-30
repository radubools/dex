"""Reads and writes for threads, messages, and tasks.

Everything the UI needs after a restart lives here. The in-memory dataclasses
are views over these rows, never the source of truth.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import socket  # noqa: F401  (hostname is supplied by the caller)
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

from .config import DEFAULT_CONCURRENCY, MAX_WORKERS
from .db import Database
from .models import INTERACTIVE_SCOPES, Task, TaskState
from .threads import Thread, ThreadMessage

TASK_COLUMNS = """
    id, thread_id, title, slug, problem, state, activity,
    extract(epoch from created_at)::float8 AS created_at,
    extract(epoch from started_at)::float8 AS started_at,
    extract(epoch from finished_at)::float8 AS finished_at,
    error, cost_usd, turns, session_id, attempt, parent_id, resumed_from, output_slug,
    model, cost_is_estimate, project, scope, held, anchor, waiting_on, wake_note, options, priority
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
    task.held = row["held"]
    # JSONB comes back as text through asyncpg unless a codec is registered,
    # and every other column here is a scalar — decoding the one field is
    # cheaper than teaching the pool about JSON for it.
    raw_anchor = row["anchor"]
    if isinstance(raw_anchor, str):
        try:
            task.anchor = json.loads(raw_anchor)
        except ValueError:
            task.anchor = None
    else:
        task.anchor = raw_anchor
    raw_waiting = row["waiting_on"]
    task.waiting_on = json.loads(raw_waiting) if isinstance(raw_waiting, str) else raw_waiting
    task.wake_note = row["wake_note"]
    task.priority = row["priority"] or 0
    raw_options = row["options"]
    task.options = (json.loads(raw_options) if isinstance(raw_options, str) else raw_options) or {}
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

    async def viewed(self, thread_id: str) -> str | None:
        """Records that the thread was opened, and its project with it
        (`last_viewed_at` on both). Returns the project, or None for no thread."""
        row = await self.db.pool.fetchrow(
            """UPDATE threads SET last_viewed_at = now() WHERE id = $1
               RETURNING project""",
            thread_id,
        )
        if row is None:
            return None
        if row["project"]:
            await self.db.pool.execute(
                "UPDATE projects SET last_viewed_at = now() WHERE slug = $1", row["project"]
            )
        return row["project"] or ""

    async def list(
        self, project: str | None = None, include_hidden: bool = False, by_viewed: bool = False
    ) -> list[Thread]:
        """A project's threads: the design thread first, then the latest
        updated; or, `by_viewed`, the last opened first (`last_viewed_at`),
        then the never-opened ones, newest first."""
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
               ORDER BY CASE WHEN $3 THEN COALESCE(t.last_viewed_at, t.created_at) END
                            DESC NULLS LAST,
                        -- Otherwise design threads lead: they are the project's
                        -- own conversation.
                        (t.kind = 'project_design') DESC, t.updated_at DESC""",
            project, include_hidden, by_viewed,
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
            """SELECT t.thread_id, sum(u.cost_usd)::float8 AS cost
               FROM task_usage u JOIN tasks t ON t.id = u.task_id
               WHERE t.thread_id IS NOT NULL GROUP BY t.thread_id"""
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
                                  model, project, scope, held, anchor, options)
               VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7), $8, $9, $10, $11, $12, $13, $14,
                       $15, $16, $17, $18)""",
            task.id, task.thread_id, task.title, task.slug, task.problem,
            task.state.value, task.created_at, task.attempt, task.parent_id, task.session_id,
            task.output_slug, task.resumed_from, task.model, task.project, task.scope,
            task.held,
            json.dumps(task.anchor) if task.anchor else None,
            json.dumps(task.options) if task.options else None,
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

    async def latest_by_package(self, project: str) -> dict[str, tuple[str, str]]:
        """The newest task behind each of a project's packages: id and title.

        A package is written by every attempt at it (a resume or a follow-up
        writes into its parent's `output_slug`), so the newest one is the run
        that last changed it, and the one a follow-up message should continue.
        An archived task counts only when nothing else made the package.
        """
        rows = await self.db.pool.fetch(
            """SELECT DISTINCT ON (coalesce(output_slug, slug))
                      coalesce(output_slug, slug) AS package, id, title
                 FROM tasks
                WHERE project = $1
                ORDER BY coalesce(output_slug, slug), (state = 'archived'), created_at DESC""",
            project,
        )
        return {row["package"]: (row["id"], row["title"]) for row in rows}

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
        """Move a task to a state, unless it has been archived out from under it.

        Archiving cancels whatever is running, and the run then writes its own
        ending — a pause, usually, since it was cancelled rather than failed.
        That write landed after the archive and undid it: the task reappeared,
        waited for capacity, and started again. Archived is the one state a
        late write may not overturn.
        """
        assignments = ["state = $2"]
        values: list[Any] = [task_id, state.value]
        for column, value in fields.items():
            if column in {"started_at", "finished_at"}:
                values.append(value)
                assignments.append(f"{column} = to_timestamp(${len(values)})")
            else:
                values.append(value)
                assignments.append(f"{column} = ${len(values)}")
        # Archiving itself is always allowed; only moving *out* of it is not.
        guard = "" if state is TaskState.ARCHIVED else " AND state <> 'archived'"
        result = await self.db.pool.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = $1{guard}", *values
        )
        # Whether the write landed. A caller that has already told the UI what
        # it was about to do needs to know when the database disagreed, or the
        # screen keeps showing a state that was never stored.
        return result.rsplit(" ", 1)[-1] != "0"

    async def set_cost(self, task_id: str, cost: float, *, estimate: bool) -> None:
        """Record spend. An estimate never overwrites an authoritative figure."""
        await self.db.pool.execute(
            """UPDATE tasks SET cost_usd = $2, cost_is_estimate = $3
               WHERE id = $1 AND ($3 = false OR cost_is_estimate = true OR cost_usd IS NULL)""",
            task_id, cost, estimate,
        )

    async def add_usage(
        self, task_id: str, hour: datetime, skill: str, cost: float, counts: dict[str, int],
    ) -> None:
        """Add one message's spend to the rollup the dashboard reads."""
        await self.db.pool.execute(
            """INSERT INTO task_usage (task_id, hour, skill, cost_usd, input_tokens,
                   cache_read_tokens, cache_write_tokens, output_tokens, thinking_tokens)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (task_id, hour, skill) DO UPDATE SET
                   cost_usd = task_usage.cost_usd + EXCLUDED.cost_usd,
                   input_tokens = task_usage.input_tokens + EXCLUDED.input_tokens,
                   cache_read_tokens = task_usage.cache_read_tokens + EXCLUDED.cache_read_tokens,
                   cache_write_tokens = task_usage.cache_write_tokens + EXCLUDED.cache_write_tokens,
                   output_tokens = task_usage.output_tokens + EXCLUDED.output_tokens,
                   thinking_tokens = task_usage.thinking_tokens + EXCLUDED.thinking_tokens""",
            task_id, hour, skill, cost,
            counts.get("input_tokens", 0),
            counts.get("cache_read_tokens", 0),
            counts.get("cache_write_tokens", 0),
            counts.get("output_tokens", 0),
            counts.get("thinking_tokens", 0),
        )

    async def session_usage(self, task_id: str, session_id: str | None) -> float:
        """What a task's whole agent session has cost so far, over all its tasks."""
        value = await self.db.pool.fetchval(
            """SELECT COALESCE(sum(u.cost_usd), 0)::float8 FROM task_usage u
               JOIN tasks t ON t.id = u.task_id
               WHERE u.task_id = $1 OR ($2::text IS NOT NULL AND t.session_id = $2)""",
            task_id, session_id,
        )
        return float(value or 0)

    async def name_usage_skill(self, task_id: str, skill: str) -> None:
        """Give a task's unattributed spend to the first skill it touched.

        Reading the brief and deciding what to do is work for that skill, and
        it comes before anything that could say so.
        """
        async with self.db.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO task_usage (task_id, hour, skill, cost_usd, input_tokens,
                       cache_read_tokens, cache_write_tokens, output_tokens, thinking_tokens)
                   SELECT task_id, hour, $2, cost_usd, input_tokens,
                       cache_read_tokens, cache_write_tokens, output_tokens, thinking_tokens
                   FROM task_usage WHERE task_id = $1 AND skill = ''
                   ON CONFLICT (task_id, hour, skill) DO UPDATE SET
                       cost_usd = task_usage.cost_usd + EXCLUDED.cost_usd,
                       input_tokens = task_usage.input_tokens + EXCLUDED.input_tokens,
                       cache_read_tokens = task_usage.cache_read_tokens + EXCLUDED.cache_read_tokens,
                       cache_write_tokens = task_usage.cache_write_tokens + EXCLUDED.cache_write_tokens,
                       output_tokens = task_usage.output_tokens + EXCLUDED.output_tokens,
                       thinking_tokens = task_usage.thinking_tokens + EXCLUDED.thinking_tokens""",
                task_id, skill,
            )
            await conn.execute("DELETE FROM task_usage WHERE task_id = $1 AND skill = ''", task_id)

    async def set_tokens(self, task_id: str, counts: dict[str, int]) -> None:
        """Record the token counts the agent reported for a finished run.

        Written once, from the result: the per-message usage carries no thinking
        detail, so accumulating it would give a total that cannot be split.
        """
        await self.db.pool.execute(
            """UPDATE tasks SET input_tokens = $2, cache_read_tokens = $3,
                   cache_write_tokens = $4, output_tokens = $5, thinking_tokens = $6
               WHERE id = $1""",
            task_id,
            counts.get("input_tokens", 0),
            counts.get("cache_read_tokens", 0),
            counts.get("cache_write_tokens", 0),
            counts.get("output_tokens", 0),
            counts.get("thinking_tokens", 0),
        )

    async def set_options(self, task_id: str, options: dict[str, Any]) -> None:
        """Replace a task's run options (a chat's player turned a switch while it runs)."""
        await self.db.pool.execute("UPDATE tasks SET options = $2 WHERE id = $1", task_id,
                                   json.dumps(options) if options else None)

    async def set_session(self, task_id: str, session_id: str) -> None:
        await self.db.pool.execute(
            "UPDATE tasks SET session_id = $2 WHERE id = $1", task_id, session_id
        )

    async def claim(self, worker: str, interactive: bool | None = None) -> Task | None:
        """Take the oldest queued task, skipping rows another worker holds.

        `interactive` picks which kind of work to look at: True for the chat
        turns that run as tasks, False for generation, None for either. The two
        have separate ceilings, so a worker belonging to one pool must not claim
        the other's work — a chat worker that took a generation task would put
        that task past the task limit and leave the reply waiting anyway.
        """
        scope_filter = ""
        if interactive is True:
            scope_filter = "AND scope = ANY($1::text[])"
        elif interactive is False:
            scope_filter = "AND NOT (scope = ANY($1::text[]))"
        scopes = [list(INTERACTIVE_SCOPES)] if scope_filter else []
        async with self.db.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                # Only queued work is claimable. A paused task is deliberately
                # held back until `resume_paused` decides there is room for it.
                # Work that has run before goes first: a task parked by a
                # pause has already cost tokens and holds an agent session
                # worth continuing, so it is finished before anything untouched
                # is begun. `seq` alone made that true only by coincidence,
                # because parked tasks usually happen to be the older ones.
                # A woken task (priority) goes before both: what it waited on
                # is done, and it holds the session that asked for it.
                # A paused task is claimable too once a worker has a free slot
                # (the fleet held or at capacity claims nothing): waiting for
                # `resume_paused` let queued work take every freed slot first,
                # so a prioritized paused task never got one.
                f"""SELECT id FROM tasks
                   WHERE (state = 'queued' OR (state = 'paused' AND started_at IS NOT NULL AND NOT held))
                     {scope_filter}
                   ORDER BY priority DESC, (started_at IS NOT NULL) DESC, seq
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                *scopes,
            )
            if row is None:
                return None
            claimed = await conn.fetchrow(
                f"""UPDATE tasks
                    SET state = 'running', claimed_by = $2, claimed_at = now(),
                        started_at = now(), activity = 'starting', priority = 0,
                        -- a paused one continues its own session, as resume_paused puts it back
                        resumed_from = CASE WHEN state = 'paused' THEN COALESCE(resumed_from, session_id)
                                            ELSE resumed_from END,
                        finished_at = NULL
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

    async def prioritize(self, ids: list[str], level: int) -> list[Task]:
        """Raise these tasks' claim order to `level` (never lower it), if they are
        still waiting to run: queued, paused or blocked. Returns those raised."""
        rows = await self.db.pool.fetch(
            f"""UPDATE tasks SET priority = $2
                WHERE id = ANY($1::text[]) AND priority < $2
                  AND state IN ('queued', 'paused', 'blocked')
                RETURNING {TASK_COLUMNS}""",
            ids, level,
        )
        return [task_from_row(r) for r in rows]

    async def paused(self, limit: int = 10, interactive: bool | None = None) -> list[Task]:
        """Tasks dex paused and may start again, oldest first.

        Not the ones the operator paused: those are `held`, and starting them
        again is exactly what the button was pressed to prevent. Only ones that
        actually started. A row can be paused without ever
        having run — the tasks imported from the old file-backed store are
        marked that way — and starting those would build directories for
        packages that have since been superseded.
        """
        # Filtered the same way as `claim`, and for the same reason: each pool
        # puts back only what it is allowed to run.
        scope_filter = ""
        if interactive is True:
            scope_filter = "AND scope = ANY($2::text[])"
        elif interactive is False:
            scope_filter = "AND NOT (scope = ANY($2::text[]))"
        scopes = [list(INTERACTIVE_SCOPES)] if scope_filter else []
        rows = await self.db.pool.fetch(
            f"""SELECT {TASK_COLUMNS} FROM tasks
                WHERE state = 'paused' AND started_at IS NOT NULL AND NOT held
                  {scope_filter}
                ORDER BY priority DESC, seq LIMIT $1""",
            limit, *scopes,
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


class DepStore:
    """Task dependencies (design/18_task_dependencies.md).

    A task that starts or continues another from its own run depends on it.
    An edge points at the attempt it was made to; the attempt that counts is
    the latest one of that chain (attempts link by `parent_id`), so a
    follow-up of the dependency is waited for too.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, task_id: str, depends_on: str, kind: str = "started") -> None:
        await self.db.pool.execute(
            """INSERT INTO task_deps (task_id, depends_on, kind) VALUES ($1, $2, $3)
               ON CONFLICT (task_id, depends_on) DO NOTHING""",
            task_id, depends_on, kind,
        )

    async def of(self, task_id: str) -> list[str]:
        """What a task depends on, oldest edge first."""
        rows = await self.db.pool.fetch(
            "SELECT depends_on FROM task_deps WHERE task_id = $1 ORDER BY created_at", task_id)
        return [r["depends_on"] for r in rows]

    async def chain(self, task_id: str) -> list[str]:
        """Every attempt of the package `task_id` is an attempt of, first to last."""
        rows = await self.db.pool.fetch(
            """WITH RECURSIVE up AS (
                    SELECT id, parent_id FROM tasks WHERE id = $1
                    UNION SELECT t.id, t.parent_id FROM tasks t JOIN up ON t.id = up.parent_id),
                down AS (
                    SELECT id FROM up WHERE parent_id IS NULL
                    UNION SELECT t.id FROM tasks t JOIN down d ON t.parent_id = d.id)
                SELECT id FROM tasks WHERE id IN (SELECT id FROM down) OR id = $1
                ORDER BY attempt, created_at""",
            task_id,
        )
        return [r["id"] for r in rows]

    async def latest(self, task_id: str) -> Task | None:
        """The newest attempt of the chain `task_id` belongs to (itself if none follow it)."""
        row = await self.db.pool.fetchrow(
            f"""WITH RECURSIVE chain AS (
                    SELECT id FROM tasks WHERE id = $1
                    UNION SELECT t.id FROM tasks t JOIN chain c ON t.parent_id = c.id)
                SELECT {TASK_COLUMNS} FROM tasks WHERE id IN (SELECT id FROM chain)
                ORDER BY attempt DESC, created_at DESC LIMIT 1""",
            task_id,
        )
        return task_from_row(row) if row else None

    async def status(self, task_id: str) -> dict[str, Any] | None:
        """Where a dependency stands: its latest attempt, and whether it is done.

        Done means that attempt has ended (succeeded, failed, cancelled or
        archived) and no follow-up note is still queued for it: a note becomes
        another attempt, which is waited for in turn.
        """
        latest = await self.latest(task_id)
        if latest is None:
            return None
        queued = await self.db.pool.fetchval(
            "SELECT count(*) FROM task_messages WHERE task_id = $1 AND delivered_at IS NULL", latest.id)
        summary = await self.db.pool.fetchval(
            """SELECT data->>'summary' FROM events WHERE task_id = $1 AND type = 'result'
               ORDER BY seq DESC LIMIT 1""", latest.id)
        thread = await self.db.pool.fetchval(
            "SELECT title FROM threads WHERE id = $1", latest.thread_id) if latest.thread_id else None
        return {
            "task": latest, "done": latest.state.terminal and not queued,
            "queuedNotes": queued, "summary": summary, "threadTitle": thread,
        }

    async def waiters(self, task_id: str) -> list[str]:
        """Every task that waits on `task_id`, directly or through others."""
        rows = await self.db.pool.fetch(
            """WITH RECURSIVE up AS (
                   SELECT task_id FROM task_deps WHERE depends_on = $1
                   UNION SELECT d.task_id FROM task_deps d JOIN up ON d.depends_on = up.task_id)
               SELECT task_id FROM up""",
            task_id,
        )
        return [r["task_id"] for r in rows]

    async def packages(self, ids: list[str]) -> set[str]:
        """The package keys (output directory names) of these tasks."""
        if not ids:
            return set()
        rows = await self.db.pool.fetch(
            "SELECT coalesce(output_slug, slug) AS pkg FROM tasks WHERE id = ANY($1::text[])", ids)
        return {r["pkg"] for r in rows}

    async def depth(self, task_id: str) -> int:
        """How many tasks stand above this one in the graph (0 for a task nobody waits on)."""
        depth, level = 0, [task_id]
        while level and depth < 50:
            rows = await self.db.pool.fetch(
                "SELECT DISTINCT task_id FROM task_deps WHERE depends_on = ANY($1::text[])", level)
            level = [r["task_id"] for r in rows]
            if level:
                depth += 1
        return depth

    async def count(self, task_id: str) -> int:
        return await self.db.pool.fetchval("SELECT count(*) FROM task_deps WHERE task_id = $1", task_id)

    async def blocked(self) -> list[Task]:
        rows = await self.db.pool.fetch(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE state = 'blocked' AND NOT held ORDER BY seq")
        return [task_from_row(r) for r in rows]

    async def graph(self, project: str | None, recent_s: float = 6 * 3600) -> dict[str, Any]:
        """The active part of the task graph: nodes and the edges between them.

        A node is a task chain, shown by its latest attempt, or a request: the
        operator's message in a thread whose plan started tasks, with a
        `planned` edge to each chain it started. Active chains are the ones
        queued, running, waiting on input, paused or blocked; finished ones are
        kept when an edge joins them to an active one, or when they ended in
        the last `recent_s` seconds and took part in an edge. A request is kept
        with all of its chains when any of them is kept, or when its last one
        ended in the last `recent_s` seconds.
        """
        rows = await self.db.pool.fetch(
            f"""WITH RECURSIVE roots AS (
                    SELECT id, id AS root FROM tasks WHERE parent_id IS NULL OR parent_id NOT IN (SELECT id FROM tasks)
                    UNION SELECT t.id, r.root FROM tasks t JOIN roots r ON t.parent_id = r.id)
                SELECT {TASK_COLUMNS},
                       (SELECT r.root FROM roots r WHERE r.id = tasks.id LIMIT 1) AS root,
                       (SELECT th.title FROM threads th WHERE th.id = tasks.thread_id) AS thread_title
                  FROM tasks
                 WHERE ($1::text IS NULL OR project = $1) AND state <> 'archived'""",
            project,
        )
        latest: dict[str, Any] = {}
        root_of: dict[str, str] = {}
        for r in rows:
            root_of[r["id"]] = r["root"]
            cur = latest.get(r["root"])
            if cur is None or (r["attempt"], r["created_at"] or 0) > (cur["attempt"], cur["created_at"] or 0):
                latest[r["root"]] = r
        edges_rows = await self.db.pool.fetch("SELECT task_id, depends_on, kind FROM task_deps")
        edges = {}
        for e in edges_rows:
            a, b = root_of.get(e["task_id"]), root_of.get(e["depends_on"])
            if a and b and a != b:
                edges[(a, b)] = e["kind"]
        active = {"queued", "running", "awaiting_input", "paused", "blocked"}
        now = time.time()
        keep = {k for k, r in latest.items() if r["state"] in active}
        linked = {k for pair in edges for k in pair}
        for a, b in edges:
            if a in keep or b in keep:
                keep |= {a, b}
        keep |= {k for k in linked if (latest[k]["finished_at"] or 0) > now - recent_s}
        requests = await self._requests(project, root_of, {b for (_, b), kind in edges.items() if kind == "started"})
        request_nodes = []
        for req in requests:
            chains = [k for k in req.pop("chains") if k in latest]
            ended = max(((latest[k]["finished_at"] or 0) for k in chains), default=0)
            if not chains or not (any(k in keep for k in chains) or ended > now - recent_s):
                continue
            keep |= set(chains)
            states = {latest[k]["state"] for k in chains}
            req["state"] = next((s for s in ("awaiting_input", "running", "blocked", "paused", "queued") if s in states),
                                "failed" if "failed" in states else "succeeded" if "succeeded" in states else "cancelled")
            req["finishedAt"] = None if states & active else ended or None
            req["tasks"] = len(chains)
            req["done"] = sum(latest[k]["state"] == "succeeded" for k in chains)
            req["chains"] = chains
            request_nodes.append(req)
        nodes = []
        for k in keep:
            r = latest[k]
            nodes.append({
                "kind": "task",
                "id": r["id"], "chain": k, "slug": r["slug"], "package": r["output_slug"] or r["slug"],
                "title": r["title"], "state": r["state"], "activity": r["activity"],
                "attempt": r["attempt"], "threadId": r["thread_id"], "threadTitle": r["thread_title"],
                "costUsd": r["cost_usd"], "createdAt": r["created_at"], "finishedAt": r["finished_at"],
            })
        ids = {n["chain"]: n["id"] for n in nodes}
        planned = [{"from": req["id"], "to": ids[k], "kind": "planned"}
                   for req in request_nodes for k in req.pop("chains") if k in ids]
        nodes += request_nodes
        nodes.sort(key=lambda n: n["createdAt"] or 0)
        return {
            "nodes": nodes,
            "edges": planned + [{"from": ids[a], "to": ids[b], "kind": kind}
                                for (a, b), kind in edges.items() if a in ids and b in ids],
        }

    async def _requests(self, project: str | None, root_of: dict[str, str], started: set[str]) -> list[dict[str, Any]]:
        """The operator's requests that started tasks, each with the chains its plan started.

        A plan's tasks are announced in its thread as a `tasks` message
        ("Started 15 tasks"), after the operator's message and the plan; a
        resume or a re-run is announced the same way, so a chain belongs to
        the request before the first message that names its first attempt. A
        chain another task started (a `started` edge) is that task's, even
        though it is announced in its own thread too.
        """
        rows = await self.db.pool.fetch(
            """WITH named AS (
                   SELECT m.thread_id, m.ts, t->>'id' AS task_id
                     FROM messages m JOIN threads th ON th.id = m.thread_id,
                          jsonb_array_elements(CASE WHEN jsonb_typeof(m.data->'tasks') = 'array'
                                                    THEN m.data->'tasks' ELSE '[]'::jsonb END) t
                    WHERE m.role = 'dex' AND m.kind = 'tasks' AND ($1::text IS NULL OR th.project = $1)),
               first AS (SELECT DISTINCT ON (task_id) task_id, thread_id, ts FROM named ORDER BY task_id, ts)
               SELECT f.task_id, r.id AS request_id, r.body, r.ts AS request_ts, r.thread_id, th.title AS thread_title
                 FROM first f
                 JOIN LATERAL (SELECT u.id, u.body, u.ts, u.thread_id FROM messages u
                                WHERE u.thread_id = f.thread_id AND u.role = 'user' AND u.ts <= f.ts
                                ORDER BY u.ts DESC LIMIT 1) r ON true
                 JOIN threads th ON th.id = r.thread_id""",
            project,
        )
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            chain = root_of.get(r["task_id"])
            # Only a chain's first attempt was started by the plan; later ones are its follow-ups.
            if chain != r["task_id"] or chain in started:
                continue
            req = out.get(r["request_id"])
            if req is None:
                # Its words, on one line, without a leading list marker ("- I want…").
                text = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", " ".join((r["body"] or "").split()))
                req = out[r["request_id"]] = {
                    "kind": "request", "id": f"request:{r['request_id']}", "chain": f"request:{r['request_id']}",
                    "title": text[:140] + ("…" if len(text) > 140 else ""),
                    "threadId": r["thread_id"], "threadTitle": r["thread_title"],
                    "createdAt": r["request_ts"].timestamp(), "chains": [],
                }
            req["chains"].append(chain)
        return list(out.values())


class TaskMessageStore:
    """Follow-up notes queued against a running task.

    Delivery is deliberately deferred: a message typed while the agent is
    working is held until the task stops, then becomes the brief for its next
    attempt. Interrupting a run mid-flight would throw away the work in
    progress, which is rarely what someone typing a note wants.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, task_id: str, body: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = uuid.uuid4().hex[:12]
        row = await self.db.pool.fetchrow(
            """INSERT INTO task_messages (id, task_id, body, options)
               VALUES ($1, $2, $3, $4)
               RETURNING id, body, extract(epoch from created_at)::float8 AS created_at""",
            message_id, task_id, body, json.dumps(options) if options else None,
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

    async def pending_options(self, task_id: str) -> dict[str, Any]:
        """The options the undelivered notes carry, merged oldest first (the last word wins)."""
        rows = await self.db.pool.fetch(
            """SELECT options FROM task_messages
               WHERE task_id = $1 AND delivered_at IS NULL AND options IS NOT NULL
               ORDER BY created_at""", task_id)
        merged: dict[str, Any] = {}
        for r in rows:
            o = r["options"]
            merged.update(json.loads(o) if isinstance(o, str) else o or {})
        return merged


class CostStore:
    """Spend rollups for the dashboard and the per-thread totals."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def totals(self) -> dict[str, float]:
        """Spend over the usual windows, for the header menu."""
        row = await self.db.pool.fetchrow(
            """SELECT
                 COALESCE(sum(cost_usd) FILTER (WHERE hour > now() - interval '1 day'), 0) AS day,
                 COALESCE(sum(cost_usd) FILTER (WHERE hour > now() - interval '7 days'), 0) AS week,
                 COALESCE(sum(cost_usd) FILTER (WHERE hour > now() - interval '30 days'), 0) AS month,
                 COALESCE(sum(cost_usd), 0) AS all_time
               FROM task_usage"""
        )
        return {k: float(v) for k, v in dict(row).items()}

    async def token_totals(self) -> dict[str, int]:
        """All-time tokens, with thinking split out of output.

        `thinking_tokens` is the part of `output_tokens` the model spent
        thinking, so `visible` is the remainder. The three never sum to more
        than the output: thinking + visible = output, by construction.
        """
        row = await self.db.pool.fetchrow(
            """SELECT
                 COALESCE(sum(input_tokens), 0)       AS input,
                 COALESCE(sum(cache_read_tokens), 0)  AS cache_read,
                 COALESCE(sum(cache_write_tokens), 0) AS cache_write,
                 COALESCE(sum(output_tokens), 0)      AS output,
                 COALESCE(sum(thinking_tokens), 0)    AS thinking,
                 count(DISTINCT task_id) FILTER (WHERE output_tokens > 0) AS counted,
                 count(DISTINCT task_id) FILTER (WHERE cost_usd > 0)      AS with_cost
               FROM task_usage"""
        )
        totals = {k: int(v) for k, v in dict(row).items()}
        totals["visible"] = max(0, totals["output"] - totals["thinking"])
        return totals

    async def coverage(self, since: str = "week") -> dict[str, float]:
        """How much of a period's spend has tokens, and how much was inferred."""
        interval = _INTERVALS.get(since, "7 days")
        row = await self.db.pool.fetchrow(
            f"""SELECT
                  COALESCE(sum(cost_usd), 0)::float8 AS spend,
                  COALESCE(sum(cost_usd) FILTER (WHERE output_tokens > 0), 0)::float8 AS with_tokens,
                  COALESCE(sum(cost_usd) FILTER (WHERE inferred), 0)::float8 AS inferred
                FROM task_usage WHERE hour > now() - interval '{interval}'"""
        )
        return dict(row)

    async def by_thread(self, thread_id: str) -> float:
        """A thread costs what the tasks it started cost."""
        value = await self.db.pool.fetchval(
            """SELECT COALESCE(sum(u.cost_usd), 0) FROM task_usage u
               JOIN tasks t ON t.id = u.task_id WHERE t.thread_id = $1""",
            thread_id,
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
            "model": "COALESCE(t.model, 'unknown')",
            "project": "COALESCE(t.project, 'none')",
            "skill": "COALESCE(NULLIF(u.skill, ''), '(no skill)')",
            "state": "t.state",
        }.get(group_by, "COALESCE(t.model, 'unknown')")
        interval = _INTERVALS.get(since, "7 days")
        width = _BUCKETS.get(bucket, "day")

        rows = await self.db.pool.fetch(
            f"""SELECT date_trunc('{width}', u.hour) AS bucket,
                       {expression} AS series,
                       sum(u.cost_usd)::float8 AS cost,
                       count(DISTINCT u.task_id) AS tasks,
                       sum(u.input_tokens)::bigint AS input,
                       sum(u.cache_read_tokens)::bigint AS cache_read,
                       sum(u.cache_write_tokens)::bigint AS cache_write,
                       sum(u.output_tokens)::bigint AS output,
                       sum(u.thinking_tokens)::bigint AS thinking
                FROM task_usage u JOIN tasks t ON t.id = u.task_id
                WHERE u.hour > now() - interval '{interval}'
                GROUP BY 1, 2
                ORDER BY 1""",
        )
        return [
            {
                "bucket": row["bucket"].isoformat(),
                "series": row["series"],
                "cost": row["cost"],
                "tasks": row["tasks"],
                "input": row["input"],
                "cacheRead": row["cache_read"],
                "cacheWrite": row["cache_write"],
                "output": row["output"],
                "thinking": row["thinking"],
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
    #: Whether a task may propose promoting a helper into the project's
    #: `utils/`. Read at the moment a task is about to ask, so turning it off
    #: silences runs already in flight rather than only the next ones.
    UTILITY_PROPOSALS = "utility_proposals"
    MODEL = "model"
    #: How long a task may think before acting. None defers to DEX_EFFORT, so
    #: an operator who never touches it keeps whatever the deployment set.
    EFFORT = "effort"
    ANIMATION_SPEED = "animation_speed"
    #: The latest answer to each task question the operator has answered, per
    #: project. A sibling task that asks the same thing within the window is
    #: answered from it instead of parking again.
    ASK_ANSWERS = "ask_answers"

    #: How long a recorded answer may still be used. Half a day: past that
    #: a choice made yesterday morning is a guess, not a decision.
    ASK_ANSWER_TTL = 12 * 3600.0

    #: Applied when nothing is stored yet.
    DEFAULTS: dict[str, Any] = {
        AUTO_APPROVE: False,
        PAUSED: False,
        LIMIT_PAUSED: False,
        TASK_CONCURRENCY: DEFAULT_CONCURRENCY,
        CHAT_CONCURRENCY: DEFAULT_CONCURRENCY,
        # On by default: the loop is the point, and an operator who does not
        # want to be asked turns it off once.
        UTILITY_PROPOSALS: True,
        MODEL: None,
        EFFORT: None,
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

    async def effort(self) -> str | None:
        """The operator's thinking-effort choice, or None to use the default."""
        value = await self.get(self.EFFORT)
        return str(value) if value else None

    async def utility_proposals(self) -> bool:
        """Whether a task may ask about promoting a helper into `utils/`."""
        return bool(await self.get(self.UTILITY_PROPOSALS, True))

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

    async def ask_answers(self, project: str | None) -> dict[str, Any]:
        """This project's recorded answers, still inside the window."""
        raw = await self.get(self.ASK_ANSWERS, {})
        if not isinstance(raw, dict):
            return {}
        now = time.time()
        return {
            key: entry
            for key, entry in raw.items()
            if isinstance(entry, dict)
            and entry.get("project") == project
            and now - entry.get("at", 0) <= self.ASK_ANSWER_TTL
        }

    async def remember_answer(
        self, question: str, options: list[str], answer: str, *, project: str | None
    ) -> None:
        """Record the answer to one task question, latest per question.

        Keyed by the question's own text, so a sibling task that asks it again
        gets the same answer without another park. Old entries are dropped on
        the way in, which keeps the row to roughly the last window's worth of
        questions without any sweeping job to schedule.
        """
        raw = await self.get(self.ASK_ANSWERS, {})
        raw = raw if isinstance(raw, dict) else {}
        now = time.time()
        raw = {
            key: entry
            for key, entry in raw.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("at"), (int, float))
            and now - entry["at"] < self.ASK_ANSWER_TTL
        }
        entry = {"question": question, "options": options, "answer": answer,
                 "at": now, "project": project}
        if not answer:
            return
        raw[_answer_key(question)] = entry
        await self.set(self.ASK_ANSWERS, raw)

    async def prior_answer(
        self, question: str, options: list[str], *, project: str | None
    ) -> str | None:
        """The answer this question already got, if it was asked here recently.

        The recorded answer must still be one of the options the question now
        offers — a question asked again with different options is a different
        question, and answering it from an option nobody offered would be
        inventing a choice. A question with no options accepts the answer
        verbatim, because its answer was free text in the first place.
        """
        entry = (await self.ask_answers(project)).get(_answer_key(question))
        if not isinstance(entry, dict):
            return None
        answer = entry.get("answer")
        if not isinstance(answer, str) or not answer:
            return None
        if options and answer not in options:
            return None
        return answer


def _answer_key(question: str) -> str:
    """One key for one question, however it was wrapped across runs."""
    collapsed = re.sub(r"\s+", " ", question.strip().lower())
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:24]


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
