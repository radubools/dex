"""Continuing a design turn keeps it a design turn.

A turn of the project design chat runs as a task with `scope='design'`: that is
what gives it the design prompt, the widget tools, and a project directory
rather than a package of its own. Continuing one — a follow-up note typed into
it, or a resume — went through the ordinary task path and came back as a
`package` task, so the continuation ran with the generation prompt and built a
package directory for a conversation.
"""

from __future__ import annotations

import json

import pytest

from dex.bus import EventBus
from dex.models import Task, TaskState, continued_problem
from dex.queue import TaskManager


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


async def follow_up_of(manager: TaskManager, parent_id: str) -> Task | None:
    """The task a delivered follow-up started."""
    for task in await manager.tasks.list(limit=50):
        if task.parent_id == parent_id:
            return task
    return None


def design_task(**over) -> Task:
    task = Task(
        problem=json.dumps({
            "message": "add a widget for scores",
            "guide": "# Music\n\nThe guide as it stood.",
            "history": "user: hello\ndex: hi",
        }),
        title="add a widget for scores",
        slug="design-turn",
    )
    task.scope = "design"
    for key, value in over.items():
        setattr(task, key, value)
    return task


# ----------------------------------------------------- the packed payload

def test_a_continued_design_turn_keeps_its_guide_and_history():
    """The note goes inside the message, not after the JSON.

    `problem` is JSON for a design turn. Appending prose left a string that no
    longer parsed, and `design_payload` then treated the whole blob as the
    message — so the turn continued with an empty guide and no history, and
    would have rewritten the project's guide from nothing.
    """
    task = design_task()

    packed = json.loads(continued_problem(task, "## Follow-up\n\nalso add tempo"))

    assert packed["guide"] == "# Music\n\nThe guide as it stood."
    assert packed["history"] == "user: hello\ndex: hi"
    assert "add a widget for scores" in packed["message"]
    assert "also add tempo" in packed["message"]


def test_the_continued_payload_is_still_readable_as_a_design_turn():
    task = design_task()
    task.problem = continued_problem(task, "## Follow-up\n\nand a legend")

    payload = task.design_payload()

    assert payload["guide"].startswith("# Music")
    assert "and a legend" in payload["message"]


def test_an_ordinary_task_still_gets_the_note_appended():
    """Prose is prose; only the design payload needs unpacking."""
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")

    assert continued_problem(task, "NOTE") == "Two Sum\n\nNOTE"


# --------------------------------------------------------------- the scope

async def test_a_follow_up_on_a_design_turn_is_still_a_design_turn(manager):
    """Typing into a finished turn is how the designer continues one."""
    task = await manager.submit(
        design_task().problem, title="design turn", scope="design"
    )
    await manager.tasks.set_state(task.id, TaskState.FAILED, error="stopped")

    # `queue_message` delivers straight away when the task has already stopped,
    # which is exactly the case the designer hits.
    await manager.queue_message(task.id, "try again, and keep the tempo marks")
    started = await follow_up_of(manager, task.id)

    assert started is not None
    assert started.scope == "design", "the continuation came back as a package task"
    assert started.is_design
    # And it is a usable turn, not a blob.
    assert started.design_payload()["guide"].startswith("# Music")


async def test_a_resumed_design_turn_is_still_a_design_turn(manager):
    task = await manager.submit(
        design_task().problem, title="design turn", scope="design"
    )
    await manager.tasks.set_state(task.id, TaskState.FAILED, error="stopped")

    resumed = await manager.resume(task.id)

    assert resumed is not None
    assert resumed.scope == "design"
    assert resumed.design_payload()["history"] == "user: hello\ndex: hi"


async def test_a_follow_up_on_an_ordinary_task_stays_a_package_task(manager):
    """The other half: carrying the scope must not make everything design."""
    task = await manager.submit("Two Sum", title="Two Sum")
    await manager.tasks.set_state(task.id, TaskState.SUCCEEDED)

    await manager.queue_message(task.id, "add a test")
    started = await follow_up_of(manager, task.id)

    assert started.scope == "package"
    assert "add a test" in started.problem


class _CollectingBus:
    """Stands in for the event bus, which this is not about.

    A real one writes every published event to the database from a background
    writer, and these tests care about the row `append` wrote, not about event
    persistence. Keeping the real bus out also keeps its writer from outliving
    the test that started it.
    """

    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, event: object) -> object:
        self.published.append(event)
        return event


def _design_runner(config, db, thread_id: str, slug: str):
    from dex.runner import TaskRunner
    from dex.store import SettingsStore, TaskStore

    task = Task(
        project=config.default_project, slug=slug, title="Design turn",
        problem=json.dumps({"message": "x", "guide": "", "history": ""}),
        scope="design", thread_id=thread_id,
    )
    bus = _CollectingBus()
    runner = TaskRunner(
        task, config, bus, TaskStore(db), SettingsStore(db),  # type: ignore[arg-type]
    )
    return runner, bus


async def test_a_design_turn_posts_its_reply_into_the_thread(config, db):
    """The reply is the only thing linking a finished turn back to its run.

    `_post_design_reply` imported `ThreadMessage` from `dex.models`, where it
    has never lived, inside a `try` that caught everything. Every design turn
    ever run raised `ImportError` there and logged one line; the thread kept
    the operator's question and lost every answer, and the run's activity
    became unreachable the moment it stopped being live. Nothing exercised
    this path, so nothing said so.
    """
    from dex.store import ThreadStore

    threads = ThreadStore(db)
    thread = await threads.create("Design", kind="project_design")
    runner, bus = _design_runner(config, db, thread.id, "design-turn")

    await runner._post_design_reply(thread.id, "Renamed the deliverable.")

    posted = (await threads.get(thread.id)).messages
    assert [m.text for m in posted] == ["Renamed the deliverable."]
    assert posted[0].role == "dex" and posted[0].kind == "text"
    # The id is the way back into the run's activity.
    assert posted[0].data["taskId"] == runner.task.id
    assert posted[0].data["failed"] is False
    # And the thread is told without waiting for a reload.
    assert [getattr(e, "type", None) for e in bus.published] == ["thread_message"]


async def test_a_failed_design_turn_still_leaves_a_way_back(config, db):
    """A turn that failed is the one most worth being able to open."""
    from dex.store import ThreadStore

    threads = ThreadStore(db)
    thread = await threads.create("Design", kind="project_design")
    runner, _ = _design_runner(config, db, thread.id, "design-fail")

    await runner._post_design_reply(thread.id, "That turn failed.\n\nboom", failed=True)

    posted = (await threads.get(thread.id)).messages
    assert posted[0].data["failed"] is True
    assert posted[0].data["taskId"] == runner.task.id
