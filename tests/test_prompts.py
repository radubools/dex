"""The brief handed to the generation agent."""

from __future__ import annotations

from pathlib import Path

from dex.prompts import generation_prompt, project_instructions

REPO = Path(__file__).resolve().parent.parent
ALGORITHMS = REPO / "assets" / "algorithms"


def brief(task_dir: Path) -> str:
    return generation_prompt(
        problem="Two Sum",
        task_dir=task_dir,
        python=Path("/w/.venv/bin/python"),
        manim_available=True,
    )


def test_the_project_guide_is_part_of_the_brief():
    text = brief(ALGORITHMS / "two-sum")
    # Two guides now: the common one, then this project's own. They were one
    # file copied per project, which meant four copies of the same 289 lines
    # drifting apart.
    assert "# How work is done here" in text
    assert "# What this project produces" in text
    # The agent is told, not left to discover.
    assert "manim" in text.lower()


def test_the_brief_explains_how_checkpoints_are_made():
    """Sections drive the step-through control, so the agent must know."""
    text = brief(ALGORITHMS / "two-sum")
    assert "next_section" in text
    # The specific traps, verified against manim's real behaviour.
    assert "autocreated" in text
    assert "discarded" in text.lower() or "dropped" in text.lower()


def test_the_brief_says_not_to_recreate_the_viewer():
    text = brief(ALGORITHMS / "two-sum")
    assert "viewer already exists" in text


def test_the_interpreter_path_is_substituted():
    text = brief(ALGORITHMS / "two-sum")
    assert "{python}" not in text
    assert "/w/.venv/bin/python" in text


def test_a_project_without_a_guide_still_produces_a_brief(tmp_path: Path):
    assert project_instructions(tmp_path / "no-guide", Path("/w/python")) == ""
    text = generation_prompt(
        problem="Two Sum",
        task_dir=tmp_path / "project" / "two-sum",
        python=Path("/w/python"),
        manim_available=True,
    )
    assert "# Request" in text
    assert "# Project instructions" not in text
    # With nothing defining the deliverable, the agent must ask rather than
    # invent one -- it used to inherit the algorithm spec by default.
    assert "No project instructions found" in text
    assert "ask_user" in text


def test_the_algorithms_guide_asks_for_narration_and_captions(tmp_path: Path):
    """The guide is the whole spec for a task, so the workflow has to be in it."""
    guide = (Path(__file__).resolve().parent.parent
             / "assets" / "algorithms" / "AGENTS.md").read_text()
    for expected in (".mp4", ".vtt", "piper", "ffprobe", "at most five times",
                     "--narration", "sections.json"):
        assert expected in guide, expected
    # The audio goes inside the video; a sidecar track drifts when you seek.
    assert "muxed into the video" in guide
    # Piper is a local neural voice; `say` is the robotic system one it replaced.
    assert "en_US-lessac-high" in guide
    assert "say -v" not in guide
    # The pacing complaint that started this: the old animations were unreadable.
    assert "self.wait(1.2)" in guide
    assert "self.wait(0.6)" not in guide


def test_the_brief_opens_a_door_for_shared_utilities():
    """Confinement told the agent nothing outside its directory was ever its own.

    The project guides now define a `utils/` protocol: prove a helper locally,
    then ask the operator at the end whether it should be promoted. Without a
    carve-out here the agent reads the confinement rule last and declines to
    act on an answer the operator already gave.
    """
    text = brief(ALGORITHMS / "two-sum")
    # The confinement itself has to survive -- the exception is narrow, not a
    # replacement for the rule.
    assert "Nothing outside" in text
    assert "The one exception is shared utilities" in text
    # The three things that make the loop actually run.
    assert "Reading is always allowed" in text
    assert '`kind` set to `"utility"`' in text
    assert "may come back instantly" in text
    assert "stops for the operator" in text
    # And it fires at the finish, not mid-task.
    assert "before you write your summary" in text


def test_a_project_wide_sweep_can_promote_a_utility_too():
    """A sweep is the pass most likely to write one helper into a dozen packages."""
    text = generation_prompt(
        problem="tag everything",
        task_dir=ALGORITHMS,
        python=Path("/w/.venv/bin/python"),
        manim_available=True,
        project_wide=True,
    )
    assert "do not create directories" in text
    # Not spanning the line wrap: the brief is hard-wrapped at 79 columns.
    assert "A skill's `utils/` is" in text
    assert "the one exception" in text
    assert "utils/API.md" in text


def test_the_common_guide_defines_the_protocol_the_brief_points_at():
    """The brief defers to the instructions, so they must carry the protocol.

    One file now, not four copies of it. Read once here rather than per
    project, which is the duplication that made a fix to one guide reach
    nobody else.
    """
    for project in ("common",):
        guide = (REPO / "AGENTS.common.md").read_text(encoding="utf-8")
        # The generated index, not the modules: a tenth of the bytes.
        assert "Shared utilities" in guide, project
        assert "utils/API.md" in guide, project
        # Ask at the end, with the operator's two options.
        assert "prove it first, then ask at the end" in guide, project
        assert "dex answers this" in guide, project
        # A helper is promoted into a *skill*. The project's own `utils/` is
        # materialised from the enabled skills, so a module written there is
        # undone by the next materialise and reaches no other project — which
        # is the mistake the wording exists to prevent.
        assert "Put it in the `<skill>` skill" in guide, project
        assert "skills/<skill>/utils/" in guide, project
        # And dex regenerates the index now, so the guide must not send the
        # task to do it by hand.
        assert "dex.tools.utils_api" not in guide, project
        # Utilities general enough that tasks are not forever editing them.
        assert "Keep them general" in guide, project
        assert "Return data, not verdicts or prose" in guide, project
        assert "Prefer\ncomposing over changing" in guide, project
        # A change to something already shared must not break its callers.
        assert "backward-compatible" in guide, project
        assert "already import that module and you cannot test them" in guide, project
        # A "no" sticks, so the next task does not ask the same question again.
        assert "Kept local" in guide, project
        assert "Why it stays local" in guide, project
        assert "ruled it out" in guide, project


def test_declining_a_promotion_is_recorded_so_it_is_not_re_asked():
    """A "no" that leaves no trace gets re-litigated by every task after it.

    Without the *Kept local* table the operator answers "keep it local", the
    next task writes the same helper, reaches the same conclusion, and asks
    again.
    """
    text = brief(ALGORITHMS / "two-sum")
    assert "The project instructions carry a veto" in text
    assert "never edit that list yourself" in text
    # A declined write is an answer, not a reason to give up halfway.
    assert "If a write\n  is declined" in text
    assert "leave `utils/` as you found\n  it" in text


def test_a_new_project_starts_with_a_skeleton_not_the_protocol():
    """The template seeds only what is particular to a project.

    The protocol — environment, data directory, viewers, the sharing rules —
    is read from the common guide, so copying it into every new project is
    exactly the duplication this removed.
    """
    from dex.projects import starter_guide

    guide = starter_guide(REPO, "Demo", "A demo.")
    assert "## What to produce" in guide
    assert "## Conventions" in guide
    assert "Shared utilities" not in guide
    assert len(guide.splitlines()) < 40, "a seed, not a protocol"


def test_the_common_guide_carries_the_protocol():
    """Whatever left the project guides has to be somewhere, and this is it."""
    common = (REPO / "AGENTS.common.md").read_text(encoding="utf-8")
    for expected in (
        "Shared utilities",
        "utils/API.md",
        "mcp__dex__utility_proposals_enabled",
        "prove it first, then ask at the end",
        "Put it in the `<skill>` skill",
        "skills/<skill>/utils/",
    ):
        assert expected in common, expected


def test_narration_files_are_recognised_as_assets():
    """Otherwise the watcher ignores them and they never reach the UI."""
    from dex.watcher import KINDS, is_artifact

    assert KINDS[".m4a"] == "audio"
    assert KINDS[".vtt"] == "captions"
    assert is_artifact(Path("algorithms/merge-sort/bottom_up.m4a"))
    assert is_artifact(Path("algorithms/merge-sort/bottom_up.vtt"))
    # The per-cue working files are scratch, not deliverables.
    assert not is_artifact(Path("algorithms/merge-sort/.cues/01.aiff"))


def test_video_sections_become_seek_times():
    """Looping a step needs times, not frames — a player seeks in seconds."""
    import json
    from dex.animation import describe

    root = Path(__file__).resolve().parent.parent
    video = root / "assets" / "algorithms" / "merge-sort" / "narrated_demo.mp4"
    if not video.exists():
        import pytest as _pytest
        _pytest.skip("no narrated sample present")
    info = describe(video)
    assert info["kind"] == "video"
    marks = info["checkpoints"]
    assert [m["name"] for m in marks] == ["Lay out the array", "Compare the pair"]
    # Contiguous, and starting at zero: that is what makes looping exact.
    assert marks[0]["start"] == 0.0
    assert marks[0]["end"] == marks[1]["start"]


def test_the_design_brief_makes_it_look_before_it_writes():
    """A guide written from the pasted copy alone describes a project that isn't there.

    The designer used to be a single tool-less call: it could not look at
    anything even if it wanted to. Now it can, so it has to be told to.
    """
    from dex.prompts import DESIGN_SYSTEM, design_prompt

    brief = design_prompt(
        message="tags are inconsistent",
        project="music",
        project_dir=Path("/w/assets/music"),
        python=Path("/w/.venv/bin/python"),
        workspace=Path("/w"),
        guide="# Project: Music",
        history="(nothing yet)",
    )
    assert "Look before you write" in brief
    assert "grep" in brief
    # Reading is unrestricted; only writing is confined. Worth saying, because
    # the generation brief says the opposite for ordinary tasks.
    assert "read anywhere in the repository" in brief

    assert "Check what you wrote" in brief
    assert "Read the file back" in brief
    # The failure mode that is silent: a broken registry is ignored, and every
    # file in the project quietly falls back to the code viewer.
    assert "valid JSON" in brief

    # And the same expectation survives in the system prompt, which is what
    # persists across a long conversation.
    assert "Look before you write" in DESIGN_SYSTEM
    assert "Check what you wrote" in DESIGN_SYSTEM


def test_the_promotion_path_carries_the_version():
    """A skill's directory is `name@version`, and only that path is writable.

    A brief naming `skills/yoga-figure/utils/` sends a task somewhere that does
    not exist, and the permission policy refuses the write — so the path in the
    brief has to be the one the runner actually allows.
    """
    text = generation_prompt(
        problem="x",
        task_dir=ALGORITHMS,
        python=Path("/w/.venv/bin/python"),
        manim_available=False,
        skills=[("figure", "Geometry.", "/w/skills/figure@1fc7b2", True)],
    )
    # The versioned path, because that is the only one the permission policy
    # allows — and the SKILL.md beside it, which is the second tier.
    assert "Read `/w/skills/figure@1fc7b2/SKILL.md`." in text
    assert "`/w/skills/figure@1fc7b2/utils/`" in text
    assert "skills/figure/utils" not in text


def test_a_widget_only_skill_is_listed_but_not_offered_for_promotion():
    """Its SKILL.md is worth reading; there is nowhere in it to put a module."""
    text = generation_prompt(
        problem="x",
        task_dir=ALGORITHMS,
        python=Path("/w/.venv/bin/python"),
        manim_available=False,
        skills=[("viewer", "Draws a pose.", "/w/skills/viewer@aaaaaa", False)],
    )
    assert "Read `/w/skills/viewer@aaaaaa/SKILL.md`." in text
    assert "Promote a helper" not in text


def test_a_skill_with_no_utils_is_not_offered_for_promotion():
    """A widget-only skill has nowhere to put a module."""
    text = generation_prompt(
        problem="x",
        task_dir=ALGORITHMS,
        python=Path("/w/.venv/bin/python"),
        manim_available=False,
        skills=[],
    )
    assert "Skills enabled" not in text


# ------------------------------------------------- the generated index


def test_the_index_says_which_skill_a_module_came_from(tmp_path):
    """`utils/` is materialised from several skills, and a promotion targets one.

    The brief lists the skills and their paths; the index lists the module
    names a reader actually has in hand. Without the attribution nothing joins
    the two, so a task wanting to add a function beside `rig` cannot tell which
    skill to write it into.
    """
    import json

    from dex import skills
    from dex.tools.utils_api import render, providers

    staged = tmp_path / "skills" / "figure" / "new"
    (staged / "utils").mkdir(parents=True)
    (staged / "utils" / "rig.py").write_text('"""Place a limb."""\n\n\ndef lean(x):\n    return x\n')
    (staged / skills.MANIFEST_NAME).write_text(json.dumps({"name": "figure"}) + "\n")
    skills.publish(skills.read(staged))

    project = tmp_path / "assets" / "demo"
    project.mkdir(parents=True)
    skills.materialise(tmp_path, project, ["figure"])

    text = render(project / "utils", providers(project))
    assert "## `rig` — from `figure`" in text
    assert "lean(x)" in text


def test_a_module_from_no_skill_is_still_indexed(tmp_path):
    """Dropped in by hand, or an install mid-migration. It gets no attribution."""
    from dex.tools.utils_api import render

    utils = tmp_path / "utils"
    utils.mkdir()
    (utils / "loose.py").write_text('"""Local."""\n\n\ndef f():\n    pass\n')
    text = render(utils, {})
    assert "## `loose`" in text
    assert "from `" not in text
