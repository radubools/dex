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
    assert "# Project instructions" in text
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
