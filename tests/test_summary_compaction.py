"""A finished task's summary lists the files it changed, and a follow-up starts from it.

Every summary lists its files. With the Compact setting on, the summary also
doubles as compaction: a note on a task that succeeded starts a fresh session
seeded with what the previous attempt reported, rather than resuming its whole
conversation. With it off (the default), or for an attempt that did not
succeed, or one with no summary, the follow-up resumes the full session.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from claude_agent_sdk import ResultMessage

from dex.bus import EventBus
from dex.changes import FILES_HEADING
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.runner import TaskRunner
from dex.store import SettingsStore, TaskStore


@pytest.fixture
async def bus(db):
    bus = EventBus(db)
    await bus.start()
    try:
        yield bus
    finally:
        await bus.stop()


@pytest.fixture
async def manager(config, db, bus):
    return TaskManager(config, bus, db)


@pytest.fixture
async def compact(db):
    await SettingsStore(db).set(SettingsStore.COMPACT_FOLLOW_UPS, True)


def _context(tool_use_id):
    return type("Ctx", (), {"tool_use_id": tool_use_id, "title": None, "description": None})()


async def _result_summary(bus, task_id):
    events = [e for e in await bus.history() if e.type == "result" and e.task_id == task_id]
    assert events, "the run should have reported a result"
    return events[-1].data["summary"]


async def test_a_finished_summary_lists_the_files_it_changed(config, db, bus):
    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="t")
    await tasks.create(task)
    run = TaskRunner(task, config, bus, tasks, SettingsStore(db))

    edited = config.workspace / "solver.py"
    edited.write_text("def solve():\n    return 1\n\n\ndef helper():\n    return 0\n")
    created = config.workspace / "notes.md"
    plain = config.workspace / "data.txt"
    plain.write_text("a\nb\nc\n")

    async def allow(_tool, _data, _ctx):
        return "allowed"

    decide = run._with_diffs(allow)
    await decide("Edit", {"file_path": str(edited), "old_string": "return 1", "new_string": "return 2"},
                 _context("e1"))
    run._settle_change("e1", ok=True)
    # Applied, as the tool would have, so the next diff of the file reads from it.
    edited.write_text(edited.read_text().replace("return 1", "return 2"))
    await decide("Edit", {"file_path": str(edited), "old_string": "def helper():\n    return 0\n",
                          "new_string": "def check(x):\n    return x\n"}, _context("e2"))
    run._settle_change("e2", ok=True)
    await decide("Write", {"file_path": str(created), "content": "# Notes\n\n## Usage\n\nRun it.\n"},
                 _context("w1"))
    run._settle_change("w1", ok=True)
    await decide("Edit", {"file_path": str(plain), "old_string": "b", "new_string": "B"}, _context("e3"))
    run._settle_change("e3", ok=True)
    # A refused write never happened, so it is not listed.
    await decide("Write", {"file_path": str(config.workspace / "refused.py"), "content": "x"},
                 _context("w2"))
    run._settle_change("w2", ok=False)

    run._translate(ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
        session_id="s", total_cost_usd=0.0, result="Fixed the solver.",
    ))
    await asyncio.sleep(0.3)

    summary = await _result_summary(bus, task.id)
    assert summary.startswith("Fixed the solver.")
    assert FILES_HEADING in summary
    # Two edits to one file read as one line, naming what they touched.
    assert "- `solver.py` — added `check`; changed `solve`; removed `helper` (+3 −3)" in summary
    assert "- `notes.md` — new file, sections § Notes, § Usage (5 lines)" in summary
    # Nothing to name: where the edit is.
    assert "- `data.txt` — edited around line 2 (+1 −1)" in summary
    assert "refused.py" not in summary


async def test_a_summary_with_nothing_changed_has_no_file_list(config, db, bus):
    tasks = TaskStore(db)
    task = Task(problem="p", title="t", slug="t")
    await tasks.create(task)
    run = TaskRunner(task, config, bus, tasks, SettingsStore(db))

    run._translate(ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
        session_id="s", total_cost_usd=0.0, result="Answered the question.",
    ))
    await asyncio.sleep(0.3)

    assert await _result_summary(bus, task.id) == "Answered the question."


async def _finished(manager, db, state, summary=None):
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    await manager.tasks.create(task)
    await manager.tasks.set_state(task.id, TaskState.RUNNING)
    await manager.tasks.set_session(task.id, "old-session")
    if summary is not None:
        await db.pool.execute(
            "INSERT INTO events (task_id, type, data, ts) VALUES ($1, 'result', $2::jsonb, now())",
            task.id, json.dumps({"summary": summary}),
        )
    await manager.queue_message(task.id, "also handle duplicates")
    await manager.tasks.set_state(task.id, TaskState(state))
    return task


SUMMARY = f"Two approaches, 8 tests passing.\n\n{FILES_HEADING}\n\n- `solutions.py` — created (40 lines)"


async def test_a_follow_up_on_a_succeeded_task_starts_from_its_summary(manager, db, compact):
    task = await _finished(manager, db, "succeeded", SUMMARY)

    started = await manager.deliver_messages(task.id)

    assert started is not None
    # The summary is the seed, in place of the session.
    assert SUMMARY in started.problem
    assert "also handle duplicates" in started.problem
    assert started.resumed_from is None
    assert (await manager.tasks.get(started.id)).resumed_from is None


async def test_a_follow_up_on_a_failed_task_keeps_its_full_session(manager, db, compact):
    task = await _finished(manager, db, "failed", SUMMARY)

    started = await manager.deliver_messages(task.id)

    assert started is not None
    assert started.resumed_from == "old-session"
    assert SUMMARY not in started.problem


async def test_a_follow_up_with_no_summary_resumes_as_before(manager, db, compact):
    task = await _finished(manager, db, "succeeded")

    started = await manager.deliver_messages(task.id)

    assert started is not None
    assert started.resumed_from == "old-session"
    assert "also handle duplicates" in started.problem


async def test_an_older_summary_without_files_still_seeds_the_follow_up(manager, db, compact):
    task = await _finished(manager, db, "succeeded", "Two approaches, 8 tests passing.")

    started = await manager.deliver_messages(task.id)

    assert started is not None
    assert "Two approaches, 8 tests passing." in started.problem
    assert started.resumed_from is None


async def test_with_compact_off_a_follow_up_resumes_as_before(manager, db):
    """Off by default: the summary is still listed, but the session is resumed."""
    assert await manager.settings.compact_follow_ups() is False
    task = await _finished(manager, db, "succeeded", SUMMARY)

    started = await manager.deliver_messages(task.id)

    assert started is not None
    assert started.resumed_from == "old-session"
    assert SUMMARY not in started.problem
    assert "also handle duplicates" in started.problem
