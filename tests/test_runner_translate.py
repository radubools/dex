"""The runner's message translation.

These paths only ever ran against a live agent, so a missing import or a bad
attribute stayed invisible until a real task hit it. The fake agent emits
events directly and never produces an AssistantMessage, so it does not cover
them either — hence this.
"""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from dex.bus import EventBus
from dex.models import Task
from dex.runner import TaskRunner
from dex.store import SettingsStore, TaskStore


@pytest.fixture
async def runner(config, db):
    bus = EventBus(db)
    await bus.start()
    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="t")
    task.model = "claude-opus-5"
    await tasks.create(task)
    runner = TaskRunner(task, config, bus, tasks, SettingsStore(db))
    try:
        yield runner, bus
    finally:
        await bus.stop()


def usage(**fields):
    return {"input_tokens": 0, "output_tokens": 0, **fields}


async def test_assistant_usage_accrues_a_cost_estimate(runner, db):
    run, bus = runner
    message = AssistantMessage(
        content=[TextBlock(text="hello")],
        model="claude-opus-5",
        usage=usage(output_tokens=1_000_000),
    )
    run._translate(message)
    await asyncio.sleep(0.3)

    # 1M output tokens on Opus 5 is $25 at list price.
    assert run.task.cost_usd == pytest.approx(25.0)
    assert run.task.cost_is_estimate is True

    events = [e for e in await bus.history() if e.type == "cost"]
    assert events and events[-1].data["estimate"] is True


async def test_usage_accumulates_across_messages(runner):
    run, _ = runner
    for _ in range(3):
        run._translate(
            AssistantMessage(content=[], model="claude-opus-5", usage=usage(output_tokens=100_000))
        )
    await asyncio.sleep(0.2)
    assert run.task.cost_usd == pytest.approx(7.5)


async def test_a_result_replaces_the_estimate_with_the_reported_total(runner, db):
    run, bus = runner
    run._translate(
        AssistantMessage(content=[], model="claude-opus-5", usage=usage(output_tokens=1_000_000))
    )
    run._translate(
        ResultMessage(
            subtype="success", duration_ms=1000, duration_api_ms=900, is_error=False,
            num_turns=4, session_id="s", total_cost_usd=1.25, result="done",
        )
    )
    await asyncio.sleep(0.3)

    assert run.task.cost_usd == pytest.approx(1.25)
    assert run.task.cost_is_estimate is False
    stored = await TaskStore(db).get(run.task.id)
    assert stored.cost_usd == pytest.approx(1.25)


async def test_messages_without_usage_are_harmless(runner):
    run, _ = runner
    run._translate(AssistantMessage(content=[], model="claude-opus-5", usage=None))
    await asyncio.sleep(0.1)
    assert run.task.cost_usd in (None, 0.0)


async def test_tool_use_emits_a_tool_event_carrying_the_activity(runner):
    run, bus = runner
    run._translate(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Bash", input={"description": "run tests"})],
            model="claude-opus-5",
            usage=None,
        )
    )
    await asyncio.sleep(0.2)

    tools = [e for e in await bus.history() if e.type == "tool"]
    assert tools[-1].data["activity"] == "run tests"
    assert run.task.activity == "run tests"


def stream(run, uuid, event):
    run._translate(StreamEvent(uuid=uuid, session_id="s", event=event))


async def test_thinking_that_never_streamed_is_still_shown(runner):
    """A high-effort run spends minutes here, and it was all being dropped.

    The CLI omits thinking from the stream unless a display is asked for, so
    `_translate_delta` saw no deltas and the AssistantMessage handler skipped
    the finished block as a duplicate. The result was a task that looked idle
    for two minutes with nothing in the panel.
    """
    run, bus = runner
    run._translate(
        AssistantMessage(
            content=[ThinkingBlock(thinking="weighing the approach", signature="x")],
            model="claude-opus-5",
            usage=None,
        )
    )
    await asyncio.sleep(0.2)

    thinking = [e for e in await bus.history() if e.type == "thinking"]
    assert [e.data["text"] for e in thinking] == ["weighing the approach"]


async def test_streamed_thinking_is_not_repeated_as_a_whole_block(runner):
    """When deltas do arrive, the finished block must not double them up."""
    run, bus = runner
    stream(run, "u1", {"type": "content_block_start", "index": 0,
                       "content_block": {"type": "thinking"}})
    for piece in ("weighing ", "the ", "approach"):
        stream(run, "u1", {"type": "content_block_delta", "index": 0,
                           "delta": {"type": "thinking_delta", "thinking": piece}})
    stream(run, "u1", {"type": "content_block_stop", "index": 0})
    run._translate(
        AssistantMessage(
            content=[ThinkingBlock(thinking="weighing the approach", signature="x")],
            model="claude-opus-5",
            usage=None,
        )
    )
    await asyncio.sleep(0.2)

    history = await bus.history()
    deltas = [e for e in history if e.type == "thinking_delta"]
    assert "".join(e.data["delta"] for e in deltas) == "weighing the approach"
    # The completed block adds nothing: the UI already has every word.
    assert [e for e in history if e.type == "thinking"] == []


async def test_text_blocks_are_still_skipped_after_the_thinking_change(runner):
    """Splitting the isinstance check must not start duplicating text."""
    run, bus = runner
    run._translate(
        AssistantMessage(content=[TextBlock(text="hello")], model="claude-opus-5", usage=None)
    )
    await asyncio.sleep(0.2)
    assert [e for e in await bus.history() if e.type == "text"] == []


def test_the_agent_is_asked_for_visible_thinking():
    """Nothing reaches the UI unless the CLI is told to emit thinking at all."""
    import inspect

    import dex.runner

    source = inspect.getsource(dex.runner.TaskRunner.run)
    assert '"display": "summarized"' in source
    assert '"type": "adaptive"' in source


async def test_a_pricing_failure_costs_only_the_estimate(runner, monkeypatch):
    """Reporting must not take the run with it — the `pricing` NameError case."""
    from dex import pricing

    def explode(*_a, **_k):
        raise RuntimeError("pricing is broken")

    monkeypatch.setattr(pricing, "estimate", explode)
    run, bus = runner

    run._observe(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Bash", input={"description": "run tests"})],
            model="claude-opus-5",
            usage=usage(output_tokens=1_000),
        )
    )
    await asyncio.sleep(0.2)

    # The tool event still arrives; only the estimate is missing.
    tools = [e for e in await bus.history() if e.type == "tool"]
    assert tools and tools[-1].data["activity"] == "run tests"
    assert run.task.cost_usd in (None, 0.0)
    assert [e for e in await bus.history() if e.type == "cost"] == []
    assert run.task.error is None


async def test_an_unreadable_message_does_not_end_the_run(runner):
    run, bus = runner

    class HostileContent(list):
        """Passes isinstance checks, then raises the moment it is read."""

        def __iter__(self):
            raise ValueError("nope")

    # A real AssistantMessage, so translation enters the branch that reads it.
    run._observe(
        AssistantMessage(content=HostileContent(), model="claude-opus-5", usage=None)
    )
    await asyncio.sleep(0.2)

    errors = [e for e in await bus.history() if e.type == "error"]
    assert errors, "the operator should be told something was dropped"
    # Non-fatal: the task is not marked failed.
    assert all(e.data.get("fatal") is False for e in errors)
    assert run.task.error is None

    # And the runner keeps working afterwards.
    run._observe(
        AssistantMessage(
            content=[ToolUseBlock(id="t2", name="Read", input={"file_path": "/a/b.py"})],
            model="claude-opus-5",
            usage=None,
        )
    )
    await asyncio.sleep(0.2)
    assert [e for e in await bus.history() if e.type == "tool"]


async def test_a_diff_failure_does_not_block_the_edit(runner, monkeypatch):
    run, _ = runner
    monkeypatch.setattr(
        "dex.runner.preview_change", lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom"))
    )

    async def allow(_tool, data, _ctx):
        return "allowed"

    decide = run._with_diffs(allow)
    context = type("Ctx", (), {"tool_use_id": "t1", "title": None, "description": None})()
    # The edit still goes through even though its diff could not be built.
    assert await decide("Write", {"file_path": "/tmp/x.py", "content": "x"}, context) == "allowed"
