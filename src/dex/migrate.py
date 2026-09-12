"""One-time backfills, run at startup and safe to repeat."""

from __future__ import annotations

import logging

from .config import Config
from .db import Database
from .projects import ProjectStore
from .store import ThreadStore
from .threads import ThreadMessage

log = logging.getLogger("dex.migrate")

DESIGN_OPENER = """This is the design thread for **{name}**.

Tell me how tasks in this project should work and I will keep `AGENTS.md` in
step — what to produce, the conventions to follow, and which tools are already
available. Every task in this project reads that guide before it starts.
"""


async def adopt_existing_project(db: Database, config: Config) -> None:
    """Bring pre-project data under a project.

    dex had one implicit project before it had the concept, so its threads and
    tasks have no project and its guide already sits on disk. Adopt them rather
    than leaving them stranded.
    """
    projects = ProjectStore(db, config.assets_dir)
    slug = config.default_project

    if await projects.get(slug) is None:
        # Only claim a name for it if there is something to adopt.
        existing = await db.pool.fetchval(
            "SELECT count(*) FROM threads WHERE project IS NULL"
        )
        on_disk = projects.directory(slug).exists()
        if not existing and not on_disk:
            return
        await projects.create(name=slug.replace("-", " ").title(), slug=slug,
                              description="Adopted from before dex had projects.")
        log.info("adopted the existing work as project %r", slug)

    orphans = await db.pool.execute(
        "UPDATE threads SET project = $1 WHERE project IS NULL", slug
    )
    await db.pool.execute("UPDATE tasks SET project = $1 WHERE project IS NULL", slug)
    if orphans.endswith(" 0") is False:
        log.info("attached pre-project threads and tasks to %r", slug)

    await ensure_design_thread(db, projects, slug)


async def ensure_design_thread(db: Database, projects: ProjectStore, slug: str) -> str | None:
    """Every project gets exactly one design thread, created on demand."""
    existing = await db.pool.fetchval(
        "SELECT id FROM threads WHERE project = $1 AND kind = 'project_design' ORDER BY created_at LIMIT 1",
        slug,
    )
    if existing:
        return existing

    project = await projects.get(slug)
    if project is None:
        return None

    threads = ThreadStore(db)
    thread = await threads.create(
        title=f"{project.name} · design", project=slug, kind="project_design"
    )
    await threads.append(
        thread.id,
        ThreadMessage(role="dex", kind="text", text=DESIGN_OPENER.format(name=project.name)),
    )
    log.info("created the design thread for %r", slug)
    return thread.id
