"""Pausing, restarting and archiving a task — or a whole collapsed group.

The thread collapses tasks by status, so a single control can stand for a
hundred and thirty-seven of them. That is the reason these take a list: acting
on a group one request at a time leaves a half-applied action behind whenever
something goes wrong in the middle.
"""

from __future__ import annotations

import time

import pytest

from fastapi.testclient import TestClient

from dex.api import create_app
from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import TaskStore


@pytest.fixture
def client(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


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


async def a_task(manager: TaskManager, state: TaskState, **fields) -> Task:
    task = await manager.submit("Two Sum", title="Two Sum")
    if state is not TaskState.QUEUED:
        await manager.tasks.set_state(task.id, state, **fields)
    return await manager.tasks.get(task.id)


# ------------------------------------------------------------------- pause

async def test_pausing_a_queued_task_leaves_it_ready_to_be_resumed(manager):
    task = await a_task(manager, TaskState.QUEUED)

    paused = await manager.pause_task(task.id)

    assert paused.state is TaskState.PAUSED
    # It still has to look like a row that ran, or nothing would ever start it
    # again — including the operator asking for it by name.
    assert paused.started_at is not None
    # But dex does not start it: that is what `held` means, and it is covered
    # by the tests further down.
    assert paused.held is True


async def test_a_finished_task_cannot_be_paused(manager):
    task = await a_task(manager, TaskState.SUCCEEDED)
    assert await manager.pause_task(task.id) is None


# ----------------------------------------------------------------- restart

async def test_restarting_drops_the_session_so_it_begins_again(manager):
    task = await a_task(manager, TaskState.FAILED, error="went wrong")
    await manager.tasks.set_session(task.id, "session-abc")

    restarted = await manager.restart(task.id)

    assert restarted.state is TaskState.QUEUED
    # The point of a restart rather than a resume: the conversation that went
    # wrong is not carried into the new run.
    assert restarted.session_id is None
    assert restarted.resumed_from is None
    assert restarted.error is None
    assert restarted.started_at is None
    assert restarted.attempt == task.attempt + 1


async def test_restarting_keeps_the_same_brief_and_output_directory(manager, config):
    task = await manager.submit("Two Sum", title="Two Sum", slug="two-sum")

    restarted = await manager.restart(task.id)

    assert restarted.problem == task.problem
    assert restarted.output_dir(config.assets_dir) == task.output_dir(config.assets_dir)


async def test_restarting_an_archived_task_is_refused(manager):
    task = await a_task(manager, TaskState.ARCHIVED)
    assert await manager.restart(task.id) is None


# ----------------------------------------------------------------- archive

async def test_archiving_hides_the_task_from_every_listing(manager):
    kept = await a_task(manager, TaskState.SUCCEEDED)
    gone = await a_task(manager, TaskState.SUCCEEDED)

    await manager.archive(gone.id)

    listed = {t.id for t in await manager.tasks.list(limit=50)}
    assert kept.id in listed
    assert gone.id not in listed
    # Still there when something asks for it: archiving is not deleting.
    assert (await manager.tasks.get(gone.id)).state is TaskState.ARCHIVED
    assert gone.id in {t.id for t in await manager.tasks.list(limit=50, include_archived=True)}


async def test_archiving_leaves_what_the_task_built_on_disk(manager, config):
    task = await manager.submit("Two Sum", title="Two Sum", slug="two-sum")
    package = task.output_dir(config.assets_dir)
    package.mkdir(parents=True, exist_ok=True)
    (package / "solutions.py").write_text("x = 1", encoding="utf-8")

    await manager.archive(task.id)

    assert (package / "solutions.py").read_text(encoding="utf-8") == "x = 1"


async def test_archiving_twice_is_refused_rather_than_repeated(manager):
    task = await a_task(manager, TaskState.SUCCEEDED)
    assert await manager.archive(task.id) is not None
    assert await manager.archive(task.id) is None


async def test_an_archived_task_is_out_of_its_thread(manager, db):
    from dex.store import ThreadStore

    thread = await ThreadStore(db).create(title="T", project="algorithms")
    task = await manager.submit("Two Sum", title="Two Sum", thread_id=thread.id)
    await manager.archive(task.id)

    assert await manager.tasks.list(thread_id=thread.id) == []


# -------------------------------------------------------------- the group

def test_an_action_covers_every_task_it_is_given(client):
    """One control can stand for a collapsed group of many."""
    made = [
        client.post("/api/tasks", json={"problem": f"Problem {n}"}).json()["task"]
        for n in range(3)
    ]

    body = client.post(
        "/api/tasks/actions",
        json={"ids": [t["id"] for t in made], "action": "archive"},
    ).json()

    assert len(body["tasks"]) == 3
    assert body["skipped"] == []
    assert client.get("/api/tasks").json()["tasks"] == []


def test_a_task_the_action_does_not_apply_to_is_skipped_not_fatal(client):
    """In a group of a hundred, some are always in the wrong state."""
    ok = client.post("/api/tasks", json={"problem": "Runnable"}).json()["task"]
    wait_for_terminal(client, ok["id"])

    body = client.post(
        "/api/tasks/actions",
        json={"ids": [ok["id"], "no-such-task"], "action": "archive"},
    ).json()

    assert [t["id"] for t in body["tasks"]] == [ok["id"]]
    assert body["skipped"] == ["no-such-task"]


def wait_for_terminal(client, task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()["task"]
        if task["state"] in {"succeeded", "failed", "cancelled", "archived"}:
            return task
        time.sleep(0.05)
    raise AssertionError("task did not finish")


# ------------------------------------------------------------------ resume

async def test_a_paused_task_can_be_put_straight_back_in_the_queue(manager):
    """Pause and resume are the same control in two states."""
    task = await a_task(manager, TaskState.QUEUED)
    await manager.pause_task(task.id)

    resumed = await manager.resume_in_place(task.id)

    assert resumed.state is TaskState.QUEUED


async def test_resuming_keeps_the_session_where_a_restart_drops_it(manager):
    task = await a_task(manager, TaskState.FAILED, error="stopped")
    await manager.tasks.set_session(task.id, "session-abc")

    resumed = await manager.resume_in_place(task.id)

    # The difference between the two controls: resume continues the agent's own
    # conversation, restart begins again without it.
    assert resumed.resumed_from == "session-abc"


async def test_a_running_task_is_not_resumable(manager):
    task = await a_task(manager, TaskState.RUNNING)
    assert await manager.resume_in_place(task.id) is None


@pytest.mark.parametrize(
    "state, pause, resume",
    [
        (TaskState.QUEUED, True, False),
        (TaskState.RUNNING, True, False),
        (TaskState.AWAITING_INPUT, True, False),
        (TaskState.PAUSED, False, True),
        (TaskState.FAILED, False, True),
        (TaskState.CANCELLED, False, True),
        (TaskState.SUCCEEDED, False, False),
        (TaskState.ARCHIVED, False, False),
    ],
)
def test_a_task_is_never_offered_both_pause_and_resume(state, pause, resume):
    """They share one slot in the chip, so the states must not overlap."""
    assert state.pausable is pause
    assert state.continuable is resume
    assert not (state.pausable and state.continuable)


def test_resume_is_an_action_the_endpoint_accepts(client):
    made = client.post("/api/tasks", json={"problem": "Two Sum"}).json()["task"]
    wait_for_terminal(client, made["id"])
    client.post("/api/tasks/actions", json={"ids": [made["id"]], "action": "archive"})

    # Archived is not continuable, so this is skipped rather than accepted —
    # which also proves the action name itself is valid.
    body = client.post(
        "/api/tasks/actions", json={"ids": [made["id"]], "action": "resume"}
    ).json()
    assert body["action"] == "resume"
    assert body["skipped"] == [made["id"]]


# ------------------------------------------------- a hold that actually holds

async def test_a_pause_the_operator_asked_for_is_not_undone_by_the_queue(manager):
    """Pausing showed the task paused, then waiting for capacity, then running.

    `paused` means two things — dex made room, or the operator said stop — and
    the queue revived both. Only dex's own may be picked up again.
    """
    task = await a_task(manager, TaskState.QUEUED)
    await manager.pause_task(task.id)

    assert (await manager.tasks.get(task.id)).held is True
    # The queue's own revival pass must pass it over, however much room there is.
    assert await manager.resume_paused(slots=10) == 0
    assert (await manager.tasks.get(task.id)).state is TaskState.PAUSED


async def test_a_pause_dex_made_for_room_is_still_picked_up_again(manager):
    """The other half: preemption has to keep working."""
    task = await a_task(manager, TaskState.QUEUED)
    await manager.tasks.set_state(task.id, TaskState.PAUSED, started_at=time.time())

    assert (await manager.tasks.get(task.id)).held is False
    assert await manager.resume_paused(slots=10) == 1
    assert (await manager.tasks.get(task.id)).state is TaskState.QUEUED


async def test_resuming_by_hand_lifts_the_hold(manager):
    task = await a_task(manager, TaskState.QUEUED)
    await manager.pause_task(task.id)

    resumed = await manager.resume_in_place(task.id)

    assert resumed.state is TaskState.QUEUED
    assert resumed.held is False


async def test_restarting_lifts_the_hold_too(manager):
    task = await a_task(manager, TaskState.QUEUED)
    await manager.pause_task(task.id)

    restarted = await manager.restart(task.id)

    assert restarted.state is TaskState.QUEUED
    assert restarted.held is False


async def test_an_archived_task_is_not_revived_by_a_late_write(manager):
    """Archiving cancels the run, and the run then records its own ending.

    That write arrived after the archive and undid it, so the task came back,
    waited for capacity and started again.
    """
    task = await a_task(manager, TaskState.RUNNING, started_at=time.time())
    await manager.archive(task.id)

    # Exactly what a cancelled run writes on its way out.
    await manager.tasks.set_state(task.id, TaskState.PAUSED, finished_at=None)

    assert (await manager.tasks.get(task.id)).state is TaskState.ARCHIVED
    assert await manager.resume_paused(slots=10) == 0


async def test_archiving_a_running_task_sticks(config, db, monkeypatch):
    """Archiving used to look like it did nothing.

    The archive cancels the run; the run then wrote its own ending -- a pause,
    since it was cancelled rather than failed. The database refused that write,
    but the *event* had already gone out, so the row said archived and the
    screen said paused until someone reloaded. And a paused task is one dex
    starts again on its own, which is the opposite of archiving.
    """
    import asyncio

    from dex.bus import EventBus
    from dex.models import Task, TaskState
    from dex.runner import TaskRunner
    from dex.store import SettingsStore, TaskStore

    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="archive-me")
    task.project = "algorithms"
    await tasks.create(task)
    await tasks.set_state(task.id, TaskState.RUNNING)

    bus = EventBus(db)
    await bus.start()
    try:
        runner = TaskRunner(task, config, bus, tasks, SettingsStore(db))
        # What `archive()` does to a live task before cancelling it.
        task.archived = True
        runner._fail("agent reported an error")
        await asyncio.sleep(0.3)

        # The row keeps it...
        assert (await tasks.get(task.id)).state is TaskState.ARCHIVED
        # ...and so does the last thing the UI was told.
        states = [
            e.data["state"] for e in await bus.history() if e.type == "task_state"
        ]
        assert states[-1] == "archived", states
    finally:
        await bus.stop()


async def test_an_archived_task_is_never_picked_up_again(db):
    """`resume_paused` is what restarts work on its own; archived must be invisible to it."""
    from dex.models import Task, TaskState
    from dex.store import TaskStore

    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="left-alone")
    await tasks.create(task)
    await tasks.set_state(task.id, TaskState.RUNNING, started_at=1.0)
    await tasks.set_state(task.id, TaskState.ARCHIVED)

    waiting = await tasks.paused(limit=50)
    assert task.id not in [t.id for t in waiting]

    # And a late write from the dying run cannot drag it back out.
    applied = await tasks.set_state(task.id, TaskState.PAUSED)
    assert applied is False
    assert (await tasks.get(task.id)).state is TaskState.ARCHIVED


async def test_archived_offers_rerun_but_not_restart(db, config):
    """Manual rerun is the only way back, which is what archiving means."""
    from dex.models import Task, TaskState
    from dex.store import TaskStore

    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="flags")
    await tasks.create(task)
    await tasks.set_state(task.id, TaskState.ARCHIVED)

    shown = (await tasks.get(task.id)).to_json(config.assets_dir)
    assert shown["canRerun"] is True, "a manual rerun must still be possible"
    assert shown["canRestart"] is False, "restart would resurrect it in place"
    assert shown["canArchive"] is False
    assert shown["state"] == "archived"
