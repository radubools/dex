"""A project's work stays inside that project, and an edit rewrites in place.

Asking to regenerate every algorithm produced a plan that included yoga poses:
the planner was shown every slug in the database as "packages that already
exist in this project". And a regeneration built `word-rectangle-2` beside
`word-rectangle` rather than rewriting it, which is how one problem ended up
with three directories.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dex.api import create_app
from dex.models import Task
from dex.planner import Plan, PlannedTask, parse_plan
from dex.prompts import generation_prompt, planner_prompt
from dex.store import TaskStore


@pytest.fixture
def client(config, db, monkeypatch):
    monkeypatch.setattr("dex.queue.TaskRunner", __import__(
        "tests.conftest", fromlist=["InstantRunner"]).InstantRunner)
    with TestClient(create_app(config)) as c:
        yield c


async def test_slugs_are_listed_per_project_but_unique_globally(db):
    tasks = TaskStore(db)
    # Tasks reference their project, so it has to exist first.
    await db.pool.execute(
        "INSERT INTO projects (slug, name) VALUES ('yoga', 'Yoga') ON CONFLICT DO NOTHING"
    )
    for slug, project in (("two-sum", "algorithms"), ("malasana", "yoga")):
        task = Task(problem="p", title=slug, slug=slug)
        task.project = project
        await tasks.create(task)

    # What the planner is shown: this project only.
    assert await tasks.taken_slugs("algorithms") == {"two-sum"}
    assert await tasks.taken_slugs("yoga") == {"malasana"}
    # What a new slug must avoid: everything, because slug is unique in the table.
    assert {"two-sum", "malasana"} <= await tasks.taken_slugs()


def test_the_planner_is_told_the_list_is_the_whole_world():
    prompt = planner_prompt("regenerate everything", ["two-sum"], "algorithms", "# guide")
    assert "this project is all there is" in prompt
    assert "`updates` names an existing package" in prompt


def test_a_plan_can_say_it_rewrites_an_existing_package():
    raw = """```json
    {"tasks": [
      {"title": "Two Sum", "slug": "two-sum-again", "problem": "redo it",
       "updates": "two-sum"}
    ], "notes": "", "needs_clarification": "", "remaining": ""}
    ```"""
    plan = parse_plan(raw, ["two-sum"])
    assert plan.tasks[0].updates == "two-sum"


def test_an_invented_target_is_ignored():
    """`updates` must name something real, or the task writes to a made-up dir."""
    raw = """```json
    {"tasks": [
      {"title": "X", "slug": "x", "problem": "do x", "updates": "not-a-package"}
    ], "notes": "", "needs_clarification": "", "remaining": ""}
    ```"""
    plan = parse_plan(raw, ["two-sum"])
    assert plan.tasks[0].updates == ""


async def test_confirming_an_edit_writes_into_the_existing_directory(client, config):
    thread = client.post("/api/threads", json={}).json()["thread"]
    body = client.post(
        "/api/chat/confirm",
        json={
            "thread_id": thread["id"],
            "tasks": [{"problem": "regenerate it", "title": "Two Sum",
                       "slug": "two-sum-again", "updates": "two-sum"}],
        },
    ).json()

    task = body["tasks"][0]
    # A slug of its own, but the parent's directory.
    assert task["slug"] != "two-sum"
    assert task["outputSlug"] == "two-sum"
    assert Path(task["outputDir"]).name == "two-sum"


def test_the_brief_says_when_it_is_an_edit(tmp_path: Path):
    package = tmp_path / "algorithms" / "two-sum"
    package.mkdir(parents=True)
    (package / "solutions.py").write_text("# already here")

    edit = generation_prompt(
        problem="add narration", task_dir=package,
        python=Path("/usr/bin/python3"), manim_available=True,
    )
    assert "already exists" in edit
    assert "solutions.py" in edit

    fresh = generation_prompt(
        problem="build it", task_dir=tmp_path / "algorithms" / "brand-new",
        python=Path("/usr/bin/python3"), manim_available=True,
    )
    assert "already exists" not in fresh


def test_the_brief_confines_the_agent_to_its_own_directory(tmp_path: Path):
    prompt = generation_prompt(
        problem="x", task_dir=tmp_path / "algorithms" / "thing",
        python=Path("/usr/bin/python3"), manim_available=True,
    )
    assert "Nothing outside" in prompt
    assert "other projects" in prompt


async def test_a_task_runs_from_its_own_directory(config, db, monkeypatch):
    """A relative path must land in the package, not in the repository.

    The agent used to start at the repo root, so anything it ran directly wrote
    where it stood — a stray `manim` left a 15 MB `media/` beside dex's source.
    """
    import asyncio
    import contextlib

    from dex.bus import EventBus
    from dex.runner import TaskRunner
    from dex.store import SettingsStore, TaskStore

    seen: dict[str, str] = {}

    class CapturingClient:
        def __init__(self, options):
            seen["cwd"] = options.cwd

        async def __aenter__(self):
            raise asyncio.CancelledError  # far enough: the options are built

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("dex.runner.ClaudeSDKClient", CapturingClient, raising=False)

    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    task.project = "algorithms"
    bus = EventBus(db)
    runner = TaskRunner(task, config, bus, TaskStore(db), SettingsStore(db))
    with contextlib.suppress(BaseException):
        await runner.run()

    assert seen.get("cwd") == str(config.assets_dir / "algorithms" / "two-sum")
    assert seen["cwd"] != str(config.workspace)


def test_the_planner_is_shown_packages_not_task_names(client, config):
    """`updates` must name a directory the agent can write into.

    Task slugs were included in the list of "packages that already exist", so
    a narration task called `bit-insertion-narrate` looked like a package. The
    planner then wrote briefs to edit it, and thirty tasks were queued against
    directories that had never existed.
    """
    project = config.assets_dir / "algorithms"
    (project / "bit-insertion").mkdir(parents=True, exist_ok=True)
    # A task whose slug is not a package, exactly as a narration pass leaves.
    client.post(
        "/api/tasks",
        json={"problem": "Narrate bit insertion", "slug": "bit-insertion-narrate",
              "updates": "bit-insertion", "project": "algorithms"},
    )

    captured: dict[str, list[str]] = {}

    async def capture(message, cfg, existing, model=None, project=None, guide="", survey=""):
        captured["existing"] = list(existing)
        return Plan(tasks=[])

    import dex.api

    original = dex.api.plan_from_message
    dex.api.plan_from_message = capture
    try:
        client.post("/api/chat/plan", json={"message": "anything", "project": "algorithms"})
    finally:
        dex.api.plan_from_message = original

    assert "bit-insertion" in captured["existing"]
    assert "bit-insertion-narrate" not in captured["existing"]


def test_the_guide_says_what_to_do_with_narration_that_is_already_there(tmp_path: Path):
    """Every narration instruction described building from nothing.

    Asked to prepend an intro to a package whose scenes already spoke, two
    agents read a guide that only ever described writing the whole script, and
    stopped to ask whether they were meant to discard six existing cues.
    """
    guide = (Path("assets") / "algorithms" / "AGENTS.md").read_text(encoding="utf-8")

    assert "Revising a package that is already narrated" in guide
    assert "existing narration is part of the deliverable" in guide
    # The specific misreading that stopped both tasks.
    assert "only spoken audio" in guide
    # And the reason a re-synthesis has to cover every cue, not just the new one.
    assert "slightly different length each time" in guide
