"""A project-scoped task: one small uniform edit across every package.

The alternative is a generation run per package, which for something like
tagging is a hundred agent sessions doing a few greps each. The scope widens
what a task may touch — deliberately paired with a much narrower mandate in the
brief — and never past the one project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dex.models import Task
from dex.planner import parse_plan
from dex.prompts import generation_prompt, planner_prompt


def package_task() -> Task:
    task = Task(problem="Two Sum", title="Two Sum", slug="two-sum")
    task.project = "algorithms"
    return task


def sweep_task() -> Task:
    task = Task(problem="Tag everything", title="Tag packages", slug="tag-packages")
    task.project = "algorithms"
    task.scope = "project"
    return task


def test_a_package_task_still_gets_its_own_directory():
    assert package_task().output_dir(Path("/assets")) == Path("/assets/algorithms/two-sum")


def test_a_project_task_gets_the_project_directory():
    # Not a directory of its own: it edits packages that are already there.
    assert sweep_task().output_dir(Path("/assets")) == Path("/assets/algorithms")


def test_a_project_task_never_reaches_past_its_project():
    # The whole point of the confinement work: wider than one package, but the
    # project boundary still holds.
    assert sweep_task().output_dir(Path("/assets")) != Path("/assets")


def test_the_plan_defaults_to_package_scope():
    plan = parse_plan('```json\n{"tasks":[{"title":"T","problem":"P","slug":"t"}]}\n```', [])
    assert plan.tasks[0].scope == "package"


def test_the_plan_can_ask_for_a_project_scoped_task():
    raw = '```json\n{"tasks":[{"title":"Tag","problem":"Tag them","slug":"tag","scope":"project"}]}\n```'
    assert parse_plan(raw, []).tasks[0].scope == "project"


def test_an_unrecognised_scope_is_read_as_package():
    # A planner typo must not silently widen what a task may touch.
    raw = '```json\n{"tasks":[{"title":"T","problem":"P","slug":"t","scope":"everything"}]}\n```'
    assert parse_plan(raw, []).tasks[0].scope == "package"


def test_a_project_scoped_task_does_not_claim_to_rewrite_one_package():
    raw = (
        '```json\n{"tasks":[{"title":"Tag","problem":"Tag them","slug":"tag",'
        '"scope":"project","updates":"two-sum"}]}\n```'
    )
    task = parse_plan(raw, ["two-sum"]).tasks[0]
    # `updates` would send it into one package's directory, which is not what a
    # sweep does.
    assert task.updates == ""


def test_the_sweep_brief_forbids_the_work_a_package_task_does(tmp_path):
    project = tmp_path / "algorithms"
    project.mkdir()
    (project / "AGENTS.md").write_text("Project guide here.", encoding="utf-8")

    brief = generation_prompt(
        problem="Add tags to every manifest.",
        task_dir=project,
        python=Path("/py"),
        manim_available=True,
        project_wide=True,
    )

    assert "Project guide here." in brief  # the guide is at the root for a sweep
    assert "Do not regenerate" in brief
    assert "Do not create packages" in brief
    assert f"Nothing outside `{project}` is yours" in brief


def test_the_package_brief_is_unchanged_by_the_new_shape(tmp_path):
    project = tmp_path / "algorithms"
    (project / "two-sum").mkdir(parents=True)
    (project / "AGENTS.md").write_text("Project guide here.", encoding="utf-8")

    brief = generation_prompt(
        problem="Two Sum",
        task_dir=project / "two-sum",
        python=Path("/py"),
        manim_available=True,
    )

    assert "Project guide here." in brief  # still found one level up
    assert "Do not regenerate" not in brief
    # The package, not the project and not the repository: this is the
    # directory every command in the run starts from.
    assert f"Your working directory is `{project / 'two-sum'}`" in brief


def test_the_planner_is_told_when_one_task_beats_a_hundred():
    prompt = planner_prompt("tag all my algorithms", ["two-sum"], "algorithms", "guide")
    assert '"scope": "project"' in prompt
    # The distinction that decides it, stated in the prompt rather than left to
    # the planner's judgement about how many packages are involved.
    assert "mechanical and uniform" in prompt


async def test_a_sweep_may_write_into_any_package_but_not_out_of_the_project(tmp_path):
    """The permission policy follows the task's directory, so widening the
    scope widens what may be written — as far as the project and no further."""
    from dex.permissions import PermissionPolicy

    project = tmp_path / "assets" / "algorithms"
    (project / "two-sum").mkdir(parents=True)
    (tmp_path / "assets" / "yoga").mkdir(parents=True)

    task = sweep_task()
    policy = PermissionPolicy(
        workspace=tmp_path,
        task_dir=task.output_dir(tmp_path / "assets"),
        escalate=None,  # type: ignore[arg-type]
    )

    assert policy._within(str(project / "two-sum" / "manifest.json"), policy.task_dir)
    assert not policy._within(str(tmp_path / "assets" / "yoga" / "pose.json"), policy.task_dir)
    assert not policy._within(str(tmp_path / "elsewhere.txt"), policy.task_dir)
