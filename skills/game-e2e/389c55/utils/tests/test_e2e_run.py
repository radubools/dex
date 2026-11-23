"""game-e2e's runner, without a browser: merging, the freshness check, adopting, cutting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import e2e_run


def story_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "story-knn"
    pkg.mkdir()
    story = {"schema": 1, "id": "knn", "beats": [
        {"id": "a", "say": "one"},
        {"id": "b", "say": "two", "wait": {"choice": {"options": [{"label": "x", "goto": "d"}, {"label": "y"}]}}},
        {"id": "c", "say": "three", "goto": "a"},
        {"id": "d", "say": "four"},
    ]}
    (pkg / "story.json").write_text(json.dumps(story))
    (pkg / "e2e.json").write_text(json.dumps({"schema": 1, "beats": {"a": {"text": ["one"]}, "d": {"text": ["four"]}},
                                              "routes": [{"name": "x", "choose": {"b": 0}}]}))
    return pkg


def passing(pkg: Path, out: Path, size: str, passed: bool = True) -> None:
    (out / size).mkdir(parents=True, exist_ok=True)
    rep = {"passed": passed, "story": {"sha256": e2e_run.sha256(pkg / "story.json")},
           "tests": {"sha256": e2e_run.sha256(pkg / "e2e.json")}, "runs": [{"viewport": size}]}
    (out / size / "report.json").write_text(json.dumps(rep))
    (out / size / "report.html").write_text("<p>ok</p>")


def test_both_sizes_merge_into_one_report(tmp_path):
    pkg, out = story_pkg(tmp_path), tmp_path / "e2e-knn" / "e2e"
    passing(pkg, out, "desktop")
    assert e2e_run.merge(out) is None            # the phone has not run
    passing(pkg, out, "phone")
    merged = e2e_run.merge(out)
    assert merged["passed"] and len(merged["runs"]) == 2
    assert json.loads((out / "report.json").read_text())["parts"] == ["desktop/report.html", "phone/report.html"]


def test_one_failing_size_fails_the_report(tmp_path):
    pkg, out = story_pkg(tmp_path), tmp_path / "out"
    passing(pkg, out, "desktop")
    passing(pkg, out, "phone", passed=False)
    assert not e2e_run.merge(out)["passed"]


def test_a_report_is_only_good_for_the_content_it_tested(tmp_path):
    pkg, out = story_pkg(tmp_path), tmp_path / "out"
    assert "no end-to-end report" in e2e_run.problem(pkg, out)
    passing(pkg, out, "desktop")
    passing(pkg, out, "phone")
    e2e_run.merge(out)
    assert e2e_run.problem(pkg, out) is None
    (pkg / "e2e.json").write_text("{}")
    assert "e2e.json changed" in e2e_run.problem(pkg, out)


def test_adopt_takes_a_reviewed_passing_report_into_the_story(tmp_path):
    pkg, out = story_pkg(tmp_path), tmp_path / "e2e-knn" / "e2e"
    passing(pkg, out, "desktop")
    passing(pkg, out, "phone")
    e2e_run.merge(out)
    (out / "desktop" / "shot.png").write_bytes(b"png")
    with pytest.raises(ValueError, match="visual review"):
        e2e_run.adopt(pkg, out)
    (out / "review.md").write_text("- b: card covered p3; moved the camera\nreviewed: desktop, phone\n")
    copied = e2e_run.adopt(pkg, out)
    assert {"report.json", "review.md", "desktop/report.json", "phone/report.html"} <= set(copied)
    assert (pkg / "e2e" / "report.json").read_text() == (out / "report.json").read_text()
    assert not (pkg / "e2e" / "desktop" / "shot.png").exists()     # screenshots stay with the e2e task


def test_adopt_refuses_a_stale_report(tmp_path):
    pkg, out = story_pkg(tmp_path), tmp_path / "out"
    passing(pkg, out, "desktop")
    passing(pkg, out, "phone")
    e2e_run.merge(out)
    (out / "review.md").write_text("reviewed: desktop, phone\n")
    (pkg / "story.json").write_text(json.dumps({"schema": 1, "beats": [{"id": "a", "say": "changed"}]}))
    with pytest.raises(ValueError, match="story.json changed"):
        e2e_run.adopt(pkg, out)
    assert not (pkg / "e2e").exists()


def test_run_refuses_to_write_into_the_story(tmp_path):
    pkg = story_pkg(tmp_path)
    with pytest.raises(ValueError, match="own package"):
        e2e_run.run(pkg, tmp_path, pkg / "e2e")


def test_a_segment_ends_at_its_beat_and_drops_what_leads_past_it(tmp_path):
    pkg = story_pkg(tmp_path)
    story = json.loads((pkg / "story.json").read_text())
    tests = json.loads((pkg / "e2e.json").read_text())
    s, t = e2e_run.cut(story, tests, "c")
    assert [b["id"] for b in s["beats"]] == ["a", "b", "c"] and s["beats"][-1]["end"]
    assert "goto" not in s["beats"][1]["wait"]["choice"]["options"][0]   # led to d, which is cut
    assert s["beats"][2]["goto"] == "a"                                   # still inside
    assert set(t["beats"]) == {"a"} and t["routes"] == [{"name": "x", "choose": {"b": 0}}]
    with pytest.raises(ValueError):
        e2e_run.cut(story, tests, "zz")


def test_clear_shots_keeps_the_reports(tmp_path):
    out = tmp_path / "out"
    (out / "desktop" / "shots").mkdir(parents=True)
    (out / "desktop" / "shots" / "a.png").write_bytes(b"x")
    (out / "report.json").write_text("{}")
    assert e2e_run.clear_shots(out) == 1
    assert (out / "report.json").exists()
