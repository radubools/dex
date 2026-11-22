"""The cost dashboard's drill-down: the tasks behind one slice of the chart.

What matters is that the list and the chart agree. A slice's tasks are read
through the same FROM/WHERE as the chart's sums, so every slice's listed cost
must equal what the chart drew for it, whatever the group, bucket or projects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.models import Task
from dex.store import CostStore, TaskStore


async def _task(tasks: TaskStore, db, slug: str, *, project: str = "algorithms", model: str = "m1") -> Task:
    task = Task(problem="p", title=slug.title(), slug=slug)
    await tasks.create(task)
    await db.pool.execute("UPDATE tasks SET project = $1, model = $2 WHERE id = $3", project, model, task.id)
    return task


@pytest.fixture
async def spend(db):
    """Four tasks over two days, two models, two skills and two projects."""
    await db.pool.execute("INSERT INTO projects (slug, name) VALUES ('games', 'Games') ON CONFLICT DO NOTHING")
    tasks = TaskStore(db)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    yesterday = now - timedelta(days=1)
    a = await _task(tasks, db, "alpha", model="m1")
    b = await _task(tasks, db, "beta", model="m2")
    c = await _task(tasks, db, "gamma", model="m1", project="games")
    d = await _task(tasks, db, "delta", model="m2", project="games")
    rows = [
        (a, now, "game-story", 3.0, 100), (a, yesterday, "game-story", 1.0, 10),
        (a, now, "", 0.5, 5),
        (b, now, "game-mesh", 2.0, 400), (b, yesterday, "game-story", 0.25, 1),
        (c, yesterday, "game-mesh", 4.0, 50),
        (d, now, "", 0.75, 7),
    ]
    for task, hour, skill, cost, out in rows:
        await tasks.add_usage(task.id, hour, skill, cost, {"input_tokens": 1, "output_tokens": out})
    await tasks.add_changes(a.id, 120, 3)
    await tasks.add_changes(c.id, 0, 40)
    return {"a": a, "b": b, "c": c, "d": d, "now": now}


@pytest.mark.parametrize("group", ["skill", "model", "project", "state"])
async def test_every_slice_lists_exactly_what_the_chart_sums(db, spend, group):
    store = CostStore(db)
    points = await store.series("week", "day", group)
    assert points
    for point in points:
        at = datetime.fromisoformat(point["bucket"])
        got = await store.slice_tasks("week", "day", group, at=at, series=[point["series"]], limit=100)
        assert got["cost"] == pytest.approx(point["cost"])
        assert got["total"] == point["tasks"]
        assert sum(t["cost"] for t in got["tasks"]) == pytest.approx(point["cost"])
        assert sum(t["output"] for t in got["tasks"]) == point["output"]

    # No bucket and no series is the whole chart.
    whole = await store.slice_tasks("week", "day", group, limit=100)
    assert whole["cost"] == pytest.approx(sum(p["cost"] for p in points))


async def test_a_slice_is_its_bucket_and_series_only(db, spend):
    store = CostStore(db)
    # Today's game-story bar: alpha's 3.00 (yesterday's holds alpha's 1.00 and beta's 0.25).
    (today,) = [p["bucket"] for p in await store.series("week", "day", "skill")
                if p["series"] == "game-story" and p["cost"] == 3.0]
    got = await store.slice_tasks("week", "day", "skill", at=datetime.fromisoformat(today), series=["game-story"])
    # Only alpha's 3.00 today: not its 1.00 yesterday, nor its unskilled 0.50.
    assert [(t["slug"], t["cost"]) for t in got["tasks"]] == [("alpha", 3.0)]

    # Several series at once, as Other folds them.
    got = await store.slice_tasks("week", "day", "skill", series=["game-mesh", "(no skill)"])
    assert {t["slug"]: t["cost"] for t in got["tasks"]} == {"gamma": 4.0, "beta": 2.0, "delta": 0.75, "alpha": 0.5}


async def test_the_project_filter_narrows_the_slice_like_the_chart(db, spend):
    store = CostStore(db)
    got = await store.slice_tasks("week", "day", "model", ["games"], series=["m1"])
    assert [t["slug"] for t in got["tasks"]] == ["gamma"]
    chart = sum(p["cost"] for p in await store.series("week", "day", "model", ["games"]) if p["series"] == "m1")
    assert got["cost"] == chart == 4.0


async def test_pages_are_largest_first_and_meet_without_overlap(db, spend):
    store = CostStore(db)
    first = await store.slice_tasks("week", "day", "model", limit=3)
    second = await store.slice_tasks("week", "day", "model", limit=3, page=1)
    assert first["total"] == second["total"] == 4
    assert [t["slug"] for t in first["tasks"]] == ["alpha", "gamma", "beta"]
    assert [t["slug"] for t in second["tasks"]] == ["delta"]
    assert (await store.slice_tasks("week", "day", "model", limit=3, page=5))["tasks"] == []
    # A page past the end still knows what the slice holds.
    assert (await store.slice_tasks("week", "day", "model", limit=3, page=5))["cost"] == pytest.approx(11.5)

    by_tokens = await store.slice_tasks("week", "day", "model", sort="tokens")
    assert [t["slug"] for t in by_tokens["tasks"]][:2] == ["beta", "alpha"]
    assert (await store.slice_tasks("week", "day", "model", sort="nonsense"))["sort"] == "cost"


async def test_a_page_that_ends_before_the_spend_lists_nothing(db, spend):
    got = await CostStore(db).slice_tasks("week", "day", "model", offset=3)
    assert got["total"] == 0 and got["tasks"] == [] and got["cost"] == 0


async def test_lines_slices_match_the_line_chart(db, spend):
    store = CostStore(db)
    lines = await store.line_series("week", "day", "project")
    for point in lines:
        got = await store.slice_tasks("week", "day", "project", at=datetime.fromisoformat(point["bucket"]),
                                      series=[point["series"]], source="lines")
        assert (got["code"], got["assets"]) == (point["code"], point["assets"])
    got = await store.slice_tasks("week", "day", "model", source="lines")
    assert [t["slug"] for t in got["tasks"]] == ["alpha", "gamma"]
    assert got["source"] == "lines" and got["sort"] == "lines"


def test_the_endpoint_pages_a_slice(config, db, spend, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        chart = client.get("/api/costs", params={"period": "week", "granularity": "day", "group": "skill"}).json()
        story = [p for p in chart["series"] if p["series"] == "game-story"]
        for point in story:
            body = client.get("/api/costs/tasks", params={
                "period": "week", "granularity": "day", "group": "skill",
                "bucket": point["bucket"], "series": "game-story",
            }).json()
            assert body["cost"] == pytest.approx(point["cost"])

        body = client.get("/api/costs/tasks", params=[
            ("group", "skill"), ("series", "game-mesh"), ("series", "(no skill)"),
            ("limit", "2"), ("page", "1"),
        ]).json()
        assert body["total"] == 4 and body["page"] == 1 and body["limit"] == 2
        assert [t["slug"] for t in body["tasks"]] == ["delta", "alpha"]
        assert set(body["tasks"][0]) >= {"id", "title", "project", "model", "state", "cost", "tokens", "createdAt"}

        assert client.get("/api/costs/tasks", params={"bucket": "yesterday"}).status_code == 400
        assert client.get("/api/costs/tasks", params={"bucket": "2026-10-01T00:00:00"}).status_code == 400
        assert client.get("/api/costs/tasks", params={"limit": "1000"}).status_code == 422
