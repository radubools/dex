"""A global pause parks running work and stops new work starting.

It reuses the preemption path rather than adding a second one: capacity goes to
zero, which is a state the queue already knows how to handle — `rebalance`
parks what is running and the workers stop claiming.
"""

from __future__ import annotations

import asyncio

import pytest

from dex.bus import EventBus
from dex.models import TaskState
from dex.queue import TaskManager
from dex.store import SettingsStore


class BlockingRunner:
    started: dict[str, asyncio.Event] = {}
    release: dict[str, asyncio.Event] = {}

    def __init__(self, task, config, bus, store, settings=None):
        self.task, self.config, self.bus, self.store = task, config, bus, store

    async def run(self) -> None:
        BlockingRunner.started[self.task.slug].set()
        self.task.session_id = f"session-{self.task.slug}"
        await self.store.set_session(self.task.id, self.task.session_id)
        try:
            await BlockingRunner.release[self.task.slug].wait()
        except asyncio.CancelledError:
            raise
        self.task.state = TaskState.SUCCEEDED
        await self.store.set_state(self.task.id, TaskState.SUCCEEDED)


def arm(*slugs: str) -> None:
    for slug in slugs:
        BlockingRunner.started[slug] = asyncio.Event()
        BlockingRunner.release[slug] = asyncio.Event()


@pytest.fixture
async def manager(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", BlockingRunner)
    BlockingRunner.started.clear()
    BlockingRunner.release.clear()
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        yield manager
    finally:
        await manager.stop()
        await bus.stop()


async def test_pausing_parks_running_work_and_resuming_puts_it_back(manager):
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.start()
    task = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)

    parked = await manager.set_paused(True)
    assert parked == 1
    for _ in range(200):
        if (await manager.tasks.get(task.id)).state is TaskState.PAUSED:
            break
        await asyncio.sleep(0.05)
    assert (await manager.tasks.get(task.id)).state is TaskState.PAUSED
    assert await manager.task_capacity() == 0

    arm("a")  # the resumed attempt starts the runner again
    put_back = await manager.set_paused(False)
    assert put_back == 1
    for _ in range(200):
        if (await manager.tasks.get(task.id)).state is not TaskState.PAUSED:
            break
        await asyncio.sleep(0.05)
    assert (await manager.tasks.get(task.id)).state is not TaskState.PAUSED
    BlockingRunner.release["a"].set()


async def test_nothing_starts_while_paused(manager):
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    await manager.set_paused(True)
    await manager.start()
    await manager.submit("problem a", title="a", slug="a")

    await asyncio.sleep(1.0)
    assert not BlockingRunner.started["a"].is_set(), "a paused queue must not claim"

    await manager.set_paused(False)
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)
    BlockingRunner.release["a"].set()


async def test_the_flag_survives_a_restart(manager, config, db):
    await manager.set_paused(True)
    # A second manager reads the same setting; a pause is not per-process state.
    other = TaskManager(config, manager.bus, db)
    assert await other.task_capacity() == 0
    await manager.set_paused(False)


async def test_resumed_work_is_claimed_before_untouched_work(db, config):
    """A parked task is finished before a fresh one is begun.

    Ordering by `seq` alone got this right only by coincidence — parked tasks
    are usually the older ones. Constructed here the other way round: the task
    that has run carries the *higher* seq, so `seq` order would pick the wrong
    one.
    """
    from dex.models import Task
    from dex.store import TaskStore

    tasks = TaskStore(db)
    fresh = Task(problem="p", title="fresh", slug="fresh")
    await tasks.create(fresh)                      # lower seq, never started
    parked = Task(problem="p", title="parked", slug="parked")
    await tasks.create(parked)                     # higher seq, has run
    await tasks.set_state(parked.id, TaskState.RUNNING, started_at=1.0)
    await tasks.set_state(parked.id, TaskState.QUEUED)

    claimed = await tasks.claim("worker-1")
    assert claimed is not None
    assert claimed.slug == "parked", "work already under way comes first"


async def test_a_task_submitted_while_paused_does_not_start(manager):
    """Pausing has to hold work that arrives after it, not just what was running."""
    arm("later")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    await manager.start()
    await manager.set_paused(True)

    await manager.submit("problem later", title="later", slug="later")
    await asyncio.sleep(1.0)
    assert not BlockingRunner.started["later"].is_set()
    assert await manager.task_capacity() == 0

    await manager.set_paused(False)
    await asyncio.wait_for(BlockingRunner.started["later"].wait(), 45)
    BlockingRunner.release["later"].set()


async def test_a_run_the_server_killed_is_picked_up_again(manager, db):
    """One state for "stopped mid-run", so resume needs no special case.

    This used to be `interrupted` — a separate state that the resume path did
    not look at, so a run killed by a restart sat there while fresh work
    started ahead of it.
    """
    from dex.models import Task

    cut_off = Task(problem="p", title="cut off", slug="cut-off")
    await manager.tasks.create(cut_off)
    await manager.tasks.set_state(cut_off.id, TaskState.RUNNING, started_at=1.0)
    await manager.tasks.set_state(cut_off.id, TaskState.PAUSED)

    # Never ran: imported rows carry no start time and must stay put.
    imported = Task(problem="p", title="imported", slug="imported")
    await manager.tasks.create(imported)
    await manager.tasks.set_state(imported.id, TaskState.PAUSED)

    assert [t.slug for t in await manager.tasks.paused()] == ["cut-off"]

    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    assert await manager.resume_paused(2) == 1
    assert (await manager.tasks.get(cut_off.id)).state is TaskState.QUEUED
    assert (await manager.tasks.get(imported.id)).state is TaskState.PAUSED


async def test_a_restarted_run_is_claimed_before_untouched_work(manager, db):
    """And once back, it goes first — that is the point of putting it back."""
    from dex.models import Task

    fresh = Task(problem="p", title="fresh", slug="fresh")
    await manager.tasks.create(fresh)
    cut_off = Task(problem="p", title="cut off", slug="cut-off")
    await manager.tasks.create(cut_off)          # higher seq
    await manager.tasks.set_state(cut_off.id, TaskState.RUNNING, started_at=1.0)
    await manager.tasks.set_state(cut_off.id, TaskState.PAUSED)

    await manager.resume_paused(1)
    claimed = await manager.tasks.claim("worker-1")
    assert claimed is not None and claimed.slug == "cut-off"


async def test_a_shutdown_pauses_in_flight_work_rather_than_failing_it(manager):
    """Restarting the server was marking every running task failed.

    The agent reports an error when its subprocess is killed, so the runner's
    failure path ran before the cancellation path. Nothing is wrong with the
    work; it should go again.
    """
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.start()
    task = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)

    await manager.stop()

    after = await manager.tasks.get(task.id)
    assert after.state is not TaskState.FAILED, "a restart is not a task failure"

    # Either the runner recorded the pause on its way out, or the row was left
    # running for the next startup's orphan sweep. Both end here — the sweep is
    # what the restarted process does, seeing a claim held by a dead pid.
    if after.state is TaskState.RUNNING:
        import socket
        await manager.db.pool.execute(
            "UPDATE tasks SET claimed_by = $2 WHERE id = $1",
            task.id, f"{socket.gethostname()}:999999",
        )
        await manager.tasks.release_orphans(90, socket.gethostname())
        after = await manager.tasks.get(task.id)
    assert after.state is TaskState.PAUSED


def test_the_runner_records_a_stop_as_a_pause(config):
    """The path the SDK actually takes: a result message flagged as an error."""
    from dex.models import Task
    from dex.runner import TaskRunner

    task = Task(problem="p", title="t", slug="t")
    task.preempted = True
    recorded: list = []

    runner = TaskRunner.__new__(TaskRunner)
    runner.task = task
    runner.config = config
    runner.set_state = lambda state, **kw: recorded.append((state, kw))
    runner.emit = lambda *a, **kw: None

    runner._fail("agent reported an error")
    assert recorded == [(TaskState.PAUSED, {})]
    assert not task.error
