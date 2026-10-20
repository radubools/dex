#!/usr/bin/env python3
"""Queue a task (and, if asked, its thread) by writing straight into Postgres.

The running server picks the task up by itself: idle workers re-check for
queued rows every few seconds precisely so that work created by another
process is found. It writes what `TaskQueue.submit` writes — the `tasks` row
and its `task_created` event — using dex's own models, so the slug, model and
event shape match a task submitted from the UI.

    .venv/bin/python scripts/enqueue_task.py --project games \\
        --thread "Players" --create-thread \\
        --title "Builder character" --slug builder --problem @brief.md

    --problem @file       read the brief from a file
    --thread <id | text>  a thread id, or text its title contains (in the project)
    --create-thread       make a thread with that title if none matches
    --dry-run             print what would be inserted and stop

Continuing a finished (failed, cancelled, succeeded) task is a message to it,
as in the UI: the message is recorded against the task, and a follow-up
attempt starts that resumes its agent session in the same package directory
with the message appended to its brief:

    .venv/bin/python scripts/enqueue_task.py --project games \
        --message-to landmark-tower --problem "Finish: tag, validate, export."

Prints one JSON line: task id, slug, thread id. Every task spends the user's
Claude usage the moment a worker claims it, exactly like the UI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
# Never write .pyc files into src/dex: the user's server runs with --reload
# watching that directory, and this script must not be a reason it restarts.
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from dex.models import Event, Task, continued_problem, slugify  # noqa: E402
from dex.store import TASK_COLUMNS, task_from_row  # noqa: E402

DSN = os.environ.get("DEX_DATABASE_URL", "postgresql://127.0.0.1/dex")
ASSETS = Path(os.environ.get("DEX_ASSETS", ROOT / "assets"))
DEFAULT_MODEL = os.environ.get("DEX_MODEL", "claude-opus-5-5")


async def resolve_thread(conn: asyncpg.Connection, project: str, thread: str | None, create: bool) -> str | None:
    if not thread:
        return None
    row = await conn.fetchrow("SELECT id FROM threads WHERE id = $1 AND project = $2", thread, project)
    if row:
        return row["id"]
    rows = await conn.fetch(
        "SELECT id, title FROM threads WHERE project = $1 AND title ILIKE $2 AND NOT hidden ORDER BY updated_at DESC",
        project, f"%{thread}%",
    )
    if len(rows) == 1:
        return rows[0]["id"]
    if not rows and create:
        thread_id = uuid.uuid4().hex[:12]
        now = time.time()
        await conn.execute(
            """INSERT INTO threads (id, title, created_at, updated_at, project, kind)
               VALUES ($1, $2, to_timestamp($3), to_timestamp($3), $4, 'chat')""",
            thread_id, thread[:80], now, project,
        )
        return thread_id
    found = ", ".join(f"{r['id']} ({r['title']!r})" for r in rows) or "none"
    sys.exit(f"--thread {thread!r} must match exactly one thread in {project}; found {found}")


async def setting(conn: asyncpg.Connection, key: str):
    raw = await conn.fetchval("SELECT value FROM settings WHERE key = $1", key)
    if raw is None:
        return None
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value.get("v") if isinstance(value, dict) else value


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--problem", required=True, help="the brief, or @file to read it from")
    ap.add_argument("--title")
    ap.add_argument("--slug")
    ap.add_argument("--thread")
    ap.add_argument("--create-thread", action="store_true")
    ap.add_argument("--package", help="existing package directory this task rewrites")
    ap.add_argument("--message-to", help="slug or id of a finished task to continue with this message")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.message_to:
        await message_task(args)
        return

    problem = Path(args.problem[1:]).read_text() if args.problem.startswith("@") else args.problem
    conn = await asyncpg.connect(DSN)
    try:
        if not await conn.fetchval("SELECT 1 FROM projects WHERE slug = $1", args.project):
            sys.exit(f"no project {args.project!r}")
        title = (args.title or problem.strip().splitlines()[0])[:80]
        taken = {r["slug"] for r in await conn.fetch("SELECT slug FROM tasks")}
        taken |= {p.name for p in (ASSETS / args.project).glob("*") if p.is_dir()}
        task = Task(problem=problem.strip(), title=title, slug=slugify(args.slug or title, taken))
        task.project = args.project
        task.output_slug = args.package
        # Same precedence as SettingsStore.model_for: project override, global, default.
        task.model = (await setting(conn, f"model:{args.project}") or await setting(conn, "model")
                      or DEFAULT_MODEL)
        if args.dry_run:
            print(json.dumps({"slug": task.slug, "title": task.title, "model": task.model,
                              "thread": args.thread, "chars": len(task.problem)}))
            return

        async with conn.transaction():
            task.thread_id = await resolve_thread(conn, args.project, args.thread, args.create_thread)
            await conn.execute(
                """INSERT INTO tasks (id, thread_id, title, slug, problem, state, created_at,
                                      attempt, output_slug, model, project, scope, held)
                   VALUES ($1, $2, $3, $4, $5, 'queued', to_timestamp($6), 1, $7, $8, $9, 'package', false)""",
                task.id, task.thread_id, task.title, task.slug, task.problem, task.created_at,
                task.output_slug, task.model, task.project,
            )
            event = Event(type="task_created", task_id=task.id,
                          data={"task": task.to_json(ASSETS), "threadId": task.thread_id}, project=task.project)
            await conn.execute(
                """INSERT INTO events (task_id, thread_id, type, data, ts, project)
                   VALUES ($1, $2, $3, $4::jsonb, to_timestamp($5), $6)""",
                event.task_id, task.thread_id, event.type, json.dumps(event.data, default=str), event.ts, event.project,
            )
            if task.thread_id:
                await conn.execute("UPDATE threads SET updated_at = now() WHERE id = $1", task.thread_id)
        print(json.dumps({"id": task.id, "slug": task.slug, "thread": task.thread_id, "model": task.model}))
    finally:
        await conn.close()


async def message_task(args: argparse.Namespace) -> None:
    """What `TaskQueue.queue_message` then `deliver_messages` do for a finished task."""
    body = (Path(args.problem[1:]).read_text() if args.problem.startswith("@") else args.problem).strip()
    conn = await asyncpg.connect(DSN)
    try:
        row = await conn.fetchrow(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE (slug = $1 OR id = $1) AND project = $2", args.message_to, args.project)
        if row is None:
            sys.exit(f"no task {args.message_to!r} in {args.project}")
        task = task_from_row(row)
        if not task.state.terminal:
            sys.exit(f"{task.slug} is {task.state.value}; dex delivers a message to a running task when it ends, "
                     "which only the server can do. Wait for it to finish.")
        note = ("## Follow-up\n\nThe previous attempt has finished. The operator has since asked for:\n\n"
                f"- {body}")
        if not task.is_design:
            note += ("\n\nWork in the existing task directory, keep what is still "
                     "correct, and re-run the test suite before you finish.")
        taken = {r["slug"] for r in await conn.fetch("SELECT slug FROM tasks")}
        taken |= {p.name for p in (ASSETS / task.project).glob("*") if p.is_dir()}
        follow = Task(problem=continued_problem(task, note), title=task.title, slug=slugify(task.slug, taken))
        follow.thread_id, follow.project, follow.scope = task.thread_id, task.project, task.scope
        follow.attempt, follow.parent_id = task.attempt + 1, task.id
        follow.session_id = follow.resumed_from = task.session_id
        follow.output_slug = task.output_slug or task.slug
        follow.model = task.model or DEFAULT_MODEL
        if args.dry_run:
            print(json.dumps({"continues": task.slug, "slug": follow.slug, "writes_into": follow.output_slug,
                              "resumes_session": bool(task.session_id)}))
            return
        now = time.time()

        async def event(task_id: str, type_: str, data: dict) -> None:
            await conn.execute(
                """INSERT INTO events (task_id, thread_id, type, data, ts, project)
                   VALUES ($1, $2, $3, $4::jsonb, to_timestamp($5), $6)""",
                task_id, task.thread_id, type_, json.dumps(data, default=str), time.time(), task.project)

        async with conn.transaction():
            message_id = uuid.uuid4().hex[:12]
            await conn.execute(
                "INSERT INTO task_messages (id, task_id, body, delivered_at) VALUES ($1, $2, $3, now())",
                message_id, task.id, body)
            await event(task.id, "task_message", {"message": {"id": message_id, "body": body, "createdAt": now,
                                                              "delivered": False}})
            await conn.execute(
                """INSERT INTO tasks (id, thread_id, title, slug, problem, state, created_at, attempt, parent_id,
                                      session_id, output_slug, resumed_from, model, project, scope, held)
                   VALUES ($1, $2, $3, $4, $5, 'queued', to_timestamp($6), $7, $8, $9, $10, $11, $12, $13, $14, false)""",
                follow.id, follow.thread_id, follow.title, follow.slug, follow.problem, follow.created_at,
                follow.attempt, follow.parent_id, follow.session_id, follow.output_slug, follow.resumed_from,
                follow.model, follow.project, follow.scope)
            await event(follow.id, "task_created", {"task": follow.to_json(ASSETS), "threadId": follow.thread_id})
            await event(follow.id, "task_message", {"message": {"id": f"{follow.id}-note-0", "body": body,
                                                                "createdAt": now, "delivered": True}})
            await event(task.id, "task_message_delivered", {"count": 1, "startedTaskId": follow.id})
            if task.thread_id:
                await conn.execute("UPDATE threads SET updated_at = now() WHERE id = $1", task.thread_id)
        print(json.dumps({"continues": task.slug, "id": follow.id, "slug": follow.slug,
                          "writes_into": follow.output_slug, "attempt": follow.attempt}))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
