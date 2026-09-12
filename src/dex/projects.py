"""Projects: a named body of work with its own guide and its own threads.

On disk a project is a subdirectory of the assets root holding `AGENTS.md` and
one directory per task. In the database it owns its threads and tasks, so
switching project switches everything the UI shows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database

log = logging.getLogger("dex.projects")

#: The guide a new project starts from, so the design chat has something to edit
#: rather than a blank page.
STARTER_GUIDE = """# Project: {name}

{description}

## The environment is ready — do not go looking

Everything below is installed and working. **Do not check whether a tool exists,
do not try to install anything, and do not add a fallback for a missing
dependency.**

| Need | Use |
|---|---|
| Anything Python | `{{python}}` — the interpreter named in your brief |

Your shell runs one command at a time: no `&&`, no pipes, no `cd`. Pass absolute
paths.

## What to produce

Describe the deliverable for a task in this project here: the files, what each
one is for, and how to tell when it is done.

## Conventions

Describe the naming, structure, and style a task in this project should follow.
"""


def slugify(name: str, taken: set[str] | None = None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = (cleaned or "project")[:48].strip("-")
    if taken and slug in taken:
        suffix = 2
        while f"{slug}-{suffix}" in taken:
            suffix += 1
        slug = f"{slug}-{suffix}"
    return slug


@dataclass
class Project:
    slug: str
    name: str
    description: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    #: Counts filled in by the store for the picker.
    threads: int = 0
    tasks: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "threads": self.threads,
            "tasks": self.tasks,
        }


class ProjectStore:
    def __init__(self, db: Database, assets_root: Path) -> None:
        self.db = db
        self.assets_root = assets_root

    def directory(self, slug: str) -> Path:
        return self.assets_root / slug

    def guide_path(self, slug: str) -> Path:
        return self.directory(slug) / "AGENTS.md"

    async def list(self) -> list[Project]:
        rows = await self.db.pool.fetch(
            """SELECT p.slug, p.name, p.description,
                      extract(epoch from p.created_at)::float8 AS created_at,
                      extract(epoch from p.updated_at)::float8 AS updated_at,
                      (SELECT count(*) FROM threads t WHERE t.project = p.slug) AS threads,
                      (SELECT count(*) FROM tasks k WHERE k.project = p.slug) AS tasks
               FROM projects p ORDER BY p.name"""
        )
        return [Project(**dict(row)) for row in rows]

    async def get(self, slug: str) -> Project | None:
        rows = [p for p in await self.list() if p.slug == slug]
        return rows[0] if rows else None

    async def create(self, name: str, description: str = "", slug: str | None = None) -> Project:
        """Create a project, or return the existing one with that slug.

        An explicit `slug` is taken at its word — it names a project that
        already exists on disk or is being adopted. Only a slug derived from a
        name avoids collisions, since that is dex choosing for you.
        """
        if slug:
            chosen = slugify(slug)
        else:
            taken = {p.slug for p in await self.list()}
            if self.assets_root.exists():
                taken |= {d.name for d in self.assets_root.iterdir() if d.is_dir()}
            chosen = slugify(name, taken)
        project = Project(slug=chosen, name=name.strip()[:80], description=description.strip())
        await self.db.pool.execute(
            """INSERT INTO projects (slug, name, description) VALUES ($1, $2, $3)
               ON CONFLICT (slug) DO NOTHING""",
            project.slug, project.name, project.description,
        )
        # The directory and its guide are what tasks actually read.
        self.directory(project.slug).mkdir(parents=True, exist_ok=True)
        guide = self.guide_path(project.slug)
        if not guide.exists():
            guide.write_text(
                STARTER_GUIDE.format(name=project.name, description=project.description
                                     or "What this project is for."),
                encoding="utf-8",
            )
        return await self.get(project.slug) or project

    async def delete(self, slug: str) -> bool:
        """Removes the project and its threads. Files on disk are left alone."""
        result = await self.db.pool.execute("DELETE FROM projects WHERE slug = $1", slug)
        return result.endswith(" 1")

    def read_guide(self, slug: str) -> str:
        try:
            return self.guide_path(slug).read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_guide(self, slug: str, text: str) -> None:
        path = self.guide_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename, so a crash cannot truncate the guide every task reads.
        scratch = path.with_suffix(".md.tmp")
        scratch.write_text(text, encoding="utf-8")
        scratch.replace(path)
