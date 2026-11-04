"""A turn that failed on the connection to the model is tried again, for
every provider, rather than failing the task: on 3 Nov a chat on OpenCode
failed outright on "Upstream response was not valid JSON"."""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from dex import runner as runner_module
from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.runner import CARRY_ON, TaskRunner
from dex.store import SettingsStore, TaskStore

UPSTREAM = "Upstream request failed: [api_error] Upstream response was not valid JSON"


def scripted(failures: int, error: str = UPSTREAM):
    """An agent whose turns fail with `error` `failures` times, then succeed."""
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
            failed = len(queries) <= failures
            self.turns.put_nowait(ResultMessage(
                subtype="error" if failed else "success", duration_ms=10, duration_api_ms=10,
                is_error=failed, num_turns=1, session_id="ses_1", result=error if failed else "done"))

        async def interrupt(self):
            return None

        async def receive_messages(self):
            yield SystemMessage(subtype="init", data={"session_id": "ses_1"})
            while True:
                yield await self.turns.get()

        receive_response = receive_messages

    return Client, queries


async def run_with(config, db, client, monkeypatch) -> Task:
    monkeypatch.setattr(runner_module, "RETRY_BASE_S", 0.001)
    monkeypatch.setattr("dex.runner.ClaudeSDKClient", client, raising=False)
    bus = EventBus(db)
    await bus.start()
    store = TaskStore(db)
    task = Task(problem="Say done", title="Done", slug="say-done", scope="chat")
    task.project = "algorithms"
    try:
        await store.create(task)
        await TaskRunner(task, config, bus, store, SettingsStore(db)).run()
    finally:
        await bus.stop()
    return task


async def test_a_connection_failure_is_tried_again_on_the_same_session(config, db, monkeypatch):
    client, queries = scripted(failures=2)
    task = await run_with(config, db, client, monkeypatch)
    assert queries[1:] == [CARRY_ON, CARRY_ON]
    assert UPSTREAM not in (task.error or "")


async def test_it_gives_up_after_five_tries(config, db, monkeypatch):
    client, queries = scripted(failures=99)
    task = await run_with(config, db, client, monkeypatch)
    assert len(queries) == 6
    assert task.state is TaskState.FAILED and UPSTREAM in (task.error or "")


@pytest.mark.parametrize("error", ["API Error: 529 overloaded", "OpenCode exited (1)", "Rate limit reached"])
async def test_other_failures_are_not_retried(config, db, monkeypatch, error):
    client, queries = scripted(failures=99, error=error)
    task = await run_with(config, db, client, monkeypatch)
    # Failed, or paused for a limit as before: never tried again here.
    assert len(queries) == 1 and task.state in (TaskState.FAILED, TaskState.PAUSED)
