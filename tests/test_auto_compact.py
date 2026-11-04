"""A follow-up to a task that succeeded can start from a compacted session
(the Auto-compact setting): `/compact` goes to the resumed session before
the follow-up's brief, and only then."""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.runner import COMPACT, TaskRunner
from dex.store import SettingsStore, TaskStore


def recording(compact_fails: bool = False):
    queries: list[str] = []

    class Client:
        def __init__(self, options):
            self.options = options
            self.turns: asyncio.Queue = asyncio.Queue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            queries.append(prompt)
            if prompt == COMPACT and not compact_fails:
                self.turns.put_nowait(SystemMessage(subtype="compact_boundary", data={}))
            failed = prompt == COMPACT and compact_fails
            self.turns.put_nowait(ResultMessage(
                subtype="error" if failed else "success", duration_ms=10, duration_api_ms=10,
                is_error=failed, num_turns=1, session_id="sess-1",
                result="no room" if failed else "done"))

        async def interrupt(self):
            return None

        async def receive_messages(self):
            yield SystemMessage(subtype="init", data={"session_id": "sess-1"})
            while True:
                message = await self.turns.get()
                yield message
                if isinstance(message, ResultMessage):
                    return

        receive_response = receive_messages

    return Client, queries


async def follow_up(config, db, monkeypatch, *, on=True, parent_state=TaskState.SUCCEEDED,
                    model=None, compact_fails=False):
    client, queries = recording(compact_fails)
    monkeypatch.setattr("dex.runner.ClaudeSDKClient", client, raising=False)
    monkeypatch.setattr(TaskRunner, "_resumable", lambda self, model, session: True)
    monkeypatch.setattr(TaskRunner, "_client", lambda self, options: client(options))
    bus = EventBus(db)
    await bus.start()
    store = TaskStore(db)
    settings = SettingsStore(db)
    await settings.set(SettingsStore.AUTO_COMPACT, on)
    parent = Task(problem="Say done", title="Done", slug="say-done-1")
    parent.project = "algorithms"
    parent.session_id = "sess-1"
    await store.create(parent)
    await store.set_state(parent.id, parent_state)
    task = Task(problem="Say it again", title="Done", slug="say-done-2",
                attempt=2, parent_id=parent.id, resumed_from="sess-1")
    task.project = "algorithms"
    if model:
        task.model = model
    try:
        await store.create(task)
        await TaskRunner(task, config, bus, store, settings).run()
    finally:
        await bus.stop()
    return task, queries


async def test_a_follow_up_to_a_success_compacts_first(config, db, monkeypatch):
    _, queries = await follow_up(config, db, monkeypatch)
    assert queries[0] == COMPACT and len(queries) >= 2


async def test_a_failed_compaction_carries_on_with_the_whole_session(config, db, monkeypatch):
    task, queries = await follow_up(config, db, monkeypatch, compact_fails=True)
    assert queries[0] == COMPACT and len(queries) >= 2
    # The brief still went in; whatever ends the run, it is not the compaction.
    assert "no room" not in (task.error or "")


@pytest.mark.parametrize("case", [
    {"on": False},
    {"parent_state": TaskState.FAILED},
    {"model": "oc:qwen3.8-flash"},
])
async def test_otherwise_the_session_is_resumed_whole(config, db, monkeypatch, case):
    _, queries = await follow_up(config, db, monkeypatch, **case)
    assert COMPACT not in queries
