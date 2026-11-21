"""Input and output tokens per second, for every agent dex runs.

The meter sees the same three things from Claude, Codex, OpenCode and
Antigravity — a boundary (new input), a token (output), a usage — but in
different orders: Claude reports a message's usage before its tool runs,
OpenCode a step's after. These pin both orders, the cases that leave a side
untimed, the runner's wiring, the rollup, and the backfill's arithmetic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from dex import throughput
from dex.bus import EventBus
from dex.models import Task
from dex.runner import TaskRunner
from dex.store import CostStore, SettingsStore, TaskStore
from dex.throughput import Meter

ROOT = Path(__file__).resolve().parents[1]


def _backfill():
    spec = importlib.util.spec_from_file_location("backfill_throughput", ROOT / "scripts" / "backfill_throughput.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def total(changes):
    sums = dict.fromkeys(throughput.KEYS, 0.0)
    for change in changes:
        for k, v in (change or {}).items():
            sums[k] += v
    return sums


# ------------------------------------------------------------------ the meter


def test_a_claude_message_reads_then_writes():
    """Usage per block, mid-call, then the final count at the message's end."""
    meter = Meter()
    meter.boundary(100.0)                 # a tool's result came back
    meter.token(102.0)                    # its first block begins
    meter.stream()                        # ...and streamed
    changes = [meter.usage(103.0, in_tokens=1000, out_tokens=5, message_id="m1")]
    meter.token(106.0)
    changes.append(meter.usage(106.0, out_tokens=395, message_id="m1", ends=True))
    sums = total(changes)
    assert sums["in_tokens"] == 1000 and sums["in_seconds"] == pytest.approx(2.0)
    assert sums["out_tokens"] == 400 and sums["out_seconds"] == pytest.approx(4.0)
    assert throughput.rate(sums["in_tokens"], sums["in_seconds"]) == pytest.approx(500)
    assert throughput.rate(sums["out_tokens"], sums["out_seconds"]) == pytest.approx(100)


def test_an_opencode_step_reports_after_its_tool_ran():
    """The step's usage comes after its tool's result: it is still that step's."""
    meter = Meter()
    meter.boundary(0.0)
    meter.token(1.0); meter.token(3.0); meter.stream()
    meter.boundary(50.0)                  # the tool took 47 s: not the model's time
    first = meter.usage(50.1, in_tokens=200, out_tokens=60, ends=True)
    assert first["in_seconds"] == pytest.approx(1.0) and first["out_seconds"] == pytest.approx(2.0)
    # The next step starts at the tool's result, not at the late usage.
    meter.token(51.0); meter.token(52.0); meter.stream()
    second = meter.usage(52.0, in_tokens=100, out_tokens=10, ends=True)
    assert second["in_seconds"] == pytest.approx(1.0)
    assert second["out_seconds"] == pytest.approx(1.0)


def test_steps_with_no_tool_are_timed_one_by_one():
    """A step that ends with usage alone is followed by the next, no boundary between."""
    meter = Meter()
    meter.boundary(0.0)
    for start in (0.0, 10.0, 20.0):
        meter.token(start + 1); meter.token(start + 3); meter.stream()
        change = meter.usage(start + 10, in_tokens=10, out_tokens=20, ends=True)
        assert change["out_seconds"] == pytest.approx(2.0)
    assert len(meter.calls) == 3


def test_a_turns_first_call_has_no_prefill():
    """It also waited for the agent's process to start."""
    meter = Meter()
    meter.boundary(0.0, cold=True)
    meter.token(8.0); meter.token(10.0); meter.stream()
    change = meter.usage(10.0, in_tokens=5000, out_tokens=100)
    assert change["in_tokens"] == 0 and change["in_seconds"] == 0
    assert change["out_tokens"] == 100 and change["out_seconds"] == pytest.approx(2.0)


def test_a_call_with_no_token_seen_records_nothing():
    meter = Meter()
    meter.boundary(0.0)
    assert meter.usage(5.0, in_tokens=100, out_tokens=100) is None


def test_whole_parts_time_the_whole_answer_and_no_reading():
    """No token marks where a whole part's decode began."""
    meter = Meter()
    meter.boundary(0.0)
    meter.token(4.0)                      # the part, written and handed over at once
    change = meter.usage(4.0, in_tokens=300, out_tokens=80, ends=True)
    assert change["in_tokens"] == 0
    assert change["out_tokens"] == 80 and change["out_seconds"] == pytest.approx(4.0)


def test_parallel_tool_results_start_the_call_at_the_last():
    meter = Meter()
    meter.boundary(0.0); meter.boundary(5.0)
    meter.token(6.0); meter.token(7.0); meter.stream()
    change = meter.usage(7.0, in_tokens=10, out_tokens=10)
    assert change["in_seconds"] == pytest.approx(1.0)


def test_a_failed_call_does_not_take_the_next_turns_usage():
    meter = Meter()
    meter.boundary(0.0)
    meter.token(1.0); meter.token(2.0); meter.stream()
    meter.boundary(3.0)                   # its tool failed; it never got a usage
    meter.boundary(100.0, cold=True)      # a new turn
    meter.token(101.0); meter.token(105.0); meter.stream()
    change = meter.usage(105.0, out_tokens=40, ends=True)
    assert change["out_seconds"] == pytest.approx(4.0)


def test_subagents_are_timed_apart():
    meter = Meter()
    meter.boundary(0.0, lane=None)
    meter.boundary(0.0, lane="task-1")
    meter.token(1.0, lane="task-1"); meter.token(9.0, lane="task-1"); meter.stream(lane="task-1")
    meter.token(2.0); meter.token(3.0); meter.stream()
    main = meter.usage(3.0, out_tokens=10)
    sub = meter.usage(9.0, out_tokens=80, lane="task-1")
    assert main["out_seconds"] == pytest.approx(1.0)
    assert sub["out_seconds"] == pytest.approx(8.0)


def test_usage_repeated_without_change_records_nothing_more():
    meter = Meter()
    meter.boundary(0.0)
    meter.token(1.0); meter.token(2.0); meter.stream()
    assert meter.usage(2.0, out_tokens=10, message_id="m")
    assert meter.usage(2.0, out_tokens=0, message_id="m") is None


# ------------------------------------------------------------ the runner


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


def stream(kind, **fields):
    return StreamEvent(uuid="u", session_id="s", event={"type": kind, **fields})


async def test_the_runner_times_a_streamed_claude_message(runner, db, monkeypatch):
    run, bus = runner
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    run._meter.boundary(1000.0)           # as a tool's result would
    clock[0] = 1002.0
    run._translate(stream("message_start", message={"id": "msg_1"}))
    run._translate(stream("content_block_start", index=0, content_block={"type": "text"}))
    clock[0] = 1003.0
    run._translate(stream("content_block_delta", index=0, delta={"type": "text_delta", "text": "hi"}))
    clock[0] = 1006.0
    run._translate(stream("content_block_stop", index=0))
    run._translate(AssistantMessage(
        content=[TextBlock(text="hi")], model="claude-opus-5", message_id="msg_1",
        usage={"input_tokens": 10, "cache_creation_input_tokens": 990, "output_tokens": 3},
    ))
    # The final output count comes only with the message's end.
    run._translate(stream("message_delta", delta={}, usage={"output_tokens": 400}))
    await asyncio.sleep(0.3)

    row = await db.pool.fetchrow("SELECT * FROM task_throughput WHERE task_id = $1", run.task.id)
    assert row["in_tokens"] == 1000 and row["in_seconds"] == pytest.approx(2.0)
    assert row["out_tokens"] == 400 and row["out_seconds"] == pytest.approx(4.0)
    # ...and the spend rollup now has the message's whole output, not its first block's.
    out = await db.pool.fetchval("SELECT sum(output_tokens) FROM task_usage WHERE task_id = $1", run.task.id)
    assert out == 400

    marks = [e.data["throughput"] for e in await bus.history() if e.type == "cost" and "throughput" in e.data]
    assert sum(m["out"] for m in marks) == 400
    assert sum(m["outS"] for m in marks) == pytest.approx(4.0)


async def test_the_runner_times_an_opencode_step_reported_after_its_tool(runner, db, monkeypatch):
    run, _ = runner
    run.task.model = "oc:local/qwen"
    clock = [0.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    run._meter.boundary(0.0)
    clock[0] = 1.0
    run._translate(stream("content_block_start", index=1, content_block={"type": "thinking"}))
    run._translate(stream("content_block_delta", index=1, delta={"type": "thinking_delta", "thinking": "hm"}))
    clock[0] = 3.0
    run._translate(stream("content_block_stop", index=1))
    run._translate(AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={})], model="oc:local/qwen"))
    clock[0] = 30.0
    run._translate(UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)]))
    clock[0] = 30.1
    run._translate(AssistantMessage(content=[], model="oc:local/qwen", message_id="oc-1",
                                    usage={"input_tokens": 100, "output_tokens": 50}))
    await asyncio.sleep(0.3)

    row = await db.pool.fetchrow("SELECT * FROM task_throughput WHERE task_id = $1", run.task.id)
    assert row["in_seconds"] == pytest.approx(1.0) and row["in_tokens"] == 100
    assert row["out_seconds"] == pytest.approx(2.0) and row["out_tokens"] == 50


# ------------------------------------------------------------ the rollup

HOUR = datetime(2026, 10, 1, 12, tzinfo=UTC)


async def test_the_dashboard_series_carries_each_buckets_sums(db):
    tasks = TaskStore(db)
    for slug, model, sums in (
        ("a", "claude-opus-5", {"in_tokens": 1000, "in_seconds": 2, "out_tokens": 300, "out_seconds": 3}),
        ("b", "claude-opus-5", {"in_tokens": 500, "in_seconds": 3, "out_tokens": 100, "out_seconds": 1}),
        ("c", "oc:local/qwen", {"in_tokens": 50, "in_seconds": 1, "out_tokens": 20, "out_seconds": 2}),
    ):
        task = Task(problem="p", title=slug, slug=slug)
        task.model = model
        await tasks.create(task)
        await tasks.add_usage(task.id, HOUR, "", 1.0, {"output_tokens": 1})
        await tasks.add_throughput(task.id, HOUR, "", sums)

    series = await CostStore(db).series("all", "day", "model")
    by = {p["series"]: p for p in series}
    opus = by["claude-opus-5"]
    # Tokens over seconds for the bucket, never an average of the rates.
    assert (opus["timedIn"], opus["inSeconds"]) == (1500, 5.0)
    assert (opus["timedOut"], opus["outSeconds"]) == (400, 4.0)
    assert by["oc:local/qwen"]["timedOut"] == 20


async def test_an_untimed_bucket_has_zero_sums(db):
    tasks = TaskStore(db)
    task = Task(problem="p", title="x", slug="x")
    await tasks.create(task)
    await tasks.add_usage(task.id, HOUR, "", 1.0, {"output_tokens": 1})
    (point,) = await CostStore(db).series("all", "day", "model")
    assert point["timedIn"] == 0 and point["outSeconds"] == 0


async def test_throughput_moves_with_the_spend_to_the_first_skill(db):
    tasks = TaskStore(db)
    task = Task(problem="p", title="x", slug="x")
    await tasks.create(task)
    await tasks.add_usage(task.id, HOUR, "", 1.0, {"output_tokens": 1})
    await tasks.add_throughput(task.id, HOUR, "", {"out_tokens": 10, "out_seconds": 1})
    await tasks.name_usage_skill(task.id, "game-mesh")
    rows = await db.pool.fetch("SELECT skill, out_tokens FROM task_throughput WHERE task_id = $1", task.id)
    assert [(r["skill"], r["out_tokens"]) for r in rows] == [("game-mesh", 10)]


# ------------------------------------------------------------ the backfill


def ev(kind, ts, **data):
    return {"type": kind, "ts": ts, "data": data}


def test_the_backfill_times_calls_from_stored_events():
    b = _backfill()
    events = [
        ev("task_state", 0.0, state="running"),
        ev("thinking_delta", 5.0, id="oc-s-1:1", delta="..."),
        ev("block_end", 7.0, id="oc-s-1:1"),
        ev("tool", 7.1, id="t1", name="Read"),
        ev("tool_result", 9.0, id="t1", ok=True),
        ev("cost", 9.1, costUsd=0.1, estimate=True, contextTokens=10_000),
        ev("text_delta", 10.0, id="oc-s-1:2", delta="..."),
        ev("block_end", 12.0, id="oc-s-1:2"),
        ev("cost", 12.1, costUsd=0.2, estimate=True, contextTokens=12_000),
    ]
    calls = b.calls(events, "oc:local/qwen")
    assert len(calls) == 2
    assert calls[0].cold and calls[0].prefill == 0 and calls[0].decode == pytest.approx(2.1)
    assert calls[1].prefill == pytest.approx(1.0) and calls[1].decode == pytest.approx(2.0)

    hour = b.hour_of(9.1)
    rows = b.rows(calls, [{"hour": hour, "skill": "", "in_tokens": 12_000, "out_tokens": 800}])
    (row,) = rows
    # Input by what each call read fresh: the second added 2,000 of 12,000.
    assert row["in_tokens"] == 2000 and row["in_seconds"] == pytest.approx(1.0)
    assert row["out_tokens"] == 800 and row["out_seconds"] == pytest.approx(4.1)


def test_the_backfill_corrects_claudes_output_only_where_it_can():
    b = _backfill()
    usage = [{"hour": HOUR, "skill": "", "in_tokens": 10, "out_tokens": 100}]
    one = [ev("result", 1.0)]
    assert b.out_scale({"model": "oc:x", "output_tokens": 5, "resumed_from": None}, one, usage) == 1.0
    assert b.out_scale({"model": "claude-opus-5", "output_tokens": 2500, "resumed_from": None}, one, usage) == 25
    # A resumed session's result is not the task's own; nor is one of two.
    assert b.out_scale({"model": "claude-opus-5", "output_tokens": 2500, "resumed_from": "s"}, one, usage) is None
    assert b.out_scale({"model": "claude-opus-5", "output_tokens": 2500, "resumed_from": None}, one * 2, usage) is None


def test_the_backfill_leaves_a_stray_even_share_untimed():
    """Without contexts to weigh by, one timed call in many says nothing."""
    b = _backfill()
    calls = []
    for i in range(10):
        call = throughput.Call(start=i * 10.0, first=i * 10.0 + 1, last=i * 10.0 + 2, claimed=True,
                               claimed_at=i * 10.0 + 2, streamed=(i == 0))
        calls.append(call)
    hour = b.hour_of(0.0)
    (row,) = b.rows(calls, [{"hour": hour, "skill": "", "in_tokens": 100_000, "out_tokens": 1000}])
    assert row["in_tokens"] == 0
    assert row["out_tokens"] == 1000
