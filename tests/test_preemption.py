"""Two ceilings, and what still gives way.

Chat work and generation work are budgeted separately: a planner call or a design
turn runs against `chat_concurrency`, a task against `task_concurrency`, and
neither borrows from the other. Chat used to take a slot off the task limit and
park a running task to get it, which meant raising one number quietly lowered the
other.

What still gives way is a run inside its own pool: lower a ceiling under what is
running and the newest run of that kind is paused rather than lost — it resumes
on its own, continuing the same agent session.
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


async def test_a_chat_does_not_eat_into_task_capacity(manager):
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 3)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 2)
    assert await manager.task_capacity() == 3

    async with manager.chat_limiter:
        # The chat ceiling is what a planner call spends from.
        assert await manager.task_capacity() == 3
        assert await manager.chat_capacity() == 1
        async with manager.chat_limiter:
            assert await manager.task_capacity() == 3
            assert await manager.chat_capacity() == 0
    assert await manager.task_capacity() == 3
    assert await manager.chat_capacity() == 2


async def test_the_two_ceilings_are_set_independently(manager):
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 4)
    assert await manager.task_capacity() == 1
    assert await manager.chat_capacity() == 4

    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 1)
    assert await manager.task_capacity() == 1


async def test_a_chat_leaves_running_tasks_alone(manager):
    arm("a", "b")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 1)
    await manager.start()
    first = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)
    second = await manager.submit("problem b", title="b", slug="b")
    await asyncio.wait_for(BlockingRunner.started["b"].wait(), 45)

    async with manager.chat_limiter:
        # Long enough for the supervisor to have rebalanced had it wanted to.
        await asyncio.sleep(0.6)
        assert await manager.tasks.paused() == []
        for task in (first, second):
            assert (await manager.tasks.get(task.id)).state is TaskState.RUNNING

    for slug in ("a", "b"):
        BlockingRunner.release[slug].set()


async def test_lowering_the_task_ceiling_pauses_the_newest_and_it_resumes(manager):
    """A run still gives way — to its own pool's ceiling, not to a chat."""
    arm("a", "b")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    await manager.start()
    first = await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)
    second = await manager.submit("problem b", title="b", slug="b")
    await asyncio.wait_for(BlockingRunner.started["b"].wait(), 45)

    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.rebalance()
    await asyncio.sleep(0.4)
    paused = [t.id for t in await manager.tasks.paused()]
    # The newest run is the one displaced: least work lost.
    assert paused == [second.id]
    assert (await manager.tasks.get(first.id)).state is TaskState.RUNNING
    # It keeps the session, so resuming continues rather than restarts.
    assert (await manager.tasks.get(second.id)).resumed_from == "session-b"

    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
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


async def test_a_task_starts_while_a_chat_holds_the_chat_ceiling(manager):
    arm("a")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 1)
    await manager.start()

    async with manager.chat_limiter:          # chat capacity 1 -> 0
        task = await manager.submit("problem a", title="a", slug="a")
        # Asserted on the stored state rather than the runner's own signal: the
        # question is whether it was allowed to start, not what it then did.
        for _ in range(200):
            if (await manager.tasks.get(task.id)).state is not TaskState.QUEUED:
                break
            await asyncio.sleep(0.1)
        assert (await manager.tasks.get(task.id)).state is not TaskState.QUEUED

    for event in BlockingRunner.release.values():
        event.set()


async def test_a_design_turn_runs_while_the_task_pool_is_full(manager):
    """The reason for the split: a reply must not queue behind generation work."""
    arm("a", "turn")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 1)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 1)
    await manager.start()

    await manager.submit("problem a", title="a", slug="a")
    await asyncio.wait_for(BlockingRunner.started["a"].wait(), 45)

    turn = await manager.submit("a design turn", title="turn", slug="turn", scope="design")
    await asyncio.wait_for(BlockingRunner.started["turn"].wait(), 45)
    assert (await manager.tasks.get(turn.id)).state is TaskState.RUNNING

    for event in BlockingRunner.release.values():
        event.set()


async def test_chat_work_is_bounded_by_its_own_ceiling(manager):
    """Separate does not mean unbounded: the chat pool has a ceiling too."""
    arm("first", "second")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 4)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 1)
    await manager.start()

    await manager.submit("turn one", title="first", slug="first", scope="design")
    await asyncio.wait_for(BlockingRunner.started["first"].wait(), 45)
    second = await manager.submit("turn two", title="second", slug="second", scope="survey")
    await asyncio.sleep(0.8)
    # Four task workers are idle, and none of them may take it: it is chat work.
    assert (await manager.tasks.get(second.id)).state is TaskState.QUEUED

    BlockingRunner.release["first"].set()
    for _ in range(200):
        if (await manager.tasks.get(second.id)).state is not TaskState.QUEUED:
            break
        await asyncio.sleep(0.1)
    assert (await manager.tasks.get(second.id)).state is not TaskState.QUEUED

    for event in BlockingRunner.release.values():
        event.set()


async def test_a_planner_call_does_not_park_a_running_chat_turn(manager):
    """A survey plans from inside its own run, so parking it would deadlock."""
    arm("turn")
    await manager.set_concurrency(SettingsStore.TASK_CONCURRENCY, 2)
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 1)
    await manager.start()

    turn = await manager.submit("a survey", title="turn", slug="turn", scope="survey")
    await asyncio.wait_for(BlockingRunner.started["turn"].wait(), 45)

    async with manager.chat_limiter:          # chat capacity 1 -> 0
        await asyncio.sleep(0.6)
        assert (await manager.tasks.get(turn.id)).state is TaskState.RUNNING

    BlockingRunner.release["turn"].set()


async def test_a_pause_still_parks_a_running_chat_turn(manager):
    """Holding work is about spend, and a chat turn spends."""
    arm("turn")
    await manager.set_concurrency(SettingsStore.CHAT_CONCURRENCY, 2)
    await manager.start()

    turn = await manager.submit("a design turn", title="turn", slug="turn", scope="design")
    await asyncio.wait_for(BlockingRunner.started["turn"].wait(), 45)

    await manager.set_paused(True)
    await asyncio.sleep(0.4)
    assert (await manager.tasks.get(turn.id)).state is TaskState.PAUSED

    BlockingRunner.release["turn"].set()


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
