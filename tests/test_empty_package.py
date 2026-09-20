"""A run that ends cleanly having written nothing has not succeeded.

Eight tasks in one batch reported success with empty directories: each had
decided to wait for a file a sibling was going to write, and ended its turn to
do the waiting. Nothing re-invokes a finished task, so the wait never ended —
and because the runner asked only whether the agent had raised, every one of
them came out green. The package is what says whether the work happened.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dex.bus import EventBus
from dex.models import Task, TaskState
from dex.runner import EMPTY_PACKAGE, TaskRunner
from dex.store import SettingsStore, TaskStore


class QuietClient:
    """An agent that finishes its turn without saying or writing anything."""

    def __init__(self, options):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        return None

    async def receive_response(self):
        return
        yield  # pragma: no cover - never reached, makes this a generator


def agent_that(write) -> type:
    """A client that writes while the run is open, as a real agent would."""

    class WritingClient(QuietClient):
        async def query(self, prompt):
            write()

    return WritingClient


@pytest.fixture
async def run_task(config, db, monkeypatch):
    bus = EventBus(db)
    store = TaskStore(db)

    async def go(task: Task, client: type = QuietClient) -> Task:
        monkeypatch.setattr("dex.runner.ClaudeSDKClient", client, raising=False)
        await store.create(task)
        await TaskRunner(task, config, bus, store, SettingsStore(db)).run()
        return task

    try:
        yield go
    finally:
        await bus.stop()


def package_task(**fields) -> Task:
    fields.setdefault("slug", "section-one")
    task = Task(problem="Translate §1", title="§1", **fields)
    task.project = "algorithms"
    return task


def package_of(config, name: str = "section-one") -> Path:
    return config.assets_dir / "algorithms" / name


# ------------------------------------------------------------ nothing at all


async def test_a_task_that_wrote_nothing_fails(run_task):
    task = await run_task(package_task())

    assert task.state is TaskState.FAILED
    assert task.error == EMPTY_PACKAGE


async def test_the_failure_says_that_nothing_would_have_woken_it(run_task):
    """The operator has to be told why waiting was never going to work."""
    task = await run_task(package_task())

    assert "nothing would have woken it" in task.error
    assert "resume the task" in task.error


async def test_a_task_that_wrote_an_artifact_succeeds(run_task, config):
    package = package_of(config)

    def write():
        package.mkdir(parents=True, exist_ok=True)
        (package / "notes.md").write_text("# §1\n", encoding="utf-8")

    task = await run_task(package_task(), agent_that(write))

    assert task.state is TaskState.SUCCEEDED
    assert task.error is None


async def test_scratch_on_its_own_does_not_count_as_output(run_task, config):
    """The watcher's own test, so what it declines to announce is not output.

    A task that only ever ran the interpreter leaves `__pycache__` behind, and
    a narration pass that died mid-way leaves `.cues/`. Counting either would
    hand back exactly the green row this check exists to prevent.
    """
    package = package_of(config)

    def write():
        (package / "__pycache__").mkdir(parents=True, exist_ok=True)
        (package / "__pycache__" / "common.cpython-312.pyc").write_bytes(b"\x00")
        (package / ".cues").mkdir(exist_ok=True)
        (package / ".cues" / "01.wav").write_bytes(b"\x00")
        (package / "draft.part").write_text("half", encoding="utf-8")

    task = await run_task(package_task(), agent_that(write))

    assert task.state is TaskState.FAILED


async def test_a_project_wide_sweep_is_not_judged_on_a_package(run_task, config):
    """It edits packages that are already there and has none of its own."""
    (config.assets_dir / "algorithms").mkdir(parents=True, exist_ok=True)

    task = await run_task(package_task(scope="project"))

    assert task.state is TaskState.SUCCEEDED


# ------------------------------------------------- a package already full


async def test_inheriting_a_full_package_is_not_filling_one(run_task, config):
    """A resumed attempt, and a sibling sharing a package, both start full.

    Measured against emptiness, either could end its turn having done nothing
    and still come out green on work somebody else had already done — which is
    the whole failure back again, wearing a directory that looks productive.
    """
    package = package_of(config, "history-ro")
    package.mkdir(parents=True)
    (package / "skeleton.json").write_text('{"segments": []}', encoding="utf-8")

    task = package_task(slug="section-two")
    task.output_slug = "history-ro"
    task = await run_task(task)

    assert task.state is TaskState.FAILED
    # What was already there is untouched: failing a task is not a reason to
    # take a sibling's work away.
    assert (package / "skeleton.json").exists()


async def test_rewriting_an_inherited_file_counts_as_work(run_task, config):
    package = package_of(config, "history-ro")
    package.mkdir(parents=True)
    skeleton = package / "skeleton.json"
    skeleton.write_text('{"segments": []}', encoding="utf-8")
    stamp = skeleton.stat().st_mtime_ns + 1_000_000_000

    def write():
        skeleton.write_text('{"segments": [1, 2]}', encoding="utf-8")
        # Pinned rather than trusted to the clock: a filesystem with coarse
        # timestamps would read the rewrite as no change at all.
        os.utime(skeleton, ns=(stamp, stamp))

    task = package_task(slug="section-two")
    task.output_slug = "history-ro"
    task = await run_task(task, agent_that(write))

    assert task.state is TaskState.SUCCEEDED


# ------------------------------------------------------------------- telling
# The check above is only half of it: an agent that is never told how its turn
# ends cannot be blamed for parking itself. The brief has to say so, and say
# the same thing the runner does.


def test_the_brief_says_that_ending_the_turn_ends_the_task():
    from dex.prompts import GENERATION_SYSTEM

    assert "Your turn is the task" in GENERATION_SYSTEM
    assert "Nothing re-invokes you" in GENERATION_SYSTEM
    # The two things it reached for, and the two that actually work.
    assert "no watcher will call you back" in GENERATION_SYSTEM
    assert "background command you started is ever read again" in GENERATION_SYSTEM
    assert "wait for it inside this turn" in GENERATION_SYSTEM
    assert "mcp__dex__ask_user" in GENERATION_SYSTEM


def test_the_brief_says_what_ending_a_turn_to_wait_costs():
    """It must promise exactly what the runner does, or it is a bluff."""
    from dex.prompts import GENERATION_SYSTEM

    assert "the package stays empty and the task is marked failed" in GENERATION_SYSTEM


def test_the_brief_says_siblings_are_not_a_sequence():
    from dex.prompts import GENERATION_SYSTEM

    assert "Other tasks are not a sequence" in GENERATION_SYSTEM
    assert "queued alongside you is running" in GENERATION_SYSTEM


def test_the_planner_is_told_not_to_plan_a_dependency():
    """The root cause: a plan whose tasks need each other cannot come out."""
    from dex.prompts import planner_prompt

    prompt = planner_prompt("translate this page", ["two-sum"], "algorithms", "# guide")

    assert "Never plan a task that needs another task's output" in prompt
    # Not spanning the line wrap: this template is hard-wrapped.
    assert "way for one task to wait for another" in prompt


def test_the_planner_is_told_a_shared_package_is_the_exception():
    from dex.prompts import planner_prompt

    prompt = planner_prompt("translate this page", ["two-sum"], "algorithms", "# guide")

    assert "the rare exception" in prompt
    assert "the default is not to use it" in prompt
    # And that sharing does not buy them an order to run in.
    assert "A shared `package` does not change this" in prompt
