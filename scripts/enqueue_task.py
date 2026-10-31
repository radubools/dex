#!/usr/bin/env python3
"""Queue tasks (and, if asked, their thread) by writing straight into Postgres.

The running server picks the tasks up by itself: idle workers re-check for
queued rows every few seconds precisely so that work created by another
process is found.

A submission is recorded in its thread the way the UI records one, so the
thread keeps the lineage of every task: the operator's request (a `user`
message), the plan dex made of it (a `plan` message, approved automatically
— no planner runs; the plan *is* the given tasks), and the confirmation that
started them (a `tasks` message, each task carrying its `plannedSlug`). Then
the `tasks` rows and their `task_created` events, built with dex's own models
so slugs, model choice and event shapes match what `/api/chat/confirm` writes.

One task:

    .venv/bin/python scripts/enqueue_task.py --project games \\
        --thread "Players" --create-thread --request "Rebuild the builder" \\
        --title "Builder character" --slug player-builder --problem @brief.md

Several, as one plan (a JSON list of {"title", "slug", "problem"|"brief",
optional "package"}; "brief" names a file):

    .venv/bin/python scripts/enqueue_task.py --project games \\
        --thread "Landmarks" --request "Rebuild the nine landmarks" --batch plan.json

Record the lineage of tasks that already exist (they keep running, nothing is
re-queued) — for work queued before its thread had a plan:

    .venv/bin/python scripts/enqueue_task.py --project games \\
        --request "Rebuild the nine landmarks" --adopt landmark-109,landmark-palace

Continuing a finished (failed, cancelled, succeeded) task is a message to it,
as in the UI: the message is recorded against the task, and a follow-up
attempt starts that resumes its agent session in the same package directory
with the message appended to its brief:

    .venv/bin/python scripts/enqueue_task.py --project games \\
        --message-to landmark-tower --problem "Finish: tag, validate, export."

    --problem @file       read the brief from a file
    --thread <id | text>  a thread id, or text its title contains (in the project)
    --create-thread       make a thread with that title if none matches
    --dry-run             print what would be inserted and stop

Every queued task spends the user's Claude usage the moment a worker claims
it, exactly like the UI.
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
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
# Never write .pyc files into src/dex: the user's server runs with --reload
# watching that directory, and this script must not be a reason it restarts.
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from dex.config import CONFIG  # noqa: E402
from dex.models import Event, Task, continued_problem, slugify  # noqa: E402
from dex.store import TASK_COLUMNS, task_from_row  # noqa: E402
from dex.threads import ThreadMessage  # noqa: E402

DSN = os.environ.get("DEX_DATABASE_URL", "postgresql://127.0.0.1/dex")
ASSETS = Path(os.environ.get("DEX_ASSETS", ROOT / "assets"))
DEFAULT_MODEL = os.environ.get("DEX_MODEL", "claude-opus-5-5")


def text_or_file(value: str) -> str:
    return Path(value[1:]).read_text() if value.startswith("@") else value


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


async def add_event(conn: asyncpg.Connection, event: Event, thread_id: str | None) -> None:
    await conn.execute(
        """INSERT INTO events (task_id, thread_id, type, data, ts, project)
           VALUES ($1, $2, $3, $4::jsonb, to_timestamp($5), $6)""",
        event.task_id, thread_id, event.type, json.dumps(event.data, default=str), event.ts, event.project,
    )


async def add_message(conn: asyncpg.Connection, thread_id: str, message: ThreadMessage) -> None:
    """What the server's `publish_message` does: store it, and announce it to open pages."""
    await conn.execute(
        """INSERT INTO messages (id, thread_id, role, kind, body, data, ts)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, to_timestamp($7))""",
        message.id, thread_id, message.role, message.kind, message.text,
        json.dumps(message.data, default=str), message.ts,
    )
    await add_event(conn, Event(type="thread_message", data={"threadId": thread_id, "message": message.to_json()},
                                ts=message.ts), thread_id)
    await conn.execute("UPDATE threads SET updated_at = now() WHERE id = $1", thread_id)


def planned_row(task: Task) -> dict[str, Any]:
    """A plan row as `PlannedTask.to_json` writes it."""
    return {"title": task.title, "problem": task.problem, "slug": task.slug,
            "updates": task.output_slug or "", "package": "", "scope": task.scope, "anchor": task.anchor}


async def record_lineage(conn: asyncpg.Connection, thread_id: str, request: str, tasks: list[Task],
                         at: float | None = None) -> None:
    """The request, its auto-approved plan, and the confirmation that started `tasks`."""
    at = at if at is not None else time.time()
    n = len(tasks)
    await add_message(conn, thread_id, ThreadMessage(role="user", kind="text", text=request.strip(), ts=at))
    await add_message(conn, thread_id, ThreadMessage(
        role="dex", kind="plan", ts=at + 0.001,
        text=f"Planned as {n} task{'s' if n != 1 else ''} from the request; approved automatically.",
        data={"tasks": [planned_row(t) for t in tasks], "notes": "", "needsClarification": "",
              "options": [], "remaining": "", "batch": 1, "autoApproved": True},
    ))
    await add_message(conn, thread_id, ThreadMessage(
        role="dex", kind="tasks", ts=at + 0.002,
        text=f"Started {n} task{'s' if n != 1 else ''}.",
        data={"tasks": [{**t.to_json(ASSETS), "plannedSlug": t.slug} for t in tasks]},
    ))


async def submit(args: argparse.Namespace) -> None:
    if args.batch:
        rows = json.loads(Path(args.batch).read_text())
    else:
        if not args.problem:
            sys.exit("--problem (or --batch) is required")
        rows = [{"title": args.title, "slug": args.slug, "problem": text_or_file(args.problem),
                 "package": args.package}]
    conn = await asyncpg.connect(DSN)
    try:
        if not await conn.fetchval("SELECT 1 FROM projects WHERE slug = $1", args.project):
            sys.exit(f"no project {args.project!r}")
        taken = {r["slug"] for r in await conn.fetch("SELECT slug FROM tasks")}
        taken |= {p.name for p in (ASSETS / args.project).glob("*") if p.is_dir()}
        # Same precedence as SettingsStore.model_for: project override, global, default.
        model = (await setting(conn, f"model:{args.project}") or await setting(conn, "model") or DEFAULT_MODEL)
        tasks: list[Task] = []
        for row in rows:
            problem = (Path(row["brief"]).read_text() if row.get("brief") else row["problem"]).strip()
            title = (row.get("title") or problem.splitlines()[0])[:80]
            slug = row.get("slug") or title
            task = Task(problem=problem, title=title, slug=slugify(f"power-{slug}" if args.power else slug, taken))
            task.scope = "ideation" if args.ideation else "power" if args.power else "package"
            taken.add(task.slug)
            task.project, task.output_slug, task.model = args.project, row.get("package") or None, model
            if args.ideation:
                task.model = CONFIG.planner_model
            tasks.append(task)
        request = args.request or (tasks[0].title if len(tasks) == 1 else f"{len(tasks)} tasks")
        if args.dry_run:
            print(json.dumps({"thread": args.thread, "request": request,
                              "tasks": [{"slug": t.slug, "title": t.title, "chars": len(t.problem)} for t in tasks]}))
            return

        async with conn.transaction():
            thread_id = await resolve_thread(conn, args.project, args.thread, args.create_thread)
            for task in tasks:
                task.thread_id = thread_id
            # The thread's record comes first, so the plan is on screen before any task starts.
            if thread_id:
                await record_lineage(conn, thread_id, request, tasks)
            for task in tasks:
                await conn.execute(
                    """INSERT INTO tasks (id, thread_id, title, slug, problem, state, created_at,
                                          attempt, output_slug, model, project, scope, held)
                       VALUES ($1, $2, $3, $4, $5, 'queued', to_timestamp($6), 1, $7, $8, $9, $10, false)""",
                    task.id, task.thread_id, task.title, task.slug, task.problem, task.created_at,
                    task.output_slug, task.model, task.project, task.scope,
                )
                await add_event(conn, Event(type="task_created", task_id=task.id, project=task.project,
                                            data={"task": task.to_json(ASSETS), "threadId": thread_id}), thread_id)
        for task in tasks:
            print(json.dumps({"id": task.id, "slug": task.slug, "thread": thread_id, "model": task.model}))
    finally:
        await conn.close()


async def adopt(args: argparse.Namespace) -> None:
    """Record request, plan and confirmation for tasks that already exist, in their thread."""
    slugs = [s.strip() for s in args.adopt.split(",") if s.strip()]
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE project = $1 AND (slug = ANY($2::text[]) OR id = ANY($2::text[]))",
            args.project, slugs)
        tasks = sorted((task_from_row(r) for r in rows), key=lambda t: t.created_at)
        missing = set(slugs) - {t.slug for t in tasks} - {t.id for t in tasks}
        if missing:
            sys.exit(f"no such task(s) in {args.project}: {', '.join(sorted(missing))}")
        threads = {t.thread_id for t in tasks}
        if len(threads) != 1 or None in threads:
            sys.exit(f"adopted tasks must share one thread; found {sorted(map(str, threads))}")
        thread_id = threads.pop()
        already = await conn.fetch(
            """SELECT 1 FROM messages WHERE thread_id = $1 AND kind = 'tasks'
               AND data->'tasks' @> ANY(SELECT jsonb_build_array(jsonb_build_object('id', x)) FROM unnest($2::text[]) x)""",
            thread_id, [t.id for t in tasks])
        if already:
            sys.exit("some of these tasks already have a confirmation message in their thread")
        request = args.request or (tasks[0].title if len(tasks) == 1 else f"{len(tasks)} tasks")
        if args.dry_run:
            print(json.dumps({"thread": thread_id, "request": request, "tasks": [t.slug for t in tasks]}))
            return
        # Dated just before the first task, so the thread reads in the order it happened.
        async with conn.transaction():
            await record_lineage(conn, thread_id, request, tasks, at=tasks[0].created_at - 1.0)
        print(json.dumps({"thread": thread_id, "adopted": [t.slug for t in tasks]}))
    finally:
        await conn.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--problem", help="the brief, or @file to read it from")
    ap.add_argument("--title")
    ap.add_argument("--slug")
    ap.add_argument("--thread")
    ap.add_argument("--create-thread", action="store_true")
    ap.add_argument("--package", help="existing package directory this task rewrites")
    ap.add_argument("--request", help="what the operator asked for; the thread's user message (text or @file)")
    ap.add_argument("--batch", help="JSON list of tasks to submit as one plan")
    ap.add_argument("--adopt", help="comma-separated slugs or ids of existing tasks to record a plan for")
    ap.add_argument("--message-to", help="slug or id of a finished task to continue with this message")
    ap.add_argument("--power", action="store_true", help="a power task: a code change anywhere, no content")
    ap.add_argument("--ideation", action="store_true", help="an ideation run with this brief, now")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.request:
        args.request = text_or_file(args.request)
    if args.message_to:
        await message_task(args)
    elif args.adopt:
        await adopt(args)
    else:
        await submit(args)


async def message_task(args: argparse.Namespace) -> None:
    """What `TaskQueue.queue_message` then `deliver_messages` do for a finished task."""
    if not args.problem:
        sys.exit("--problem is the message to send")
    body = text_or_file(args.problem).strip()
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
            await add_event(conn, Event(type=type_, task_id=task_id, data=data, project=task.project), task.thread_id)

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
