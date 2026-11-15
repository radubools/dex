"""A skill's own model: its threads' tasks, and the dependencies dex starts for
it, run on the model chosen for that skill (design/26)."""

from __future__ import annotations

import json
import time

import pytest

from dex import skills
from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.queue import TaskManager
from dex.store import SettingsStore, ThreadStore


@pytest.fixture
async def manager(config, db):
    bus = EventBus(db)
    await bus.start()
    m = TaskManager(config, bus, db)

    async def post(thread_id, message):
        return None

    m.post = post
    try:
        yield m
    finally:
        await m.graph.stop()
        await bus.stop()


def enable(config, project: str, *names: str) -> None:
    path = skills.enabled_path(config.project_dir(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skills": {n: "000000" for n in names}}))


async def test_the_skills_model_wins_over_the_project_and_global(db):
    settings = SettingsStore(db)
    await settings.set(SettingsStore.MODEL, "claude-opus-5-5")
    await settings.set(SettingsStore.project_model_key("ml"), "agy:gemini-4-carbon-medium")
    await settings.set(SettingsStore.skill_model_key("game-reading"), "oc:qwen3.8-flash")
    assert await settings.model_for("ml", "x", skill="game-reading") == "oc:qwen3.8-flash"
    assert await settings.model_for("ml", "x", skill="game-story") == "agy:gemini-4-carbon-medium"
    await settings.set(SettingsStore.skill_model_key("game-reading"), "")   # back to inherit
    assert await settings.model_for("ml", "x", skill="game-reading") == "agy:gemini-4-carbon-medium"


async def test_a_dependency_for_a_skill_runs_on_its_model_in_a_thread_made_for_it(manager, config, db):
    project = config.default_project
    enable(config, project, "game-reading")                 # declares no thread
    settings = SettingsStore(db)
    await settings.set(SettingsStore.MODEL, "claude-opus-5-5")
    await settings.set(SettingsStore.skill_model_key("game-reading"), "oc:qwen3.8-flash")

    parent = await manager.submit("a lesson", title="Lesson", project=project)
    assert parent.model == "claude-opus-5-5"
    child = await manager.graph.spawn(parent, skill="game-reading", title="The source", brief="Retell it.")
    thread = await ThreadStore(db).get(child.thread_id)
    assert thread.title == "Game reading" and thread.skill == "game-reading"
    assert child.model == "oc:qwen3.8-flash"
    assert await manager.graph.store.of(parent.id) == [child.id]

    # Changed while it waits in the queue: the claim takes the skill's model of now.
    await settings.set(SettingsStore.skill_model_key("game-reading"), "claude-sonnet-5-5")
    claimed = await manager.tasks.claim("w", model_for=manager.model_for, skip=[parent.id])
    assert claimed is not None and claimed.id == child.id and claimed.model == "claude-sonnet-5-5"


async def test_a_skill_this_project_lacks_gets_no_thread(manager, config):
    from dex.deps import Refused

    parent = await manager.submit("a lesson", title="Lesson", project=config.default_project)
    with pytest.raises(Refused, match="no thread"):
        await manager.graph.spawn(parent, skill="game-nothing", title="x", brief="y")


async def test_a_task_whose_model_is_out_of_quota_waits_for_its_reset(manager, db):
    settings = SettingsStore(db)
    await settings.set(SettingsStore.LIMIT_SNAPSHOT, {
        "antigravity": {"status": "rejected", "resetsAt": time.time() + 900, "seenAt": time.time()},
    })
    until = await manager.spent_until("agy:gemini-4-carbon-medium")
    assert until is not None and until > time.time() + 800
    assert await manager.spent_until("oc:qwen3.8-flash") is None

    held = Task(problem="p", title="held", slug="held")
    held.model = "x"
    await manager.tasks.create(held)
    # The claim passes over what it is told to skip.
    assert await manager.tasks.claim("w", skip=[held.id]) is None
    claimed = await manager.tasks.claim("w")
    assert claimed is not None and claimed.id == held.id
    assert claimed.state is TaskState.RUNNING


async def test_the_skills_model_beats_the_projects_for_its_dependencies(manager, config, db):
    """Both overrides set: the project's runs everything else, the skill's runs its own work."""
    project = config.default_project
    enable(config, project, "game-reading")
    settings = SettingsStore(db)
    await settings.set(SettingsStore.MODEL, "claude-opus-5-5")
    await settings.set(SettingsStore.project_model_key(project), "agy:gemini-4-carbon-medium")
    await settings.set(SettingsStore.skill_model_key("game-reading"), "oc:qwen3.8-flash")

    parent = await manager.submit("a lesson", title="Lesson", project=project)
    assert parent.model == "agy:gemini-4-carbon-medium"          # the project's
    child = await manager.graph.spawn(parent, skill="game-reading", title="The source", brief="Retell it.")
    assert child.model == "oc:qwen3.8-flash"                      # the skill's, over the project's
    claimed = await manager.tasks.claim("w", model_for=manager.model_for, skip=[parent.id])
    assert claimed is not None and claimed.id == child.id and claimed.model == "oc:qwen3.8-flash"
