"""Todos and ideation: work that waits for dex to be idle (design/24).

A game chat with its Todo box ticked starts a `todos` task, which scouts the
request and splits it into power tasks here, each in state `todo` in its own
directory with a SPEC.md, so the Library lists them. They are a queue behind
everything else:

- with nothing else running, the next todo moves to `queued`, one at a time;
- as soon as regular work appears, by any route, every todo in flight goes back
  to `todo` with whatever it spawned (`options["todo"]`, handed down like any
  run option, is how a task is known to be one), keeping its session to resume;
- Prioritize takes the `todo` mark off for good: it is regular work from then on.

Behind todos is one `ideation` task per project, which runs only when there is
nothing else at all, looks for improvements and proposes them as todos of its
own. Those wait for the operator's approval (`options["approval"]`). When it
ends it adds nothing: each project's settings switch the cycle on and off, and
a timer arms a new task for any project that is on and has none. It always runs
in the project's Power thread. Its files are in `ideas/<project>/`, beside
`assets/`, where only power tasks reach them.

This is a loop rather than a hook in `submit` because tasks also arrive by
`scripts/enqueue_task.py`, straight into the database. `submit` only wakes it.
The loop also keeps each todo's `todo:*` tag in its manifest, for the Library.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import skills
from .changes import size_tags
from .deps import POWER_THREAD, PRIORITIZED, Refused, inherited
from .models import Event, Task, TaskState
from .threads import ThreadMessage

if TYPE_CHECKING:
    from .queue import TaskManager

log = logging.getLogger("dex.todos")

TICK_S = 3.0
#: How often projects with ideation on are checked for an armed task.
ARM_EVERY_S = 600.0
#: At most this many todos from one request: past it the request wants a plan.
MAX_TODOS = 20
#: How far a proposed todo reaches, measured by sampling; it is a `severity:` tag.
SEVERITIES = {"incremental": "🟢 incremental", "low": "🔵 low", "medium": "🟠 medium", "high": "🔴 high"}
#: A feature improvement, with nothing wrong to count, so it has no sample.
INCREMENTAL = "incremental"
WAITING = "waiting for dex to be idle"
SPEC_FILE = "SPEC.md"
IDEAS_FILE = "IDEAS.md"
LOG_HEADER = "# Ideas\n\n"
#: A log line is one line of this at most, so the file stays quick to read.
MAX_LOG_LINE = 200

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
TAGS = {PENDING: "todo:pending-approval", APPROVED: "todo:approved", REJECTED: "todo:rejected"}
DONE_TAG = "todo:done"

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
        #: One ideation task per project: checking and adding must not interleave.
        self._ideation = asyncio.Lock()
        self._armed_at = 0.0

    def wake(self) -> None:
        self._wake.set()

    async def add(self, root: Task, items: list[dict[str, Any]]) -> list[Task]:
        """Add todos, in the order given, to the project's Power thread.

        Each gets its own directory with its SPEC.md. A chat's are approved by
        asking for them; what an ideation task proposes waits for the operator,
        and nothing depends on it: the ideation task carries on.
        """
        items = [i for i in items if str(i.get("spec") or "").strip()]
        if not items:
            raise Refused("there are no todos with a spec")
        if root.is_ideation and not all(_estimated(i) for i in items):
            raise Refused("every proposed todo needs `code_lines` and `asset_lines`: "
                          "your upper bound on each, 0 if there are none")
        if root.is_ideation and not all(_rated(i) for i in items):
            raise Refused("every proposed todo needs `severity` (incremental, low, medium or high), and "
                          "unless it is incremental, `sampled`: what you sampled, and how many of those it hit")
        replaced = [await self._replaceable(root, ref) for i in items for ref in i.get("replaces") or []]
        if len(items) > MAX_TODOS:
            raise Refused(f"that is {len(items)} todos; {MAX_TODOS} is the most one request may split into")
        project = root.project or self.m.config.default_project
        thread = await self.m.graph.resolve_thread(project, POWER_THREAD, create=True, exact=True)
        proposed = {"approval": PENDING, "proposed": True} if root.is_ideation else {"approval": APPROVED}
        made = []
        for item in items:
            title = str(item.get("title") or "").strip() or str(item["spec"]).strip().splitlines()[0]
            task = await self.m.submit(
                f"{title}\n\nCarry out `{SPEC_FILE}` in your directory: it says what to change and why.",
                title=title[:80], slug=f"todo-{title}"[:60], thread_id=thread["id"],
                project=project, scope="power", state=TaskState.TODO,
                options={**inherited(root), "todo": root.id, **proposed},
            )
            self._dir(task).mkdir(parents=True, exist_ok=True)
            spec = str(item["spec"]).strip()
            if root.is_ideation:
                code, assets = int(item["code_lines"]), int(item["asset_lines"])
                tags = size_tags(code, assets)
                severity, sampled = SEVERITIES[item["severity"]], " ".join(str(item.get("sampled") or "").split())
                spec = (f"**Estimated change:** up to {code:,} lines of code ({tags[0].split(':')[1]}), "
                        f"up to {assets:,} lines of assets ({tags[1].split(':')[1]})\n\n"
                        f"**Severity:** {severity}.{f' Sampled: {sampled}' if sampled else ''}\n\n{spec}")
                tags.append(f"severity:{severity}")
                (self._dir(task) / "manifest.json").write_text(
                    json.dumps({"title": task.title, "tags": tags}, indent=2), encoding="utf-8")
            (self._dir(task) / SPEC_FILE).write_text(spec + "\n", encoding="utf-8")
            made.append(task)
            if root.is_ideation:
                self.log(project, f"proposed: {task.title} ({task.slug}), {SEVERITIES[item['severity']]} severity")
        for old in replaced:
            await self.decide(old.id, REJECTED, by="its reassessment")
        count = f"{len(made)} todo{'' if len(made) == 1 else 's'}"
        await self._announce(thread["id"], made, (
            f"Proposed {count}, waiting for your approval." if root.is_ideation else f"Added {count}."))
        self.wake()
        return made

    def log(self, project: str, text: str) -> str:
        """Add a line to the top of the project's log, newest first, timestamped by dex."""
        line = " ".join(text.split())
        if len(line) > MAX_LOG_LINE:
            line = line[:MAX_LOG_LINE - 1] + "…"
        path = self.m.config.ideas_dir / project / IDEAS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        rest = old.removeprefix(LOG_HEADER).lstrip("\n")
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
        scratch = path.with_suffix(".md.tmp")
        scratch.write_text(f"{LOG_HEADER}- {stamp} {line}\n{rest}", encoding="utf-8")
        scratch.replace(path)
        return line

    async def _announce(self, thread_id: str, tasks: list[Task], text: str) -> None:
        """Put tasks in their thread: the chat draws messages, so one without
        is real, and running, and nowhere to be seen. Never at the work's cost."""
        if self.m.post is None:
            return
        data = {"tasks": [t.to_json(self.m.config.assets_dir) for t in tasks]}
        try:
            await self.m.post(thread_id, ThreadMessage(role="dex", kind="tasks", text=text, data=data))
        except Exception:
            log.exception("could not post to thread %s", thread_id)

    async def _replaceable(self, root: Task, ref: Any) -> Task:
        """An older proposal this run reassessed: only one still waiting for approval."""
        task = await self.m.tasks.get(str(ref))
        if (not root.is_ideation or task is None or task.project != root.project or not task.proposed
                or task.state is not TaskState.TODO or task.options.get("approval") != PENDING):
            raise Refused(f"{ref} can't be replaced: `replaces` takes ids of this project's "
                          "todos still pending approval")
        return task

    async def decide(self, task_id: str, approval: str, by: str = "the operator") -> Task:
        """A call on a proposed todo. The operator may change their mind."""
        task = await self.m.tasks.get(task_id)
        if task is None or not task.proposed or task.state is not TaskState.TODO:
            raise Refused("only a proposed todo that has not started can be approved or rejected")
        if approval == APPROVED and not (self._dir(task) / SPEC_FILE).is_file():
            raise Refused(f"this todo has no {SPEC_FILE}: there is nothing to approve")
        await self.m.tasks.set_option(task.id, "approval", approval)
        fresh = await self.m.tasks.get(task.id)
        self.log(task.project or self.m.config.default_project, f"{approval} by {by}: {task.title} ({task.slug})")
        await self._sync_tags()
        self.m.bus.publish(Event(type="task_state", task_id=task.id, project=task.project,
                                 data={"state": task.state.value, "activity": task.activity, "approval": approval}))
        self.wake()
        return fresh

    async def make_regular(self, ids: list[str]) -> None:
        """Prioritize: these stop being todos for good, and a waiting one is queued."""
        for task_id in ids:
            task = await self.m.tasks.get(task_id)
            if task is None or not task.todo or task.state.terminal:
                continue
            if task.approval in (PENDING, REJECTED):
                raise Refused("a proposed todo has to be approved first")
            await self.m.tasks.drop_option(task.id, "todo")
            if task.state is TaskState.TODO and not task.held:
                if task.waiting_on:
                    await self._move(task, TaskState.BLOCKED, "waiting on the tasks it started")
                else:
                    await self._move(task, TaskState.QUEUED, "prioritized", finished_at=None)
            log.info("todo %s is regular work now", task.slug)

    async def discovery(self, project: str, *, force: bool = False) -> Task:
        """Ask for a project's ideation task: there is only ever one.

        Asking turns the project's cycle on, and is refused while a task exists,
        paused or waiting for its turn. `force` stops that one instead and runs
        one step now, as regular work, leaving the cycle as it is.
        """
        if not self._has_power(project):
            raise Refused(f"the Power skill is not enabled in {project}")
        async with self._ideation:
            current = await self.m.tasks.current_ideation(project)
            if current is not None and not force:
                raise Refused(f"this project already has an ideation task, {current.slug} "
                              f"({current.state.value}): it runs when dex is idle, and the project's settings "
                              "switch it on and off. Force it to run the next step now.")
            if current is not None:
                await self.m.cancel(current.id)
            task = await self._ideate(project, force=force)
            if not force:
                await self._set_switch(project, True)
        if force:
            await self.m.tasks.prioritize([task.id], PRIORITIZED)
        return task

    async def status(self, project: str) -> dict[str, Any]:
        """What a project's settings show: the switch, and only with the Power skill."""
        current = await self.m.tasks.current_ideation(project)
        return {
            "available": self._has_power(project),
            "enabled": await self._switched_on(project),
            "taskId": current.id if current else None,
            "state": current.state.value if current else None,
        }

    async def switch(self, project: str, on: bool) -> dict[str, Any]:
        """The cycle on or off. The armed task resumes or pauses with it."""
        if not self._has_power(project):
            raise Refused(f"the Power skill is not enabled in {project}")
        await self._set_switch(project, on)
        async with self._ideation:
            current = await self.m.tasks.current_ideation(project)
            if on and current is None:
                await self._ideate(project)
            elif on and current.held:
                await self._move(current, TaskState.TODO, WAITING, held=False, finished_at=None,
                                 resumed_from=current.resumed_from or current.session_id)
            elif not on and current is not None and not current.held:
                await self.m.pause_task(current.id)
        self.wake()
        return await self.status(project)

    async def skill_changed(self, project: str, on: bool) -> None:
        """Power added: its thread exists, and ideation stays off until it is asked for.
        Power removed: the cycle stops where it is, and is off if it is added again."""
        if on:
            await self.m.graph.resolve_thread(project, POWER_THREAD, create=True, exact=True)
            return
        await self._set_switch(project, False)
        current = await self.m.tasks.current_ideation(project)
        if current is not None and not current.held:
            await self.m.pause_task(current.id)

    async def _switched_on(self, project: str) -> bool:
        return bool(await self.m.settings.get(f"ideation:{project}", False))

    async def _set_switch(self, project: str, on: bool) -> None:
        await self.m.settings.set(f"ideation:{project}", on)

    def _has_power(self, project: str) -> bool:
        return "power" in skills.read_enabled(self.m.config.project_dir(project)).skills

    async def _power_threads(self) -> None:
        """Projects that had the Power skill before it came with a thread."""
        for directory in self.m.config.assets_dir.iterdir():
            if directory.is_dir() and "power" in skills.read_enabled(directory).skills:
                await self.m.graph.resolve_thread(directory.name, POWER_THREAD, create=True, exact=True)

    async def _ideate(self, project: str, *, force: bool = False) -> Task:
        thread = await self.m.graph.resolve_thread(project, POWER_THREAD, create=True, exact=True)
        directory = self.m.config.ideas_dir / project
        directory.mkdir(parents=True, exist_ok=True)
        ideas = directory / IDEAS_FILE
        if not ideas.exists():
            ideas.write_text(LOG_HEADER, encoding="utf-8")
        task = await self.m.submit(
            f"Look for improvements in {project}.", title=f"Ideation: {project}", slug=f"ideation-{project}",
            thread_id=thread["id"], project=project, scope="ideation",
            state=TaskState.QUEUED if force else TaskState.TODO,
            options={} if force else {"todo": True},
        )
        await self._announce(thread["id"], [task], "Ideation run, for when dex is idle.")
        return task

    async def tick(self) -> None:
        members = await self.m.tasks.todo_family()
        ideation = [t for t in members if t.is_ideation]
        work = [t for t in members if not t.is_todos and not t.is_ideation]
        ready = [t for t in work if t.state is TaskState.TODO and not t.held and t.approval not in (PENDING, REJECTED)]
        busy = any(t.state in ACTIVE and not t.held for t in work)
        if await self.m.tasks.regular_work():
            for task in work + ideation:
                if task.state in ACTIVE and not task.held:
                    await self._send_back(task)
        elif not self.m.starting_up and not await self.m._is_paused():
            if ready or busy:
                # Ideation runs only when there is nothing else to do.
                for task in ideation:
                    if task.state in ACTIVE and not task.held:
                        await self._send_back(task)
            if not any(t.state in HOLDS_NEXT and not t.held for t in work):
                await self._start_next(ready)
            if not ready and not busy and not any(t.state in ACTIVE and not t.held for t in ideation):
                await self._start_next([t for t in ideation if t.state is TaskState.TODO and not t.held])
        if not self.m.starting_up and time.monotonic() - self._armed_at >= ARM_EVERY_S:
            self._armed_at = time.monotonic()
            await self._arm_ideation()
        await self._sync_tags()

    async def _send_back(self, task: Task) -> None:
        # The run records a pause on its way out, after this; the next tick
        # finds that and finishes the job, which is why paused counts as active.
        if task.state is not TaskState.PAUSED:
            await self.m.pause(task.id)
        await self._move(task, TaskState.TODO, WAITING, finished_at=None,
                         resumed_from=task.resumed_from or task.session_id)
        log.info("todo %s went back: other work comes first", task.slug)

    async def _start_next(self, waiting: list[Task]) -> None:
        """Start the oldest. One that was waiting on tasks waits again."""
        for task in sorted(waiting, key=lambda t: t.created_at):
            if task.waiting_on:
                await self._move(task, TaskState.BLOCKED, "waiting on the tasks it started")
                continue
            await self._move(task, TaskState.QUEUED, "dex is idle: starting", finished_at=None)
            self.m._wake_workers()
            return

    async def _arm_ideation(self) -> None:
        """Every project with the cycle on has a task armed; if not, add one.

        This is the only thing that adds the next task, so a run can't forget,
        and a project gets at most one new run per ARM_EVERY_S, even a failing one.
        """
        async with self._ideation:
            for directory in self.m.config.assets_dir.iterdir():
                project = directory.name
                if (directory.is_dir() and self._has_power(project) and await self._switched_on(project)
                        and await self.m.tasks.current_ideation(project) is None):
                    await self._ideate(project)

    async def _sync_tags(self) -> None:
        """Keep each todo's `todo:*` tag in its manifest; the watcher tells the Library."""
        for task in await self.m.tasks.open_todos():
            tag = DONE_TAG if task.state is TaskState.SUCCEEDED else TAGS.get(task.approval or "")
            if tag and tag != task.options.get("tag"):
                _write_tag(self._dir(task), task.title, tag)
                await self.m.tasks.set_option(task.id, "tag", tag)

    def _dir(self, task: Task) -> Path:
        return task.output_dir(self.m.config.assets_dir, self.m.config.default_project)

    async def _move(self, task: Task, state: TaskState, activity: str, **fields: Any) -> None:
        landed = await self.m.tasks.set_state(task.id, state, activity=activity, **fields)
        if landed:
            self.m.bus.publish(Event(type="task_state", task_id=task.id, project=task.project,
                                     data={"state": state.value, "activity": activity}))

    async def _run(self) -> None:
        try:
            await self._power_threads()
        except Exception:
            log.exception("could not make the Power threads")
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


def _estimated(item: dict[str, Any]) -> bool:
    for key in ("code_lines", "asset_lines"):
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    return True


def _rated(item: dict[str, Any]) -> bool:
    severity, sampled = item.get("severity"), item.get("sampled")
    return severity in SEVERITIES and (severity == INCREMENTAL or (isinstance(sampled, str) and bool(sampled.strip())))


def _write_tag(directory: Path, title: str, tag: str) -> None:
    """Replace the `todo:*` tag, keeping the manifest's other fields and tags."""
    path = directory / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("title", title)
    kept = [t for t in manifest.get("tags") or [] if not str(t).startswith("todo:")]
    manifest["tags"] = [*kept, tag]
    directory.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".json.tmp")
    scratch.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    scratch.replace(path)
