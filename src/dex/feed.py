"""The review feed: which topic to show next, and what a topic is made of.

A topic is one generated package — a directory under the project's assets root
holding an animation, solutions, tests, and an explanation. The feed orders
them by spaced repetition so revisiting is weighted towards what is nearly
forgotten rather than what is newest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import animation, gifinfo
from .db import Database

log = logging.getLogger("dex.feed")

#: How a review moves a topic's schedule. Deliberately a small SM-2: enough to
#: space things sensibly without asking the reader to grade every card.
RATINGS = {
    # Seen it and it made sense: push it further out.
    "good": {"ease_delta": 0.0, "growth": 1.0},
    # Comfortable: push it out harder.
    "easy": {"ease_delta": 0.15, "growth": 1.3},
    # Did not land: bring it back soon and make it grow more slowly.
    "again": {"ease_delta": -0.2, "growth": 0.0},
}

FIRST_INTERVAL_DAYS = 1.0
SECOND_INTERVAL_DAYS = 3.0
MIN_EASE = 1.3
#: An "again" comes back within the same session rather than the same day.
AGAIN_INTERVAL_DAYS = 10 / (60 * 24)


@dataclass
class Topic:
    slug: str
    title: str
    #: The problem in the package's own words, shown under the title.
    summary: str | None = None
    #: Paths relative to the assets root, ready for the asset endpoints.
    animations: list[str] = field(default_factory=list)
    solutions: str | None = None
    tests: str | None = None
    explanation: str | None = None
    manifest: dict[str, Any] | None = None
    animation_info: dict[str, Any] | None = None
    #: Every animation the package has, in order, with how long each runs.
    #: A package holds one clip per approach, and a reel plays them in
    #: sequence — so the reel needs each one's length, not just the first's.
    clips: list[dict[str, Any]] = field(default_factory=list)
    seen_count: int = 0
    due: bool = False
    last_seen_at: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "animations": self.animations,
            "solutions": self.solutions,
            "tests": self.tests,
            "explanation": self.explanation,
            "manifest": self.manifest,
            "animation": self.animation_info,
            "clips": self.clips,
            "seenCount": self.seen_count,
            "due": self.due,
            "lastSeenAt": self.last_seen_at,
        }


def discover(project_dir: Path, project: str | None = None) -> list[Topic]:
    """Every package in a project that has something worth showing.

    Asset paths are returned relative to the assets root, so they carry their
    project and can be handed straight to the asset endpoints.
    """
    topics: list[Topic] = []
    if not project_dir.exists():
        return topics
    prefix = f"{project}/" if project else ""

    for directory in sorted(p for p in project_dir.iterdir() if p.is_dir()):
        topic = Topic(slug=directory.name, title=_title_from(directory))
        for child in sorted(directory.iterdir()):
            if not child.is_file():
                continue
            if child.suffix in (".gif", ".mp4"):
                # Narrated packages render video; older ones are silent GIFs.
                topic.animations.append(f"{prefix}{directory.name}/{child.name}")
            elif child.name.startswith("test_") and child.suffix == ".py":
                topic.tests = f"{prefix}{directory.name}/{child.name}"
            elif child.name == "solutions.py":
                topic.solutions = f"{prefix}{directory.name}/{child.name}"
            elif child.suffix in (".md", ".markdown") and child.name != "AGENTS.md":
                topic.explanation = f"{prefix}{directory.name}/{child.name}"
            elif child.name == "manifest.json":
                topic.manifest = _read_json(child)

        # A package with neither an animation nor an explanation has nothing to
        # show in a reel.
        if not topic.animations and not topic.explanation:
            continue
        topic.summary = _problem_from(directory)
        if topic.animations:
            first = directory / Path(topic.animations[0]).name
            # `gifinfo` reads GIF headers; a video carries its own timing and
            # the player reads that itself.
            info = gifinfo.read(first) if first.suffix == ".gif" else None
            topic.animation_info = info.to_json() if info else None
            topic.clips = [_clip(directory, rel) for rel in topic.animations]
        topics.append(topic)
    return topics


def _clip(directory: Path, rel: str) -> dict[str, Any]:
    """One animation and how long it actually runs.

    The reel used to time itself from a fallback of a few seconds, because
    nothing measured a video: it moved on after eight seconds of a minute-long
    animation. A video's length is the sum of its recorded sections, which is
    what `describe` reads; a GIF's comes from its frame delays.
    """
    path = directory / Path(rel).name
    duration: float | None = None
    try:
        if animation.is_video(path):
            info = animation.describe(path).get("animation") or {}
            duration = info.get("duration")
        else:
            gif = gifinfo.read(path)
            duration = gif.duration if gif else None
    except Exception:
        # A clip whose length cannot be read still plays; the reel falls back
        # to waiting for the video to end rather than refusing to show it.
        log.exception("could not measure %s", rel)
    return {"path": rel, "name": path.stem, "duration": duration}


def _title_from(directory: Path) -> str:
    """A short name for the reel.

    The manifest's problem statement is a paragraph more often than a title, so
    it is only used when it is genuinely short; otherwise the directory name
    reads better and the statement becomes the summary underneath.
    """
    problem = _problem_from(directory)
    if problem and len(problem) <= 48:
        return problem
    return directory.name.replace("-", " ").title()


def _problem_from(directory: Path) -> str | None:
    manifest = _read_json(directory / "manifest.json")
    problem = (manifest or {}).get("problem")
    if not isinstance(problem, str) or not problem.strip():
        return None
    # First sentence only: the rest is constraints, which the explanation covers.
    first = problem.strip().split(". ")[0]
    return first.rstrip(".").strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class ReviewStore:
    """Spaced-repetition state, and the ordering that follows from it."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def state(self, project: str) -> dict[str, dict[str, Any]]:
        rows = await self.db.pool.fetch(
            """SELECT slug, seen_count, interval_days, ease,
                      extract(epoch from last_seen_at)::float8 AS last_seen_at,
                      extract(epoch from due_at)::float8 AS due_at,
                      (due_at IS NOT NULL AND due_at <= now()) AS due
               FROM topic_reviews WHERE project = $1""",
            project,
        )
        return {row["slug"]: dict(row) for row in rows}

    async def order(self, project: str, topics: list[Topic]) -> list[Topic]:
        """Due first, then never seen, then whatever comes round soonest.

        Ordering by *urgency* rather than recency is the point of the feed: the
        material closest to being forgotten leads.
        """
        state = await self.state(project)
        for topic in topics:
            row = state.get(topic.slug)
            if row:
                topic.seen_count = row["seen_count"]
                topic.due = bool(row["due"])
                topic.last_seen_at = row["last_seen_at"]

        def rank(topic: Topic) -> tuple[int, float]:
            row = state.get(topic.slug)
            if row is None:
                return (1, 0.0)  # never seen: after anything overdue
            if row["due"]:
                return (0, row["due_at"] or 0.0)  # most overdue first
            return (2, row["due_at"] or 0.0)

        return sorted(topics, key=rank)

    async def record(self, project: str, slug: str, rating: str = "good") -> dict[str, Any]:
        """Advance a topic's schedule after it has been shown."""
        rule = RATINGS.get(rating, RATINGS["good"])
        row = await self.db.pool.fetchrow(
            "SELECT seen_count, interval_days, ease FROM topic_reviews WHERE project = $1 AND slug = $2",
            project, slug,
        )
        seen = (row["seen_count"] if row else 0) + 1
        ease = max(MIN_EASE, (row["ease"] if row else 2.5) + rule["ease_delta"])
        previous = row["interval_days"] if row else 0.0

        if rating == "again":
            interval = AGAIN_INTERVAL_DAYS
        elif seen == 1 or previous <= 0:
            interval = FIRST_INTERVAL_DAYS * rule["growth"]
        elif seen == 2:
            interval = SECOND_INTERVAL_DAYS * rule["growth"]
        else:
            interval = previous * ease * rule["growth"]

        saved = await self.db.pool.fetchrow(
            """INSERT INTO topic_reviews
                   (project, slug, seen_count, last_seen_at, interval_days, ease, due_at)
               VALUES ($1, $2, $3, now(), $4, $5, now() + make_interval(secs => $6))
               ON CONFLICT (project, slug) DO UPDATE
                   SET seen_count = EXCLUDED.seen_count,
                       last_seen_at = EXCLUDED.last_seen_at,
                       interval_days = EXCLUDED.interval_days,
                       ease = EXCLUDED.ease,
                       due_at = EXCLUDED.due_at
               RETURNING seen_count, interval_days, ease,
                         extract(epoch from due_at)::float8 AS due_at""",
            project, slug, seen, interval, ease, interval * 86400,
        )
        return {
            "slug": slug,
            "seenCount": saved["seen_count"],
            "intervalDays": round(saved["interval_days"], 4),
            "ease": round(saved["ease"], 2),
            "dueAt": saved["due_at"],
        }
