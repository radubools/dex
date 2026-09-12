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


async def test_utility_proposals_default_to_on(db):
    """The loop is the point; an operator who dislikes it turns it off once."""
    settings = SettingsStore(db)
    assert await settings.utility_proposals() is True
    assert (await settings.all())[SettingsStore.UTILITY_PROPOSALS] is True


async def test_turning_utility_proposals_off_is_read_back(db):
    settings = SettingsStore(db)
    await settings.set(SettingsStore.UTILITY_PROPOSALS, False)
    assert await settings.utility_proposals() is False


def test_the_settings_endpoint_toggles_utility_proposals(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        assert client.get("/api/settings").json()["settings"]["utility_proposals"] is True

        body = client.put("/api/settings", json={"utility_proposals": False}).json()
        assert body["settings"]["utility_proposals"] is False

        # And back, so a switch is a switch.
        client.put("/api/settings", json={"utility_proposals": True})
        assert client.get("/api/settings").json()["settings"]["utility_proposals"] is True


def test_the_brief_makes_a_task_check_the_switch_before_promoting():
    """A flag nothing consults is decoration."""
    from pathlib import Path

    from dex.prompts import generation_prompt

    text = generation_prompt(
        problem="x",
        task_dir=Path("assets/algorithms/two-sum"),
        python=Path("/w/python"),
        manim_available=True,
    )
    assert "mcp__dex__utility_proposals_enabled" in text
    # Off: touch nothing outside the task directory. On: ask, tagged, and the
    # answer may already be waiting.
    assert "change nothing outside your directory" in text
    assert '`kind` set to `"utility"`' in text


def test_the_guides_gate_sharing_on_the_switch():
    from pathlib import Path

    for project in ("algorithms", "yoga"):
        guide = (Path("assets") / project / "AGENTS.md").read_text(encoding="utf-8")
        assert "mcp__dex__utility_proposals_enabled" in guide, project
        # Disabled means leave the tree alone; enabled means ask, and dex
        # answers on the operator's behalf.
        assert "**ask nothing**" in guide, project
        assert "dex answers this" in guide, project
        # And the operator's veto list is theirs alone.
        assert "never add, edit, or remove a row" in guide, project


async def test_effort_defers_to_the_deployment_until_an_operator_picks_one(db):
    settings = SettingsStore(db)
    assert await settings.effort() is None
    await settings.set(SettingsStore.EFFORT, "max")
    assert await settings.effort() == "max"
    # Cleared back to the deployment default rather than stored as empty.
    await settings.set(SettingsStore.EFFORT, None)
    assert await settings.effort() is None


def test_the_settings_endpoint_takes_an_effort_and_rejects_a_bad_one(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        body = client.get("/api/settings").json()
        assert body["settings"]["effort"] is None
        assert body["defaultEffort"] == config.effort
        assert [e["id"] for e in body["efforts"]] == ["low", "medium", "high", "xhigh", "max"]

        assert client.put("/api/settings", json={"effort": "max"}).json()["settings"]["effort"] == "max"
        # "" is how the UI clears the override.
        assert client.put("/api/settings", json={"effort": ""}).json()["settings"]["effort"] is None
        # Anything else is not an effort level.
        assert client.put("/api/settings", json={"effort": "ludicrous"}).status_code == 422


async def test_a_task_runs_at_the_operators_effort(config, db, monkeypatch):
    """The setting is worth nothing if the runner still reads the env var."""
    import asyncio
    import contextlib

    from dex.bus import EventBus
    from dex.runner import TaskRunner
    from dex.store import SettingsStore as S
    from dex.store import TaskStore

    seen: dict[str, str] = {}

    class CapturingClient:
        def __init__(self, options):
            seen["effort"] = options.effort

        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("dex.runner.ClaudeSDKClient", CapturingClient, raising=False)
    settings = S(db)
    await settings.set(S.EFFORT, "low")

    task = Task(problem="p", title="t", slug="effort-probe")
    task.project = "algorithms"
    bus = EventBus(db)
    runner = TaskRunner(task, config, bus, TaskStore(db), settings)
    with contextlib.suppress(BaseException):
        await runner.run()

    assert seen.get("effort") == "low"
    assert seen["effort"] != config.effort


def test_utility_proposals_drive_the_task_concurrency(config, db, monkeypatch):
    """The loop only works in sequence.

    Six tasks in parallel all read the same roster before any of them promotes
    anything, so several write the same helper and the operator answers the
    same question six times.
    """
    from dex.config import PARALLEL_CONCURRENCY, SEQUENTIAL_CONCURRENCY

    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        on = client.put("/api/settings", json={"utility_proposals": True}).json()["settings"]
        assert on["task_concurrency"] == SEQUENTIAL_CONCURRENCY == 1

        off = client.put("/api/settings", json={"utility_proposals": False}).json()["settings"]
        assert off["task_concurrency"] == PARALLEL_CONCURRENCY == 6


def test_an_explicit_concurrency_in_the_same_request_still_wins(config, db, monkeypatch):
    """Coupling is a convenience, not a lock: the operator can still say."""
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        body = client.put(
            "/api/settings", json={"utility_proposals": True, "task_concurrency": 4}
        ).json()["settings"]
        assert body["utility_proposals"] is True
        assert body["task_concurrency"] == 4


def test_concurrency_set_on_its_own_does_not_disturb_the_flag(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as client:
        client.put("/api/settings", json={"utility_proposals": True})
        body = client.put("/api/settings", json={"task_concurrency": 5}).json()["settings"]
        assert body["task_concurrency"] == 5
        assert body["utility_proposals"] is True
