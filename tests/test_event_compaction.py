"""Streamed text is grouped as it is published and compacted once its block ends."""

from __future__ import annotations

import asyncio

import pytest

from dex import bus as busmod
from dex.bus import EventBus
from dex.models import Event
from dex.queue import TaskManager


@pytest.fixture
async def bus(db):
    b = EventBus(db)
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


@pytest.fixture
async def new_task(config, bus, db):
    manager = TaskManager(config, bus, db)

    async def make() -> str:
        task = await manager.submit("Two Sum", title="Two Sum")
        await bus._pending.join()
        return task.id
    return make


async def _rows(db, task_id, since=0):
    import json
    rows = await db.pool.fetch("SELECT seq, type, data FROM events WHERE task_id = $1 AND type <> 'task_created' "
                               "AND type <> 'task_state' ORDER BY seq", task_id)
    return [(r["type"], json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]) for r in rows]



async def _settle(b):
    await asyncio.sleep(busmod.DELTA_FLUSH_S * 2)
    await b._pending.join()


def _delta(task, block, text, kind="thinking_delta"):
    return Event(type=kind, data={"id": block, "delta": text}, task_id=task, project="ml")


async def test_deltas_go_out_in_groups_and_a_finished_block_is_one_row(bus, db, new_task):
    task = await new_task()
    for i in range(20):
        bus.publish(_delta(task, "b1", f"w{i} "))
    await bus._pending.join()
    # 20 deltas: two full groups of 8 written, 4 still held.
    written = [d for t, d in await _rows(db, task) if t == "thinking_delta"]
    assert [len(d["delta"].split()) for d in written] == [8, 8]
    bus.publish(Event(type="block_end", data={"id": "b1"}, task_id=task, project="ml"))
    await _settle(bus)
    rows = await _rows(db, task)
    # The end flushed the last 4 first, then compacted: one row with the whole text, then the end.
    assert [t for t, _ in rows] == ["thinking_delta", "block_end"]
    assert rows[0][1]["delta"] == "".join(f"w{i} " for i in range(20)) and rows[0][1]["id"] == "b1"


async def test_held_deltas_go_out_after_the_flush_interval(bus, db, new_task):
    task = await new_task()
    bus.publish(_delta(task, "b1", "hello ", "text_delta"))
    bus.publish(_delta(task, "b1", "world", "text_delta"))
    await _settle(bus)
    rows = await _rows(db, task)
    assert rows == [("text_delta", {"id": "b1", "delta": "hello world"})]


async def test_another_event_of_the_task_sends_held_deltas_first(bus, db, new_task):
    task = await new_task()
    bus.publish(_delta(task, "b1", "thinking about it"))
    bus.publish(Event(type="tool", data={"id": "x", "name": "Read"}, task_id=task, project="ml"))
    await bus._pending.join()
    assert [t for t, _ in await _rows(db, task)] == ["thinking_delta", "tool"]


async def test_a_task_that_ends_compacts_open_blocks_and_keeps_the_last_asset_per_path(bus, db, new_task):
    task = await new_task()
    for i in range(10):
        bus.publish(_delta(task, "b1", "x"))
    for change in ("added", "modified", "modified"):
        bus.publish(Event(type="asset", data={"path": "ml/p/a.json", "change": change}, task_id=task, project="ml"))
    bus.publish(Event(type="asset", data={"path": "ml/p/b.json", "change": "added"}, task_id=task, project="ml"))
    bus.publish(Event(type="task_state", data={"state": "succeeded"}, task_id=task, project="ml"))
    await _settle(bus)
    rows = await _rows(db, task)
    assert [t for t, _ in rows].count("thinking_delta") == 1 and rows[0][1]["delta"] == "x" * 10
    assets = [d for t, d in rows if t == "asset"]
    assert assets == [{"path": "ml/p/a.json", "change": "modified"}, {"path": "ml/p/b.json", "change": "added"}]


async def test_replay_after_compaction_reads_the_same_as_the_stream(bus, db, new_task):
    task = await new_task()
    for word in "one two three four five six seven eight nine ten eleven".split():
        bus.publish(_delta(task, "b9", word + " ", "text_delta"))
    bus.publish(Event(type="block_end", data={"id": "b9"}, task_id=task, project="ml"))
    await _settle(bus)
    replay = await bus.history(task)
    text = "".join(e.data["delta"] for e in replay if e.type == "text_delta")
    assert text == "one two three four five six seven eight nine ten eleven "
    assert [e.type for e in replay if e.type not in ("task_created", "task_state")] == ["text_delta", "block_end"]
