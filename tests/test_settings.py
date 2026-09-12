"""The global auto-approve toggle."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import SettingsStore


async def test_defaults_to_off_and_persists(db):
    settings = SettingsStore(db)
    assert await settings.auto_approve() is False

    await settings.set(SettingsStore.AUTO_APPROVE, True)
    assert await settings.auto_approve() is True
    # A second store over the same database sees it — it is global, not per-process.
    assert await SettingsStore(db).auto_approve() is True

    await settings.set(SettingsStore.AUTO_APPROVE, False)
    assert await settings.auto_approve() is False


async def test_turning_it_on_releases_approvals_already_waiting(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)

    task = Task(problem="p", title="t", slug="t")
    await manager.tasks.create(task)
    manager.live[task.id] = task

    loop = asyncio.get_running_loop()
    approval = loop.create_future()
    question = loop.create_future()
    task.pending = {"approval-1": approval, "question-1": question}
    task.pending_approvals = {"approval-1"}

    released = await manager.set_auto_approve(True)

    assert released == 1
    assert approval.result() == "allow"
    # A clarifying question still needs a person; the toggle must not answer it.
    assert not question.done()
    await bus.stop()


async def test_turning_it_off_releases_nothing(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)

    task = Task(problem="p", title="t", slug="t")
    await manager.tasks.create(task)
    manager.live[task.id] = task
    approval = asyncio.get_running_loop().create_future()
    task.pending = {"approval-1": approval}
    task.pending_approvals = {"approval-1"}

    assert await manager.set_auto_approve(False) == 0
    assert not approval.done()
    await bus.stop()


async def test_the_runner_reads_the_flag_at_decision_time(config, db):
    """The value is read when the agent asks, not when the task started."""
    from dex.runner import TaskRunner
    from dex.store import SettingsStore as Store, TaskStore

    bus = EventBus(db)
    await bus.start()
    tasks = TaskStore(db)
    settings = Store(db)
    task = Task(problem="p", title="t", slug="t")
    await tasks.create(task)
    runner = TaskRunner(task, config, bus, tasks, settings)

    # Off: the call parks, waiting for a person.
    escalation = asyncio.create_task(runner._escalate("a1", "Bash", {"command": "ls"}, "Run ls"))
    await asyncio.sleep(0.1)
    assert not escalation.done()

    # Flipping it on mid-flight does not retroactively resolve this one — that
    # is what TaskManager.set_auto_approve is for — but the next call sees it.
    await settings.set(Store.AUTO_APPROVE, True)
    assert await runner._escalate("a2", "Bash", {"command": "ls"}, "Run ls") == "allow"

    escalation.cancel()
    await bus.stop()


def test_settings_endpoints(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        assert client.get("/api/settings").json()["settings"]["auto_approve"] is False
        assert client.get("/api/health").json()["autoApprove"] is False

        body = client.put("/api/settings", json={"auto_approve": True}).json()
        assert body["settings"]["auto_approve"] is True
        assert body["released"] == 0

        assert client.get("/api/settings").json()["settings"]["auto_approve"] is True
        assert client.get("/api/health").json()["autoApprove"] is True


def test_manim_is_detected_by_import_not_by_path(monkeypatch):
    """PATH may hold a different manim, or none: dex must not rely on it."""
    import shutil

    from dex import runner

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    # Still available, because this interpreter can import it.
    assert runner.manim_available() is True

    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda _name: None)
    assert runner.manim_available() is False


async def test_the_worker_ceiling_has_one_home(db):
    """The queue, the store's clamp and the API's validation all bound the same
    thing; three copies of `16` is three chances to disagree."""
    from dex.config import MAX_WORKERS
    from dex.api import SettingsRequest
    from dex.queue import MAX_WORKERS as queue_ceiling

    assert queue_ceiling is MAX_WORKERS
    # The store refuses anything above it, whatever is in the row.
    settings = SettingsStore(db)
    await settings.set(SettingsStore.TASK_CONCURRENCY, MAX_WORKERS + 50)
    assert await settings.concurrency(SettingsStore.TASK_CONCURRENCY) == MAX_WORKERS
    # And so does the request model.
    field = SettingsRequest.model_fields["task_concurrency"]
    assert any(getattr(m, "le", None) == MAX_WORKERS for m in field.metadata)
