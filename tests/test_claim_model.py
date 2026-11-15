"""A queued task runs on the model set when a worker claims it, not the one set
when it was made: the settings may change while it waits in the queue."""

from __future__ import annotations

import pytest

from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import SettingsStore


@pytest.fixture
async def manager(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        yield manager
    finally:
        await manager.stop()
        await bus.stop()


async def test_a_queued_task_takes_the_model_chosen_while_it_waited(manager, db):
    settings = SettingsStore(db)
    await settings.set(SettingsStore.MODEL, "claude-sonnet-5-5")
    task = Task(problem="p", title="waits", slug="waits", project=manager.config.default_project)
    task.model = await manager.model_for(task)
    await manager.tasks.create(task)
    assert task.model == "claude-sonnet-5-5"

    await settings.set(SettingsStore.MODEL, "claude-opus-5-5")
    claimed = await manager.tasks.claim("w", model_for=manager.model_for)
    assert claimed is not None and claimed.model == "claude-opus-5-5"
    assert (await manager.tasks.get(task.id)).model == "claude-opus-5-5"


async def test_the_projects_own_model_wins(manager, db):
    settings = SettingsStore(db)
    await settings.set(SettingsStore.MODEL, "claude-opus-5-5")
    await settings.set(SettingsStore.project_model_key(manager.config.default_project), "claude-sonnet-5-5")
    task = Task(problem="p", title="ml", slug="ml-one", project=manager.config.default_project)
    assert await manager.model_for(task) == "claude-sonnet-5-5"


async def test_a_paused_task_keeps_the_model_it_ran_on(manager, db):
    settings = SettingsStore(db)
    task = Task(problem="p", title="ran", slug="ran", project=manager.config.default_project)
    task.model = "claude-sonnet-5-5"
    await manager.tasks.create(task)
    await manager.tasks.set_state(task.id, TaskState.RUNNING, started_at=1.0)
    await manager.tasks.set_state(task.id, TaskState.PAUSED)

    await settings.set(SettingsStore.MODEL, "claude-opus-5-5")
    await manager.resume_paused(1)               # back in the queue, as the server does
    assert (await manager.tasks.get(task.id)).state is TaskState.QUEUED
    claimed = await manager.tasks.claim("w", model_for=manager.model_for)
    assert claimed is not None and claimed.model == "claude-sonnet-5-5"
