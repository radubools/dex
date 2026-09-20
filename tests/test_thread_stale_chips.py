"""A thread does not keep showing tasks it no longer has.

A "started N tasks" message stores a snapshot of each task as it was when the
message was written, and the UI draws a chip from that snapshot. So archiving a
task — or deleting it — left the chip behind, still showing the state it had
when it was announced. Usually `queued`, forever, with nothing to click and no
way to clear it: archiving looked like it had done nothing.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from dex.api import _without_gone_tasks, create_app


@pytest.fixture
def client(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


def message(kind: str, ids: list[str]) -> dict:
    return {
        "id": "m1",
        "kind": kind,
        "text": f"Started {len(ids)} tasks.",
        "data": {"tasks": [{"id": i, "state": "queued"} for i in ids]},
    }


def test_a_chip_for_a_task_that_is_gone_is_dropped():
    kept = _without_gone_tasks([message("tasks", ["alive", "gone"])], {"alive"})

    assert [t["id"] for t in kept[0]["data"]["tasks"]] == ["alive"]


def test_a_message_whose_tasks_have_all_gone_is_dropped_entirely():
    """The sentence describes nothing; leaving it shows a count of zero."""
    assert _without_gone_tasks([message("tasks", ["gone"])], {"alive"}) == []


def test_other_messages_are_untouched():
    text = {"id": "m2", "kind": "text", "text": "hello", "data": {"taskId": "gone"}}

    assert _without_gone_tasks([text], set()) == [text]


def test_a_message_that_never_listed_tasks_survives():
    """An empty list is not the same as one emptied by filtering."""
    empty = {"id": "m3", "kind": "tasks", "text": "Started 0 tasks.", "data": {}}

    assert _without_gone_tasks([empty], set()) == [
        {**empty, "data": {"tasks": []}}
    ]


def wait_for_terminal(client, task_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()["task"]
        if task["state"] in {"succeeded", "failed", "cancelled", "archived"}:
            return task
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_archiving_a_task_clears_its_chip_from_the_thread(client):
    thread = client.post("/api/threads", json={}).json()["thread"]
    created = client.post(
        "/api/tasks", json={"problem": "Two Sum", "thread_id": thread["id"]}
    ).json()["task"]
    wait_for_terminal(client, created["id"])
    # The announcement the UI draws its chip from.
    client.post(
        "/api/chat/confirm",
        json={"thread_id": thread["id"],
              "tasks": [{"problem": "Two Sum", "title": "Two Sum", "slug": "two-sum"}]},
    )
    before = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    announced = [m for m in before if m["kind"] == "tasks"]
    assert announced, "nothing announced a task to begin with"
    ids = [t["id"] for m in announced for t in m["data"]["tasks"]]

    client.post("/api/tasks/actions", json={"ids": ids, "action": "archive"})

    after = client.get(f"/api/threads/{thread['id']}").json()["thread"]["messages"]
    left = [t["id"] for m in after if m["kind"] == "tasks" for t in m["data"]["tasks"]]
    assert not set(ids) & set(left), "the archived task still has a chip in the thread"
