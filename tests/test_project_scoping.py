"""A prompt must be planned for the project it was typed in, not the default.

The failure this pins down: typing a yoga pose while the Yoga project was
selected produced an algorithm-generation task, because the planner prompt was
written for algorithms and never learned which project it was planning for.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from dex.prompts import (
    GENERATION_SYSTEM,
    PLANNER_SYSTEM,
    generation_prompt,
    planner_prompt,
)
from dex.store import ThreadStore

YOGA_GUIDE = "# Yoga\nEach task emits 3D joint positions for one named pose."


def test_the_prompt_carries_the_project_and_its_guide():
    prompt = planner_prompt("Malasana", [], "yoga", YOGA_GUIDE)
    assert "**yoga** project" in prompt
    assert "3D joint positions" in prompt


def test_nothing_in_the_prompt_presumes_algorithms():
    """The planner used to say 'algorithm' regardless of the project."""
    prompt = planner_prompt("Malasana", ["two-sum"], "yoga", YOGA_GUIDE)
    assert "algorithm" not in prompt.lower()
    assert "algorithm" not in PLANNER_SYSTEM.lower()


def test_a_missing_guide_still_names_the_project():
    prompt = planner_prompt("Malasana", [], "yoga", "")
    assert "yoga" in prompt


def test_planning_without_a_project_still_works():
    prompt = planner_prompt("Two Sum", [])
    assert "Two Sum" in prompt


async def test_threads_are_listed_only_for_their_own_project(db):
    """The sidebar's refetch dropped the project; the store never did."""
    threads = ThreadStore(db)
    await db.pool.execute(
        "INSERT INTO projects (slug, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        "yoga", "Yoga",
    )
    algo = await threads.create("algo talk", project="algorithms")
    yoga = await threads.create("yoga talk", project="yoga")

    listed = {t.id for t in await threads.list("yoga")}
    assert yoga.id in listed
    assert algo.id not in listed, "another project's thread leaked into the list"

    # Unscoped listing still returns everything, which is what made the UI bug
    # invisible from the backend's side.
    assert {algo.id, yoga.id} <= {t.id for t in await threads.list()}


# --------------------------------------------------------------- generation

ASSETS = Path(__file__).resolve().parent.parent / "assets"
PYTHON = Path("/usr/bin/python3")


def _brief(project: str, slug: str, manim: bool = True) -> str:
    return generation_prompt(
        problem="anything", task_dir=ASSETS / project / slug,
        python=PYTHON, manim_available=manim,
    )


def test_the_system_prompt_names_no_subject():
    """It used to open with 'an interview-style algorithm problem'."""
    for word in ("algorithm", "pytest", "solutions.py", "interview"):
        assert word not in GENERATION_SYSTEM.lower()


def test_a_project_gets_its_own_deliverable_and_not_another_s():
    """The bug: a yoga task was briefed to write solve_<approach> functions."""
    yoga = _brief("yoga", "malasana")
    assert "solve_<approach>" not in yoga
    assert "SOLUTIONS" not in yoga
    assert "test_solutions.py" not in yoga


def test_the_algorithms_spec_survived_the_move_into_its_guide():
    algo = _brief("algorithms", "two-sum")
    for expected in ("solve_<approach>", "SOLUTIONS", "test_solutions.py",
                     "explanation.md", "manifest.json"):
        assert expected in algo, expected


def test_the_guide_s_placeholders_are_filled_in():
    """The guide names paths it cannot know when written."""
    algo = _brief("algorithms", "two-sum")
    assert "{task_dir}" not in algo and "{python}" not in algo
    assert str(ASSETS / "algorithms" / "two-sum") in algo
    assert str(PYTHON) in algo


def test_missing_manim_is_reported_as_a_caveat_only_when_it_is_missing():
    assert "manim is not installed" in _brief("algorithms", "two-sum", manim=False)
    assert "manim is not installed" not in _brief("algorithms", "two-sum", manim=True)
