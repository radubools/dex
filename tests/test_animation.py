"""Playback variants: speed, and stepping through checkpoints."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dex import animation, gifinfo


@pytest.fixture(scope="module")
def gif(tmp_path_factory) -> Path:
    """A 30-frame GIF at 10fps, so slicing and retiming have something to work on."""
    out = tmp_path_factory.mktemp("anim") / "scene.gif"
    frames = []
    for shade in range(30):
        frames += ["-size", "40x20", f"xc:rgb({shade * 8},0,0)"]
    made = subprocess.run(["magick", "-delay", "10", *frames, "-loop", "0", str(out)],
                          capture_output=True)
    if made.returncode != 0 or not out.exists():
        pytest.skip("ImageMagick is not available to build a fixture")
    return out


def test_speed_changes_duration_and_keeps_every_frame(gif, tmp_path):
    original = gifinfo.read(gif)
    faster = animation.variant(gif, tmp_path, speed=2.0)
    info = gifinfo.read(faster)

    assert faster != gif
    assert info.frames == original.frames
    assert info.duration == pytest.approx(original.duration / 2, rel=0.15)


def test_speed_one_serves_the_original_untouched(gif, tmp_path):
    assert animation.variant(gif, tmp_path, speed=1.0) == gif
    assert list(tmp_path.iterdir()) == []


def test_variants_are_cached(gif, tmp_path):
    first = animation.variant(gif, tmp_path, speed=2.0)
    before = first.stat().st_mtime_ns
    second = animation.variant(gif, tmp_path, speed=2.0)
    assert second == first
    assert second.stat().st_mtime_ns == before  # not rebuilt


def test_speed_is_clamped_to_something_sane(gif, tmp_path):
    fast = gifinfo.read(animation.variant(gif, tmp_path, speed=999))
    slow = gifinfo.read(animation.variant(gif, tmp_path, speed=0.0001))
    assert fast.duration < slow.duration


def test_unsectioned_animations_still_get_steps(gif):
    marks = animation.checkpoints(gif)
    assert len(marks) > 1
    assert [m.index for m in marks] == list(range(len(marks)))
    # Contiguous and covering the whole animation.
    assert marks[0].start_frame == 0
    for a, b in zip(marks, marks[1:]):
        assert b.start_frame == a.end_frame + 1


def test_recorded_sections_name_the_steps(gif):
    animation.sections_file(gif).write_text(json.dumps([
        {"name": "Setup", "nb_frames": "10", "duration": "1.0"},
        {"name": "Compare", "nb_frames": "20", "duration": "2.0"},
    ]))
    try:
        marks = animation.checkpoints(gif)
        assert [m.name for m in marks] == ["Setup", "Compare"]
        assert (marks[0].start_frame, marks[0].end_frame) == (0, 9)
        assert (marks[1].start_frame, marks[1].end_frame) == (10, 29)
        assert animation.describe(gif)["named"] is True
    finally:
        animation.sections_file(gif).unlink()


def test_a_segment_contains_only_its_own_frames(gif, tmp_path):
    marks = animation.checkpoints(gif)
    sliced = animation.variant(gif, tmp_path, speed=1.0, segment=marks[1].index)
    info = gifinfo.read(sliced)
    expected = marks[1].end_frame - marks[1].start_frame + 1
    assert info.frames == expected


def test_an_unknown_segment_falls_back_to_the_whole_animation(gif, tmp_path):
    assert animation.variant(gif, tmp_path, speed=1.0, segment=99) == gif


def test_a_tiny_animation_has_no_checkpoints(tmp_path):
    out = tmp_path / "short.gif"
    made = subprocess.run(
        ["magick", "-delay", "10", "-size", "10x10", "xc:red", "xc:blue", str(out)],
        capture_output=True,
    )
    if made.returncode != 0:
        pytest.skip("ImageMagick is not available")
    assert animation.checkpoints(out) == []
