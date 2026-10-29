"""The work queue: Postgres holds the jobs, workers claim them.

Queue state lives in the database rather than in memory, so a restart picks up
what was queued, and a run the server never finished is left in a state that
can be resumed instead of vanishing.
"""

from __future__ import annotations

import asyncio
import uuid
import contextlib
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable

from .bus import EventBus
from .config import DEFAULT_CONCURRENCY, MAX_WORKERS, Config
from .db import Database
from .models import Event, Task, TaskState, continued_problem, slugify
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
        # Tasks that start, continue and wait for other tasks (deps.py).
        from .deps import Dependencies

        self.graph = Dependencies(self)
        #: Posts a message into a thread (the API's publish_message), set by the API.
        self.post: Callable[..., Awaitable[None]] | None = None
        #: Only the tasks this process is running: their cancel handles and the
        #: futures the agent is parked on. Everything else comes from Postgres.
        self.live: dict[str, Task] = {}
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._workers: dict[int, asyncio.Task[None]] = {}
        #: The chat pool: the same worker loop, claiming only the runs someone
        #: is waiting on in a thread. Separate from `_workers` because the two
        #: have separate ceilings — a design turn that had to wait for a shelf
        #: of generation work to clear was the whole reason for splitting them.
        self._chat_workers: dict[int, asyncio.Task[None]] = {}
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
        #: One cached read per limit, keyed by settings key: both pools consult
        #: theirs on every pass, and a single slot meant each read evicted the
        #: other's and neither was ever a hit.
        self._limit_cache: dict[str, tuple[float, int]] = {}
        #: Set by the app so follow-ups started from the worker loop are
        #: announced in their thread, the same as a resume or a re-run.
        self.announce: Callable[[Task, str], Awaitable[None]] | None = None
        #: Set by the app so a finished survey is planned from. The queue has
        #: no planner and the runner has no thread store, so the step from
        #: "the sources divide like this" to "here are the tasks" belongs to
        #: whoever owns both — which is the API.
        self.surveyed: Callable[[Task, str], Awaitable[None]] | None = None
        #: Set by the app so a run the CLI has just refused can call for the
        #: usage hold to be re-decided now rather than at the watcher's next
        #: pass — the fleet stops being dispatched in a second or two instead
        #: of failing task after task until the queue drains.
        self.usage_recheck: Callable[[], Awaitable[bool]] | None = None
        #: Gate for planner/chat calls; its ceiling lives in the database.
        self.chat_limiter = DynamicLimiter(
            lambda: self.settings.concurrency(SettingsStore.CHAT_CONCURRENCY),
            # Rebalance the moment chat pressure changes, rather than at the
            # supervisor's next tick. It is the chat pool that moves now: a
            # planner call finishing frees a slot for a queued design turn, and
            # generation work is not consulted either way.
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
            # Looked up rather than carried: recovery starts from an id, and an
            # untagged event would be invisible to every scoped subscriber.
            orphan_project = await self.db.pool.fetchval(
                "SELECT project FROM tasks WHERE id = $1", task_id
            )
            self.bus.publish(
                Event(
                    type="task_state",
                    task_id=task_id,
                    data={"state": TaskState.PAUSED.value, "activity": None,
                          "error": "The server stopped while this task was running."},
                    project=orphan_project,
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
        # Blocked tasks go back in the queue when what they wait for is done.
        self.graph.start()
        log.info(
            "started %d task workers and %d chat workers as %s "
            "(limits %d and %d, adjustable from the UI); "
            "holding queued work for %gs",
            len(self._workers), len(self._chat_workers), self.worker_id,
            await self.settings.concurrency(SettingsStore.TASK_CONCURRENCY),
            await self.settings.concurrency(SettingsStore.CHAT_CONCURRENCY),
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
        await self.graph.stop()
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
        for pool in (self._workers, self._chat_workers):
            while pool:
                workers = [*pool.values()]
                for worker in workers:
                    worker.cancel()
                for worker in workers:
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker
                for index, worker in [*pool.items()]:
                    if worker.done():
                        pool.pop(index, None)

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
        anchor: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
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
        task.anchor = anchor
        task.project = project or self.config.default_project
        task.attempt = attempt
        task.parent_id = parent_id
        task.session_id = session_id
        task.output_slug = output_slug
        task.resumed_from = resumed_from
        task.scope = scope
        task.options = dict(options or {})
        task.model = await self.settings.model_for(self.config.project, self.config.model)
        await self.tasks.create(task)

        self.bus.publish(
            Event(
                type="task_created",
                task_id=task.id,
                data={"task": task.to_json(self.config.assets_dir), "threadId": thread_id},
                project=task.project,
            )
        )
        self._wake_workers()
        return task

    async def queue_message(self, task_id: str, body: str,
                            options: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Hold a follow-up note, and deliver it now if the task has stopped.

        `options` go with it to the attempt it starts (design/18: run options).
        """
        task = await self.tasks.get(task_id)
        if task is None:
            return None
        message = await self.messages.add(task_id, body, options)
        self.bus.publish(
            Event(type="task_message", task_id=task_id, data={"message": message})
        )
        if task.state.terminal:
            await self.deliver_messages(task_id)
        return message

    def say(self, task_id: str, body: str) -> dict[str, Any] | None:
        """Hand a message to a running chat task now: no queueing.

        The runner interrupts the turn under way and sends it as the next one
        (`TaskRunner._converse`). None when the task is not a chat that is
        running here; the caller then queues it as a follow-up note instead.
        """
        task = self.live.get(task_id)
        if task is None or task.inbox is None:
            return None
        message = {"id": f"{task_id}-live-{uuid.uuid4().hex[:8]}", "body": body, "delivered": True, "live": True, "at": time.time()}
        task.inbox.put_nowait(body)
        self.bus.publish(Event(type="task_message", task_id=task_id, data={"message": message}, project=task.project))
        return message

    async def deliver_messages(self, task_id: str) -> Task | None:
        """Start a follow-up attempt carrying everything queued for a task."""
        task = await self.tasks.get(task_id)
        if task is None or not task.state.terminal:
            return None
        options = {**(task.options or {}), **await self.messages.pending_options(task_id)}
        bodies = await self.messages.take(task_id)
        if not bodies:
            return None

        notes = "\n\n".join(f"- {b}" for b in bodies)
        note = (
            "## Follow-up\n\n"
            "The previous attempt has finished. The operator has since asked for:\n\n"
            f"{notes}"
        )
        if task.is_chat:
            # A game chat is a conversation: the player answered.
            note = "## The player replied\n\n" + "\n\n".join(bodies)
        elif not task.is_design:
            note += (
                "\n\nWork in the existing task directory, keep what is still "
                "correct, and re-run the test suite before you finish."
            )
        started = await self.submit(
            continued_problem(task, note),
            title=task.title,
            slug=task.slug,
            thread_id=task.thread_id,
            project=task.project,
            attempt=task.attempt + 1,
            parent_id=task.id,
            session_id=task.session_id,
            output_slug=task.output_slug or task.slug,
            resumed_from=task.session_id,
            # Without this a continued design turn came back as an ordinary
            # package task: it took the generation prompt, lost the design
            # tools, and built a package directory for a conversation.
            scope=task.scope,
            # The attempt runs as it was asked to (tests off stay off; a note can change it).
            options=options,
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
        note = (
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
        continuation = continued_problem(original, note)

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
            #
            # `archived` rather than `preempted`: preempted means "start this
            # again when there is room", which is the opposite of what an
            # archive asks for.
            live.archived = True
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
        self._limit_cache.pop(key, None)
        # Apply it now rather than on the supervisor's next tick. The docstring
        # above has always claimed "immediately", but the pool only grew when
        # `_supervise` next woke from its unconditional two-second sleep — so
        # raising the limit in the UI did nothing visible for up to two seconds,
        # and under load the queued work sat there longer still. `_scale` is
        # idempotent and returns early while stopping, so calling it here is
        # safe alongside the supervisor that will call it again.
        if (
            key in (SettingsStore.TASK_CONCURRENCY, SettingsStore.CHAT_CONCURRENCY)
            and self._supervisor is not None
        ):
            await self._scale()
            self._wake_workers()

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

    async def resume_paused(self, slots: int, interactive: bool | None = None) -> int:
        """Put paused tasks back in the queue, oldest first.

        `interactive` restricts it to one pool's work: free chat capacity must
        not put a generation task back, or the task limit is exceeded by however
        many chats are idle.
        """
        if slots <= 0:
            return 0
        restarted = 0
        for task in await self.tasks.paused(limit=slots, interactive=interactive):
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

    async def _worker(self, index: int, interactive: bool = False) -> None:
        pool = self._pool(interactive)
        label = "chat worker" if interactive else "worker"
        while True:
            # Retire when the limit has been lowered past this index. Checked
            # here rather than mid-task, so lowering the ceiling never cuts a
            # running task short -- a worker holding one finishes it first and
            # retires on its next time round. `_scale` spawns a replacement if
            # the limit goes back up.
            if index >= min(await self._pool_limit(interactive), MAX_WORKERS):
                if pool.get(index) is asyncio.current_task():
                    pool.pop(index, None)
                log.info("%s %d retired: the limit no longer reaches it", label, index)
                return

            # Capacity is how many runs of this kind the process has, not which
            # worker is asking: the busy workers are whichever ones won the
            # race, so gating on the index would let an idle low-index worker
            # claim past the limit. Counted per pool, because the two ceilings
            # are independent — generation work is no reason for a chat turn to
            # wait, and a chat turn is no reason to park a task.
            at_capacity = False
            async with self._gate:
                if await self._pool_full(interactive):
                    at_capacity = True
                    task = None
                else:
                    task = await self.tasks.claim(self.worker_id, interactive=interactive)
                    if task is not None:
                        # Re-check: claiming is a database round trip, and a
                        # planner call can take the last chat slot during it. The
                        # chat gate is a separate lock, so this window cannot be
                        # closed by holding one — but the claim can be given
                        # back, which is free because nothing has started yet.
                        if await self._pool_full(interactive):
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
                log.exception("%s %d crashed on %s", label, index, task.slug)
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    # Two cancellations arrive at this await and they mean
                    # opposite things: the heartbeat's own, which is expected
                    # and finished with, and this worker's, which `stop()` is
                    # waiting on. Suppressing both swallowed the second — the
                    # worker went back to waiting for work with its cancellation
                    # already spent, and shutdown hung on it forever.
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        raise
                task.runtime = None
                self.live.pop(task.id, None)
                # A note typed while this was running becomes the next attempt,
                # once the run's final state is written (deliver_messages reads it).
                with contextlib.suppress(Exception):
                    await runner.settled()
                    await self.deliver_messages(task.id)

    def _runner_for(self, task: Task) -> TaskRunner:
        """The scripted runner when DEX_FAKE_AGENT is set, otherwise the agent."""
        if self.config.fake_agent:
            from .fake_agent import FakeTaskRunner

            return FakeTaskRunner(task, self.config, self.bus, self.tasks, self.settings)
        runner = TaskRunner(task, self.config, self.bus, self.tasks, self.settings)
        runner.surveyed = self.surveyed
        runner.on_limit_reading = self.usage_recheck
        runner.graph = self.graph
        return runner

    async def _scale(self) -> None:
        """Bring each pool's worker count in line with its configured limit.

        Growing spawns; shrinking is left to the workers themselves, which
        retire once their index is above the limit, so a running task is never
        cut short by a limit change.
        """
        if self._stopping:
            return
        for interactive in (False, True):
            pool = self._pool(interactive)
            for index, worker in [*pool.items()]:
                if worker.done():
                    pool.pop(index, None)
            limit = min(await self._pool_limit(interactive), MAX_WORKERS)
            prefix = "dex-chat-worker" if interactive else "dex-worker"
            for index in range(limit):
                if index not in pool:
                    pool[index] = asyncio.create_task(
                        self._worker(index, interactive), name=f"{prefix}-{index}"
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

    def _pool(self, interactive: bool) -> dict[int, asyncio.Task[None]]:
        return self._chat_workers if interactive else self._workers

    async def _pool_full(self, interactive: bool) -> bool:
        """Whether this pool is already running everything it may."""
        running = sum(1 for t in self.live.values() if t.interactive is interactive)
        return running >= await self._pool_capacity(interactive)

    async def _pool_capacity(self, interactive: bool) -> int:
        return await (self.chat_capacity() if interactive else self.task_capacity())

    async def task_capacity(self) -> int:
        """How many generation tasks may run right now.

        Just the task limit. It used to be the task limit less the chats in
        flight, on the reasoning that a chat is short and someone is waiting on
        it: a chat took a task's slot and the task was paused to give it up. But
        the two are different kinds of work with a ceiling each, and borrowing
        across them made both numbers mean something other than what they said —
        raising "chats at once" quietly lowered how many tasks could run, and a
        burst of chat parked generation work that had nothing to do with it.
        Chat work is bounded by `chat_capacity` now, and neither limit moves the
        other.
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
        return await self._task_limit()

    async def chat_capacity(self) -> int:
        """How many chat turns may run as tasks right now.

        The chat ceiling covers both kinds of chat work: the inline planner
        calls that go through `chat_limiter`, and the design and survey turns
        that run as tasks. So the planner calls in flight come off it — that
        much is a real shared budget, one operator-facing number for "chats at
        once" — while generation work is not consulted either way.

        The accounting runs one way only: a planner call takes a slot from this
        pool, but a running turn does not hold one back from `chat_limiter`. It
        cannot, because a finished survey plans from inside its own run: the
        survey task is still live when `_plan_turn` acquires the limiter, so a
        limiter that counted it would have the survey waiting on a call that is
        waiting on the survey. The cost of the asymmetry is that chat work can
        briefly reach twice the ceiling; the cost of closing it is a deadlock.
        """
        if self.starting_up or await self._is_paused():
            # Held for the same reasons as generation work: the pause an
            # operator sets and the one dex sets when a Claude limit is nearly
            # spent are both about not spending, and a chat turn spends.
            return 0
        return max(0, await self._chat_limit() - self.chat_limiter.active)

    @property
    def chat_active(self) -> int:
        """Chat work in flight: planner calls, plus the turns that run as tasks.

        One number, because one ceiling governs both.
        """
        return self.chat_limiter.active + sum(
            1 for t in self.live.values() if t.interactive
        )

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
        """Pause or resume runs so each pool matches its own current capacity.

        Both pools, separately: a surplus of chat turns is no reason to park a
        generation task, and the paused work each pool puts back is its own.
        """
        # A chat turn gives way to a hold, never to chat pressure. Pausing the
        # fleet — or dex pausing it over a usage limit — parks a design turn like
        # anything else, because it spends. But a planner call arriving is not a
        # reason to park the turn someone is watching: the ceiling is already
        # enforced where work is claimed, so the surplus drains on its own, and
        # parking it would cancel a survey mid-run in the window where its own
        # planning call holds the slot.
        #
        # The pause flag is read fresh here. It is cached elsewhere for a
        # second, and a rebalance that runs the instant a usage hold is set —
        # this very call, from the watcher — would otherwise still see the old
        # answer and resume work that was paused a moment ago.
        self._paused_cache = None
        held = self.starting_up or await self._is_paused()
        for interactive in (False, True):
            capacity = await self._pool_capacity(interactive)
            running = [
                t for t in self.live.values()
                if t.runtime is not None and t.interactive is interactive
            ]

            if len(running) > capacity and (held or not interactive):
                # Newest first: the least work is lost, and it is the most likely
                # to still be cheap to redo. A run that already stopped itself —
                # a limit pause, say, still unwinding — has nothing to give up:
                # cancelling it again would only overwrite why it stopped.
                surplus = sorted(
                    (t for t in running if t.state in (TaskState.RUNNING, TaskState.AWAITING_INPUT)),
                    key=lambda t: t.started_at or 0, reverse=True,
                )
                for task in surplus[: len(running) - capacity]:
                    await self.pause(task.id)
                continue

            free = capacity - len(running)
            if free > 0:
                await self.resume_paused(free, interactive=interactive)
                # Capacity has opened up. Waking the workers here is what makes a
                # finished planner call hand the chat slot back at once rather
                # than at the next poll.
                self._wake_workers()

    async def _sleep_until_woken(self, timeout: float) -> None:
        """Idle until woken or until the timeout, whichever comes first.

        `asyncio.wait` rather than `wait_for`, which reports a cancellation that
        lands as its timeout expires as a `TimeoutError` — indistinguishable from
        the timeout this suppresses, so the worker's own cancellation went the
        same way as the heartbeat's above and shutdown waited on a worker that
        had gone back to sleep. `asyncio.wait` does not rewrite cancellation.
        """
        event = asyncio.Event()
        self._wake_events.add(event)
        waiter = asyncio.ensure_future(event.wait())
        try:
            await asyncio.wait({waiter}, timeout=timeout)
        finally:
            waiter.cancel()
            self._wake_events.discard(event)

    def _wake_workers(self) -> None:
        """Tell every idle worker to look again.

        Synchronous on purpose: setting an Event needs no lock, so there is no
        stray task to schedule and nothing left running after `stop()`.
        """
        for event in self._wake_events:
            event.set()

    async def _task_limit(self) -> int:
        return await self._limit(SettingsStore.TASK_CONCURRENCY)

    async def _chat_limit(self) -> int:
        return await self._limit(SettingsStore.CHAT_CONCURRENCY)

    async def _pool_limit(self, interactive: bool) -> int:
        return await (self._chat_limit() if interactive else self._task_limit())

    async def _limit(self, key: str) -> int:
        now = time.monotonic()
        cached = self._limit_cache.get(key)
        if cached is not None and now - cached[0] < LIMIT_CACHE_S:
            return cached[1]
        try:
            limit = await self.settings.concurrency(key)
        except Exception:
            log.exception("could not read the %s limit", key)
            limit = DEFAULT_CONCURRENCY
        self._limit_cache[key] = (now, limit)
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
