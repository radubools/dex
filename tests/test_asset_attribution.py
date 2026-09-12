"""Assets belong to the directory a task writes into, not to its slug.

A resumed or re-run attempt keeps its parent's output directory while having a
slug of its own, so anything keyed on the slug looks in the wrong place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dex.bus import EventBus
from dex.models import TaskState
from dex.queue import TaskManager
from dex.watcher import AssetWatcher


@pytest.fixture
async def manager(config, db):
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        yield manager
    finally:
        await bus.stop()


async def test_a_resumed_attempt_writes_into_its_parents_directory(manager, config):
    original = await manager.submit("Two Sum", title="Two Sum")
    await manager.tasks.set_state(original.id, TaskState.FAILED)
    resumed = await manager.resume(original.id)

    # Distinct identities, one shared package.
    assert resumed.slug != original.slug
    assert resumed.output_slug == original.slug
    assert resumed.output_dir(config.assets_dir) == original.output_dir(config.assets_dir)
    assert resumed.output_dir(config.assets_dir).parent.name == "algorithms"

    # This is what the Files tab asks the assets endpoint for.
    payload = resumed.to_json(config.assets_dir)
    assert payload["outputSlug"] == original.slug
    assert Path(payload["outputDir"]).name == original.slug


async def test_the_watcher_credits_the_running_attempt(manager, config, db):
    original = await manager.submit("Two Sum", title="Two Sum")
    await manager.tasks.set_state(original.id, TaskState.FAILED)
    resumed = await manager.resume(original.id)
    manager.live[resumed.id] = resumed

    watcher = AssetWatcher(config, manager.bus, manager)
    written = resumed.output_dir(config.assets_dir) / "solutions.py"
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text("x")

    task_id, rel = await watcher._task_for(written)
    assert task_id == resumed.id, "a file lands on the attempt that is running"
    # Asset paths are relative to the assets root, so they carry the project.
    assert rel == f"algorithms/{original.slug}/solutions.py"


async def test_assets_still_attribute_when_nothing_is_running(manager, config, db):
    original = await manager.submit("Two Sum", title="Two Sum")
    await manager.tasks.set_state(original.id, TaskState.FAILED)
    resumed = await manager.resume(original.id)
    await manager.tasks.set_state(resumed.id, TaskState.SUCCEEDED)

    watcher = AssetWatcher(config, manager.bus, manager)
    written = resumed.output_dir(config.assets_dir) / "explanation.md"
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text("# x")

    task_id, _ = await watcher._task_for(written)
    # The most recent attempt for that directory, found through the database.
    assert task_id == resumed.id


async def test_a_deleted_file_is_reported_so_the_count_stops_claiming_it(manager, config):
    """An agent's scratch file must leave the list when the agent removes it.

    The watcher used to skip deletions outright, so a file written and then
    removed stayed in the task's assets forever: the Files tab counted two
    where the directory held one.
    """
    from watchfiles import Change

    task = await manager.submit("Two Sum", title="Two Sum")
    manager.live[task.id] = task
    directory = task.output_dir(config.assets_dir)
    directory.mkdir(parents=True, exist_ok=True)
    scratch = directory / "check.py"
    scratch.write_text("print('scratch')")

    watcher = AssetWatcher(config, manager.bus, manager)

    await watcher._handle(Change.added, str(scratch))
    # Paths are relative to the assets root, not the task directory.
    assert task.artifacts == ["algorithms/two-sum/check.py"]

    scratch.unlink()
    await watcher._handle(Change.deleted, str(scratch))
    assert task.artifacts == [], "a removed file must leave the task's assets"


async def test_the_deletion_event_says_so(manager, config):
    """The UI drops the path on `deleted`, so the event has to carry it."""
    from watchfiles import Change

    task = await manager.submit("Two Sum", title="Two Sum")
    manager.live[task.id] = task
    directory = task.output_dir(config.assets_dir)
    directory.mkdir(parents=True, exist_ok=True)
    scratch = directory / "check.py"
    scratch.write_text("x")

    seen = []
    manager.bus.publish = lambda event: seen.append(event)

    watcher = AssetWatcher(config, manager.bus, manager)
    await watcher._handle(Change.deleted, str(scratch))

    assert [e.data["change"] for e in seen] == ["deleted"]
    assert seen[0].data["path"] == "algorithms/two-sum/check.py"


async def test_an_added_event_for_a_vanished_file_is_treated_as_a_deletion(manager, config):
    """watchfiles can batch changes into a trailing `added` for a gone path."""
    from watchfiles import Change

    task = await manager.submit("Two Sum", title="Two Sum")
    manager.live[task.id] = task
    directory = task.output_dir(config.assets_dir)
    directory.mkdir(parents=True, exist_ok=True)
    scratch = directory / "check.py"
    scratch.write_text("x")

    watcher = AssetWatcher(config, manager.bus, manager)
    await watcher._handle(Change.added, str(scratch))
    assert task.artifacts == ["algorithms/two-sum/check.py"]

    scratch.unlink()
    # The straggler: reported as added, but there is nothing there.
    seen = []
    manager.bus.publish = lambda event: seen.append(event)
    await watcher._handle(Change.added, str(scratch))
    assert task.artifacts == [], "a file that is gone must not be re-added"
    # The UI keys off this field, so it has to say what is true of the file.
    assert seen[0].data["change"] == "deleted"
