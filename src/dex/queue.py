"""The work queue: Postgres holds the jobs, workers claim them.

Queue state lives in the database rather than in memory, so a restart picks up
what was queued, and a run the server never finished is left in a state that
can be resumed instead of vanishing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable

from .bus import EventBus
from .config import DEFAULT_CONCURRENCY, MAX_WORKERS, Config
from .db import Database
from .models import Event, Task, TaskState, slugify
from .runner import TaskRunner
from .limits import DynamicLimiter
from .store import SettingsStore, TaskMessageStore, TaskStore

log = logging.getLogger("dex.queue")

#: How often a running task refreshes its claim, and how long after the last
#: refresh another process may declare it orphaned.
HEARTBEAT_S = 20
STALE_AFTER_S = 90

#: How long after a restart before any queued or paused work starts again.
#: A restart is usually someone changing something, and the moment after one is
#: when a mistake is cheapest to catch: it leaves room to pause, archive, or
#: stop the server again before a hundred agents pick up where they left off.
STARTUP_GRACE_S = 30.0

#: How often an idle worker re-reads the limit.
LIMIT_POLL_S = 2.0
#: How long a read of the limit is reused. Every worker consults it on every
#: pass while holding the claim gate, so without this the gate serialises a
#: database round-trip per worker per poll.
LIMIT_CACHE_S = 1.0


class TaskManager:
    #: How long `start` holds work back for. A class attribute rather than one
    #: set in `__init__`, so overriding it — as the test suite does, since one
    #: that waited half a minute per manager is one nobody runs — is not
    #: silently shadowed by every instance.
    startup_grace_s: float = STARTUP_GRACE_S

    def __init__(self, config: Config, bus: EventBus, db: Database) -> None:
        self.config = config
        self.bus = bus
        self.db = db
        self.tasks = TaskStore(db)
        self.settings = SettingsStore(db)
        self.messages = TaskMessageStore(db)
        #: Only the tasks this process is running: their cancel handles and the
        #: futures the agent is parked on. Everything else comes from Postgres.
        self.live: dict[str, Task] = {}
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._sweeper: asyncio.Task[None] | None = None
        #: Signalled when there may be work or room for it. A condition rather
        #: than an Event: an Event that each waiter clears loses the wakeup for
        #: every other worker, which stalled them until their next timeout.
        #: A shared Condition replaced that, but `wait_for` cancelling
        #: `Condition.wait()` has its own hazards: the cancelled waiter has to
        #: re-acquire the lock on its way out, and a notification arriving in
        #: that window is consumed rather than delivered. One private Event per
        #: sleeping worker has neither problem — nothing is shared, so there is
        #: no lock to contend for and no wakeup to swallow.
        self._wake_events: set[asyncio.Event] = set()
        #: Serialises the capacity check with the claim it authorises.
        self._gate = asyncio.Lock()
        #: Set by `stop()` before it tears anything down, so the supervisor
        #: cannot spawn a replacement worker into a pool that is going away.
        self._stopping = False
        #: Monotonic time before which no work is handed out. Set by `start`.
        self._grace_until: float = 0.0
        self._paused_cache: tuple[float, bool] | None = None
        self._limit_cache: tuple[float, int] | None = None
        #: Set by the app so follow-ups started from the worker loop are
        #: announced in their thread, the same as a resume or a re-run.
        self.announce: Callable[[Task, str], Awaitable[None]] | None = None
        #: Gate for planner/chat calls; its ceiling lives in the database.
        self.chat_limiter = DynamicLimiter(
            lambda: self.settings.concurrency(SettingsStore.CHAT_CONCURRENCY),
            # Rebalance the moment chat pressure changes, rather than at the
            # supervisor's next tick.
            on_change=self.rebalance,
        )

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        self._stopping = False
        # Read here rather than captured at construction, so a test — or a
        # future setting — can change it without rebuilding the manager.
        self._grace_until = time.monotonic() + self.startup_grace_s
        self.config.ensure_dirs()
        orphans = await self.tasks.release_orphans(STALE_AFTER_S, socket.gethostname())
        for task_id in orphans:
            log.info("task %s was left running by a stopped server; it will go again", task_id)
            self.bus.publish(
                Event(
                    type="task_state",
                    task_id=task_id,
                    data={"state": TaskState.PAUSED.value, "activity": None,
                          "error": "The server stopped while this task was running."},
                )
            )
        # Exactly as many workers as the limit allows, grown and retired by the
        # supervisor. Spawning the ceiling and having the surplus idle would
        # have every spare worker polling the connection pool for nothing.
        await self._scale()
        self._supervisor = asyncio.create_task(self._supervise(), name="dex-supervisor")
        # Sweep periodically, so a task stranded by another process is
        # recovered without waiting for someone to restart this one.
        self._sweeper = asyncio.create_task(self._sweep(), name="dex-orphan-sweep")
        log.info(
            "started %d workers as %s (limit %d, adjustable from the UI); "
            "holding queued work for %gs",
            len(self._workers), self.worker_id,
            await self.settings.concurrency(SettingsStore.TASK_CONCURRENCY),
            STARTUP_GRACE_S,
        )

    async def stop(self) -> None:
        # Order matters. The supervisor calls `_scale()`, which respawns any
        # worker that has finished — including one that has just finished
        # because `stop()` cancelled it. Cancelling workers first would race
        # that: the replacement never appears in the list being drained, and
        # clearing the dict then drops the last reference to a worker still
        # polling the database. So the supervisor goes first, and `_stopping`
        # closes the window where it is already inside `_scale()`.
        self._stopping = True
        for extra in (self._supervisor, self._sweeper):
            if extra is not None:
                extra.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await extra
        self._supervisor = self._sweeper = None

        for task in self.live.values():
            # Shutting down is not the task's fault. Marked before cancelling so
            # whichever path the runner takes on the way out — cancellation, or
            # the SDK reporting an error because its subprocess went away —
            # records a pause rather than a failure.
            task.preempted = True
            if task.runtime is not None:
                task.runtime.cancel()
        # Drained by re-reading rather than from a snapshot, so nothing spawned
        # between the cancel and the await is left behind.
        while self._workers:
            workers = [*self._workers.values()]
            for worker in workers:
                worker.cancel()
            for worker in workers:
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            for index, worker in [*self._workers.items()]:
                if worker.done():
                    self._workers.pop(index, None)

    # ----------------------------------------------------------- submissions

    async def submit(
        self,
        problem: str,
        title: str | None = None,
        slug: str | None = None,
        thread_id: str | None = None,
        *,
        project: str | None = None,
        attempt: int = 1,
        parent_id: str | None = None,
        session_id: str | None = None,
        output_slug: str | None = None,
        resumed_from: str | None = None,
        scope: str = "package",
    ) -> Task:
        display_title = (title or problem.strip().splitlines()[0])[:80]
        directory = self.config.project_dir(project or self.config.default_project)
        taken = await self.tasks.taken_slugs() | {
            p.name for p in directory.glob("*") if p.is_dir()
        }
        task = Task(
            problem=problem.strip(),
            title=display_title,
            slug=slugify(slug or display_title, taken),
        )
        task.thread_id = thread_id
        task.project = project or self.config.default_project
        task.attempt = attempt
        task.parent_id = parent_id
        task.session_id = session_id
        task.output_slug = output_slug
        task.resumed_from = resumed_from
        task.scope = scope
        task.model = await self.settings.model_for(self.config.project, self.config.model)
        await self.tasks.create(task)

        self.bus.publish(
            Event(
                type="task_created",
                task_id=task.id,
                data={"task": task.to_json(self.config.assets_dir), "threadId": thread_id},
            )
        )
        self._wake_workers()
        return task

    async def queue_message(self, task_id: str, body: str) -> dict[str, Any] | None:
        """Hold a follow-up note, and deliver it now if the task has stopped."""
        task = await self.tasks.get(task_id)
        if task is None:
            return None
        message = await self.messages.add(task_id, body)
        self.bus.publish(
            Event(type="task_message", task_id=task_id, data={"message": message})
        )
        if task.state.terminal:
            await self.deliver_messages(task_id)
        return message

    async def deliver_messages(self, task_id: str) -> Task | None:
        """Start a follow-up attempt carrying everything queued for a task."""
        task = await self.tasks.get(task_id)
        if task is None or not task.state.terminal:
            return None
        bodies = await self.messages.take(task_id)
        if not bodies:
            return None

        notes = "\n\n".join(f"- {b}" for b in bodies)
        follow_up = (
            f"{task.problem}\n\n"
            "## Follow-up\n\n"
            "The previous attempt has finished. The operator has since asked for:\n\n"
            f"{notes}\n\n"
            "Work in the existing task directory, keep what is still correct, and "
            "re-run the test suite before you finish."
        )
        started = await self.submit(
            follow_up,
            title=task.title,
            slug=task.slug,
            thread_id=task.thread_id,
            project=task.project,
            attempt=task.attempt + 1,
            parent_id=task.id,
            session_id=task.session_id,
            output_slug=task.output_slug or task.slug,
            resumed_from=task.session_id,
        )
        # Echo the notes onto the follow-up as well, so opening it shows what
        # was asked for rather than starting mid-conversation.
        for index, body in enumerate(bodies):
            self.bus.publish(
                Event(
                    type="task_message",
                    task_id=started.id,
                    data={"message": {
                        "id": f"{started.id}-note-{index}",
                        "body": body,
                        "createdAt": time.time(),
                        "delivered": True,
                    }},
                )
            )
        self.bus.publish(
            Event(type="task_message_delivered", task_id=task_id,
                  data={"count": len(bodies), "startedTaskId": started.id})
        )
        if self.announce is not None:
            with contextlib.suppress(Exception):
                await self.announce(started, "Following up on")
        return started

    async def resume(self, task_id: str) -> Task | None:
        """Continue a task that stopped before finishing.

        When the run got far enough to have an agent session, the new attempt
        picks that session up, so the agent keeps its own memory of what it had
        already done. Without one there is nothing to resume into, so the new
        attempt starts fresh but is told what is already on disk.
        """
        original = await self.tasks.get(task_id)
        if original is None or not original.state.resumable:
            return None

        # For a project-wide task this directory is the whole project, and
        # naming its hundred packages would bury the brief. What it already did
        # is in the session it resumes into.
        existing = (
            []
            if original.project_wide
            else sorted(
                p.name
                for p in original.output_dir(
                    self.config.assets_dir, self.config.default_project
                ).glob("*")
            )
        )
        continuation = (
            f"{original.problem}\n\n"
            "## Resuming\n\n"
            "A previous attempt at this task was interrupted before it finished. "
            + (
                f"The task directory already contains: {', '.join(existing)}. "
                if existing
                else ""
            )
            + "Check what is there, keep whatever is correct and complete, and finish "
            "the rest of the deliverable. Re-run the test suite before you finish."
        )

        return await self.submit(
            continuation,
            title=original.title,
            slug=original.slug,
            thread_id=original.thread_id,
            project=original.project,
            attempt=original.attempt + 1,
            parent_id=original.id,
            session_id=original.session_id,
            # Continue the same package rather than opening an empty one.
            output_slug=original.output_slug or original.slug,
            resumed_from=original.session_id,
            scope=original.scope,
        )

    async def resume_in_place(self, task_id: str) -> Task | None:
        """Put a stopped task back in the queue as itself, with no child task.

        `resume` forks a continuation, which is right when the previous attempt
        left something worth keeping apart from the new one. It is wrong for a
        task that never really failed — one the server killed mid-run — because
        the thread then carries two chips for one piece of work, and the spend
        already on the original is stranded on a row nobody looks at again.

        Here the same row goes back to `queued` carrying its agent session, so
        the agent picks up its own memory of what it had done. The error is
        cleared: it described a run that is no longer the last word on this
        task. `attempt` still counts up, so the retry is visible.
        """
        original = await self.tasks.get(task_id)
        # Wider than the forking resume: a paused task has not failed, it is
        # waiting, and continuing it means queueing this same row again.
        if original is None or not original.state.continuable:
            return None

        await self.tasks.set_state(
            task_id,
            TaskState.QUEUED,
            # Without a session there is nothing to resume into and the agent
            # starts over, which is the honest outcome for a run that died
            # before it ever spoke.
            resumed_from=original.resumed_from or original.session_id,
            attempt=original.attempt + 1,
            finished_at=None,
            error=None,
            activity=None,
            # Asked for by name, so the hold is lifted.
            held=False,
        )
        self.bus.publish(
            Event(type="task_state", task_id=task_id,
                  data={"state": TaskState.QUEUED.value, "activity": "waiting for capacity"})
        )
        self._wake_workers()
        return await self.tasks.get(task_id)

    async def pause_task(self, task_id: str) -> Task | None:
        """Stop one task now; it goes again when there is room.

        The same shape as the global pause, applied to one row: a live run is
        marked preempted before it is cancelled, so the runner records a pause
        rather than a failure on its way out.
        """
        task = await self.tasks.get(task_id)
        if task is None or not task.state.pausable:
            return None

        live = self.live.get(task_id)
        if live is not None:
            live.preempted = True
            if live.runtime is not None:
                live.runtime.cancel()
        await self.tasks.set_state(
            task_id,
            TaskState.PAUSED,
            # It has to look like it ran, or `paused()` skips it as a row that
            # never started and it would never be picked up again.
            started_at=task.started_at or time.time(),
            finished_at=None,
            # Held, so dex leaves it alone. Without this the next free slot
            # picked it straight back up and the button did nothing.
            held=True,
        )
        self.bus.publish(
            Event(type="task_state", task_id=task_id,
                  data={"state": TaskState.PAUSED.value, "activity": None})
        )
        return await self.tasks.get(task_id)

    async def restart(self, task_id: str) -> Task | None:
        """Run the same brief again, from nothing.

        Unlike a resume, the agent session is dropped: restarting is what you
        reach for when the previous run went somewhere wrong, and carrying its
        conversation would carry the wrong turn with it. The brief is rebuilt
        when the run starts, so the project's AGENTS.md is re-read as it is now
        rather than as it was.
        """
        task = await self.tasks.get(task_id)
        if task is None or task.state is TaskState.ARCHIVED:
            return None

        live = self.live.get(task_id)
        if live is not None:
            live.preempted = True
            if live.runtime is not None:
                live.runtime.cancel()

        await self.tasks.set_state(
            task_id,
            TaskState.QUEUED,
            session_id=None,
            resumed_from=None,
            attempt=task.attempt + 1,
            started_at=None,
            finished_at=None,
            error=None,
            activity=None,
            cost_usd=None,
            turns=None,
            held=False,
        )
        self.bus.publish(
            Event(type="task_state", task_id=task_id,
                  data={"state": TaskState.QUEUED.value, "activity": "waiting for capacity"})
        )
        self._wake_workers()
        return await self.tasks.get(task_id)

    async def archive(self, task_id: str) -> Task | None:
        """Take a task out of every listing, leaving what it built on disk."""
        task = await self.tasks.get(task_id)
        if task is None or task.state is TaskState.ARCHIVED:
            return None

        live = self.live.get(task_id)
        if live is not None:
            # Archiving something mid-run stops it: leaving it running would
            # keep spending on a task nobody is looking at any more.
            live.preempted = True
            if live.runtime is not None:
                live.runtime.cancel()

        await self.tasks.set_state(
            task_id, TaskState.ARCHIVED, finished_at=task.finished_at or time.time(),
            activity=None,
        )
        self.bus.publish(
            Event(type="task_state", task_id=task_id,
                  data={"state": TaskState.ARCHIVED.value, "activity": None})
        )
        return await self.tasks.get(task_id)

    async def rerun(self, task_id: str) -> Task | None:
        """Start the same problem over, leaving the previous output in place."""
        original = await self.tasks.get(task_id)
        if original is None or not original.state.terminal:
            return None
        return await self.submit(
            original.problem,
            title=original.title,
            slug=original.slug,  # slugify appends a suffix so nothing is clobbered
            thread_id=original.thread_id,
            project=original.project,
            attempt=original.attempt + 1,
            parent_id=original.id,
            scope=original.scope,
        )

    async def set_concurrency(self, key: str, value: int) -> None:
        """Write a concurrency limit and apply it immediately.

        Going through the manager rather than the settings store directly is
        what keeps the cached limit honest — otherwise a caller has to remember
        to invalidate, and forgetting is silent.
        """
        await self.settings.set(key, value)
        self._limit_cache = None

    async def set_auto_approve(self, enabled: bool) -> int:
        """Persist the toggle and release anything already waiting on approval.

        Only approvals are released. A clarifying question still needs a person,
        so turning this on must not answer one on their behalf.
        """
        await self.settings.set(SettingsStore.AUTO_APPROVE, enabled)
        released = 0
        if enabled:
            for task in list(self.live.values()):
                for key in list(task.pending_approvals):
                    future = task.pending.get(key)
                    if future is not None and not future.done():
                        future.set_result("allow")
                        self.bus.publish(
                            Event(type="approval_resolved", task_id=task.id,
                                  data={"id": key, "decision": "allow", "auto": True})
                        )
                        released += 1
        self.bus.publish(
            Event(type="settings", data={"settings": {SettingsStore.AUTO_APPROVE: enabled}})
        )
        return released

    def merge_live(self, task: Task) -> Task:
        """Overlay in-flight state the database does not hold.

        Rows carry the durable fields; the futures the agent is parked on, and
        the artifacts seen so far, exist only in the process running the task.
        """
        live = self.live.get(task.id)
        if live is not None:
            task.pending = live.pending
            task.pending_approvals = live.pending_approvals
            task.artifacts = live.artifacts
            task.activity = live.activity or task.activity
        return task

    # -------------------------------------------------------------- operator

    def answer(self, task_id: str, key: str, value: object) -> bool:
        task = self.live.get(task_id)
        if task is None:
            return False
        future = task.pending.get(key)
        if future is None or future.done():
            return False
        future.set_result(value)
        return True

    async def pause(self, task_id: str) -> bool:
        """Stop a running task to free capacity, keeping it resumable.

        The agent session is recorded first, so the run that follows continues
        the conversation rather than starting the work again.
        """
        task = self.live.get(task_id)
        if task is None or task.runtime is None:
            return False
        task.preempted = True
        log.info("pausing %s to make room for higher-priority work", task.slug)
        task.runtime.cancel()
        # Recorded here for the same reason as a cancellation: the run is being
        # torn down and cannot be relied on to write its own epitaph.
        await self.tasks.set_state(
            task_id,
            TaskState.PAUSED,
            resumed_from=task.session_id,
            finished_at=time.time(),
        )
        self.bus.publish(
            Event(type="task_state", task_id=task_id,
                  data={"state": TaskState.PAUSED.value, "activity": "paused for capacity"})
        )
        return True

    async def resume_paused(self, slots: int) -> int:
        """Put paused tasks back in the queue, oldest first."""
        if slots <= 0:
            return 0
        restarted = 0
        for task in await self.tasks.paused(limit=slots):
            await self.tasks.set_state(
                task.id,
                TaskState.QUEUED,
                resumed_from=task.resumed_from or task.session_id,
                finished_at=None,
            )
            self.bus.publish(
                Event(type="task_state", task_id=task.id,
                      data={"state": TaskState.QUEUED.value, "activity": "waiting for capacity"})
            )
            restarted += 1
        if restarted:
            self._wake_workers()
            log.info("resumed %d paused task(s)", restarted)
        return restarted

    async def cancel(self, task_id: str) -> bool:
        task = self.live.get(task_id)
        if task is not None and task.runtime is not None:
            task.runtime.cancel()
            # Recorded here rather than left to the run's own cleanup: a
            # cancelled coroutine is not a reliable place to finish work, and
            # the operator's intent should survive regardless.
            await self.tasks.set_state(task_id, TaskState.CANCELLED, finished_at=time.time())
            self.bus.publish(
                Event(type="task_state", task_id=task_id,
                      data={"state": TaskState.CANCELLED.value, "activity": None})
            )
            return True
        # Queued in the database but not running anywhere.
        stored = await self.tasks.get(task_id)
        if stored is None or stored.state.terminal:
            return False
        await self.tasks.set_state(task_id, TaskState.CANCELLED, finished_at=time.time())
        self.bus.publish(
            Event(type="task_state", task_id=task_id,
                  data={"state": TaskState.CANCELLED.value, "activity": None})
        )
        return True

    # --------------------------------------------------------------- workers

    async def _worker(self, index: int) -> None:
        while True:
            # Retire when the limit has been lowered past this index. Checked
            # here rather than mid-task, so lowering the ceiling never cuts a
            # running task short -- a worker holding one finishes it first and
            # retires on its next time round. `_scale` spawns a replacement if
            # the limit goes back up.
            if index >= min(await self._task_limit(), MAX_WORKERS):
                if self._workers.get(index) is asyncio.current_task():
                    self._workers.pop(index, None)
                log.info("worker %d retired: the limit no longer reaches it", index)
                return

            # Capacity is how many tasks this process is running, not which
            # worker is asking: the busy workers are whichever ones won the
            # race, so gating on the index would let an idle low-index worker
            # claim past the limit.
            at_capacity = False
            async with self._gate:
                if len(self.live) >= await self.task_capacity():
                    at_capacity = True
                    task = None
                else:
                    task = await self.tasks.claim(self.worker_id)
                    if task is not None:
                        # Re-check: claiming is a database round trip, and a
                        # chat can take the last slot during it. The chat gate
                        # is a separate lock, so this window cannot be closed by
                        # holding one — but the claim can be given back, which
                        # is free because nothing has started yet.
                        if len(self.live) >= await self.task_capacity():
                            await self.tasks.set_state(task.id, TaskState.QUEUED)
                            at_capacity = True
                            task = None
                        else:
                            # Reserved inside the gate so a second worker cannot
                            # read a stale count and claim past the ceiling.
                            self.live[task.id] = task

            if at_capacity:
                # Full, not idle. Wait to be told capacity changed, but re-check
                # on a timeout too: a limit raised in another process fires no
                # signal here.
                await self._sleep_until_woken(LIMIT_POLL_S)
                continue

            if task is None:
                # Nothing queued: wait to be told there is, waking periodically
                # anyway so another process's work is picked up.
                await self._sleep_until_woken(5)
                continue

            heartbeat = asyncio.create_task(self._heartbeat(task.id))
            runner = self._runner_for(task)
            runtime = asyncio.create_task(runner.run(), name=f"dex-run-{task.slug}")
            task.runtime = runtime
            try:
                await runtime
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # the worker itself is shutting down
                log.info("task %s cancelled", task.slug)
            except Exception:
                log.exception("worker %d crashed on %s", index, task.slug)
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                task.runtime = None
                self.live.pop(task.id, None)
                # A note typed while this was running becomes the next attempt.
                with contextlib.suppress(Exception):
                    await self.deliver_messages(task.id)

    def _runner_for(self, task: Task) -> TaskRunner:
        """The scripted runner when DEX_FAKE_AGENT is set, otherwise the agent."""
        if self.config.fake_agent:
            from .fake_agent import FakeTaskRunner

            return FakeTaskRunner(task, self.config, self.bus, self.tasks, self.settings)
        return TaskRunner(task, self.config, self.bus, self.tasks, self.settings)

    async def _scale(self) -> None:
        """Bring the worker count in line with the configured limit.

        Growing spawns; shrinking is left to the workers themselves, which
        retire once their index is above the limit, so a running task is never
        cut short by a limit change.
        """
        if self._stopping:
            return
        for index, worker in [*self._workers.items()]:
            if worker.done():
                self._workers.pop(index, None)
        limit = min(await self._task_limit(), MAX_WORKERS)
        for index in range(limit):
            if index not in self._workers:
                self._workers[index] = asyncio.create_task(
                    self._worker(index), name=f"dex-worker-{index}"
                )

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(LIMIT_POLL_S)
            # Logged rather than suppressed: a supervisor that quietly stops
            # scaling looks exactly like a scheduler that has hung.
            try:
                await self._scale()
            except Exception:
                log.exception("could not scale the worker pool")
            try:
                await self.rebalance()
            except Exception:
                log.exception("could not rebalance")

    async def task_capacity(self) -> int:
        """How many tasks may run right now.

        The priority ladder: a chat or planning call outranks generation work,
        so every chat in flight takes a slot away from tasks. Tasks give the
        slot back — by being paused if necessary — and take it again when the
        chat is done.
        """
        if self.starting_up:
            # The same mechanism as a pause, for the first half minute after a
            # restart. Nothing is running yet at that point, so this holds work
            # back rather than parking any.
            return 0
        if await self._is_paused():
            # Zero capacity is all a pause needs to be: `rebalance` parks what
            # is running and the workers stop claiming, both of which already
            # happen whenever capacity drops. Resuming reverses it the same way.
            return 0
        return max(0, await self._task_limit() - self.chat_limiter.active)

    @property
    def starting_up(self) -> bool:
        """Still inside the settling period after a restart."""
        return time.monotonic() < self._grace_until

    @property
    def grace_remaining(self) -> float:
        """Seconds until work may start again; 0 once it may."""
        return max(0.0, self._grace_until - time.monotonic())

    async def _is_paused(self) -> bool:
        now = time.monotonic()
        if self._paused_cache is not None and now - self._paused_cache[0] < LIMIT_CACHE_S:
            return self._paused_cache[1]
        try:
            # Both reasons to hold work, asked as one question: the operator's
            # pause, and dex's own when a Claude limit is nearly spent. Work
            # runs only when neither applies.
            paused = await self.settings.work_held()
        except Exception:
            log.exception("could not read the pause flag")
            paused = False
        self._paused_cache = (now, paused)
        return paused

    async def set_paused(self, paused: bool) -> int:
        """Hold or release generation work, and act on it now.

        Returns how many tasks were parked or put back, so the caller can say
        what actually happened rather than just what was asked for.
        """
        await self.settings.set(SettingsStore.PAUSED, paused)
        self._paused_cache = None
        before = {t.id for t in await self.tasks.paused(limit=200)}
        await self.rebalance()
        after = {t.id for t in await self.tasks.paused(limit=200)}
        self._wake_workers()
        return len(after - before) if paused else len(before - after)


    async def rebalance(self) -> None:
        """Pause or resume tasks so the running set matches current capacity."""
        capacity = await self.task_capacity()
        running = [t for t in self.live.values() if t.runtime is not None]

        if len(running) > capacity:
            # Newest first: the least work is lost, and it is the most likely to
            # still be cheap to redo.
            surplus = sorted(running, key=lambda t: t.started_at or 0, reverse=True)
            for task in surplus[: len(running) - capacity]:
                await self.pause(task.id)
            return

        free = capacity - len(running)
        if free > 0:
            await self.resume_paused(free)
            # Capacity has opened up. Waking the workers here is what makes a
            # finished chat hand the slot back at once rather than at the next
            # poll.
            self._wake_workers()

    async def _sleep_until_woken(self, timeout: float) -> None:
        """Idle until woken or until the timeout, whichever comes first."""
        event = asyncio.Event()
        self._wake_events.add(event)
        try:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=timeout)
        finally:
            self._wake_events.discard(event)

    def _wake_workers(self) -> None:
        """Tell every idle worker to look again.

        Synchronous on purpose: setting an Event needs no lock, so there is no
        stray task to schedule and nothing left running after `stop()`.
        """
        for event in self._wake_events:
            event.set()

    async def _task_limit(self) -> int:
        now = time.monotonic()
        if self._limit_cache is not None and now - self._limit_cache[0] < LIMIT_CACHE_S:
            return self._limit_cache[1]
        try:
            limit = await self.settings.concurrency(SettingsStore.TASK_CONCURRENCY)
        except Exception:
            log.exception("could not read the task concurrency limit")
            limit = DEFAULT_CONCURRENCY
        self._limit_cache = (now, limit)
        return limit

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(STALE_AFTER_S)
            try:
                for task_id in await self.tasks.release_orphans(
                    STALE_AFTER_S, socket.gethostname()
                ):
                    log.info("recovered orphaned task %s", task_id)
                    self.bus.publish(
                        Event(type="task_state", task_id=task_id,
                              data={"state": TaskState.PAUSED.value, "activity": None})
                    )
            except Exception:
                log.exception("orphan sweep failed")

    async def _heartbeat(self, task_id: str) -> None:
        """Keeps the claim fresh so this task is not mistaken for orphaned."""
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            live = self.live.get(task_id)
            with contextlib.suppress(Exception):
                await self.tasks.heartbeat(task_id, live.activity if live else None)
