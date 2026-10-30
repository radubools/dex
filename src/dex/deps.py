"""Tasks that start, continue and wait for other tasks (design/18_task_dependencies.md).

A task's run may need work that belongs in another thread: a story that needs
an asset the game does not have, a chat asked for a change to a model. It can
look the work up (`find`), start it in that thread (`spawn`) or ask an existing
task for a change (`follow_up`). Each records an edge in `task_deps`: the task
depends on the one it started or continued. Edges make a DAG; dex refuses one
that would close a cycle, go deeper than MAX_DEPTH or fan out past MAX_DEPS.

Waiting does not hold a worker. A task whose turn ends while anything it
started, continued or waits on is not done is parked in BLOCKED with
`waiting_on` set (chats too): no slot, no timeout running. The waker below
checks blocked tasks every WAKE_EVERY_S; when everything one waits on is done
it is queued again with a note saying how each went, resuming the same agent
session, and with priority so it is claimed before other queued work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .models import Event, Task, TaskState
from .store import DepStore, ThreadStore
from .threads import ThreadMessage

if TYPE_CHECKING:
    from .queue import TaskManager

log = logging.getLogger(__name__)

#: How many tasks one task may start or continue.
MAX_DEPS = 50
#: The project thread power tasks run in, made the first time one is asked for.
POWER_THREAD = "Power"
#: How deep the graph may go: a task this many levels below a waiter may not start more.
MAX_DEPTH = 3
#: Claim order (tasks.priority, highest first): a task woken because what it
#: waited on is done goes before untouched work; one the operator prioritized
#: (`prioritize`) before that.
WOKEN = 1
PRIORITIZED = 2
#: Ended runs whose stranded notes the waker starts (an archived one stays put).
TERMINAL = {s.value for s in TaskState if s.terminal and s is not TaskState.ARCHIVED}
#: How often blocked tasks are checked.
WAKE_EVERY_S = 3.0


class Refused(Exception):
    """A dependency dex will not make (a cycle, too deep, another project...)."""


#: The run options a task hands to the tasks it starts or continues.
INHERITED = ("e2e", "todo")


def inherited(task: Task) -> dict[str, Any]:
    return {k: v for k, v in (task.options or {}).items() if k in INHERITED}


def _first_line(text: str | None, limit: int = 300) -> str:
    return (text or "").strip().splitlines()[0][:limit] if (text or "").strip() else ""


class Dependencies:
    def __init__(self, manager: "TaskManager") -> None:
        self.m = manager
        self.store = DepStore(manager.db)
        self.threads = ThreadStore(manager.db)
        self._waker: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ lookups

    async def project_threads(self, project: str) -> list[dict[str, Any]]:
        rows = await self.m.db.pool.fetch(
            """SELECT th.id, th.title, th.kind,
                      (SELECT count(*) FROM tasks t WHERE t.thread_id = th.id) AS tasks
                 FROM threads th WHERE th.project = $1 AND NOT th.hidden
                ORDER BY th.updated_at DESC""",
            project,
        )
        return [dict(r) for r in rows]

    async def resolve_thread(self, project: str, ref: str, *, create: bool = False) -> dict[str, Any]:
        """A thread of `project` by id or title (exact, then a unique prefix); made when asked."""
        ref = (ref or "").strip()
        if not ref:
            raise Refused("name the thread to start the task in (its title or id)")
        rows = await self.project_threads(project)
        for test in (lambda t: t["id"] == ref, lambda t: t["title"].lower() == ref.lower()):
            hit = [t for t in rows if test(t)]
            if hit:
                return hit[0]
        prefix = [t for t in rows if t["title"].lower().startswith(ref.lower())]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            raise Refused(f"{ref!r} matches several threads: " + ", ".join(t["title"] for t in prefix))
        if not create:
            raise Refused(f"no thread {ref!r} in this project; pass create_thread to make it. "
                          "Threads: " + ", ".join(t["title"] for t in rows))
        made = await self.threads.create(ref, project)
        return {"id": made.id, "title": made.title, "kind": made.kind, "tasks": 0}

    async def resolve_task(self, project: str, ref: str) -> Task:
        """A task of `project` by id, slug or package name: the latest attempt of its chain."""
        ref = (ref or "").strip()
        row = await self.m.db.pool.fetchrow(
            """SELECT id FROM tasks WHERE project = $1 AND state <> 'archived'
                 AND (id = $2 OR slug = $2 OR coalesce(output_slug, slug) = $2)
               ORDER BY attempt DESC, created_at DESC LIMIT 1""",
            project, ref,
        )
        if row is None:
            raise Refused(f"no task {ref!r} in this project (use find_tasks)")
        latest = await self.store.latest(row["id"])
        assert latest is not None
        return latest

    async def find(self, project: str, query: str = "", thread: str | None = None,
                   limit: int = 10) -> list[dict[str, Any]]:
        """Tasks of `project` matching `query` (title, slug, package, brief), one per package."""
        words = [w for w in (query or "").lower().split() if w]
        thread_id = (await self.resolve_thread(project, thread))["id"] if thread else None
        rows = await self.m.db.pool.fetch(
            """SELECT DISTINCT ON (coalesce(output_slug, slug)) id, coalesce(output_slug, slug) AS package,
                      title, slug, state, thread_id, problem, attempt,
                      extract(epoch from created_at)::float8 AS created_at
                 FROM tasks
                WHERE project = $1 AND state <> 'archived' AND scope IN ('package', 'project')
                  AND ($2::text IS NULL OR thread_id = $2)
                ORDER BY coalesce(output_slug, slug), attempt DESC, created_at DESC""",
            project, thread_id,
        )

        def score(r: Any) -> int:
            hay = " ".join([r["title"] or "", r["slug"] or "", r["package"] or ""]).lower()
            brief = (r["problem"] or "").lower()
            return sum(3 if w in hay else 1 if w in brief else 0 for w in words)

        hits = [r for r in rows if not words or score(r) > 0]
        hits.sort(key=lambda r: (-score(r), -(r["created_at"] or 0)))
        out = []
        for r in hits[:max(1, min(limit, 25))]:
            st = await self.store.status(r["id"])
            out.append(self.describe(st))
        return out

    def describe(self, st: dict[str, Any] | None) -> dict[str, Any]:
        """A dependency's status as a tool returns it."""
        if st is None:
            return {"error": "no such task"}
        t: Task = st["task"]
        return {
            "id": t.id, "slug": t.slug, "package": t.output_slug or t.slug, "title": t.title,
            "thread": st["threadTitle"], "state": t.state.value, "done": st["done"],
            "attempt": t.attempt, "activity": t.activity,
            "packageDir": str(t.output_dir(self.m.config.assets_dir, self.m.config.default_project)),
            "summary": _first_line(st["summary"], 600), "error": _first_line(t.error),
            "queuedNotes": st["queuedNotes"],
        }

    async def statuses(self, ids: list[str]) -> list[dict[str, Any]]:
        return [self.describe(await self.store.status(i)) for i in ids]

    # ------------------------------------------------------------ edges

    async def _check(self, waiter: Task, target_package: str | None = None) -> None:
        if await self.store.count(waiter.id) >= MAX_DEPS:
            raise Refused(f"this task has already started or continued {MAX_DEPS} tasks; that is the limit")
        if await self.store.depth(waiter.id) >= MAX_DEPTH:
            raise Refused(f"this task is {MAX_DEPTH} levels down a chain of tasks; it may not start more")
        if target_package is not None:
            above = await self.store.waiters(waiter.id)
            if target_package in await self.store.packages([waiter.id, *above]):
                raise Refused("that task (or one waiting on this one) is waiting for this task: "
                              "depending on it would make a cycle")

    async def _post(self, thread_id: str | None, text: str) -> None:
        if thread_id and self.m.post is not None:
            try:
                await self.m.post(thread_id, ThreadMessage(role="dex", kind="text", text=text))
            except Exception:
                log.exception("could not post to thread %s", thread_id)

    async def spawn(self, waiter: Task, *, thread: str, title: str, brief: str,
                    slug: str | None = None, create_thread: bool = False) -> Task:
        """Start a new task in a thread of the waiter's project; the waiter depends on it."""
        if not (brief or "").strip():
            raise Refused("the brief is empty")
        project = waiter.project or self.m.config.default_project
        await self._check(waiter)
        th = await self.resolve_thread(project, thread, create=create_thread)
        if slug:
            await self._check_thread(project, slug, th)
        # It runs as the task that asked for it does (end-to-end tests on or off).
        child = await self.m.submit(brief, title=title, slug=slug, thread_id=th["id"], project=project,
                                    options=inherited(waiter))
        await self.store.add(waiter.id, child.id, "started")
        self._edge_event(waiter, child, "started")
        await self._post(th["id"], f"Requested by the task “{waiter.title}” ({waiter.slug}), which waits for it.")
        if self.m.announce is not None:
            try:
                await self.m.announce(child, "Started")
            except Exception:
                log.exception("could not announce %s", child.slug)
        return child

    async def spawn_power(self, waiter: Task, *, title: str, brief: str) -> Task:
        """Start a power task in the project's Power thread; the waiter depends on it."""
        if not (brief or "").strip():
            raise Refused("the brief is empty")
        if waiter.is_power and not waiter.todo:
            raise Refused("a power task does its own code changes: it starts no tasks")
        project = waiter.project or self.m.config.default_project
        await self._check(waiter)
        th = await self.resolve_thread(project, POWER_THREAD, create=True)
        child = await self.m.submit(brief, title=title, slug=f"power-{title}"[:60], thread_id=th["id"],
                                    project=project, scope="power", options=inherited(waiter))
        await self.store.add(waiter.id, child.id, "started")
        self._edge_event(waiter, child, "started")
        await self._post(th["id"], f"Requested by the task “{waiter.title}” ({waiter.slug}), which waits for it.")
        if self.m.announce is not None:
            try:
                await self.m.announce(child, "Started")
            except Exception:
                log.exception("could not announce %s", child.slug)
        return child

    async def home_thread(self, project: str, slug: str) -> dict[str, Any] | None:
        """The thread a package of this type is made in: where most of its prefix's tasks are."""
        prefix = slug.split("-", 1)[0]
        if not prefix or prefix == slug:
            return None
        row = await self.m.db.pool.fetchrow(
            """SELECT t.thread_id AS id, th.title, count(*) AS n
                 FROM tasks t JOIN threads th ON th.id = t.thread_id
                WHERE t.project = $1 AND t.state <> 'archived' AND th.kind <> 'project_design'
                  AND t.scope IN ('package', 'project')
                  AND coalesce(t.output_slug, t.slug) LIKE $2
                GROUP BY t.thread_id, th.title ORDER BY n DESC LIMIT 1""",
            project, f"{prefix}-%",
        )
        return dict(row) if row and row["n"] >= 2 else None

    async def _check_thread(self, project: str, slug: str, chosen: dict[str, Any]) -> None:
        home = await self.home_thread(project, slug)
        if home and home["id"] != chosen["id"]:
            raise Refused(f"`{slug.split('-', 1)[0]}-` packages are made in the thread “{home['title']}” "
                          f"({home['n']} tasks there), not “{chosen['title']}”: start it there")

    async def follow_up(self, waiter: Task, ref: str, message: str) -> dict[str, Any]:
        """Ask an existing task for a change: a note to its latest attempt; the waiter depends on it."""
        if not (message or "").strip():
            raise Refused("say what to change")
        project = waiter.project or self.m.config.default_project
        target = await self.resolve_task(project, ref)
        if target.id == waiter.id or (target.output_slug or target.slug) == (waiter.output_slug or waiter.slug):
            raise Refused("a task cannot wait on itself")
        await self._check(waiter, target.output_slug or target.slug)
        body = f"{message.strip()}\n\n(Asked by the task “{waiter.title}” ({waiter.slug}), which waits for it.)"
        if not waiter.e2e:
            body += "\n\nEnd-to-end tests are off for this change (as the task that asked runs)."
        await self.m.queue_message(target.id, body, options=inherited(waiter))
        await self.store.add(waiter.id, target.id, "continued")
        self._edge_event(waiter, target, "continued")
        latest = await self.store.latest(target.id)
        return {"task": latest or target, "running": target.state in (TaskState.RUNNING, TaskState.AWAITING_INPUT,
                                                                        TaskState.QUEUED, TaskState.BLOCKED)}

    def _edge_event(self, waiter: Task, child: Task, kind: str) -> None:
        self.m.bus.publish(Event(type="task_dependency", task_id=waiter.id,
                                 data={"dependsOn": child.id, "kind": kind}, project=waiter.project))

    # ------------------------------------------------------------ blocking and waking

    async def park(self, task: Task, ids: list[str]) -> None:
        """Record a finished run as blocked on `ids`. No slot, no timeout, until woken."""
        task.waiting_on = list(ids)
        await self.m.tasks.set_state(task.id, TaskState.BLOCKED, waiting_on=json.dumps(task.waiting_on),
                                     finished_at=None, resumed_from=task.session_id,
                                     activity=f"waiting on {len(ids)} task{'s' if len(ids) != 1 else ''}")

    def wake_note(self, statuses: list[dict[str, Any]]) -> str:
        lines = []
        for s in statuses:
            line = f"- {s.get('title')} ({s.get('package')}, thread “{s.get('thread')}”): **{s.get('state')}**"
            if s.get("summary"):
                line += f" — {s['summary']}"
            if s.get("error"):
                line += f" — error: {s['error']}"
            line += f"\n  Package: {s.get('packageDir')}"
            lines.append(line)
        return (
            "## The tasks you waited for are done\n\n"
            "dex resumed this session because every task you waited on has finished:\n\n"
            + "\n".join(lines)
            + "\n\nLook at what they produced (a failed one may need a follow-up with "
            "`continue_task`, or a way round it), then carry on with your own task and finish it. "
            "In a conversation (a chat), tell the person how it went and what changed."
        )

    async def prioritize(self, task_id: str) -> list[Task]:
        """Put a task, and every task under it in the graph, at the front of the queue.

        The tasks under it are the ones it started, continued or waits on, and
        theirs, all the way down (any attempt of each package's chain may hold
        the edge; the package's latest attempt is the one that runs). Those
        still waiting to run (queued, paused or blocked) get PRIORITIZED, so the
        next free slot takes them before anything else, the ones it waits on
        first by being older; nothing running is stopped. A blocked one keeps
        it when it wakes. Returns the tasks raised.
        """
        seen: set[str] = set()
        targets: list[str] = []
        todo = [task_id]
        while todo:
            ref = todo.pop()
            latest = await self.store.latest(ref)
            if latest is None or latest.id in seen:
                continue
            chain = await self.store.chain(latest.id)
            seen.update(chain)
            targets.append(latest.id)
            for attempt in chain:
                todo.extend(await self.store.of(attempt))
        raised = await self.m.tasks.prioritize(targets, PRIORITIZED)
        for t in raised:
            self.m.bus.publish(Event(type="task_state", task_id=t.id, project=t.project,
                                     data={"state": t.state.value, "activity": t.activity, "priority": t.priority}))
        if raised:
            self.m._wake_workers()
            log.info("prioritized %s and %d task(s) under it", task_id, len(raised) - 1)
        return raised

    async def wake_ready(self) -> int:
        """Queue every blocked task whose dependencies are all done. Returns how many."""
        woken = 0
        for task in await self.store.blocked():
            ids = task.waiting_on or await self.store.of(task.id)
            statuses = await self.statuses(ids)
            # A finished task still holding notes is waited on until they run,
            # but nothing else delivers them once its worker has moved on (a
            # note that missed its run's end, a restart): start them here.
            stranded = [s for s in statuses if s.get("queuedNotes") and s.get("state") in TERMINAL]
            for s in stranded:
                await self.m.deliver_messages(s["id"])
            if stranded or any(not s.get("done", True) for s in statuses):
                continue
            note = self.wake_note(statuses)
            landed = await self.m.tasks.set_state(
                task.id, TaskState.QUEUED, waiting_on=None, wake_note=note,
                priority=max(WOKEN, task.priority),  # a prioritized task stays ahead of woken ones
                resumed_from=task.resumed_from or task.session_id, finished_at=None,
                activity="its tasks are done; resuming first",
            )
            if not landed:
                continue
            self.m.bus.publish(Event(type="task_state", task_id=task.id, project=task.project,
                                     data={"state": TaskState.QUEUED.value,
                                           "activity": "its tasks are done; resuming first"}))
            woken += 1
        if woken:
            self.m._wake_workers()
            log.info("woke %d blocked task(s)", woken)
        return woken

    async def _wake_loop(self) -> None:
        while True:
            await asyncio.sleep(WAKE_EVERY_S)
            try:
                await self.wake_ready()
            except Exception:
                log.exception("waking blocked tasks failed")

    def start(self) -> None:
        if self._waker is None:
            self._waker = asyncio.create_task(self._wake_loop(), name="dex-dependency-waker")

    async def stop(self) -> None:
        if self._waker is not None:
            self._waker.cancel()
            try:
                await self._waker
            except asyncio.CancelledError:
                pass
            self._waker = None

    async def wait(self, ids: list[str], seconds: float, poll_s: float = 5.0) -> list[dict[str, Any]]:
        """Poll up to `seconds` (in the caller's turn) for all of `ids` to be done."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            statuses = await self.statuses(ids)
            if all(s.get("done", True) for s in statuses) or time.monotonic() >= deadline:
                return statuses
            await asyncio.sleep(min(poll_s, max(0.1, deadline - time.monotonic())))
