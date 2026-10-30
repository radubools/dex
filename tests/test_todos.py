"""Work that waits for dex to be idle, and gives way to anything else (dex/todos.py)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from dex.bus import EventBus
from dex.deps import Refused
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import ThreadStore


@pytest.fixture
async def manager(config, db, monkeypatch):
    bus = EventBus(db)
    await bus.start()
    m = TaskManager(config, bus, db)
    monkeypatch.setattr(TaskManager, "starting_up", property(lambda self: False))

    async def post(thread_id, message):
        pass

    m.post = post
    try:
        yield m
    finally:
        await m.todos.stop()
        await m.graph.stop()
        await bus.stop()


async def split(m, *titles):
    root = await m.submit("do things", title="Things", scope="todos", options={"todo": True})
    made = await m.todos.add(root, [{"title": t, "brief": f"Do {t}."} for t in titles])
    await m.tasks.set_state(root.id, TaskState.SUCCEEDED, finished_at=1.0)
    return root, made


async def state(m, task):
    return (await m.tasks.get(task.id)).state


async def test_todos_wait_in_todo_in_the_power_thread(manager, db):
    root, (a, b) = await split(manager, "a", "b")
    assert await state(manager, a) is TaskState.TODO
    assert a.scope == "power" and a.todo == root.id
    thread = await ThreadStore(db).get(a.thread_id)
    assert thread.title == "Power"


async def test_a_todo_is_never_claimed_by_a_worker(manager):
    await split(manager, "a")
    assert await manager.tasks.claim("w", interactive=False) is None


async def test_idle_starts_one_todo_at_a_time_oldest_first(manager):
    _, (a, b) = await split(manager, "a", "b")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED
    assert await state(manager, b) is TaskState.TODO

    await manager.todos.tick()
    assert await state(manager, b) is TaskState.TODO

    await manager.tasks.set_state(a.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, b) is TaskState.QUEUED


async def test_nothing_starts_while_regular_work_runs(manager):
    _, (a,) = await split(manager, "a")
    other = await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.TODO

    await manager.tasks.set_state(other.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED


async def test_a_held_pause_and_a_waiting_question_do_not_count_as_running(manager):
    _, (a,) = await split(manager, "a")
    held = await manager.submit("held", title="Held")
    await manager.tasks.set_state(held.id, TaskState.PAUSED, held=True, started_at=1.0)
    asking = await manager.submit("asking", title="Asking")
    await manager.tasks.set_state(asking.id, TaskState.AWAITING_INPUT)
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED


async def test_a_splitting_run_is_not_regular_work(manager):
    _, (a,) = await split(manager, "a")
    await manager.submit("more", title="More", scope="todos", options={"todo": True})
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.QUEUED


async def test_regular_work_sends_a_todo_and_all_it_spawned_back(manager, db):
    await ThreadStore(db).create("Music", "algorithms")
    _, (a,) = await split(manager, "a")
    await manager.todos.tick()
    await manager.tasks.set_state(a.id, TaskState.RUNNING, session_id="s1")
    a = await manager.tasks.get(a.id)

    # It starts a task, which starts another: both are its family.
    child = await manager.graph.spawn(a, thread="Music", title="c", brief="c")
    grandchild = await manager.graph.spawn(child, thread="Music", title="g", brief="g")
    assert child.todo == a.todo and grandchild.todo == a.todo
    await manager.graph.park(a, [child.id])
    await manager.graph.park(child, [grandchild.id])
    await manager.tasks.set_state(grandchild.id, TaskState.RUNNING, session_id="s3")

    live = await manager.tasks.get(grandchild.id)
    live.runtime = asyncio.create_task(asyncio.sleep(60))
    manager.live[live.id] = live

    await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    for task in (a, child, grandchild):
        assert await state(manager, task) is TaskState.TODO
    assert live.runtime.cancelling() or live.runtime.cancelled()
    assert (await manager.tasks.get(grandchild.id)).resumed_from == "s3"
    assert (await manager.tasks.get(a.id)).waiting_on == [child.id]
    manager.live.clear()


async def test_a_family_resumes_where_it_was(manager, db):
    await ThreadStore(db).create("Music", "algorithms")
    _, (a,) = await split(manager, "a")
    await manager.todos.tick()
    await manager.tasks.set_state(a.id, TaskState.RUNNING, session_id="s1")
    child = await manager.graph.spawn(await manager.tasks.get(a.id), thread="Music", title="c", brief="c")
    await manager.graph.park(await manager.tasks.get(a.id), [child.id])

    regular = await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.TODO
    assert await state(manager, child) is TaskState.TODO

    await manager.tasks.set_state(regular.id, TaskState.SUCCEEDED, finished_at=1.0)
    await manager.todos.tick()
    # The parent goes back to waiting, and what it waits for runs.
    assert await state(manager, a) is TaskState.BLOCKED
    assert await state(manager, child) is TaskState.QUEUED


async def test_a_run_recording_its_pause_late_is_still_sent_back(manager):
    _, (a,) = await split(manager, "a")
    await manager.tasks.set_state(a.id, TaskState.PAUSED, started_at=1.0)
    assert await manager.tasks.paused(interactive=False) == []
    await manager.submit("regular", title="Regular")
    await manager.todos.tick()
    assert await state(manager, a) is TaskState.TODO


async def test_a_todo_can_start_tasks_and_a_plain_power_task_cannot(manager, db):
    await ThreadStore(db).create("Power", "algorithms")
    _, (a,) = await split(manager, "a")
    assert (await manager.graph.spawn_power(a, title="x", brief="y")).todo == a.todo
    plain = await manager.submit("p", title="P", scope="power")
    with pytest.raises(Refused, match="starts no tasks"):
        await manager.graph.spawn_power(plain, title="x", brief="y")


async def test_a_split_is_bounded_and_needs_briefs(manager):
    root = await manager.submit("r", title="R", scope="todos", options={"todo": True})
    with pytest.raises(Refused, match="no todos"):
        await manager.todos.add(root, [{"title": "x", "brief": " "}])
    with pytest.raises(Refused, match="most one request"):
        await manager.todos.add(root, [{"title": "x", "brief": "y"}] * 21)


async def test_the_status_file_follows_the_list(manager, config, db):
    await ThreadStore(db).create("Music", "algorithms")
    root, (a, b) = await split(manager, "a", "b")
    await manager.todos.tick()
    path = root.output_dir(config.assets_dir, config.default_project) / "todos.json"
    doc = json.loads(path.read_text())
    assert doc["request"] == "Things"
    assert [(t["title"], t["state"]) for t in doc["todos"]] == [("a", "queued"), ("b", "todo")]

    child = await manager.graph.spawn(a, thread="Music", title="c", brief="c")
    await manager.tasks.add_usage(a.id, datetime.now(UTC), "", 1.5, {})
    await manager.todos.tick()
    doc = json.loads(path.read_text())
    rows = {t["title"]: t for t in doc["todos"]}
    assert rows["c"]["spawnedBy"] == a.id
    assert rows["a"]["costUsd"] == 1.5

    before = path.stat().st_mtime_ns
    await manager.todos.tick()
    assert path.stat().st_mtime_ns == before  # nothing changed, so viewers aren't told it did


async def test_submitting_regular_work_wakes_the_loop(manager):
    await manager.submit("regular", title="Regular")
    assert manager.todos._wake.is_set()


def test_the_todo_state_is_not_terminal():
    assert not TaskState.TODO.terminal
    assert TaskState.TODO.waiting
    assert Task(problem="p", title="t", slug="t", scope="todos").is_todos
    assert Task(problem="p", title="t", slug="t", scope="todos").interactive
