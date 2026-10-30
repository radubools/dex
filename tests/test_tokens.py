"""Token accounting, with thinking split out of output.

Thinking bills as output and is reported inside `output_tokens`, so the cost
was never wrong — but dex kept only dollars, which made "what did the thinking
cost" unanswerable. These cover the one arithmetic trap: thinking is a subset,
not an addition.
"""

from __future__ import annotations

from dex.models import Task
from dex.pricing import tokens
from datetime import UTC, datetime, timedelta

from dex.game_chat import _summary
from dex.store import CostStore, TaskStore

#: The shape the agent's result actually reports, from a live probe.
REAL_USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 16802,
    "cache_read_input_tokens": 0,
    "output_tokens": 112,
    "output_tokens_details": {"thinking_tokens": 105},
    "service_tier": "standard",
}


def test_thinking_is_read_out_of_the_output_detail():
    counts = tokens(REAL_USAGE)
    assert counts == {
        "input_tokens": 10,
        "cache_read_tokens": 0,
        "cache_write_tokens": 16802,
        "output_tokens": 112,
        "thinking_tokens": 105,
    }
    # The trap: thinking is part of output, so this must not be 217.
    assert counts["thinking_tokens"] <= counts["output_tokens"]


def test_a_usage_without_the_detail_reports_no_thinking():
    """Per-message usage carries no `output_tokens_details`; that is not an error."""
    counts = tokens({"input_tokens": 5, "output_tokens": 9})
    assert counts["output_tokens"] == 9
    assert counts["thinking_tokens"] == 0


def test_missing_and_malformed_usage_are_harmless():
    assert tokens(None)["output_tokens"] == 0
    assert tokens({})["thinking_tokens"] == 0
    assert tokens({"output_tokens": None})["output_tokens"] == 0
    assert tokens({"output_tokens": "x"})["output_tokens"] == 0
    assert tokens({"output_tokens": 4, "output_tokens_details": "nope"})["thinking_tokens"] == 0
    # Negatives would make a share exceed 100%.
    assert tokens({"output_tokens": -5})["output_tokens"] == 0


def test_a_thinking_count_above_its_output_is_clamped():
    """A subset cannot exceed its whole, whatever the report says."""
    counts = tokens({"output_tokens": 5, "output_tokens_details": {"thinking_tokens": 999}})
    assert counts["thinking_tokens"] == 5


HOUR = datetime(2026, 10, 1, 12, tzinfo=UTC)


async def _task(tasks, slug):
    task = Task(problem="p", title=slug, slug=slug)
    await tasks.create(task)
    return task


async def test_totals_split_thinking_from_visible_output(db):
    tasks = TaskStore(db)
    for slug, out, think in (("a", 100, 60), ("b", 50, 10)):
        task = await _task(tasks, slug)
        await tasks.add_usage(
            task.id, HOUR, "", 1.0,
            {"input_tokens": 1, "cache_read_tokens": 2, "cache_write_tokens": 3,
             "output_tokens": out, "thinking_tokens": think},
        )

    totals = await CostStore(db).token_totals()
    assert totals["output"] == 150
    assert totals["thinking"] == 70
    # The identity the UI's percentage depends on.
    assert totals["visible"] == 80
    assert totals["thinking"] + totals["visible"] == totals["output"]
    assert totals["counted"] == 2


async def test_spend_without_tokens_is_reported_apart(db):
    """Otherwise an old backlog silently drags the thinking share toward zero."""
    tasks = TaskStore(db)
    old = await _task(tasks, "old")
    await tasks.add_usage(old.id, HOUR, "", 2.0, {})
    new = await _task(tasks, "new")
    await tasks.add_usage(new.id, HOUR, "", 1.0, {"output_tokens": 10, "thinking_tokens": 4})

    totals = await CostStore(db).token_totals()
    assert totals["with_cost"] == 2
    assert totals["counted"] == 1
    assert totals["output"] == 10


async def test_usage_adds_up_per_hour_and_skill(db):
    tasks = TaskStore(db)
    task = await _task(tasks, "a")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    await tasks.add_usage(task.id, now, "game-story", 1.0, {"output_tokens": 10})
    await tasks.add_usage(task.id, now, "game-story", 0.5, {"output_tokens": 5})
    await tasks.add_usage(task.id, now, "", 0.25, {"input_tokens": 7})

    points = await CostStore(db).series("day", "hour", "skill")
    by_name = {p["series"]: p for p in points}
    assert by_name["game-story"]["cost"] == 1.5
    assert by_name["game-story"]["output"] == 15
    assert by_name["(no skill)"]["input"] == 7

    coverage = await CostStore(db).coverage("day")
    assert coverage["spend"] == 1.75
    assert coverage["with_tokens"] == 1.5


async def test_naming_a_skill_merges_what_came_before_it(db):
    tasks = TaskStore(db)
    task = await _task(tasks, "a")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    await tasks.add_usage(task.id, now, "", 1.0, {"input_tokens": 3})
    await tasks.add_usage(task.id, now, "game-story", 2.0, {"input_tokens": 4})
    await tasks.name_usage_skill(task.id, "game-story")

    points = await CostStore(db).series("day", "hour", "skill")
    assert [(p["series"], p["cost"], p["input"]) for p in points] == [("game-story", 3.0, 7)]


async def test_a_chat_costs_what_its_attempts_added_not_what_they_reported(db):
    tasks = TaskStore(db)
    first = await _task(tasks, "first")
    second = await _task(tasks, "second")
    # A resumed session reports everything before it too.
    await tasks.set_cost(first.id, 3.0, estimate=False)
    await tasks.set_cost(second.id, 5.0, estimate=False)
    await tasks.add_usage(first.id, HOUR, "", 3.0, {})
    await tasks.add_usage(second.id, HOUR, "", 2.0, {})

    costs = await CostStore(db).by_tasks([first.id, second.id])
    assert _summary("c", [first, second], costs)["costUsd"] == 5.0


async def test_the_dashboard_can_be_narrowed_to_several_projects(db):
    await db.pool.execute(
        "INSERT INTO projects (slug, name) VALUES ('a', 'A'), ('b', 'B'), ('c', 'C')"
    )
    tasks = TaskStore(db)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for slug, cost in (("a", 1.0), ("b", 2.0), ("c", 4.0)):
        task = await _task(tasks, f"t{slug}")
        await db.pool.execute("UPDATE tasks SET project = $1 WHERE id = $2", slug, task.id)
        await tasks.add_usage(task.id, now, "", cost, {"output_tokens": 1})

    store = CostStore(db)
    points = await store.series("day", "hour", "skill", ["a", "c"])
    assert sum(p["cost"] for p in points) == 5.0
    assert (await store.coverage("day", ["b"]))["spend"] == 2.0
    assert (await store.totals(["a", "b"]))["all_time"] == 3.0
    assert (await store.totals())["all_time"] == 7.0
    assert await store.projects() == ["a", "b", "c"]


async def test_pages_of_a_period_meet_without_overlapping(db):
    tasks = TaskStore(db)
    task = await _task(tasks, "a")
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    for days_ago, cost in ((0, 1.0), (6, 2.0), (7, 4.0), (13, 8.0), (14, 16.0)):
        await tasks.add_usage(task.id, today - timedelta(days=days_ago), "", cost, {})

    store = CostStore(db)
    pages = [sum(p["cost"] for p in await store.series("week", "day", "skill", offset=n)) for n in range(3)]
    assert pages == [3.0, 12.0, 16.0]
    assert (await store.coverage("week", None, "day", 1))["spend"] == 12.0
    assert (await store.earliest()) is not None
