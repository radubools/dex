"""The local model proxy's machine snapshot (power, DRAM, GPU, tokens per second) reaches a
task's activity: the bridge reads it from the stream, OpenCode's client hands it to the runner,
and the runner records it as a `machine` event, at most every few seconds."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from claude_agent_sdk import SystemMessage

from dex import opencode, runner as runner_mod
from dex.antigravity import MACHINE_FIELD, _read_sse
from dex.bus import EventBus
from dex.game_chat import _merge
from dex.models import Task
from dex.runner import TaskRunner
from dex.store import SettingsStore, TaskStore

SNAP = {"at": 1795316952, "power": {"adapter_w": 72.1, "wall_w": 74.2, "battery_w": 9.2, "fresh_s": 4},
        "dram": {"read": {"avg_gbs": 109.1, "p95_gbs": 224.0}, "write": {"avg_gbs": 26.5, "p95_gbs": 96.0},
                 "total": {"avg_gbs": 127.3, "p95_gbs": 256.0}},
        "gpu": {"active": 0.995, "mhz": 1182, "ceiling_tflops": 12.1, "busy_ceiling_tflops": 12.0},
        "tokens": {"in_tps": 0.0, "out_tps": 43.2, "tflops": 0.58}}


def test_the_bridge_reads_the_snapshot_beside_the_deltas():
    calls = []
    line = b"data: " + json.dumps({"choices": [{"delta": {"content": "Hi"}}], MACHINE_FIELD: SNAP}).encode()
    _read_sse(line, lambda *a: calls.append(a))
    assert calls == [("machine", SNAP), ("text", "Hi")]
    calls.clear()
    _read_sse(b'data: {"choices": [{"delta": {"content": "x"}}], "dex_machine": "junk"}', lambda *a: calls.append(a))
    _read_sse(b"data: [1, 2]", lambda *a: calls.append(a))
    assert calls == [("text", "x")]


def test_opencode_hands_the_snapshot_to_the_runner(monkeypatch):
    entry = {"id": "m", "url": "http://127.0.0.1:9/v1", "served": "m"}
    monkeypatch.setattr(opencode, "_local", lambda _id: entry)
    options = SimpleNamespace(cwd=".", model="oc:local/m", effort="medium", can_use_tool=None, system_prompt="",
                              resume=None, mcp_servers={}, env={}, stderr=None)
    client = opencode.OpencodeClient(options)
    client._stream("machine", SNAP)
    client._stream("machine", "not a snapshot")
    got = []
    while not client._messages.empty():
        got.append(client._messages.get_nowait())
    assert len(got) == 1 and isinstance(got[0], SystemMessage)
    assert got[0].subtype == "machine" and got[0].data == SNAP


@pytest.fixture
async def run(config, db):
    bus = EventBus(db)
    await bus.start()
    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="t")
    task.model = "oc:local/qwen3.8-flash-next"
    await tasks.create(task)
    runner = TaskRunner(task, config, bus, tasks, SettingsStore(db))
    try:
        yield runner
    finally:
        await bus.stop()


async def test_the_runner_records_the_snapshot_at_most_every_few_seconds(run, monkeypatch):
    sent = []
    monkeypatch.setattr(run, "emit", lambda kind, **data: sent.append((kind, data)))
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    run._observe(SystemMessage(subtype="machine", data={**SNAP, "extra": 1}))
    clock[0] += 2.0
    run._observe(SystemMessage(subtype="machine", data=SNAP))  # too soon: dropped
    clock[0] += runner_mod.MACHINE_EVERY_S
    run._observe(SystemMessage(subtype="machine", data={"power": SNAP["power"]}))
    assert [k for k, _ in sent] == ["machine", "machine"]
    assert sent[0][1] == {k: SNAP[k] for k in ("power", "dram", "gpu", "tokens")}  # only what the panel shows
    assert sent[1][1] == {"power": SNAP["power"]}  # a partial snapshot as it is


async def test_a_snapshot_with_nothing_to_show_records_nothing(run, monkeypatch):
    sent = []
    monkeypatch.setattr(run, "emit", lambda kind, **data: sent.append(kind))
    run._observe(SystemMessage(subtype="machine", data={"at": 1}))
    assert sent == [] and run._machine_sent == 0.0


def test_game_chats_keep_only_the_last_reading_of_a_run():
    events = [{"type": "machine", "taskId": "a", "seq": 1, "power": {"adapter_w": 1}},
              {"type": "machine", "taskId": "a", "seq": 2, "power": {"adapter_w": 2}},
              {"type": "text", "taskId": "a", "seq": 3, "text": "hi"},
              {"type": "machine", "taskId": "a", "seq": 4, "power": {"adapter_w": 3}}]
    assert [e["seq"] for e in _merge(events)] == [2, 3, 4]
