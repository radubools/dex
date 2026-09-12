"""Chat calls retry, and a request too big for one reply is planned in batches.

A single dropped connection used to lose a whole planning turn, and a request
that implied hundreds of tasks produced one over-long reply that parsed to
nothing at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.planner import Plan, PlannedTask


@pytest.fixture
def client(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


def _plan(n: int, prefix: str, remaining: str = "") -> Plan:
    return Plan(
        tasks=[
            PlannedTask(title=f"{prefix}{i}", problem=f"do {prefix}{i}", slug=f"{prefix}{i}")
            for i in range(n)
        ],
        notes=f"{prefix} batch",
        remaining=remaining,
    )


async def test_a_transient_failure_is_retried_rather_than_surfaced(client, monkeypatch):
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("connection reset")
        return _plan(1, "ok")

    monkeypatch.setattr("dex.api.plan_from_message", flaky)
    thread = client.post("/api/threads", json={}).json()["thread"]
    body = client.post(f"/api/threads/{thread['id']}/messages", json={"text": "go"}).json()

    assert calls["n"] == 3, "should have retried twice before succeeding"
    assert [t["slug"] for t in body["plan"]["tasks"]] == ["ok0"]


async def test_it_gives_up_after_three_attempts_and_reports_why(client, monkeypatch):
    calls = {"n": 0}

    async def always_fails(*a, **k):
        calls["n"] += 1
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr("dex.api.plan_from_message", always_fails)
    thread = client.post("/api/threads", json={}).json()["thread"]
    response = client.post(f"/api/threads/{thread['id']}/messages", json={"text": "go"})

    assert response.status_code == 502
    assert calls["n"] == 3, "three attempts, not more"

    # The thread carries the failure, with the detail kept for the UI to expand.
    messages = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    errors = [m for m in messages if m["kind"] == "error"]
    assert errors, "the failure has to be visible in the thread"
    assert "upstream exploded" in errors[-1]["data"]["detail"]


async def test_an_auth_failure_is_not_retried(client, monkeypatch):
    calls = {"n": 0}

    async def not_logged_in(*a, **k):
        calls["n"] += 1
        raise RuntimeError("not logged in")

    monkeypatch.setattr("dex.api.plan_from_message", not_logged_in)
    thread = client.post("/api/threads", json={}).json()["thread"]
    response = client.post(f"/api/threads/{thread['id']}/messages", json={"text": "go"})

    assert response.status_code == 503
    assert calls["n"] == 1, "logging in again is not something a retry can fix"


async def test_a_big_request_is_planned_in_sequenced_batches(client, monkeypatch):
    batches = {"n": 0}

    async def in_batches(*a, **k):
        batches["n"] += 1
        # Three batches, the last one covering everything left.
        remaining = "more chapters" if batches["n"] < 3 else ""
        return _plan(2, f"b{batches['n']}-", remaining)

    monkeypatch.setattr("dex.api.plan_from_message", in_batches)
    thread = client.post("/api/threads", json={}).json()["thread"]
    client.post(f"/api/threads/{thread['id']}/messages", json={"text": "the whole book"})

    assert batches["n"] == 3, "should stop as soon as nothing remains"
    messages = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    plans = [m for m in messages if m["kind"] == "plan"]
    assert len(plans) == 3, "each batch is its own plan in the thread"
    assert [p["data"]["batch"] for p in plans] == [1, 2, 3]


async def test_batching_stops_at_ten_plans(client, monkeypatch):
    batches = {"n": 0}

    async def never_finishes(*a, **k):
        batches["n"] += 1
        return _plan(1, f"n{batches['n']}-", "always more")

    monkeypatch.setattr("dex.api.plan_from_message", never_finishes)
    thread = client.post("/api/threads", json={}).json()["thread"]
    client.post(f"/api/threads/{thread['id']}/messages", json={"text": "everything"})

    assert batches["n"] == 10, "ten plans is the ceiling"
    messages = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    assert any("Stopped after 10 plans" in m["text"] for m in messages)


async def test_a_thread_reports_a_call_that_is_still_in_flight(client, monkeypatch):
    """A page loaded mid-call has to learn that from the server.

    `planning` was client-side state, so refreshing while dex was thinking
    showed an idle thread until the reply happened to land.
    """
    import asyncio

    thread = client.post("/api/threads", json={}).json()["thread"]
    assert client.get(f"/api/threads/{thread['id']}").json()["thread"]["planning"] is False

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(*a, **k):
        started.set()
        await release.wait()
        return _plan(1, "late")

    monkeypatch.setattr("dex.api.plan_from_message", slow)

    async def ask() -> None:
        await asyncio.to_thread(
            client.post, f"/api/threads/{thread['id']}/messages", json={"text": "go"}
        )

    turn = asyncio.create_task(ask())
    try:
        await asyncio.wait_for(started.wait(), 5)
        # Mid-call: a fresh page load must see that dex is working.
        body = client.get(f"/api/threads/{thread['id']}").json()
        assert body["thread"]["planning"] is True
    finally:
        release.set()
        await asyncio.wait_for(turn, 10)

    assert client.get(f"/api/threads/{thread['id']}").json()["thread"]["planning"] is False
