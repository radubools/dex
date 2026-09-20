"""HTTP API: submit work, watch it happen, read what came out."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from .bus import EventBus
from . import widgets
from .authn import (
    Access,
    build_access_dependency,
    build_admin_dependency,
    build_capability_dependency,
    build_router,
)
from .identity import (
    CAP_DESIGN,
    CAP_MANAGE_PROJECTS,
    CAP_RUN_TASKS,
    CAP_VIEW,
    IdentityStore,
)
from .config import CONFIG, MAX_WORKERS, PARALLEL_CONCURRENCY, SEQUENTIAL_CONCURRENCY, Config
from .db import Database, import_json_threads
from . import animation
from . import designer, skills, surveyor, uploads
from .feed import ReviewStore, discover
from .migrate import adopt_existing_project, ensure_design_thread
from .projects import ProjectStore
from .models import Event, TaskState
from .planner import Plan, plan_from_message
from .queue import TaskManager
from .store import (
    CostStore, PackageTagStore, SettingsStore, TaskMessageStore, ThreadStore,
    read_manifest_tags,
)
from .threads import ThreadMessage
from .runner import AUTH_HINT, credential_source, is_auth_error, manim_available
from .usage import PAUSE_AT, UsageWatcher, closest_limit
from .watcher import AssetWatcher, KINDS

log = logging.getLogger("dex.api")

PING_INTERVAL_S = 20.0

#: Offered in the model picker. The list is short on purpose: these are the
#: models worth running an agent on.
MODEL_CHOICES = [
    {"id": "claude-opus-5", "label": "Opus 5", "note": "most capable"},
    {"id": "claude-sonnet-5", "label": "Sonnet 5", "note": "faster, cheaper"},
    {"id": "claude-haiku-4-5", "label": "Haiku 4.5", "note": "cheapest"},
]

#: How hard a task thinks before it acts. More effort buys better work on a
#: hard problem and spends both wall-clock and tokens on an easy one — a
#: high-effort run put 122 seconds of thinking in front of its first token.
EFFORT_CHOICES = [
    {"id": "low", "label": "Low", "note": "fastest, least thinking"},
    {"id": "medium", "label": "Medium", "note": "brief deliberation"},
    {"id": "high", "label": "High", "note": "the usual default"},
    {"id": "xhigh", "label": "Extra high", "note": "slower, harder problems"},
    {"id": "max", "label": "Max", "note": "slowest and dearest"},
]
EFFORT_IDS = [choice["id"] for choice in EFFORT_CHOICES]

#: Far above any real thread, but bounded: a runaway thread should not be able
#: to make one request read the whole table.
THREAD_TASK_LIMIT = 10_000

#: What `/api/assets` will return as text. Everything else is binary and goes
#: through `/api/assets/raw`.
#:
#: This was eight suffixes, which meant a project producing a `player.html`
#: could not open its own deliverable: the viewer asked for it as text and got
#: 415. The rule is "would a person read this in an editor", not "did dex
#: think of it".
TEXT_SUFFIXES = {
    ".py", ".md", ".markdown", ".json", ".txt", ".toml", ".yaml", ".yml", ".cfg",
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".csv", ".tsv", ".vtt", ".srt", ".xml", ".sql", ".sh", ".ini", ".rst",
    ".abc", ".ly",  # music projects: ABC notation and LilyPond are both text
}


# --------------------------------------------------------------- request models


class SubmitRequest(BaseModel):
    problem: str = Field(min_length=3)
    title: str | None = None
    slug: str | None = None
    thread_id: str | None = None
    project: str | None = None
    #: An existing package this rewrites; it writes into that directory rather
    #: than creating one beside it.
    updates: str | None = None
    #: A package this task fills alongside its siblings. Unlike `updates` it
    #: need not exist yet. Ignored when `updates` is set: that one names a
    #: directory known to be there.
    package: str | None = None
    #: "package" (the default) or "project" for a uniform edit across every
    #: package the project already has. Anything else is read as "package":
    #: widening what a task may touch should take saying so exactly.
    scope: str = "package"
    #: Upload batches whose files this task may read as sources.
    uploads: list[str] = Field(default_factory=list)
    #: Where in that material this task's work is, as the survey anchored it:
    #: a page range of a PDF, a section of a document, a URL. Round-trips
    #: through the UI untouched — it is the survey's, not the browser's.
    anchor: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    #: Which project to plan for. Falls back to the default when unset, so the
    #: planner is never left guessing what kind of work is wanted.
    project: str | None = None


class ConfirmRequest(BaseModel):
    """The plan the operator accepted, possibly edited in the UI."""

    tasks: list[SubmitRequest]
    thread_id: str | None = None
    #: Attachments from the message that produced this plan. Applied to every
    #: task in it: the operator attached them to the request, not to one task
    #: the planner happened to split out.
    uploads: list[str] = Field(default_factory=list)


class ThreadCreateRequest(BaseModel):
    title: str | None = None
    project: str | None = None


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""


class GuideRequest(BaseModel):
    text: str


class ThreadMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    #: Upload batches attached to this message.
    uploads: list[str] = Field(default_factory=list)


class SettingsRequest(BaseModel):
    """Every field optional: the UI sends only what changed."""

    auto_approve: bool | None = None
    #: Hold every generation task; chats and planning keep running.
    paused: bool | None = None
    #: Override dex's own hold when a Claude limit is nearly spent.
    limit_paused: bool | None = None
    task_concurrency: int | None = Field(default=None, ge=1, le=MAX_WORKERS)
    animation_speed: float | None = Field(default=None, ge=0.1, le=8.0)
    chat_concurrency: int | None = Field(default=None, ge=1, le=MAX_WORKERS)
    #: Whether tasks may propose promoting a helper into a project's `utils/`.
    utility_proposals: bool | None = None
    #: One of EFFORT_IDS, or "" to fall back to the deployment default.
    effort: Literal["low", "medium", "high", "xhigh", "max", ""] | None = None
    model: str | None = None
    #: Per-project override, e.g. {"algorithms": "claude-sonnet-5"}.
    project_models: dict[str, str] | None = None


class TaskActionRequest(BaseModel):
    """One action, applied to every task named.

    A list rather than a path parameter because the UI collapses tasks by
    status: acting on a group of a hundred and thirty-seven would otherwise be
    a hundred and thirty-seven requests, and a half-applied action is worse
    than none.
    """

    ids: list[str] = Field(min_length=1, max_length=1000)
    action: Literal["pause", "resume", "restart", "archive"]


class ReviewRequest(BaseModel):
    rating: Literal["again", "good", "easy"] = "good"


class TaskMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    #: Upload batches attached to this note.
    uploads: list[str] = Field(default_factory=list)


class AnswerRequest(BaseModel):
    id: str
    answer: str


class ApprovalRequest(BaseModel):
    id: str
    decision: Literal["allow", "deny"]


# ------------------------------------------------------------------------- app


def _manifest_title(manifest: Path) -> str | None:
    """The name a package gives itself, when it gives one."""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # `title` first, then `pose` — yoga packages name themselves with the
    # latter, and either is the subject rather than the job that touched it.
    for key in ("title", "pose"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _limit_status(settings: SettingsStore) -> dict[str, Any]:
    """What the UI shows about Claude usage: how close, and who decided."""
    snapshot = await settings.limit_snapshot()
    window, used = closest_limit(snapshot)
    override = await settings.get(SettingsStore.LIMIT_OVERRIDE)
    reading = (snapshot.get(window or "") or {}) if window else {}
    return {
        "paused": await settings.limit_paused(),
        # None when no run has reported yet: dex learns this only from the CLI
        # while a task is in flight, so a fresh install knows nothing.
        "utilization": used if window else None,
        # Whether that number came from the CLI or was inferred from a status.
        # Some plans never send `utilization`, and a made-up percentage shown
        # as a measurement would be worse than saying which state it is in.
        "measured": isinstance(reading.get("utilization"), (int, float)),
        "status": reading.get("status"),
        "window": window,
        "resetsAt": reading.get("resetsAt"),
        "pauseAt": PAUSE_AT,
        "overridden": isinstance(override, dict),
        # What dex has spent finding this out, so polling is never invisible.
        "probe": await settings.get(SettingsStore.LIMIT_PROBE),
    }


def _without_gone_tasks(
    messages: list[dict[str, Any]], live: set[str]
) -> list[dict[str, Any]]:
    """Drop chips for tasks the thread no longer has.

    A "started N tasks" message stores a snapshot of each task as it was when
    the message was written, and the UI renders a chip from that snapshot when
    the store has nothing newer. So a task that was archived — or deleted —
    went on showing the state it had at the moment it was announced, usually
    `queued`, with no way to clear it: archiving appeared to do nothing at all.
    The thread's own task list is the authority on what still exists.
    """
    kept: list[dict[str, Any]] = []
    for message in messages:
        if message.get("kind") != "tasks":
            kept.append(message)
            continue
        data = message.get("data") or {}
        listed = data.get("tasks") or []
        remaining = [t for t in listed if isinstance(t, dict) and t.get("id") in live]
        if listed and not remaining:
            # Every task it announced is gone; the sentence describes nothing.
            continue
        kept.append({**message, "data": {**data, "tasks": remaining}})
    return kept


def create_app(config: Config = CONFIG) -> FastAPI:
    db = Database(config.database_url)
    bus = EventBus(db)
    tasks = TaskManager(config, bus, db)
    threads = ThreadStore(db)
    watcher = AssetWatcher(config, bus, tasks)
    # Holds work before a Claude limit runs out. `rebalance` is what a change
    # has to reach: it parks running work and stops the workers claiming, the
    # same path the operator's own pause takes.
    async def _pending_work() -> tuple[int, int]:
        """(running, waiting) — what decides whether a probe is worth paying for."""
        waiting = await db.pool.fetchval(
            "SELECT count(*) FROM tasks WHERE state IN ('queued', 'paused')"
        )
        return len(tasks.live), int(waiting or 0)

    usage = UsageWatcher(
        SettingsStore(db), bus, on_change=tasks.rebalance, pending=_pending_work
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await db.connect()
        await import_json_threads(db, config.threads_dir)
        await adopt_existing_project(db, config)
        if config.password_auth:
            # Something has to get the first admin in, and with password
            # sign-in on there is no env-var equivalent of DEX_ADMIN_EMAILS.
            # A no-op once any admin exists.
            seeded = await IdentityStore(db).ensure_seed_admin(
                config.seed_admin_username, config.seed_admin_password
            )
            if seeded is not None:
                log.warning(
                    "created the first admin: username %r, password %r — "
                    "dex will require a new password at first sign-in",
                    config.seed_admin_username, config.seed_admin_password,
                )
        await bus.start()
        await tasks.start()
        await watcher.start()
        await usage.start()
        try:
            yield
        finally:
            await usage.stop()
            await watcher.stop()
            await tasks.stop()
            await bus.stop()
            await db.close()

    app = FastAPI(title="dex", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.bus = bus
    app.state.tasks = tasks
    app.state.threads = threads
    app.state.db = db
    # Exposed so a caller can re-index after writing manifests directly, which
    # is what the tests do and what a manual repair would need.
    app.state.watcher = watcher
    app.state.usage = usage

    # One dependency behind every route. It resolves the caller to an `Access`:
    # the service credential (DEX_TOKEN), a signed-in user with a role, or a
    # rejection. `guard` therefore now means "may use dex at all" everywhere it
    # already appeared, and project scoping is applied per route where the
    # project is actually known.
    access_dep = build_access_dependency(config, db)
    admin_only = [Depends(build_admin_dependency(access_dep))]
    # One dependency per capability. A route needs both halves: the capability
    # says *what* the caller may do, and `access.check(project)` in the body
    # says *where*. Neither implies the other -- an operator granted `music`
    # may queue a task there and may not rewrite its guide.
    view_dep = build_capability_dependency(access_dep, CAP_VIEW)
    run_dep = build_capability_dependency(access_dep, CAP_RUN_TASKS)
    design_dep = build_capability_dependency(access_dep, CAP_DESIGN)
    projects_dep = build_capability_dependency(access_dep, CAP_MANAGE_PROJECTS)

    # Reading is the floor: every role has `view`, so this is what `guard` has
    # always meant -- "signed in, with a role" -- said in terms of capability
    # rather than of nothing in particular.
    guard = [Depends(view_dep)]
    can_run = [Depends(run_dep)]
    can_design = [Depends(design_dep)]
    can_manage_projects = [Depends(projects_dep)]

    app.include_router(build_router(config, db, access_dep))

    async def visible(access: Access) -> list[str]:
        """The project slugs this caller may see, in listing order."""
        return access.visible_projects([p.slug for p in await projects.list()])

    # Path-aware guards. FastAPI injects a path parameter into a dependency the
    # same way it does into a route, so these attach to `dependencies=[...]` and
    # the route bodies need no changes at all -- which is what makes covering
    # thirteen per-id routes a one-line edit each rather than thirteen new
    # signatures to get wrong.
    async def guard_task(task_id: str, access: Access = Depends(access_dep)) -> None:
        project = await db.pool.fetchval("SELECT project FROM tasks WHERE id = $1", task_id)
        if project is None:
            # Missing, or a task from before projects existed. Either way this
            # caller has no business with it unless they see everything.
            if not access.is_admin:
                raise HTTPException(status_code=404, detail="no such task")
            return
        access.check(project)

    async def guard_thread(thread_id: str, access: Access = Depends(access_dep)) -> None:
        project = await db.pool.fetchval("SELECT project FROM threads WHERE id = $1", thread_id)
        if project is None:
            if not access.is_admin:
                raise HTTPException(status_code=404, detail="no such thread")
            return
        access.check(project)

    async def guard_slug(slug: str, access: Access = Depends(access_dep)) -> None:
        """For routes whose path carries the project slug itself."""
        access.check(slug)

    task_guard = [Depends(access_dep), Depends(guard_task)]
    thread_guard = [Depends(access_dep), Depends(guard_thread)]
    slug_guard = [Depends(access_dep), Depends(guard_slug)]
    # The same path guards, but for a caller who must also hold a capability.
    # A task's project scopes *which* tasks; the capability decides whether
    # acting on one at all is this person's job.
    task_run_guard = [Depends(run_dep), Depends(guard_task)]
    thread_run_guard = [Depends(run_dep), Depends(guard_thread)]
    slug_design_guard = [Depends(design_dep), Depends(guard_slug)]

    # ---------------------------------------------------------------- status

    @app.get("/api/health", dependencies=guard)
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "workspace": str(config.workspace),
            "assetsDir": str(config.assets_dir),
            "model": config.model,
            # The live limit, not a value fixed at startup: it is a setting,
            # and reporting the startup guess told the operator 3 while 6 were
            # running.
            "concurrency": await SettingsStore(db).concurrency(
                SettingsStore.TASK_CONCURRENCY
            ),
            "maxConcurrency": MAX_WORKERS,
            "manim": manim_available(),
            "autoApprove": await SettingsStore(db).auto_approve(),
            "project": config.default_project,
            "authenticated": credential_source() is not None,
            "authSource": credential_source(),
            "running": len(tasks.live),
            # The priority ladder, visible: capacity is the task limit less the
            # chats currently holding a slot.
            "taskCapacity": await tasks.task_capacity(),
            "chatsActive": tasks.chat_limiter.active,
            "paused": len(await tasks.tasks.paused(limit=50)),
            # Capacity is zero for the first half minute after a restart. Said
            # out loud, because otherwise it looks like a pause nobody set.
            "startingUp": tasks.starting_up,
            "graceSeconds": round(tasks.grace_remaining, 1),
            "database": _safe_dsn(config.database_url),
        }

    # ----------------------------------------------------------------- tasks

    projects = ProjectStore(db, config.assets_dir, config.workspace)
    package_tags = PackageTagStore(db)

    async def resolve_project(slug: str | None) -> str:
        """Fall back to the default project so every path has one."""
        if slug and await projects.get(slug):
            return slug
        return config.default_project

    #: Threads with a chat call in flight. `planning` is otherwise known only to
    #: the tab that started it, so a reload showed a still-thinking thread as
    #: idle until the reply landed.
    planning_threads: set[str] = set()

    @contextlib.contextmanager
    def _planning(thread_id: str) -> Any:
        planning_threads.add(thread_id)
        # `threadId` in the data, which is what the bus stores in the column and
        # what the UI reads — `Event` itself carries only a task id.
        bus.publish(Event(type="thread_busy", data={"threadId": thread_id, "busy": True}))
        try:
            yield
        finally:
            planning_threads.discard(thread_id)
            bus.publish(Event(type="thread_busy", data={"threadId": thread_id, "busy": False}))

    CHAT_ATTEMPTS = 3

    async def _with_retries(what: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Run a chat call, retrying a couple of times before giving up.

        Model calls fail transiently — a dropped connection, a rate limit, a
        truncated stream — and losing a whole planning turn to one of those is
        needless. An authentication failure is not transient, so it is raised
        on the first attempt rather than tried three times.
        """
        last: Exception | None = None
        for attempt in range(1, CHAT_ATTEMPTS + 1):
            try:
                return await call()
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if is_auth_error(detail):
                    raise
                last = exc
                if attempt < CHAT_ATTEMPTS:
                    log.warning(
                        "%s failed (attempt %d/%d), retrying: %s",
                        what, attempt, CHAT_ATTEMPTS, detail,
                    )
                    await asyncio.sleep(attempt)  # a beat longer each time
        assert last is not None
        log.exception("%s failed after %d attempts", what, CHAT_ATTEMPTS, exc_info=last)
        raise last

    @app.get("/api/widgets", dependencies=guard)
    async def list_widgets(
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        """The widgets on disk, and this project's rules for using them.

        Read fresh from disk on every call. That is what makes a newly built
        widget appear without a restart, and the cost is a handful of small
        file reads.
        """
        chosen = await resolve_project(project)
        access.check(chosen)
        return {
            "project": chosen,
            "widgets": [w.to_json() for w in widgets.available(config.workspace)],
            "rules": [r.to_json() for r in widgets.rules_for(config.assets_dir, chosen)],
        }

    @app.get(
        "/api/widgets/{skill}/{version}/{widget}/index.js", dependencies=guard
    )
    async def widget_bundle(skill: str, version: str, widget: str) -> FileResponse:
        """One widget's built ES module, straight out of its skill.

        The browser imports exactly this inside a sandboxed frame, so it is
        served as JavaScript and cached hard: the URL carries the bundle's
        mtime, so a rebuilt widget is a different URL and an unchanged one is
        never re-fetched.
        """
        found = widgets.bundle_path(config.workspace, skill, version, widget)
        if found is None:
            raise HTTPException(404, "no such widget")
        return FileResponse(
            found,
            media_type="text/javascript",
            headers={"cache-control": "private, max-age=31536000, immutable"},
        )

    @app.get("/api/widgets/resolve", dependencies=guard)
    async def resolve_widget(
        path: str = Query(...), access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Which widget opens `path`, if any.

        The project comes from the path's first segment rather than a parameter,
        because that is how every asset is addressed here -- and it means the
        same access check as the asset routes.
        """
        _check_asset_path(access, path)
        project = path.strip("/").split("/")[0] if path.strip("/") else ""
        widget = widgets.resolve(config.assets_dir, config.workspace, project, path)
        return {"widget": widget.to_json() if widget else None}

    @app.get("/api/projects", dependencies=guard)
    async def list_projects(access: Access = Depends(access_dep)) -> dict[str, Any]:
        allowed = set(await visible(access))
        listed = [p for p in await projects.list() if p.slug in allowed]
        # The default is only a default if the caller can actually open it;
        # otherwise the UI would boot into a project it cannot read.
        fallback = (
            config.default_project
            if config.default_project in allowed
            else (listed[0].slug if listed else None)
        )
        return {
            "projects": [p.to_json() for p in listed],
            "default": fallback,
        }

    @app.post("/api/projects", dependencies=can_manage_projects)
    async def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
        """Creates the row, the directory, a starter guide, and a design thread."""
        project = await projects.create(body.name, body.description)
        await ensure_design_thread(db, projects, project.slug)
        return {"project": project.to_json()}

    @app.delete("/api/projects/{slug}", dependencies=can_manage_projects)
    async def delete_project(slug: str) -> dict[str, bool]:
        if slug == config.default_project:
            raise HTTPException(409, "the default project cannot be deleted")
        return {"ok": await projects.delete(slug)}

    @app.get("/api/projects/{slug}/guide", dependencies=slug_guard)
    async def read_guide(slug: str) -> dict[str, Any]:
        project = await projects.get(slug)
        if project is None:
            raise HTTPException(404, "no such project")
        return {
            "project": project.to_json(),
            "path": str(projects.guide_path(slug)),
            "text": projects.read_guide(slug),
        }

    @app.put("/api/projects/{slug}/guide", dependencies=slug_design_guard)
    async def write_guide(slug: str, body: GuideRequest) -> dict[str, Any]:
        """Replaces the guide. Every later task reads it, including resumes."""
        if await projects.get(slug) is None:
            raise HTTPException(404, "no such project")
        projects.write_guide(slug, body.text)
        bus.publish(Event(type="guide", data={"project": slug}, project=slug))
        return {"ok": True, "text": projects.read_guide(slug)}

    @app.get("/api/projects/{slug}/skills", dependencies=slug_guard)
    async def project_skills(slug: str) -> dict[str, Any]:
        """The skills this project has on, for the design thread's picker.

        Narrower than `GET /api/skills`, which is the admin's list of
        everything: this is what *this* project reads, which is what somebody
        editing its instructions needs to see.
        """
        if await projects.get(slug) is None:
            raise HTTPException(404, "no such project")
        on = skills.read_enabled(config.project_dir(slug)).skills
        return {
            "skills": [
                {
                    **skill.to_json(),
                    "doc": (skill.path / "SKILL.md").is_file(),
                }
                for skill in skills.all_skills(config.workspace)
                if on.get(skill.name) == skill.version
            ]
        }

    @app.get("/api/skills/{name}/doc", dependencies=guard)
    async def read_skill_doc(
        name: str, version: str = Query(default="")
    ) -> dict[str, Any]:
        skill = skills.find(config.workspace, name, version)
        if skill is None:
            raise HTTPException(404, "no such skill")
        doc = skill.path / "SKILL.md"
        return {
            "name": skill.name,
            "version": skill.version,
            "path": str(doc),
            "text": doc.read_text(encoding="utf-8") if doc.is_file() else "",
        }

    @app.put("/api/skills/{name}/doc", dependencies=[Depends(design_dep)])
    async def write_skill_doc(
        name: str, body: GuideRequest, version: str = Query(default="")
    ) -> dict[str, Any]:
        """Edit a skill's instructions, by publishing a new version of it.

        Never in place. A published version is what some project is running on
        and what another install may have copied, and its version *is* a hash
        of its contents — editing it would leave the name describing something
        that no longer exists. So this forks, writes the fork, and publishes,
        which is the same path the design chat takes.
        """
        source = skills.find(config.workspace, name, version)
        if source is None:
            raise HTTPException(404, "no such skill")

        draft = skills.fork(config.workspace, name, source.version)
        (draft.path / "SKILL.md").write_text(body.text, encoding="utf-8")
        try:
            done = skills.adopt(config.workspace, config.assets_dir)
        except skills.Collision as exc:
            raise HTTPException(409, str(exc)) from exc
        published = next((d for d in done if d[0] == name), None)
        if published is None:
            # The text was identical, so there was nothing to publish.
            return {"ok": True, "version": source.version, "moved": [], "text": body.text}
        _, new_version, moved = published
        log.info("published %s@%s from a guide edit, moved %s", name, new_version, moved)
        return {"ok": True, "version": new_version, "moved": moved, "text": body.text}

    @app.get("/api/settings", dependencies=guard)
    async def read_settings(access: Access = Depends(access_dep)) -> dict[str, Any]:
        settings = SettingsStore(db)
        if not access.can(CAP_MANAGE_PROJECTS):
            # Readable, but without the figures that describe projects they
            # cannot see. The menu renders; the spend panel is simply absent.
            return {
                "settings": await settings.all(),
                "costs": {},
                "tokens": {},
                "models": MODEL_CHOICES,
                "efforts": EFFORT_CHOICES,
                "project": config.project,
                "defaultModel": config.model,
                "defaultEffort": config.effort,
                "readOnly": True,
            }
        return {
            "settings": await settings.all(),
            "costs": await CostStore(db).totals(),
            "tokens": await CostStore(db).token_totals(),
            "models": MODEL_CHOICES,
            "efforts": EFFORT_CHOICES,
            "defaultEffort": config.effort,
            "project": config.project,
            "defaultModel": config.model,
            "running": len(tasks.live),
            # The priority ladder, visible: capacity is the task limit less the
            # chats currently holding a slot.
            "taskCapacity": await tasks.task_capacity(),
            "chatsActive": tasks.chat_limiter.active,
            "paused": len(await tasks.tasks.paused(limit=50)),
            "planning": tasks.chat_limiter.active,
            "limit": await _limit_status(settings),
        }

    @app.put("/api/settings", dependencies=can_manage_projects)
    async def write_settings(body: SettingsRequest) -> dict[str, Any]:
        settings = SettingsStore(db)
        released = moved = 0
        if body.auto_approve is not None:
            released = await tasks.set_auto_approve(body.auto_approve)
        if body.paused is not None:
            moved = await tasks.set_paused(body.paused)
        if body.utility_proposals is not None:
            await settings.set(SettingsStore.UTILITY_PROPOSALS, body.utility_proposals)
            # The loop only works in sequence: a promotion has to land before
            # the next task reads the index. An explicit task_concurrency in the
            # same request still wins — it is applied below, after this.
            await tasks.set_concurrency(
                SettingsStore.TASK_CONCURRENCY,
                SEQUENTIAL_CONCURRENCY if body.utility_proposals else PARALLEL_CONCURRENCY,
            )
            await tasks.rebalance()
        if body.effort is not None:
            # "" clears the override rather than storing an invalid effort.
            await settings.set(SettingsStore.EFFORT, body.effort or None)
        if body.limit_paused is not None:
            # Recorded as an override, not just a value: the watcher runs every
            # five minutes and would otherwise undo the operator's decision on
            # its next pass. It lasts as long as the limit window it was made
            # in, so one click cannot disable the guard permanently.
            window, _ = closest_limit(await settings.limit_snapshot())
            snapshot = await settings.limit_snapshot()
            resets_at = (snapshot.get(window or "") or {}).get("resetsAt")
            await settings.set(
                SettingsStore.LIMIT_OVERRIDE,
                {"paused": body.limit_paused, "until": resets_at},
            )
            await settings.set(SettingsStore.LIMIT_PAUSED, body.limit_paused)
            await tasks.rebalance()
        for key, value in (
            (SettingsStore.TASK_CONCURRENCY, body.task_concurrency),
            (SettingsStore.CHAT_CONCURRENCY, body.chat_concurrency),
        ):
            if value is not None:
                await tasks.set_concurrency(key, value)
        if body.model is not None:
            await settings.set(SettingsStore.MODEL, body.model)
        if body.animation_speed is not None:
            await settings.set(SettingsStore.ANIMATION_SPEED, body.animation_speed)
        for project, model in (body.project_models or {}).items():
            await settings.set(SettingsStore.project_model_key(project), model)

        current = await settings.all()
        bus.publish(Event(type="settings", data={"settings": current}))
        return {
            "settings": current,
            "limit": await _limit_status(settings),
            "costs": await CostStore(db).totals(),
            # How many parked approvals the change immediately let through.
            "released": released,
            # How many tasks the pause parked, or the resume put back.
            "moved": moved,
        }

    @app.get("/api/feed", dependencies=guard)
    async def feed(
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        """Topics for the review feed, ordered by what is closest to due."""
        name = await resolve_project(project)
        access.check(name)
        topics = await ReviewStore(db).order(name, discover(config.project_dir(name), name))
        return {
            "project": name,
            "projects": [p.slug for p in await projects.list()],
            "topics": [t.to_json() for t in topics],
        }

    @app.post("/api/feed/{slug}/reviewed", dependencies=can_run)
    async def reviewed(
        slug: str,
        body: ReviewRequest,
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        """Record that a topic was shown, and reschedule it."""
        chosen = await resolve_project(project)
        access.check(chosen)
        return {"review": await ReviewStore(db).record(chosen, slug, body.rating)}

    # ---------------------------------------------------------------- skills

    @app.get("/api/skills", dependencies=admin_only)
    async def list_skills() -> dict[str, Any]:
        """Every skill, its versions, and which version each project is on.

        Grouped by name rather than one entry per version: an install that has
        published a skill ten times has one capability, not ten, and a list
        that showed ten rows would bury the one thing an operator acts on.
        The versions ride along for the dropdown.

        Admin-only, like costs and user administration: enabling a skill
        changes what every task in a project may import.
        """
        slugs = [p.slug for p in await projects.list()]
        on: dict[str, dict[str, str]] = {}
        for slug in slugs:
            for name, version in skills.read_enabled(config.project_dir(slug)).skills.items():
                on.setdefault(name, {})[slug] = version

        grouped: dict[str, list[Any]] = {}
        for skill in skills.all_skills(config.workspace):
            grouped.setdefault(skill.name, []).append(skill)

        listed = []
        for name, versions in sorted(grouped.items()):
            # Newest first, so the dropdown opens on the current version and
            # follows a publish without anybody choosing again.
            versions.sort(key=lambda s: s.updated, reverse=True)
            listed.append({
                "name": name,
                "description": versions[0].description,
                "current": versions[0].version,
                "versions": [s.to_json() for s in versions],
                # Which version each project is on, if any.
                "projects": on.get(name, {}),
            })
        return {"projects": slugs, "skills": listed}

    @app.post("/api/skills/{name}/projects/{slug}", dependencies=admin_only)
    async def enable_skill(
        name: str, slug: str, version: str = Query(default="")
    ) -> dict[str, Any]:
        return await _set_skill(name, slug, version, on=True)

    @app.delete("/api/skills/{name}/projects/{slug}", dependencies=admin_only)
    async def disable_skill(
        name: str, slug: str, version: str = Query(default="")
    ) -> dict[str, Any]:
        return await _set_skill(name, slug, version, on=False)

    async def _set_skill(
        name: str, slug: str, version: str, *, on: bool
    ) -> dict[str, Any]:
        """Turn one version of one skill on or off for a project.

        Enabling a version replaces whatever version of that name was on:
        a project is on one version of a skill, and moving between them is
        what the version in the directory name exists for.
        """
        if await projects.get(slug) is None:
            raise HTTPException(404, "no such project")
        if skills.find(config.workspace, name, version) is None:
            raise HTTPException(404, "no such skill")

        project_dir = config.project_dir(slug)
        current = dict(skills.read_enabled(project_dir).skills)
        if on:
            current[name] = version
        else:
            current.pop(name, None)
        try:
            now = skills.materialise(
                config.workspace, project_dir, list(current), versions=current
            )
        except skills.Collision as exc:
            # Named, not resolved: two skills claiming one module is a rename,
            # and guessing a winner would hide which one is in use.
            raise HTTPException(409, str(exc)) from exc
        log.info("%s skill %s for %s", "enabled" if on else "disabled", name, slug)
        return {"project": slug, "skills": now.skills}

    @app.get("/api/costs", dependencies=admin_only)
    async def costs(
        period: str = Query(default="week"),
        granularity: str = Query(default="day"),
        group: str = Query(default="model"),
    ) -> dict[str, Any]:
        store = CostStore(db)
        return {
            "totals": await store.totals(),
            "series": await store.series(period, granularity, group),
            "period": period,
            "granularity": granularity,
            "group": group,
        }

    @app.get("/api/tasks", dependencies=guard)
    async def list_tasks(access: Access = Depends(access_dep)) -> dict[str, Any]:
        allowed = None if access.is_admin else set(await visible(access))
        return {
            "tasks": [
                tasks.merge_live(t).to_json(config.assets_dir)
                for t in await tasks.tasks.list()
                if allowed is None or t.project in allowed
            ]
        }

    @app.post("/api/uploads", dependencies=can_run)
    async def upload_sources(
        files: list[UploadFile] = File(...),
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        """Store files for a task to read as sources.

        One batch per call, so several files chosen together stay together and
        a brief can name them as one set. Nothing is parsed here: what a PDF or
        a recording means is the agent's problem, and guessing at it now would
        only be a guess that has to be right.
        """
        chosen = await resolve_project(project)
        access.check(chosen)
        target = config.project_datasets(chosen)
        stored: list[uploads.Upload] = []
        for item in files:
            data = await item.read()
            if not data:
                continue
            if len(data) > uploads.MAX_BYTES:
                raise HTTPException(
                    413,
                    f"{item.filename or 'file'} is "
                    f"{len(data) / 1_048_576:.0f} MB; the limit is "
                    f"{uploads.MAX_BYTES // 1_048_576} MB",
                )
            stored.append(uploads.store(target, item.filename or "attachment", data))
        if not stored:
            raise HTTPException(400, "no files were uploaded")
        log.info("stored %d source file(s) in %s", len(stored), target)
        return {
            "project": chosen,
            "directory": str(target),
            "files": [u.to_json() for u in stored],
        }

    @app.get("/api/datasets", dependencies=guard)
    async def list_sources(
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        """What is in a project's data directory, for the preview pane."""
        chosen = await resolve_project(project)
        access.check(chosen)
        root = config.project_datasets(chosen)
        files = [
            {"name": f.name, "bytes": f.stat().st_size, "mtime": f.stat().st_mtime}
            for f in sorted(root.glob("*"))
            if f.is_file() and not f.name.startswith(".")
        ]
        return {"project": chosen, "files": files}

    @app.get("/api/datasets/raw", dependencies=guard)
    async def raw_source(
        name: str = Query(...),
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> FileResponse:
        """One uploaded source file, for the viewer to render.

        Served inline rather than as an attachment: the point is that the
        browser shows the PDF in a frame, and a `content-disposition:
        attachment` header makes it download instead. The name is re-resolved
        against the project's own directory, so a `../` in it reaches nothing.
        """
        chosen = await resolve_project(project)
        access.check(chosen)
        root = config.project_datasets(chosen)
        found = uploads.resolve(root, [name])
        if not found:
            raise HTTPException(404, "not a file in this project's data")
        target = found[0].path
        media_type, _ = mimetypes.guess_type(target.name)
        return FileResponse(
            target,
            media_type=media_type or "application/octet-stream",
            # Both halves matter. Without `filename` starlette writes no
            # content-disposition at all, so a download has no name to save
            # under; with it but without `inline` the default is `attachment`,
            # and the frame downloads the PDF instead of rendering it. The
            # filename itself may be non-ASCII, which starlette encodes.
            filename=target.name,
            content_disposition_type="inline",
        )

    @app.post("/api/tasks", dependencies=can_run)
    async def submit(
        body: SubmitRequest, access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        chosen_project = await resolve_project(body.project)
        access.check(chosen_project)
        sources = config.project_datasets(chosen_project)
        task = await tasks.submit(
            uploads.with_sources(
                body.problem, uploads.resolve(sources, body.uploads), sources
            ),
            body.title, body.slug, body.thread_id,
            project=chosen_project,
            output_slug=body.updates or body.package or None,
            scope=body.scope,
            anchor=body.anchor,
        )
        if body.thread_id:
            await threads.touch(body.thread_id)
        return {"task": task.to_json(config.assets_dir)}

    @app.get("/api/tasks/{task_id}", dependencies=task_guard)
    async def get_task(task_id: str) -> dict[str, Any]:
        task = await tasks.tasks.get(task_id)
        if not task:
            raise HTTPException(404, "no such task")
        return {"task": tasks.merge_live(task).to_json(config.assets_dir)}

    @app.post("/api/tasks/{task_id}/resume", dependencies=task_run_guard)
    async def resume_task(
        task_id: str,
        in_place: bool = Query(
            default=False,
            description="Requeue this same task instead of forking a continuation.",
        ),
    ) -> dict[str, Any]:
        """Continue a paused, failed, or cancelled task.

        By default this forks a continuation, which keeps the failed attempt
        intact beside the new one. `in_place` sends the same task back to the
        queue instead — the right shape when the run did not really fail, so
        the thread keeps one chip and one running total for one piece of work.
        """
        if in_place:
            resumed = await tasks.resume_in_place(task_id)
            if resumed is None:
                raise HTTPException(409, "that task cannot be resumed")
            # No announcement: the thread already has this task's chip, and it
            # follows the state change on its own.
            return {"task": resumed.to_json(config.assets_dir)}
        resumed = await tasks.resume(task_id)
        if resumed is None:
            raise HTTPException(409, "that task cannot be resumed")
        await _announce(resumed, "Resuming")
        return {"task": resumed.to_json(config.assets_dir)}

    @app.post("/api/tasks/actions", dependencies=can_run)
    async def act_on_tasks(
        body: TaskActionRequest, access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Pause, restart, or archive one task or a whole collapsed group."""
        if not access.is_admin:
            # The request carries a list of ids, not one -- the UI collapses
            # tasks by status and acts on a whole group. Every project involved
            # has to be one this caller may act in, or a group action becomes a
            # way to reach past a grant.
            owners = await db.pool.fetch(
                "SELECT DISTINCT project FROM tasks WHERE id = ANY($1::text[])", body.ids
            )
            for row in owners:
                access.check(row["project"])
        run = {
            "pause": tasks.pause_task,
            "resume": tasks.resume_in_place,
            "restart": tasks.restart,
            "archive": tasks.archive,
        }[body.action]

        changed: list[dict[str, Any]] = []
        skipped: list[str] = []
        for task_id in body.ids:
            # One at a time, and a task that cannot take the action is skipped
            # rather than failing the batch: in a group of a hundred, some are
            # always in a state the action does not apply to.
            result = await run(task_id)
            if result is None:
                skipped.append(task_id)
            else:
                changed.append(result.to_json(config.assets_dir))

        if body.action in {"pause", "archive", "resume"}:
            # Both free capacity, so whatever is waiting can take it.
            await tasks.rebalance()
        return {"action": body.action, "tasks": changed, "skipped": skipped}

    @app.post("/api/tasks/{task_id}/rerun", dependencies=task_run_guard)
    async def rerun_task(task_id: str) -> dict[str, Any]:
        """Run the same problem again, keeping the earlier output."""
        again = await tasks.rerun(task_id)
        if again is None:
            raise HTTPException(409, "that task is still running")
        await _announce(again, "Re-running")
        return {"task": again.to_json(config.assets_dir)}

    @app.post("/api/tasks/{task_id}/answer", dependencies=task_run_guard)
    async def answer(task_id: str, body: AnswerRequest) -> dict[str, bool]:
        return {"ok": tasks.answer(task_id, body.id, body.answer)}

    @app.post("/api/tasks/{task_id}/approve", dependencies=task_run_guard)
    async def approve(task_id: str, body: ApprovalRequest) -> dict[str, bool]:
        return {"ok": tasks.answer(task_id, body.id, body.decision)}

    @app.get("/api/tasks/{task_id}/messages", dependencies=task_guard)
    async def read_task_messages(task_id: str) -> dict[str, Any]:
        return {"messages": await TaskMessageStore(db).pending(task_id)}

    @app.post("/api/tasks/{task_id}/messages", dependencies=task_run_guard)
    async def post_task_message(task_id: str, body: TaskMessageRequest) -> dict[str, Any]:
        """Queue a follow-up for a task.

        It is held while the task runs and delivered when it stops, as the brief
        for a follow-up attempt.
        """
        # Named in the note itself: the note becomes the follow-up's brief, so
        # this is what puts the paths in front of the agent that reads it.
        owner = await tasks.tasks.get(task_id)
        sources = config.project_datasets(owner.project if owner else None)
        note = uploads.with_sources(
            body.text, uploads.resolve(sources, body.uploads), sources
        )
        message = await tasks.queue_message(task_id, note)
        if message is None:
            raise HTTPException(404, "no such task")
        return {"message": message}

    @app.post("/api/tasks/{task_id}/cancel", dependencies=task_run_guard)
    async def cancel(task_id: str) -> dict[str, bool]:
        return {"ok": await tasks.cancel(task_id)}

    async def _announce(task: Any, verb: str) -> None:
        """Put a resumed or re-run task into its thread, so the chat shows it."""
        if not task.thread_id:
            return
        await publish_message(
            task.thread_id,
            ThreadMessage(
                role="dex", kind="tasks",
                text=f"{verb} {task.title} (attempt {task.attempt}).",
                data={"tasks": [task.to_json(config.assets_dir)]},
            ),
        )

    # ------------------------------------------------------------------ chat

    @app.post("/api/chat/plan", dependencies=can_run)
    async def chat_plan(
        body: ChatRequest, access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Propose a split into parallel tasks. Nothing is enqueued yet."""
        project_slug = await resolve_project(body.project)
        access.check(project_slug)
        # Directories only. A task slug is not a package: listing them here
        # let the planner read `bit-insertion-narrate` — the name of the task
        # that narrated `bit-insertion` — as a package of its own, and write
        # thirty briefs to edit packages that had never existed.
        existing = sorted(_existing_slugs(config, project_slug))
        try:
            model = await SettingsStore(db).model_for(project_slug, config.planner_model)

            async def attempt() -> Plan:
                async with tasks.chat_limiter:
                    return await plan_from_message(
                        body.message, config, existing, model,
                        project=project_slug, guide=projects.read_guide(project_slug),
                    )

            plan: Plan = await _with_retries("planning", attempt)
        except Exception as exc:
            log.exception("planning failed")
            detail = f"{type(exc).__name__}: {exc}"
            if is_auth_error(detail):
                raise HTTPException(503, AUTH_HINT) from exc
            raise HTTPException(502, f"planning failed: {detail}") from exc
        return {"plan": plan.to_json()}

    @app.post("/api/chat/confirm", dependencies=can_run)
    async def chat_confirm(
        body: ConfirmRequest, access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Enqueue an accepted plan. Tasks run in parallel up to the worker cap."""
        thread = await threads.get(body.thread_id) if body.thread_id else None
        project = thread.project if thread else config.default_project
        # The project comes from the thread, which the caller may not own: this
        # is the route that actually spends money, so it is checked even though
        # the plan that produced it already was.
        access.check(project)
        # Attached to the request, so they belong to every task the planner
        # split it into rather than to whichever one happens to be first.
        sources = config.project_datasets(project)
        attached = uploads.resolve(
            sources, body.uploads + [u for t in body.tasks for u in t.uploads]
        )
        created = [
            await tasks.submit(
                uploads.with_sources(t.problem, attached, sources),
                t.title, t.slug, body.thread_id, project=project,
                # Rewriting a package means writing into its directory, not
                # beside it — and so does joining siblings in a shared one. The
                # task still gets a slug of its own for identity.
                output_slug=t.updates or t.package or None,
                scope=t.scope,
                # What the operator's own material contributed to this task.
                # It came from the survey, through the plan, and is kept on the
                # task so the Files tab can show the input beside the output.
                anchor=t.anchor,
            )
            for t in body.tasks
        ]
        # The slug a task ends up with is not the slug the plan asked for: a
        # collision makes `slugify` append a suffix, so a plan row for
        # `x-narrate-2` can produce `x-narrate-2-2`. Recording what was asked
        # for is what lets the plan card find its own tasks again, instead of
        # showing them as never started and offering to run them a second time.
        payload = [
            {**task.to_json(config.assets_dir), "plannedSlug": planned.slug or task.slug}
            for planned, task in zip(body.tasks, created, strict=True)
        ]
        if body.thread_id and await threads.get(body.thread_id):
            await publish_message(
                body.thread_id,
                ThreadMessage(
                    role="dex",
                    kind="tasks",
                    text=f"Started {len(created)} task{'s' if len(created) != 1 else ''}.",
                    data={"tasks": payload},
                ),
            )
        return {"tasks": payload}

    # --------------------------------------------------------------- threads

    async def publish_message(thread_id: str, message: ThreadMessage) -> None:
        await threads.append(thread_id, message)
        bus.publish(
            Event(type="thread_message", data={"threadId": thread_id, "message": message.to_json()})
        )

    @app.get("/api/threads", dependencies=guard)
    async def list_threads(
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        chosen = await resolve_project(project)
        access.check(chosen)
        await ensure_design_thread(db, projects, chosen)
        costs = await threads.costs_by_thread()
        return {
            "project": chosen,
            "threads": [
                {**t.summary(), "costUsd": costs.get(t.id, 0.0)}
                for t in await threads.list(chosen)
            ],
        }

    @app.post("/api/threads", dependencies=can_run)
    async def create_thread(
        body: ThreadCreateRequest, access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        chosen = await resolve_project(body.project)
        access.check(chosen)
        thread = await threads.create(body.title, project=chosen)
        return {"thread": thread.to_json()}

    @app.get("/api/threads/{thread_id}", dependencies=thread_guard)
    async def get_thread(thread_id: str) -> dict[str, Any]:
        thread = await threads.get(thread_id)
        if thread is None:
            raise HTTPException(404, "no such thread")
        # Every task of the thread, whatever ran it and however long ago. The
        # default limit is 200, and this comment claimed "every" while a thread
        # of 546 delivered its oldest 200: plan cards then judged their own rows
        # against tasks the browser had never been sent, showed them as never
        # started, and offered to run work that was already queued.
        stored = await tasks.tasks.list(thread_id=thread_id, limit=THREAD_TASK_LIMIT)
        payload = thread.to_json()
        payload["messages"] = _without_gone_tasks(
            payload.get("messages") or [], {t.id for t in stored}
        )
        return {
            "thread": {
                **payload,
                "costUsd": await CostStore(db).by_thread(thread_id),
                "planning": thread_id in planning_threads,
            },
            "tasks": [tasks.merge_live(t).to_json(config.assets_dir) for t in stored],
        }

    @app.delete("/api/threads/{thread_id}", dependencies=thread_run_guard)
    async def hide_thread(thread_id: str) -> dict[str, bool]:
        """Hides rather than deletes. The route keeps its shape; the effect is
        now reversible, because deleting took the messages with it."""
        return {"ok": await threads.hide(thread_id)}

    @app.post("/api/threads/{thread_id}/unhide", dependencies=thread_run_guard)
    async def unhide_thread(thread_id: str) -> dict[str, bool]:
        return {"ok": await threads.unhide(thread_id)}

    @app.post("/api/threads/{thread_id}/messages", dependencies=thread_guard)
    async def post_message(
        thread_id: str, body: ThreadMessageRequest, access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Say something in a thread.

        What happens next depends on the thread: a chat thread answers with a
        plan of tasks to confirm, a design thread answers by redrafting the
        project's guide.

        Which is why the capability is checked here rather than in
        `dependencies`: the two branches are different jobs. Speaking in a
        design thread rewrites the guide and authors widgets, which is the
        author's work; speaking in a chat thread produces tasks to run, which
        is the operator's. One route, and the thread decides which it is.
        """
        thread = await threads.get(thread_id)
        if thread is None:
            raise HTTPException(404, "no such thread")

        design = thread.kind == "project_design"
        access.require(CAP_DESIGN if design else CAP_RUN_TASKS)

        # The transcript keeps what was typed; the model is given the paths as
        # well. Putting the paths in the transcript would make every reread of
        # the conversation carry a wall of absolute paths.
        sources = config.project_datasets(thread.project)
        attached = uploads.resolve(sources, body.uploads)
        await publish_message(
            thread_id,
            ThreadMessage(
                role="user", kind="text", text=body.text,
                data={"attachments": [u.name for u in attached]} if attached else {},
            ),
        )
        prompt = uploads.with_sources(body.text, attached, sources)
        if design:
            return await _design_turn(thread, prompt)
        # Hardcoded, and the same in every project: a message that arrives with
        # material attached is surveyed before it is planned. No guide gets a
        # say in it — the plan an operator reads has to mean the same thing
        # whichever project they happen to be in, and planning from a filename
        # when the file is right there is guessing.
        if surveyor.needs_survey(body.text, [u.name for u in attached]):
            return await _survey_turn(
                thread, body.text, [u.name for u in attached],
                surveyor.urls_in(body.text),
            )
        return await _plan_turn(thread, prompt)


    async def _survey_turn(
        thread: Any,
        text: str,
        attachments: list[str],
        urls: list[str],
        ask: str = "",
        options: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start the pre-planning pass, as a task.

        A task rather than an inline call because it is a real agent run with
        tools: as a task it gets the activity surface, the cost accounting and
        the retry path for free, and the operator can watch it decide. It
        posts no message of its own — the plan it leads to is the answer, and a
        placeholder saying "surveying…" would be a second thing in the thread
        saying what the task chip already says.
        """
        project_slug = thread.project or config.default_project
        # A question the planner wanted asked rides in the message. The survey
        # is the only step that may ask, so a planner that needs something is
        # escalated to here rather than putting a sentence in the chat that the
        # next planning call would not remember.
        message = (
            f"{text}\n\nBefore this can be planned, ask the operator: {ask}"
            + (f"\nLikely answers: {', '.join(options)}." if options else "")
            if ask
            else text
        )
        payload = json.dumps({
            "message": message,
            "attachments": attachments,
            "urls": urls,
            "guide": projects.read_guide(project_slug),
        })
        task = await tasks.submit(
            payload,
            title=(
                f"Ask: {ask[:60]}" if ask
                else f"Survey: {text.strip().splitlines()[0][:60] or 'attached sources'}"
            ),
            thread_id=thread.id,
            project=project_slug,
            scope="survey",
        )
        return {"task": task.to_json(config.assets_dir), "survey": {"queued": True}}

    async def _plan_from_survey(task: Any, result: str) -> None:
        """Plan from a survey that has just finished.

        Called by the runner through the queue. The survey's own account goes
        into the thread first: it is what the plan is built on, so an operator
        looking at a task list that surprises them can see the reasoning that
        produced it without opening the run.
        """
        if not task.thread_id:
            return
        thread = await threads.get(task.thread_id)
        if thread is None:
            return
        survey = surveyor.parse_survey(result)
        payload = task.survey_payload()

        # Deliberately not posted to the thread. The survey is working, not an
        # answer: its whole account is in the task's own activity, where the
        # operator can read it if they want to. What belongs in the chat is the
        # plan it led to, and a wall of segments above that plan only buries it.
        #
        # The planner is tool-less and never sees the sources, so the survey's
        # rendering is the only account of them it gets.
        await _plan_turn(
            thread,
            uploads.with_sources(
                payload["message"],
                uploads.resolve(
                    config.project_datasets(task.project), payload["attachments"]
                ),
                config.project_datasets(task.project),
            ),
            survey=survey.render(),
            # This plan *is* the escalation's result. Escalating again would
            # start a second pre-planning task over the same request, and a
            # third over that one.
            may_escalate=False,
        )

    async def _design_turn(thread: Any, text: str) -> dict[str, Any]:
        with _planning(thread.id):
            return await _design_turn_inner(thread, text)

    async def _design_turn_inner(thread: Any, text: str) -> dict[str, Any]:
        """Run one design turn as a task.

        It used to be a single tool-less call that returned the whole guide,
        which meant it could not author anything and had nothing to show while
        it worked. As a task it gets the tools it needs *and* the entire
        activity surface — streamed text, collapsed thinking, tool calls,
        diffs, questions — because that surface belongs to tasks and this is
        now one.
        """
        project_slug = thread.project or config.default_project
        project = await projects.get(project_slug)
        if project is None:
            raise HTTPException(404, "no such project")

        history = designer.history_text(
            [{"role": m.role, "text": m.text} for m in thread.messages]
        )
        payload = json.dumps({
            "message": text,
            "guide": projects.read_guide(project_slug),
            "history": history,
        })

        task = await tasks.submit(
            payload,
            title=text.strip().splitlines()[0][:80] or "Design turn",
            thread_id=thread.id,
            project=project_slug,
            scope="design",
        )
        # No summary message here: the task's own reply is the answer, and it
        # arrives streamed. Posting a placeholder would leave two messages
        # saying different things about the same turn.
        return {"task": task.to_json(config.assets_dir), "design": {"queued": True}}

    #: A request can imply far more work than fits one reply, so planning runs in
    #: batches: each is a plan of its own in the thread, and the planner says in
    #: `remaining` what it has not reached yet.
    PLAN_BATCHES = 10

    async def _plan_turn(
        thread: Any, text: str, survey: str = "", may_escalate: bool = True
    ) -> dict[str, Any]:
        with _planning(thread.id):
            return await _plan_turn_inner(thread, text, survey, may_escalate)

    async def _plan_turn_inner(
        thread: Any, text: str, survey: str = "", may_escalate: bool = True
    ) -> dict[str, Any]:
        project_slug = thread.project or config.default_project
        # Directories only — see the note in `chat_plan`.
        taken = _existing_slugs(config, project_slug)
        model = await SettingsStore(db).model_for(project_slug, config.planner_model)
        guide = projects.read_guide(project_slug)

        first: Plan | None = None
        first_reply: ThreadMessage | None = None
        request = text

        for batch in range(1, PLAN_BATCHES + 1):
            try:
                async def attempt(request: str = request) -> Plan:
                    async with tasks.chat_limiter:
                        return await plan_from_message(
                            request, config, sorted(taken), model,
                            project=project_slug, guide=guide, survey=survey,
                        )

                plan = await _with_retries("planning", attempt)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                message = AUTH_HINT if is_auth_error(detail) else f"Planning failed: {detail}"
                await publish_message(
                    thread.id,
                    ThreadMessage(role="dex", kind="error", text=message, data={"detail": detail}),
                )
                # A later batch failing still leaves the earlier ones standing.
                if first is not None:
                    break
                raise HTTPException(
                    503 if is_auth_error(detail) else 502, message
                ) from exc

            # A question is not the chat's to ask. The planner is one stateless
            # call: it cannot follow up, and an answer typed underneath it
            # reaches a *different* call that remembers none of it — which is
            # how a link surveyed into twenty parts came back as one task.
            # Pre-planning can ask, because it parks a task with an activity
            # pane and carries the answer forward itself, so the question is
            # handed there. Never from a run that came *out* of pre-planning:
            # that one has already had its chance to ask.
            if (
                may_escalate
                and plan.needs_clarification
                and not plan.tasks
                and first is None
            ):
                return await _survey_turn(
                    thread, text, [], surveyor.urls_in(text),
                    ask=plan.needs_clarification, options=plan.options,
                )
            # Not escalating leaves the old behaviour: the question is posted
            # as text and the operator replies to it. That is the path for a
            # run that already came out of pre-planning, which has had its
            # chance to ask and must not start another round.

            reply = ThreadMessage(
                role="dex",
                kind="plan" if plan.tasks else "text",
                text=plan.needs_clarification or plan.notes,
                data={**plan.to_json(), "batch": batch},
            )
            await publish_message(thread.id, reply)
            if first is None:
                first, first_reply = plan, reply

            taken |= {t.slug for t in plan.tasks}
            if not plan.tasks or not plan.remaining.strip():
                break
            if batch == PLAN_BATCHES:
                await publish_message(
                    thread.id,
                    ThreadMessage(
                        role="dex",
                        kind="text",
                        text=(
                            f"Stopped after {PLAN_BATCHES} plans. Still outstanding: "
                            f"{plan.remaining.strip()}"
                        ),
                    ),
                )
                break
            # The next batch is told what is left and what it must not repeat.
            request = (
                f"{text}\n\nAlready planned, do not repeat: "
                f"{', '.join(sorted(taken)) or 'nothing yet'}.\n"
                f"Continue with what is left: {plan.remaining.strip()}"
            )

        assert first is not None and first_reply is not None
        return {"plan": first.to_json(), "message": first_reply.to_json()}

    # ---------------------------------------------------------------- events

    @app.get("/api/tasks/{task_id}/events", dependencies=task_guard)
    async def task_events(task_id: str, limit: int = Query(default=2000, le=10_000)) -> dict[str, Any]:
        """Everything one task did, however long ago.

        The live stream replays only the most recent events across every task —
        a few thousand — which after a busy day reaches back minutes, not days.
        A task opened from last week had a blank Activity tab, not because its
        events were gone but because nothing ever asked for them.
        """
        events = await bus.history(task_id=task_id, after_seq=0, limit=limit)
        return {"taskId": task_id, "events": [e.to_json() for e in events]}

    @app.get("/api/events", dependencies=guard)
    async def events(
        request: Request,
        task: str | None = Query(default=None, description="filter to one task id"),
        after: int = Query(default=0, description="resume after this event seq"),
        access: Access = Depends(access_dep),
    ) -> StreamingResponse:
        # Resolved once, before the stream opens: a long-lived SSE connection
        # cannot re-ask per event, and re-reading grants for every one of the
        # ~3,000 events a second this carries would be a query storm. The cost
        # is that a grant changed mid-stream applies on reconnect -- which is
        # why removing a role also ends that user's sessions.
        allowed = None if access.is_admin else await visible(access)

        async def stream() -> AsyncIterator[bytes]:
            yield b": connected\n\n"
            subscription = bus.subscribe(task_id=task, after_seq=after, projects=allowed)
            pending = asyncio.ensure_future(subscription.__anext__())
            last_ping = time.monotonic()
            try:
                while True:
                    # Poll for a hung-up client every second instead of only
                    # when an event or the keepalive arrives — otherwise a
                    # closed tab keeps this generator (and its bus
                    # subscription) alive until the next ping.
                    await asyncio.wait({pending}, timeout=1.0)
                    if await request.is_disconnected():
                        return
                    if pending.done():
                        try:
                            event = pending.result()
                        except StopAsyncIteration:
                            return
                        pending = asyncio.ensure_future(subscription.__anext__())
                        yield f"id: {event.seq}\ndata: {json.dumps(event.to_json())}\n\n".encode()
                        last_ping = time.monotonic()
                    elif time.monotonic() - last_ping >= PING_INTERVAL_S:
                        # Keeps proxies and sleeping phones from dropping the stream.
                        yield b": ping\n\n"
                        last_ping = time.monotonic()
            finally:
                pending.cancel()
                with contextlib.suppress(Exception):
                    await subscription.aclose()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    # ---------------------------------------------------------------- assets

    @app.get("/api/assets", dependencies=guard)
    async def read_asset(
        path: str = Query(default=""),
        match: str | None = Query(
            default=None,
            description="glob, relative to path, to gather files from below it",
        ),
        access: Access = Depends(access_dep),
    ) -> Any:
        """Directory listing or text file content, confined to the assets root."""
        _check_asset_path(access, path)
        target = _resolve(config, path)
        if target.is_dir() and match:
            # A project's shared assets do not all live in one directory: each
            # task writes into its own output directory, so a pose generated by
            # a task sits under it rather than under the project. Gathering them
            # here keeps that one request instead of the browser walking the
            # tree a directory at a time.
            if ".." in match or match.startswith("/"):
                raise HTTPException(400, "match must stay inside the directory")
            root = config.assets_dir.resolve()
            found = sorted(
                child
                for child in target.glob(match)
                if child.is_file()
                and not child.name.startswith(".")
                and root in child.resolve().parents
            )
            return {
                "kind": "dir",
                "path": _rel(config, target),
                # `name` is the path from the assets root, so each hit can be
                # read back through this same endpoint.
                "entries": [
                    {
                        "name": _rel(config, child),
                        "dir": False,
                        "kind": KINDS.get(child.suffix, "file"),
                        "bytes": child.stat().st_size,
                    }
                    for child in found
                ],
            }
        if target.is_dir():
            return {
                "kind": "dir",
                "path": _rel(config, target),
                "entries": sorted(
                    (
                        {
                            "name": child.name,
                            "dir": child.is_dir(),
                            "kind": KINDS.get(child.suffix, "file"),
                            "bytes": child.stat().st_size if child.is_file() else 0,
                        }
                        for child in target.iterdir()
                        if not child.name.startswith(".")
                    ),
                    key=lambda e: (not e["dir"], e["name"]),
                ),
            }
        if target.suffix not in TEXT_SUFFIXES:
            raise HTTPException(415, f"{target.suffix} is binary — use /api/assets/raw")
        if target.stat().st_size > 2_000_000:
            raise HTTPException(413, "file too large")
        return {
            "kind": "file",
            "path": _rel(config, target),
            "ext": target.suffix.lstrip("."),
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }

    @app.get("/api/packages", dependencies=guard)
    async def list_packages(
        project: str | None = Query(default=None),
        access: Access = Depends(access_dep),
    ) -> dict[str, Any]:
        """Every package in a project, with the tags its manifest carries.

        Built here rather than in the browser because the tags live one file
        deep in each package: the Library would otherwise fetch a hundred and
        thirty manifests to draw its filter pills.
        """
        chosen = await resolve_project(project)
        access.check(chosen)
        root = (config.assets_dir / chosen).resolve()
        if not root.is_dir():
            return {"packages": [], "tags": []}

        # Titles come from the tasks that produced the packages; a package with
        # no surviving task still lists under its slug.
        titles: dict[str, str] = {}
        # Newest first, so a re-run's title wins over the attempt it replaced.
        for task in await tasks.tasks.list(limit=1000):
            if task.project == chosen:
                titles.setdefault(task.output_slug or task.slug, task.title)

        # Tags come from the index the watcher maintains, not from opening a
        # hundred and thirty manifests on every load.
        indexed = await package_tags.for_project(chosen)
        # A package the index has never seen is read once and recorded, so a
        # manifest that arrived while the watcher was not listening still shows
        # its tags. Bounded by packages never indexed, which is zero once the
        # project has been opened.
        unseen = [p for p in root.iterdir() if p.is_dir() and p.name not in indexed]
        for directory in unseen:
            tags = read_manifest_tags(directory / "manifest.json")
            await package_tags.set(chosen, directory.name, tags)
            indexed[directory.name] = tags

        packages: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            files = [f for f in directory.rglob("*") if f.is_file() and not f.name.startswith(".")]
            tags = indexed.get(directory.name, [])
            for tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
            packages.append({
                "slug": directory.name,
                # The manifest's own name first. A task is named after the job
                # it did — "Add problem-intro voiceover section — Two Sum" — and
                # the Library wants the subject, which is what the package
                # calls itself.
                "displayName": _manifest_title(directory / "manifest.json")
                or titles.get(directory.name)
                or directory.name.replace("-", " ").replace("_", " ").title(),
                "files": len(files),
                "path": f"{chosen}/{directory.name}",
                "tags": tags,
            })

        packages.sort(key=lambda p: p["displayName"].lower())
        return {
            "packages": packages,
            # Ordered by how many packages carry each, so the pills that
            # actually divide the library come first. The UI groups them; what
            # the groups are is decided by the tags themselves, not here.
            "tags": [
                {"name": name, "count": count}
                for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        }

    @app.get("/api/assets/animation", dependencies=guard)
    async def animation_info(
        path: str = Query(...), access: Access = Depends(access_dep)
    ) -> dict[str, Any]:
        """Duration, and the checkpoints this animation can be stepped through."""
        _check_asset_path(access, path)
        target = _resolve(config, path)
        if not target.is_file():
            raise HTTPException(404, "not a file")
        return {
            **animation.describe(target),
            "defaultSpeed": await SettingsStore(db).animation_speed(),
        }

    @app.get("/api/assets/animation/play", dependencies=guard)
    async def animation_play(
        path: str = Query(...),
        speed: float | None = Query(default=None, ge=0.1, le=8.0),
        segment: int | None = Query(default=None, ge=0),
        access: Access = Depends(access_dep),
    ) -> FileResponse:
        """The animation at a chosen speed, optionally just one checkpoint."""
        _check_asset_path(access, path)
        target = _resolve(config, path)
        if not target.is_file():
            raise HTTPException(404, "not a file")
        chosen = speed if speed is not None else await SettingsStore(db).animation_speed()
        served = await asyncio.to_thread(
            animation.variant, target, config.animation_cache, speed=chosen, segment=segment
        )
        # The type the file actually is. This said `image/gif` for everything,
        # from when animations only were GIFs; browsers sniffed an mp4 and
        # played it anyway, but a video announced as an image is a poor bet to
        # keep making — it is the kind of thing that decides whether a range
        # request or a cache entry behaves.
        media_type = mimetypes.guess_type(served.name)[0] or "application/octet-stream"
        return FileResponse(
            served,
            media_type=media_type,
            # Prefetching a video is pointless if activating the reel throws the
            # bytes away. These are content-addressed by mtime and etag, so a
            # short window is safe and a changed file still revalidates.
            headers={"cache-control": "private, max-age=300"},
        )

    @app.get("/api/assets/raw", dependencies=guard)
    async def raw_asset(
        path: str = Query(...), access: Access = Depends(access_dep)
    ) -> FileResponse:
        """Serves generated GIFs and other binaries to the viewer."""
        _check_asset_path(access, path)
        target = _resolve(config, path)
        if not target.is_file():
            raise HTTPException(404, "not a file")
        media_type, _ = mimetypes.guess_type(target.name)
        return FileResponse(target, media_type=media_type or "application/octet-stream")

    # A follow-up started by a worker (from a queued note) is announced the
    # same way one started from a button is.
    tasks.announce = _announce
    # The queue hands a finished survey back here, because planning from one
    # needs the thread store and the planner and the queue has neither.
    tasks.surveyed = _plan_from_survey

    # ------------------------------------------------------------------- ui

    # In production the API and the built UI share an origin, so a phone only
    # needs the one URL the server prints. Mounted last so /api wins.
    # Built widget bundles. `check_dir=False` so an install with no widgets
    # still starts; the directory can appear later without a restart because
    # StaticFiles resolves each request against the filesystem as it arrives.
    # Widgets are served out of the skills that provide them — see
    # `/api/widgets/{skill}/{version}/{widget}/index.js` above. There used to
    # be a `StaticFiles` mount over a top-level `widgets/` directory holding a
    # materialised copy of each, which meant a bundle could be built in one
    # place and served from the other. One silently was.

    web_dist = config.workspace / "web" / "dist"
    if (web_dist / "index.html").exists():
        app.mount("/", _SpaFiles(directory=web_dist, html=True), name="web")
        log.info("serving the UI from %s", web_dist)
    else:
        log.info("no built UI at %s — run `npm run build` in web/", web_dist)

    return app


# ------------------------------------------------------------------- helpers


class _SpaFiles(StaticFiles):
    """Static files, with unknown routes falling back to the app shell.

    The UI keeps its place in the URL, so a reload can ask for a path that only
    the client knows how to render. Plain `StaticFiles` answers 404 and the
    reload fails, which defeats the point of putting state in the URL.

    Only extensionless paths fall back: a missing `.js` or `.css` must stay a
    404 rather than quietly returning HTML, which turns a broken build into a
    confusing parse error instead of an obvious missing file.
    """

    async def get_response(self, path: str, scope: Any) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # StaticFiles raises rather than returning a 404 response, so the
            # fallback has to catch rather than inspect.
            # An unknown API route must stay a 404: answering it with the app
            # shell turns a client bug into a confusing HTML parse error.
            if (
                exc.status_code != 404
                or PurePosixPath(path).suffix
                or path.startswith("api/")
            ):
                raise
            return await super().get_response("index.html", scope)


def _check_asset_path(access: Access, path: str) -> None:
    """Refuse an asset path outside the caller's projects.

    Assets are addressed by path rather than by slug, and the first segment is
    the project — `yoga/bird-of-paradise-pose/poses/x.json`. This is the one
    surface where guessing a string is enough to reach another project's work,
    so every asset route has to run it, not just the listing one.

    An empty path is the assets root, which lists the projects themselves; only
    a caller who can see everything gets that.
    """
    if access.unrestricted or access.is_admin:
        return
    first = path.strip("/").split("/")[0] if path.strip("/") else ""
    if not first:
        raise HTTPException(status_code=404, detail="not found")
    access.check(first)


def _resolve(config: Config, path: str) -> Path:
    """Resolve a client-supplied path inside the assets root, or 403/404."""
    root = config.assets_dir.resolve()
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(403, "path is outside the assets directory")
    if not candidate.exists():
        raise HTTPException(404, "not found")
    return candidate


def _rel(config: Config, path: Path) -> str:
    return str(path.relative_to(config.assets_dir.resolve()))


def _safe_dsn(dsn: str) -> str:
    from .db import _safe

    return _safe(dsn)


def _projects(config: Config) -> list[str]:
    """Sibling directories of the assets root, each its own project."""
    root = config.assets_dir.parent
    if not root.exists():
        return [config.project]
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def _existing_slugs(config: Config, project: str | None = None) -> set[str]:
    directory = config.project_dir(project)
    if not directory.exists():
        return set()
    return {p.name for p in directory.iterdir() if p.is_dir()}
