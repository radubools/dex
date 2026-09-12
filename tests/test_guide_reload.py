"""AGENTS.md must be re-read for every run, not baked in at task creation.

The guide changes as the project's conventions change, and a task that started
yesterday — or is resumed after failing — should be told today's rules.
"""

from __future__ import annotations

import pytest

from dex.bus import EventBus
from dex.models import TaskState
from dex.queue import TaskManager


class CapturingClient:
    """Stands in for ClaudeSDKClient and records the brief it is given."""

    prompts: list[str] = []
    options: list[object] = []

    def __init__(self, options=None):
        CapturingClient.options.append(options)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def query(self, prompt, session_id="default"):
        CapturingClient.prompts.append(prompt)

    async def receive_response(self):
        # No messages: the run completes immediately after the brief is sent.
        return
        yield  # pragma: no cover


@pytest.fixture
async def manager(config, db, monkeypatch):
    monkeypatch.setattr("dex.runner.ClaudeSDKClient", CapturingClient)
    CapturingClient.prompts = []
    CapturingClient.options = []
    bus = EventBus(db)
    await bus.start()
    manager = TaskManager(config, bus, db)
    try:
        yield manager
    finally:
        await manager.stop()
        await bus.stop()


def write_guide(config, text: str) -> None:
    """The guide lives in the project directory, beside its task directories."""
    directory = config.project_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "AGENTS.md").write_text(text)


async def run_one(manager, task) -> str:
    """Run a task through the real runner and return the brief it sent."""
    from dex.runner import TaskRunner

    before = len(CapturingClient.prompts)
    runner = TaskRunner(task, manager.config, manager.bus, manager.tasks, manager.settings)
    await runner.run()
    assert len(CapturingClient.prompts) > before, "the runner never sent a brief"
    return CapturingClient.prompts[-1]


async def test_a_fresh_task_gets_the_guide_as_it_is_on_disk(manager, config):
    write_guide(config, "# Project\n\nMARKER-ORIGINAL\n")
    task = await manager.submit("Two Sum", title="Two Sum")

    brief = await run_one(manager, task)
    assert "MARKER-ORIGINAL" in brief


async def test_a_later_run_picks_up_an_edited_guide(manager, config):
    """The guide is read per run, so editing it changes the next task."""
    write_guide(config, "# Project\n\nMARKER-ORIGINAL\n")
    first = await manager.submit("Two Sum", title="Two Sum")
    assert "MARKER-ORIGINAL" in await run_one(manager, first)

    write_guide(config, "# Project\n\nMARKER-EDITED\n")
    second = await manager.submit("LRU Cache", title="LRU Cache")
    brief = await run_one(manager, second)

    assert "MARKER-EDITED" in brief
    assert "MARKER-ORIGINAL" not in brief


async def test_resuming_a_failed_task_reloads_the_edited_guide(manager, config):
    """The case that matters: the guide changed while the task was broken."""
    write_guide(config, "# Project\n\nMARKER-ORIGINAL\n")
    task = await manager.submit("Two Sum", title="Two Sum")
    assert "MARKER-ORIGINAL" in await run_one(manager, task)

    await manager.tasks.set_state(task.id, TaskState.FAILED)
    write_guide(config, "# Project\n\nMARKER-EDITED\n")

    resumed = await manager.resume(task.id)
    assert resumed is not None
    brief = await run_one(manager, resumed)

    assert "MARKER-EDITED" in brief, "a resumed run must not reuse the old guide"
    assert "MARKER-ORIGINAL" not in brief
    # And it is still a resume: the continuation instruction is there.
    assert "Resuming" in brief


async def test_rerunning_reloads_the_edited_guide(manager, config):
    write_guide(config, "# Project\n\nMARKER-ORIGINAL\n")
    task = await manager.submit("Two Sum", title="Two Sum")
    await run_one(manager, task)
    await manager.tasks.set_state(task.id, TaskState.SUCCEEDED)

    write_guide(config, "# Project\n\nMARKER-EDITED\n")
    again = await manager.rerun(task.id)
    assert "MARKER-EDITED" in await run_one(manager, again)


async def test_a_queued_follow_up_reloads_the_edited_guide(manager, config):
    write_guide(config, "# Project\n\nMARKER-ORIGINAL\n")
    task = await manager.submit("Two Sum", title="Two Sum")
    await run_one(manager, task)
    await manager.tasks.set_state(task.id, TaskState.SUCCEEDED)

    await manager.queue_message(task.id, "add a benchmark")
    write_guide(config, "# Project\n\nMARKER-EDITED\n")
    follow_up = [t for t in await manager.tasks.list() if t.attempt == 2]
    assert follow_up, "queueing a note on a finished task should start a follow-up"

    brief = await run_one(manager, follow_up[0])
    assert "MARKER-EDITED" in brief
