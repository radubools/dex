"""A limit the CLI refuses to spend against stops the run, it does not kill it.

Before this, a spent limit failed every task handed to it: fifty-five tasks in
seventy seconds on 27 Oct, each one rejected within a second of starting,
because the queue kept dispatching while the CLI refused the calls.
"""

from __future__ import annotations

import asyncio

from claude_agent_sdk import ResultMessage, SystemMessage

from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.runner import TaskRunner, is_usage_limit_error, limit_pause_note
from dex.store import SettingsStore, TaskStore

LIMIT_TEXT = "You've hit your weekly limit · resets Oct 29 at 5pm (Europe/Bucharest)"


class LimitedClient:
    """An agent whose first act is refused: the limit came back, not a turn."""

    def __init__(self, options):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        return None

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={"session_id": "sess-abc"})
        yield ResultMessage(
            subtype="result", duration_ms=600, duration_api_ms=600,
            is_error=True, num_turns=1, session_id="sess-abc", result=LIMIT_TEXT,
        )


class RefusedAtStart:
    """The CLI's raw path: the refusal arrives raised, not yielded."""

    def __init__(self, options):
        self.options = options

    async def __aenter__(self):
        raise RuntimeError(f"ResultError: Claude Code returned an error result: {LIMIT_TEXT}")

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        return None

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={"session_id": "sess-abc"})


def package_task(**fields) -> Task:
    fields.setdefault("slug", "section-one")
    task = Task(problem="Translate §1", title="§1", **fields)
    task.project = "algorithms"
    return task


async def run_with(config, db, task, client, monkeypatch) -> tuple[Task, TaskRunner, list[bool]]:
    bus = EventBus(db)
    await bus.start()
    store = TaskStore(db)
    recheck: list[bool] = []
    try:
        monkeypatch.setattr("dex.runner.ClaudeSDKClient", client, raising=False)
        await store.create(task)
        runner = TaskRunner(task, config, bus, store, SettingsStore(db))

        async def redecide() -> bool:
            recheck.append(True)
            return True

        runner.on_limit_reading = redecide
        await runner.run()
        # The pause is persisted from a created task rather than awaited, so
        # the row's write can still be in flight. Poll briefly rather than
        # assuming the clock: the suite runs against a tunnel sleepy enough
        # that one fixed wait would be a guess.
        after = await store.get(task.id)
        for _ in range(80):
            if after.state is TaskState.PAUSED and recheck:
                break
            await asyncio.sleep(0.05)
            after = await store.get(task.id)
    finally:
        await bus.stop()
    return after, runner, recheck


async def test_a_limit_rejection_pauses_instead_of_failing(config, db, monkeypatch):
    settings = SettingsStore(db)
    task, _, _ = await run_with(config, db, package_task(), LimitedClient, monkeypatch)

    assert task.state is TaskState.PAUSED
    assert task.error is None
    # The session is kept, so the run that follows continues the conversation
    # rather than starting the work again.
    assert task.resumed_from == "sess-abc"
    assert task.finished_at is None
    assert "resets Oct 29" in (task.activity or "")
    # This refusal came back as text alone — no RateLimitEvent — so dex held
    # on a conservative reading of its own; the next real one replaces it.
    snapshot = await settings.limit_snapshot()
    assert snapshot["seven_day"]["status"] == "rejected"


async def test_a_refused_run_asks_for_the_hold_to_be_re_decided(config, db, monkeypatch):
    _, _, recheck = await run_with(config, db, package_task(), LimitedClient, monkeypatch)

    assert recheck == [True]


async def test_a_refusal_that_arrives_raised_pauses_too(config, db, monkeypatch):
    task, _, _ = await run_with(config, db, package_task(), RefusedAtStart, monkeypatch)

    assert task.state is TaskState.PAUSED
    assert task.error is None


async def test_a_rejection_reading_re_decides_the_hold_at_once(config, db):
    """The reading a refused call carries decides the hold now, not in five."""
    bus = EventBus(db)
    await bus.start()
    tasks = TaskStore(db)
    settings = SettingsStore(db)
    recheck: list[bool] = []
    try:
        task = package_task()
        await tasks.create(task)
        runner = TaskRunner(task, config, bus, tasks, settings)

        async def redecide() -> bool:
            recheck.append(True)
            return True

        runner.on_limit_reading = redecide

        # The route the RateLimitEvent handler takes: recorded off the stream,
        # then acted on with a fresh check while the run is in flight.
        await runner._record_limit(
            {"rateLimitType": "seven_day", "status": "rejected",
             "utilization": 1.0, "resetsAt": 0})
        assert recheck == [True]

        # A warning rides along harmlessly: the watcher's five-minute pass is
        # soon enough to act on those.
        await runner._record_limit(
            {"rateLimitType": "five_hour", "status": "allowed_warning",
             "resetsAt": 0})
        assert recheck == [True]
        snapshot = await settings.limit_snapshot()
        assert snapshot["five_hour"]["status"] == "allowed_warning"
    finally:
        await bus.stop()


def test_a_limit_error_is_recognised():
    assert is_usage_limit_error(LIMIT_TEXT)
    assert is_usage_limit_error("You've hit your 5-hour limit · resets 4:02pm")
    assert is_usage_limit_error("API error 429: rate limit exceeded")
    assert is_usage_limit_error(
        "ResultError: Claude Code returned an error result: " + LIMIT_TEXT + " (exit code: 1)")
    # A run that merely talks about limits is not being held by one.
    assert not is_usage_limit_error("Read the limit field of the manifest")


def test_the_pause_note_carries_the_window():
    note = limit_pause_note(LIMIT_TEXT)
    assert "paused" in note
    assert "resets Oct 29" in note
