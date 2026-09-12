"""Topic discovery and the spaced-repetition ordering behind the feed."""

from __future__ import annotations

from pathlib import Path

import pytest

from dex.feed import ReviewStore, Topic, discover


def make_package(root: Path, slug: str, *, gif=True, md=True, manifest=None) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "solutions.py").write_text("def solve(): ...\n")
    (d / "test_solutions.py").write_text("def test_solve(): ...\n")
    if md:
        (d / "explanation.md").write_text("# Title\n\n```mermaid\nflowchart LR\nA-->B\n```\n")
    if gif:
        (d / "brute.gif").write_bytes(b"GIF89a" + b"\x00" * 20)
    if manifest:
        (d / "manifest.json").write_text(manifest)
    return d


def test_discovery_classifies_each_file(tmp_path: Path):
    make_package(tmp_path, "two-sum", manifest='{"problem": "Return the two indices."}')
    topics = discover(tmp_path)

    assert [t.slug for t in topics] == ["two-sum"]
    topic = topics[0]
    assert topic.solutions == "two-sum/solutions.py"
    assert topic.tests == "two-sum/test_solutions.py"
    assert topic.explanation == "two-sum/explanation.md"
    assert topic.animations == ["two-sum/brute.gif"]
    # A short problem statement makes a better title than the slug.
    assert topic.title == "Return the two indices"
    assert topic.summary == "Return the two indices"


def test_title_falls_back_to_the_slug(tmp_path: Path):
    make_package(tmp_path, "merge-sort")
    assert discover(tmp_path)[0].title == "Merge Sort"


def test_a_long_problem_becomes_the_summary_not_the_title(tmp_path: Path):
    long = "Sort an array of up to 10^4 32-bit signed integers with duplicates and negatives"
    make_package(tmp_path, "bubble-sort", manifest='{"problem": "%s. Constraints follow."}' % long)
    topic = discover(tmp_path)[0]
    # The reel needs a name, not a paragraph.
    assert topic.title == "Bubble Sort"
    assert topic.summary == long


def test_packages_with_nothing_to_show_are_skipped(tmp_path: Path):
    make_package(tmp_path, "empty", gif=False, md=False)
    make_package(tmp_path, "real")
    assert [t.slug for t in discover(tmp_path)] == ["real"]


def test_missing_assets_root_is_not_an_error(tmp_path: Path):
    assert discover(tmp_path / "nope") == []


async def test_never_seen_topics_come_before_scheduled_ones(db):
    store = ReviewStore(db)
    topics = [Topic(slug=s, title=s) for s in ("a", "b")]
    await store.record("algorithms", "a")  # a is now scheduled into the future

    ordered = await store.order("algorithms", topics)
    assert [t.slug for t in ordered] == ["b", "a"]
    assert ordered[1].seen_count == 1


async def test_due_topics_lead(db):
    store = ReviewStore(db)
    await store.record("algorithms", "a")
    await db.pool.execute(
        "UPDATE topic_reviews SET due_at = now() - interval '2 days' WHERE slug = 'a'"
    )
    topics = [Topic(slug=s, title=s) for s in ("b", "a")]

    ordered = await store.order("algorithms", topics)
    assert [t.slug for t in ordered] == ["a", "b"]
    assert ordered[0].due is True


async def test_intervals_grow_with_each_good_review(db):
    store = ReviewStore(db)
    first = await store.record("algorithms", "a", "good")
    second = await store.record("algorithms", "a", "good")
    third = await store.record("algorithms", "a", "good")

    assert first["intervalDays"] == pytest.approx(1.0)
    assert second["intervalDays"] == pytest.approx(3.0)
    assert third["intervalDays"] > second["intervalDays"]
    assert third["seenCount"] == 3


async def test_again_brings_a_topic_straight_back_and_lowers_ease(db):
    store = ReviewStore(db)
    await store.record("algorithms", "a", "good")
    await store.record("algorithms", "a", "good")
    lapsed = await store.record("algorithms", "a", "again")

    assert lapsed["intervalDays"] < 0.01  # minutes, not days
    assert lapsed["ease"] < 2.5


async def test_easy_pushes_further_out_than_good(db):
    store = ReviewStore(db)
    for _ in range(2):
        await store.record("algorithms", "steady", "good")
        await store.record("algorithms", "keen", "good")
    steady = await store.record("algorithms", "steady", "good")
    keen = await store.record("algorithms", "keen", "easy")
    assert keen["intervalDays"] > steady["intervalDays"]


async def test_ease_never_falls_below_the_floor(db):
    store = ReviewStore(db)
    for _ in range(12):
        result = await store.record("algorithms", "hard", "again")
    assert result["ease"] >= 1.3


async def test_projects_keep_separate_schedules(db):
    store = ReviewStore(db)
    await store.record("algorithms", "a")
    assert await store.state("algorithms") != {}
    assert await store.state("other-project") == {}


def test_narrated_videos_are_discovered_like_gifs(config):
    """A narrated package is mp4; collecting only .gif made it invisible.

    The feed showed "No animation for this topic" for a package that had three
    of them, because discovery only looked for GIFs.
    """
    from dex.feed import discover

    project = config.assets_dir / "algorithms"
    package = project / "narrated-thing"
    package.mkdir(parents=True, exist_ok=True)
    (package / "explanation.md").write_text("# Narrated thing\n\nWhy it works.")
    (package / "brute_force.mp4").write_bytes(b"not really a video")

    topic = next(t for t in discover(project, "algorithms") if t.slug == "narrated-thing")
    assert topic.animations == ["algorithms/narrated-thing/brute_force.mp4"]
    # GIF header reading is for GIFs; a video carries its own timing.
    assert topic.animation_info is None
