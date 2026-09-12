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

async def test_pausing_a_queued_task_leaves_it_ready_to_go_again(manager):
    task = await a_task(manager, TaskState.QUEUED)

    paused = await manager.pause_task(task.id)

    assert paused.state is TaskState.PAUSED
    # `paused()` skips rows that never started, so a paused task has to look
    # like one that ran or it would never be picked up again.
    assert paused.started_at is not None
    assert [t.id for t in await manager.tasks.paused(limit=10)] == [task.id]


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
