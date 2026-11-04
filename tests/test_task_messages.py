"""Follow-up notes queued against a task."""

from __future__ import annotations

import asyncio

import pytest

from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import TaskMessageStore


@pytest.fixture
async def manager(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        yield manager
    finally:
        await bus.stop()


async def test_a_note_is_held_while_the_task_runs(manager, db):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)
    await manager.tasks.set_state(task.id, TaskState.RUNNING)

    queued = await manager.queue_message(task.id, "also handle duplicates")
    assert queued is not None

    # Nothing starts while the task is still going.
    assert await manager.tasks.list() == [await manager.tasks.get(task.id)]
    assert [m["body"] for m in await TaskMessageStore(db).pending(task.id)] == [
        "also handle duplicates"
    ]


async def test_send_hands_a_message_to_any_running_task_at_once(manager, db):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)
    task.inbox = asyncio.Queue()
    manager.live[task.id] = task

    message = await manager.send(task.id, "also handle duplicates")

    assert message["delivered"] is True
    assert task.inbox.get_nowait() == "also handle duplicates"
    assert await TaskMessageStore(db).pending(task.id) == []


async def test_send_to_a_paused_task_continues_it_now(manager, db):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)
    await manager.tasks.set_state(task.id, TaskState.PAUSED)

    await manager.send(task.id, "also handle duplicates")

    assert (await manager.tasks.get(task.id)).state is TaskState.CANCELLED
    follow_up = next(t for t in await manager.tasks.list() if t.parent_id == task.id)
    assert "also handle duplicates" in follow_up.problem


async def test_send_holds_a_message_for_a_task_with_no_run_yet(manager, db):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)

    await manager.send(task.id, "also handle duplicates")

    assert [m["body"] for m in await TaskMessageStore(db).pending(task.id)] == ["also handle duplicates"]


async def test_a_run_takes_what_was_held_for_it_when_it_starts(config, db):
    from claude_agent_sdk import ResultMessage

    from dex.runner import TaskRunner, chat_interjection

    class Client:
        def __init__(self):
            self.sent = []
            self.results = asyncio.Queue()

        def finish_turn(self):
            self.results.put_nowait(ResultMessage(subtype="result", duration_ms=1, duration_api_ms=1,
                                                  is_error=False, num_turns=1, session_id="s"))

        async def interrupt(self):
            self.finish_turn()

        async def query(self, text):
            self.sent.append(text)
            if len(self.sent) > 1:  # the opening turn is under way until it is interrupted
                self.finish_turn()

        async def receive_messages(self):
            while True:
                yield await self.results.get()

    bus = EventBus(db)
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    runner = TaskRunner(task, config, bus, TaskManager(config, bus, db).tasks, None)
    await runner.store.create(task)
    await TaskMessageStore(db).add(task.id, "one", {"e2e": False})
    await TaskMessageStore(db).add(task.id, "two")
    runner._observe = lambda message: None
    client = Client()
    await client.query("opening")

    await asyncio.wait_for(runner._converse(client), timeout=3)

    assert client.sent == ["opening", chat_interjection("one\n\ntwo", chat=False)]
    assert task.options["e2e"] is False
    assert await TaskMessageStore(db).pending(task.id) == []


async def test_delivery_starts_a_follow_up_attempt_when_the_task_stops(manager, db):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)
    await manager.tasks.set_state(task.id, TaskState.RUNNING)
    await manager.queue_message(task.id, "also handle duplicates")
    await manager.queue_message(task.id, "and add a benchmark")

    await manager.tasks.set_state(task.id, TaskState.SUCCEEDED)
    started = await manager.deliver_messages(task.id)

    assert started is not None
    assert started.attempt == 2
    assert started.parent_id == task.id
    # The follow-up continues the same package.
    assert started.output_slug == "two-sum"
    assert "also handle duplicates" in started.problem
    assert "and add a benchmark" in started.problem
    # Delivered notes are not delivered twice.
    assert await TaskMessageStore(db).pending(task.id) == []
    assert await manager.deliver_messages(task.id) is None


async def test_a_note_on_an_already_finished_task_is_delivered_at_once(manager, db):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)
    await manager.tasks.set_state(task.id, TaskState.SUCCEEDED)

    await manager.queue_message(task.id, "add an animation")
    await asyncio.sleep(0)

    follow_ups = [t for t in await manager.tasks.list() if t.attempt == 2]
    assert len(follow_ups) == 1
    assert "add an animation" in follow_ups[0].problem


async def test_messages_for_an_unknown_task_are_refused(manager):
    assert await manager.queue_message("nope", "hello") is None
