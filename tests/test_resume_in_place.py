"""Resuming a task as itself, rather than forking a continuation.

`resume` makes a child task, which is right when the failed attempt is worth
keeping beside the new one. It is wrong for a run the server killed mid-flight:
the thread ends up with two chips and two running totals for one piece of work,
and the spend already on the original is stranded.
"""

from __future__ import annotations

import time

import pytest

from dex.bus import EventBus
from dex.models import TaskState
from dex.queue import TaskManager


@pytest.fixture
async def manager(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        yield manager
    finally:
        await manager.stop()
        await bus.stop()


async def stopped_task(manager: TaskManager, state: TaskState, **fields):
    """A task that ran, got a session, and then stopped short."""
    task = await manager.submit("Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.set_session(task.id, "session-abc")
    await manager.tasks.set_state(
        task.id, state, started_at=time.time(), finished_at=time.time(), **fields
    )
    return task


async def test_it_requeues_the_same_task_rather_than_making_a_child(manager):
    original = await stopped_task(manager, TaskState.FAILED, error="agent reported an error")

    resumed = await manager.resume_in_place(original.id)

    assert resumed is not None
    # The point of the whole exercise: one task, not two.
    assert resumed.id == original.id
    assert resumed.parent_id is None
    assert resumed.state is TaskState.QUEUED
    all_tasks = await manager.tasks.list(limit=50)
    assert [t.id for t in all_tasks] == [original.id]


async def test_it_carries_the_agent_session_so_the_work_continues(manager):
    original = await stopped_task(manager, TaskState.FAILED, error="agent reported an error")

    resumed = await manager.resume_in_place(original.id)

    # Without this the agent starts the package over and the spend already on
    # the row buys nothing.
    assert resumed.resumed_from == "session-abc"
    assert resumed.attempt == original.attempt + 1


async def test_it_clears_the_error_from_the_run_that_no_longer_stands(manager):
    original = await stopped_task(manager, TaskState.FAILED, error="agent reported an error")

    resumed = await manager.resume_in_place(original.id)

    # A queued task showing the previous run's error reads as though it failed
    # again before it has done anything.
    assert resumed.error is None
    assert resumed.finished_at is None


async def test_a_task_with_no_session_still_goes_back_to_the_queue(manager):
    """A run that died before the agent spoke has nothing to resume into."""
    original = await stopped_task(manager, TaskState.CANCELLED)
    await manager.tasks.set_state(original.id, TaskState.CANCELLED, session_id=None)

    resumed = await manager.resume_in_place(original.id)

    assert resumed.state is TaskState.QUEUED
    assert resumed.resumed_from is None


async def test_a_finished_task_is_refused(manager):
    original = await stopped_task(manager, TaskState.SUCCEEDED)
    assert await manager.resume_in_place(original.id) is None


async def test_an_unknown_task_is_refused(manager):
    assert await manager.resume_in_place("nope") is None
