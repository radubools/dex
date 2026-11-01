"""One-time backfills, run at startup and safe to repeat."""

from __future__ import annotations

import logging
from typing import Any

from . import skills
from .config import Config
from .db import Database
from .projects import ProjectStore
from .store import ThreadStore
from .deps import CHATS_THREAD, POWER_SKILL, POWER_THREAD
from .threads import ThreadMessage

log = logging.getLogger("dex.migrate")

MAIN_OPENER = (
    "Post anything here. dex works out whether it is about the project's design, a kind of "
    "asset or a task, carries on with the right task if one exists, and otherwise "
    "opens it in the right thread."
)

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
    projects = ProjectStore(db, config.assets_dir, config.workspace)
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


async def ensure_main_thread(db: Database, slug: str) -> str:
    """Every project has one thread to post in: dex routes from there (design/26)."""
    existing = await db.pool.fetchval(
        "SELECT id FROM threads WHERE project = $1 AND kind = 'main' ORDER BY created_at LIMIT 1", slug,
    )
    if existing:
        return existing
    threads = ThreadStore(db)
    thread = await threads.create(title="Main", project=slug, kind="main")
    await threads.append(thread.id, ThreadMessage(role="dex", kind="text", text=MAIN_OPENER))
    log.info("created the main thread for %r", slug)
    return thread.id


#: Below this share of a thread's tasks, a skill only passed through it.
TIE_SHARE = 0.25


async def tie_threads(db: Database, config: Config, slug: str) -> None:
    """Tie a project's old threads to skills, once: by the title a skill declares,
    or else by what the thread's tasks used, a specific skill before a base one.
    After that a tie only changes when someone changes it."""
    done = f"threads_tied:{slug}"
    if await db.pool.fetchval("SELECT 1 FROM settings WHERE key = $1", done):
        return
    declared = skills.thread_skills(config.workspace, config.project_dir(slug))
    rows = await db.pool.fetch(
        """SELECT th.id, th.title, u.skill, count(DISTINCT u.task_id) AS tasks
             FROM threads th
             LEFT JOIN tasks t ON t.thread_id = th.id
             LEFT JOIN task_usage u ON u.task_id = t.id AND u.skill <> ''
            WHERE th.project = $1 AND th.kind = 'chat' AND th.skill IS NULL AND NOT th.hidden
            GROUP BY th.id, th.title, u.skill""",
        slug,
    )
    threads: dict[str, dict[str, Any]] = {}
    for row in rows:
        thread = threads.setdefault(row["id"], {"title": row["title"], "used": {}})
        if row["skill"]:
            thread["used"][row["skill"]] = row["tasks"]
    for thread_id, thread in threads.items():
        skill = _skill_of(thread["title"], thread["used"], declared)
        if skill:
            await ThreadStore(db).tie(thread_id, skill)
            log.info("tied the thread %r of %r to %s", thread["title"], slug, skill)
    await db.pool.execute(
        "INSERT INTO settings (key, value) VALUES ($1, 'true'::jsonb) ON CONFLICT (key) DO NOTHING", done,
    )


def _skill_of(title: str, used: dict[str, int], declared: dict[str, skills.Skill]) -> str | None:
    if title == CHATS_THREAD:
        return None
    if title == POWER_THREAD:
        return POWER_SKILL
    for skill in declared.values():
        named = skill.thread.lower()
        if title.lower() == named or title.lower().startswith(f"{named} "):
            return skill.name
    total = sum(used.values())
    fits = [name for name, tasks in used.items() if name in declared and tasks >= total * TIE_SHARE]
    return max(fits, key=lambda name: (not declared[name].base, used[name]), default=None)


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
