"""The priority ladder: chat outranks generation, and tasks give way.

A chat or planning call is short and someone is waiting on it; a generation task
is long and nobody is. So a chat in flight takes a slot from tasks, and the task
it displaces is paused rather than lost — it resumes on its own once the chat is
done, continuing the same agent session.
"""

from __future__ import annotations

import asyncio

import pytest

from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import SettingsStore


class BlockingRunner:
    started: dict[str, asyncio.Event] = {}
    release: dict[str, asyncio.Event] = {}

    def __init__(self, task, config, bus, store, settings=None):
        self.task, self.config, self.bus, self.store = task, config, bus, store

    async def run(self) -> None:
        if self.task.slug not in BlockingRunner.started:
            # Fail loudly: a slug the test did not arm would otherwise hang it
            # for the full timeout with nothing to say.
            raise AssertionError(
                f"runner started for un-armed slug {self.task.slug!r}; "
                f"armed: {sorted(BlockingRunner.started)}"
            )
        BlockingRunner.started[self.task.slug].set()
        self.task.session_id = f"session-{self.task.slug}"
        await self.store.set_session(self.task.id, self.task.session_id)
        try:
            await BlockingRunner.release[self.task.slug].wait()
        except asyncio.CancelledError:
            # The manager records the outcome; a cancelled coroutine is not a
            # dependable place to await a database write.
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


async def test_capacity_drops_while_a_chat_is_running(manager):
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 3)
    assert await manager.task_capacity() == 3

    async with manager.chat_limiter:
        assert await manager.task_capacity() == 2
        async with manager.chat_limiter:
            assert await manager.task_capacity() == 1
    assert await manager.task_capacity() == 3


async def test_a_chat_pauses_the_newest_task_and_it_resumes_after(manager):
    arm("a", "b")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    await manager.start()
    first = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)
    second = await manager.submit("problem b", title="b", slug="b")
    await asyncio.wait_for(BlockingRunner.started["b"].wait(), 45)

    async with manager.chat_limiter:          # capacity 2 -> 1
        await asyncio.sleep(0.4)
        paused = [t.id for t in await manager.tasks.paused()]
        # The newest run is the one displaced: least work lost.
        assert paused == [second.id]
        assert (await manager.tasks.get(first.id)).state is TaskState.RUNNING
        # It keeps the session, so resuming continues rather than restarts.
        assert (await manager.tasks.get(second.id)).resumed_from == "session-b"

    # Chat done: the paused task is queued again by itself.
    for _ in range(60):
        if (await manager.tasks.get(second.id)).state is not TaskState.PAUSED:
            break
        await asyncio.sleep(0.1)
    assert (await manager.tasks.get(second.id)).state in (TaskState.QUEUED, TaskState.RUNNING)

    for slug in ("a", "b"):
        BlockingRunner.release[slug].set()


async def test_a_paused_task_is_not_reported_as_finished(manager):
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.start()
    task = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)

    assert await manager.pause(task.id)
    await asyncio.sleep(0.4)

    stored = await manager.tasks.get(task.id)
    assert stored.state is TaskState.PAUSED
    # Nobody needs to do anything about a pause.
    assert not stored.state.terminal
    assert stored.state.waiting
    BlockingRunner.release["a"].set()


async def test_stop_still_cancels_rather_than_pausing(manager):
    """A person pressing Stop means it, and it must not come back."""
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.start()
    task = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)

    assert await manager.cancel(task.id)
    await asyncio.sleep(0.4)
    assert (await manager.tasks.get(task.id)).state is TaskState.CANCELLED

    # And a rebalance does not resurrect it.
    await manager.rebalance()
    await asyncio.sleep(0.3)
    assert (await manager.tasks.get(task.id)).state is TaskState.CANCELLED
    BlockingRunner.release["a"].set()


async def test_tasks_wait_rather_than_starting_while_chats_hold_capacity(manager):
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.start()

    async with manager.chat_limiter:          # capacity 1 -> 0
        task = await manager.submit("problem a", title="a", slug="a")
        await asyncio.sleep(0.8)
        # Asserted on the stored state rather than the runner's own signal: the
        # question is whether it was allowed to start, not what it then did.
        assert (await manager.tasks.get(task.id)).state is TaskState.QUEUED

    for _ in range(200):
        if (await manager.tasks.get(task.id)).state is not TaskState.QUEUED:
            break
        await asyncio.sleep(0.1)
    assert (await manager.tasks.get(task.id)).state is not TaskState.QUEUED

    for event in BlockingRunner.release.values():
        event.set()


async def test_paused_tasks_resume_oldest_first(manager, db):
    """Fairness: the task that has waited longest goes back first."""
    for slug in ("old", "new"):
        task = Task(problem="p", title=slug, slug=slug)
        await manager.tasks.create(task)
        # Paused means it had been running; the start time is what says so.
        await manager.tasks.set_state(task.id, TaskState.RUNNING, started_at=1.0)
        await manager.tasks.set_state(task.id, TaskState.PAUSED)

    resumed = await manager.resume_paused(1)
    assert resumed == 1
    states = {t.slug: t.state for t in await manager.tasks.list()}
    assert states["old"] is TaskState.QUEUED
    assert states["new"] is TaskState.PAUSED
