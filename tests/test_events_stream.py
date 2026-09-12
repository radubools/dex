"""The event stream, including replay of a run that a previous process made."""

from __future__ import annotations

import asyncio
import json

import pytest

from dex.api import create_app
from dex.bus import EventBus
from dex.config import Config
from dex.models import Event


async def drive_sse(app, query: str, want_events: int) -> tuple[list[dict], asyncio.Task]:
    """Open /api/events, collect events, then hang up.

    Speaks ASGI directly: Starlette's TestClient cannot hang up on an endless
    stream, which is exactly what an event stream is.
    """
    hang_up = asyncio.Event()
    started = asyncio.Event()
    chunks: list[bytes] = []

    async def receive():
        if started.is_set():
            await hang_up.wait()
            return {"type": "http.disconnect"}
        started.set()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/api/events",
        "raw_path": b"/api/events", "query_string": query.encode(),
        "root_path": "", "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    runner = asyncio.create_task(app(scope, receive, send))

    events: list[dict] = []
    deadline = asyncio.get_running_loop().time() + 5
    while len(events) < want_events and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
        events = [
            json.loads(line[6:])
            for line in b"".join(chunks).decode().splitlines()
            if line.startswith("data: ")
        ]
    hang_up.set()
    return events, runner


@pytest.fixture
def app(config: Config, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    return create_app(config)


async def test_replays_history_and_stops_on_disconnect(app, db):
    async with app.router.lifespan_context(app):
        first = await app.state.tasks.submit("Two Sum", title="Two Sum")
        await app.state.tasks.submit("LRU Cache", title="LRU Cache")

        events, runner = await drive_sse(app, "after=0", want_events=2)

        assert [e["type"] for e in events][:1] == ["task_created"]
        assert events[0]["taskId"] == first.id
        # The disconnect must end the generator promptly, not at the next ping.
        await asyncio.wait_for(runner, timeout=3)


async def test_replays_a_run_recorded_by_a_previous_process(app, db, config):
    """A restart must not lose what already happened — the reported bug."""
    bus = EventBus(db)
    await bus.start()
    manager_app = create_app(config)
    async with manager_app.router.lifespan_context(manager_app):
        task = await manager_app.state.tasks.submit("Two Sum", title="Two Sum")
        manager_app.state.bus.publish(
            Event(type="text", task_id=task.id, data={"text": "written by the first process"})
        )
        await asyncio.sleep(0.3)
    await bus.stop()

    # A brand new app, sharing only the database.
    async with app.router.lifespan_context(app):
        events, runner = await drive_sse(app, f"task={task.id}", want_events=2)
        texts = [e.get("text") for e in events if e["type"] == "text"]
        assert "written by the first process" in texts
        await asyncio.wait_for(runner, timeout=3)


async def test_after_seq_resumes_without_replaying_delivered_events(app, db):
    async with app.router.lifespan_context(app):
        await app.state.tasks.submit("Two Sum", title="Two Sum")
        second = await app.state.tasks.submit("LRU Cache", title="LRU Cache")
        await asyncio.sleep(0.2)

        first_seq = (await app.state.bus.history())[0].seq
        events, runner = await drive_sse(app, f"after={first_seq}", want_events=1)

        assert all(e["seq"] > first_seq for e in events)
        assert second.id in {e["taskId"] for e in events}
        await asyncio.wait_for(runner, timeout=3)


async def test_a_task_keeps_its_activity_however_long_ago_it_ran(app, db):
    """The live stream replays only the most recent events across every task.

    After a busy day that window reaches back minutes, not days, so a task
    opened from an earlier session showed an empty Activity tab — not because
    its events were gone, but because nothing asked for them.
    """
    async with app.router.lifespan_context(app):
        task = await app.state.tasks.submit("Two Sum", title="Two Sum")
        # Another task's chatter, pushing the global replay window past this
        # one's events — which is exactly what a busy day does.
        noisy = await app.state.tasks.submit("LRU Cache", title="LRU Cache")
        for n in range(50):
            app.state.bus.publish(Event(type="text", task_id=noisy.id, data={"text": str(n)}))
        await asyncio.sleep(0.2)  # the bus writes in the background

        recent = await app.state.bus.history(after_seq=0, limit=10)
        assert all(e.task_id != task.id for e in recent), "the window has moved past it"

        events = await app.state.bus.history(task_id=task.id, after_seq=0)

        assert len(events) > 0
        assert all(e.task_id == task.id for e in events)
        assert [e.seq for e in events] == sorted(e.seq for e in events)


async def test_a_restarted_task_keeps_what_the_earlier_run_did(app, db):
    """Restarting reuses the row, so the previous run's activity is still its
    own — which is the point: you restart to see what went wrong and try again."""
    async with app.router.lifespan_context(app):
        task = await app.state.tasks.submit("Two Sum", title="Two Sum")
        before = len(await app.state.bus.history(task_id=task.id, after_seq=0))

        await app.state.tasks.restart(task.id)

        assert len(await app.state.bus.history(task_id=task.id, after_seq=0)) >= before
