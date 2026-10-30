"""Todo lists: work that waits for dex to be idle (design/24).

A game chat with its Todo box ticked starts a `todos` task, a light run that
splits the request into power tasks and adds them here in state `todo`. They
are a queue behind everything else:

- with nothing else running, the next todo moves to `queued`, one at a time;
- as soon as regular work appears, by any route, every todo in flight goes back
  to `todo` with whatever it spawned (`options["todo"]`, handed down like any
  run option, is how a task is known to be one), keeping its session to resume.

This is a loop rather than a hook in `submit` because tasks also arrive by
`scripts/enqueue_task.py`, straight into the database. `submit` only wakes it.
The loop also keeps each list's `todos.json` current, which is what the
`power-todos` widget reads: the file watcher tells open viewers it changed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .deps import POWER_THREAD, Refused, inherited
from .models import Event, Task, TaskState
from .store import CostStore

if TYPE_CHECKING:
    from .queue import TaskManager

log = logging.getLogger("dex.todos")

TICK_S = 3.0
STATUS_FILE = "todos.json"
#: At most this many todos from one request: past it the request wants a plan.
MAX_TODOS = 20
WAITING = "waiting for dex to be idle"

#: In flight: these give way to regular work, and hold back the next todo.
ACTIVE = (TaskState.QUEUED, TaskState.RUNNING, TaskState.AWAITING_INPUT,
          TaskState.BLOCKED, TaskState.PAUSED)
#: Blocked work only waits on tasks, so it does not stop the next todo starting.
HOLDS_NEXT = (TaskState.QUEUED, TaskState.RUNNING, TaskState.AWAITING_INPUT, TaskState.PAUSED)


class Todos:
    def __init__(self, manager: "TaskManager") -> None:
        self.m = manager
        self._loop: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._written: dict[str, str] = {}

    def wake(self) -> None:
        self._wake.set()

    async def add(self, root: Task, items: list[dict[str, Any]]) -> list[Task]:
        """Add a request's todos, in the order given, to the project's Power thread."""
        items = [i for i in items if str(i.get("brief") or "").strip()]
        if not items:
            raise Refused("there are no todos with a brief")
        if len(items) > MAX_TODOS:
            raise Refused(f"that is {len(items)} todos; {MAX_TODOS} is the most one request may split into")
        project = root.project or self.m.config.default_project
        thread = await self.m.graph.resolve_thread(project, POWER_THREAD, create=True)
        made = []
        for item in items:
            title = str(item.get("title") or "").strip() or str(item["brief"]).strip().splitlines()[0]
            made.append(await self.m.submit(
                str(item["brief"]), title=title[:80], slug=f"todo-{title}"[:60], thread_id=thread["id"],
                project=project, scope="power", state=TaskState.TODO,
                options={**inherited(root), "todo": root.id},
            ))
        self.wake()
        return made

    async def tick(self) -> None:
        members = await self.m.tasks.todo_family()
        work = [t for t in members if not t.is_todos]
        if await self.m.tasks.regular_work():
            for task in work:
                if task.state in ACTIVE and not task.held:
                    await self._send_back(task)
        elif not self.m.starting_up and not await self.m._is_paused():
            if not any(t.state in HOLDS_NEXT for t in work):
                await self._start_next(work)
        await self._write_status(await self.m.tasks.todo_family())

    async def _send_back(self, task: Task) -> None:
        # The run records a pause on its way out, after this; the next tick
        # finds that and finishes the job, which is why paused counts as active.
        if task.state is not TaskState.PAUSED:
            await self.m.pause(task.id)
        await self._move(task, TaskState.TODO, WAITING, finished_at=None,
                         resumed_from=task.resumed_from or task.session_id)
        log.info("todo %s went back: regular work arrived", task.slug)

    async def _start_next(self, work: list[Task]) -> None:
        """Start the oldest todo. One that was waiting on tasks waits again."""
        for task in sorted((t for t in work if t.state is TaskState.TODO and not t.held), key=lambda t: t.created_at):
            if task.waiting_on:
                await self._move(task, TaskState.BLOCKED, "waiting on the tasks it started")
                continue
            await self._move(task, TaskState.QUEUED, "dex is idle: starting", finished_at=None)
            self.m._wake_workers()
            return

    async def _move(self, task: Task, state: TaskState, activity: str, **fields: Any) -> None:
        landed = await self.m.tasks.set_state(task.id, state, activity=activity, **fields)
        if landed:
            self.m.bus.publish(Event(type="task_state", task_id=task.id, project=task.project,
                                     data={"state": state.value, "activity": activity}))

    async def _write_status(self, members: list[Task]) -> None:
        """Rewrite each list's file when something about it changed."""
        roots = [t for t in members if t.is_todos]
        if not roots:
            return
        costs = await CostStore(self.m.db).by_tasks([t.id for t in members])
        spawned_by = await self.m.tasks.spawned_by([t.id for t in members])
        for root in roots:
            own = [t for t in members if not t.is_todos and (t.options or {}).get("todo") == root.id]
            body = {
                "request": root.title,
                "root": root.slug,
                "todos": [_row(t, costs, spawned_by.get(t.id)) for t in own],
            }
            text = json.dumps(body, indent=2)
            if self._written.get(root.id) == text:
                continue
            path = root.output_dir(self.m.config.assets_dir, self.m.config.default_project) / STATUS_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            scratch = path.with_suffix(".json.tmp")
            scratch.write_text(json.dumps({**body, "updatedAt": time.time()}, indent=2), encoding="utf-8")
            scratch.replace(path)
            self._written[root.id] = text

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK_S)
            self._wake.clear()
            try:
                await self.tick()
            except Exception:
                log.exception("the todo list could not be worked through")

    def start(self) -> None:
        if self._loop is None:
            self._loop = asyncio.create_task(self._run(), name="dex-todos")

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop
            self._loop = None


def _row(task: Task, costs: dict[str, float], parent: str | None) -> dict[str, Any]:
    return {
        "id": task.id,
        "slug": task.slug,
        "title": task.title,
        "state": task.state.value,
        "activity": task.activity,
        "scope": task.scope,
        "costUsd": round(costs.get(task.id, 0.0), 4),
        "spawnedBy": parent,
    }
