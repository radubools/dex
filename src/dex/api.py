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

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from .bus import EventBus
from .config import CONFIG, MAX_WORKERS, Config
from .db import Database, import_json_threads
from . import animation
from . import designer
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

#: Far above any real thread, but bounded: a runaway thread should not be able
#: to make one request read the whole table.
THREAD_TASK_LIMIT = 10_000

TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".yaml", ".yml", ".cfg"}


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
    #: "package" (the default) or "project" for a uniform edit across every
    #: package the project already has. Anything else is read as "package":
    #: widening what a task may touch should take saying so exactly.
    scope: str = "package"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    #: Which project to plan for. Falls back to the default when unset, so the
    #: planner is never left guessing what kind of work is wanted.
    project: str | None = None


class ConfirmRequest(BaseModel):
    """The plan the operator accepted, possibly edited in the UI."""

    tasks: list[SubmitRequest]
    thread_id: str | None = None


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

    def require_token(request: Request) -> None:
        """Shared secret for LAN exposure. Unset means localhost-only use."""
        if not config.token:
            return
        provided = request.headers.get("x-dex-token") or request.query_params.get("t")
        if provided != config.token:
            raise HTTPException(status_code=401, detail="bad or missing token")

    guard = [Depends(require_token)]

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

    projects = ProjectStore(db, config.assets_dir)
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

    @app.get("/api/projects", dependencies=guard)
    async def list_projects() -> dict[str, Any]:
        return {
            "projects": [p.to_json() for p in await projects.list()],
            "default": config.default_project,
        }

    @app.post("/api/projects", dependencies=guard)
    async def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
        """Creates the row, the directory, a starter guide, and a design thread."""
        project = await projects.create(body.name, body.description)
        await ensure_design_thread(db, projects, project.slug)
        return {"project": project.to_json()}

    @app.delete("/api/projects/{slug}", dependencies=guard)
    async def delete_project(slug: str) -> dict[str, bool]:
        if slug == config.default_project:
            raise HTTPException(409, "the default project cannot be deleted")
        return {"ok": await projects.delete(slug)}

    @app.get("/api/projects/{slug}/guide", dependencies=guard)
    async def read_guide(slug: str) -> dict[str, Any]:
        project = await projects.get(slug)
        if project is None:
            raise HTTPException(404, "no such project")
        return {
            "project": project.to_json(),
            "path": str(projects.guide_path(slug)),
            "text": projects.read_guide(slug),
        }

    @app.put("/api/projects/{slug}/guide", dependencies=guard)
    async def write_guide(slug: str, body: GuideRequest) -> dict[str, Any]:
        """Replaces the guide. Every later task reads it, including resumes."""
        if await projects.get(slug) is None:
            raise HTTPException(404, "no such project")
        projects.write_guide(slug, body.text)
        bus.publish(Event(type="guide", data={"project": slug}))
        return {"ok": True, "text": projects.read_guide(slug)}

    @app.get("/api/settings", dependencies=guard)
    async def read_settings() -> dict[str, Any]:
        settings = SettingsStore(db)
        return {
            "settings": await settings.all(),
            "costs": await CostStore(db).totals(),
            "models": MODEL_CHOICES,
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

    @app.put("/api/settings", dependencies=guard)
    async def write_settings(body: SettingsRequest) -> dict[str, Any]:
        settings = SettingsStore(db)
        released = moved = 0
        if body.auto_approve is not None:
            released = await tasks.set_auto_approve(body.auto_approve)
        if body.paused is not None:
            moved = await tasks.set_paused(body.paused)
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
    async def feed(project: str | None = Query(default=None)) -> dict[str, Any]:
        """Topics for the review feed, ordered by what is closest to due."""
        name = await resolve_project(project)
        topics = await ReviewStore(db).order(name, discover(config.project_dir(name), name))
        return {
            "project": name,
            "projects": [p.slug for p in await projects.list()],
            "topics": [t.to_json() for t in topics],
        }

    @app.post("/api/feed/{slug}/reviewed", dependencies=guard)
    async def reviewed(
        slug: str, body: ReviewRequest, project: str | None = Query(default=None)
    ) -> dict[str, Any]:
        """Record that a topic was shown, and reschedule it."""
        chosen = await resolve_project(project)
        return {"review": await ReviewStore(db).record(chosen, slug, body.rating)}

    @app.get("/api/costs", dependencies=guard)
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
    async def list_tasks() -> dict[str, Any]:
        return {
            "tasks": [
                tasks.merge_live(t).to_json(config.assets_dir) for t in await tasks.tasks.list()
            ]
        }

    @app.post("/api/tasks", dependencies=guard)
    async def submit(body: SubmitRequest) -> dict[str, Any]:
        task = await tasks.submit(
            body.problem, body.title, body.slug, body.thread_id,
            project=await resolve_project(body.project),
            output_slug=body.updates or None,
            scope=body.scope,
        )
        if body.thread_id:
            await threads.touch(body.thread_id)
        return {"task": task.to_json(config.assets_dir)}

    @app.get("/api/tasks/{task_id}", dependencies=guard)
    async def get_task(task_id: str) -> dict[str, Any]:
        task = await tasks.tasks.get(task_id)
        if not task:
            raise HTTPException(404, "no such task")
        return {"task": tasks.merge_live(task).to_json(config.assets_dir)}

    @app.post("/api/tasks/{task_id}/resume", dependencies=guard)
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

    @app.post("/api/tasks/actions", dependencies=guard)
    async def act_on_tasks(body: TaskActionRequest) -> dict[str, Any]:
        """Pause, restart, or archive one task or a whole collapsed group."""
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

    @app.post("/api/tasks/{task_id}/rerun", dependencies=guard)
    async def rerun_task(task_id: str) -> dict[str, Any]:
        """Run the same problem again, keeping the earlier output."""
        again = await tasks.rerun(task_id)
        if again is None:
            raise HTTPException(409, "that task is still running")
        await _announce(again, "Re-running")
        return {"task": again.to_json(config.assets_dir)}

    @app.post("/api/tasks/{task_id}/answer", dependencies=guard)
    async def answer(task_id: str, body: AnswerRequest) -> dict[str, bool]:
        return {"ok": tasks.answer(task_id, body.id, body.answer)}

    @app.post("/api/tasks/{task_id}/approve", dependencies=guard)
    async def approve(task_id: str, body: ApprovalRequest) -> dict[str, bool]:
        return {"ok": tasks.answer(task_id, body.id, body.decision)}

    @app.get("/api/tasks/{task_id}/messages", dependencies=guard)
    async def read_task_messages(task_id: str) -> dict[str, Any]:
        return {"messages": await TaskMessageStore(db).pending(task_id)}

    @app.post("/api/tasks/{task_id}/messages", dependencies=guard)
    async def post_task_message(task_id: str, body: TaskMessageRequest) -> dict[str, Any]:
        """Queue a follow-up for a task.

        It is held while the task runs and delivered when it stops, as the brief
        for a follow-up attempt.
        """
        message = await tasks.queue_message(task_id, body.text)
        if message is None:
            raise HTTPException(404, "no such task")
        return {"message": message}

    @app.post("/api/tasks/{task_id}/cancel", dependencies=guard)
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

    @app.post("/api/chat/plan", dependencies=guard)
    async def chat_plan(body: ChatRequest) -> dict[str, Any]:
        """Propose a split into parallel tasks. Nothing is enqueued yet."""
        project_slug = await resolve_project(body.project)
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

    @app.post("/api/chat/confirm", dependencies=guard)
    async def chat_confirm(body: ConfirmRequest) -> dict[str, Any]:
        """Enqueue an accepted plan. Tasks run in parallel up to the worker cap."""
        thread = await threads.get(body.thread_id) if body.thread_id else None
        project = thread.project if thread else config.default_project
        created = [
            await tasks.submit(
                t.problem, t.title, t.slug, body.thread_id, project=project,
                # Rewriting a package means writing into its directory, not
                # beside it. The task still gets a slug of its own for identity.
                output_slug=t.updates or None,
                scope=t.scope,
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
    async def list_threads(project: str | None = Query(default=None)) -> dict[str, Any]:
        chosen = await resolve_project(project)
        await ensure_design_thread(db, projects, chosen)
        costs = await threads.costs_by_thread()
        return {
            "project": chosen,
            "threads": [
                {**t.summary(), "costUsd": costs.get(t.id, 0.0)}
                for t in await threads.list(chosen)
            ],
        }

    @app.post("/api/threads", dependencies=guard)
    async def create_thread(body: ThreadCreateRequest) -> dict[str, Any]:
        thread = await threads.create(body.title, project=await resolve_project(body.project))
        return {"thread": thread.to_json()}

    @app.get("/api/threads/{thread_id}", dependencies=guard)
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
        return {
            "thread": {
                **thread.to_json(),
                "costUsd": await CostStore(db).by_thread(thread_id),
                "planning": thread_id in planning_threads,
            },
            "tasks": [tasks.merge_live(t).to_json(config.assets_dir) for t in stored],
        }

    @app.delete("/api/threads/{thread_id}", dependencies=guard)
    async def hide_thread(thread_id: str) -> dict[str, bool]:
        """Hides rather than deletes. The route keeps its shape; the effect is
        now reversible, because deleting took the messages with it."""
        return {"ok": await threads.hide(thread_id)}

    @app.post("/api/threads/{thread_id}/unhide", dependencies=guard)
    async def unhide_thread(thread_id: str) -> dict[str, bool]:
        return {"ok": await threads.unhide(thread_id)}

    @app.post("/api/threads/{thread_id}/messages", dependencies=guard)
    async def post_message(thread_id: str, body: ThreadMessageRequest) -> dict[str, Any]:
        """Say something in a thread.

        What happens next depends on the thread: a chat thread answers with a
        plan of tasks to confirm, a design thread answers by redrafting the
        project's guide.
        """
        thread = await threads.get(thread_id)
        if thread is None:
            raise HTTPException(404, "no such thread")

        await publish_message(thread_id, ThreadMessage(role="user", kind="text", text=body.text))
        if thread.kind == "project_design":
            return await _design_turn(thread, body.text)
        return await _plan_turn(thread, body.text)

    async def _design_turn(thread: Any, text: str) -> dict[str, Any]:
        with _planning(thread.id):
            return await _design_turn_inner(thread, text)

    async def _design_turn_inner(thread: Any, text: str) -> dict[str, Any]:
        project_slug = thread.project or config.default_project
        project = await projects.get(project_slug)
        if project is None:
            raise HTTPException(404, "no such project")

        history = [{"role": m.role, "text": m.text} for m in thread.messages]
        try:
            model = await SettingsStore(db).model_for(project_slug, config.planner_model)

            async def attempt() -> Any:
                async with tasks.chat_limiter:
                    return await designer.draft(
                        message=text, name=project.name,
                        guide=projects.read_guide(project_slug),
                        history=history, config=config, model=model,
                    )

            reply = await _with_retries("design turn", attempt)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            message = AUTH_HINT if is_auth_error(detail) else f"Design failed: {detail}"
            await publish_message(
                thread.id,
                ThreadMessage(role="dex", kind="error", text=message, data={"detail": detail}),
            )
            raise HTTPException(503 if is_auth_error(detail) else 502, message) from exc

        if reply.changed:
            projects.write_guide(project_slug, reply.guide)
            bus.publish(Event(type="guide", data={"project": project_slug}))

        note = ThreadMessage(
            role="dex", kind="text", text=reply.summary,
            data={"guideChanged": reply.changed, "project": project_slug},
        )
        await publish_message(thread.id, note)
        return {"design": reply.to_json(), "message": note.to_json()}

    #: A request can imply far more work than fits one reply, so planning runs in
    #: batches: each is a plan of its own in the thread, and the planner says in
    #: `remaining` what it has not reached yet.
    PLAN_BATCHES = 10

    async def _plan_turn(thread: Any, text: str) -> dict[str, Any]:
        with _planning(thread.id):
            return await _plan_turn_inner(thread, text)

    async def _plan_turn_inner(thread: Any, text: str) -> dict[str, Any]:
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
                            project=project_slug, guide=guide,
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

    @app.get("/api/tasks/{task_id}/events", dependencies=guard)
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
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            yield b": connected\n\n"
            subscription = bus.subscribe(task_id=task, after_seq=after)
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
    ) -> Any:
        """Directory listing or text file content, confined to the assets root."""
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
    async def list_packages(project: str | None = Query(default=None)) -> dict[str, Any]:
        """Every package in a project, with the tags its manifest carries.

        Built here rather than in the browser because the tags live one file
        deep in each package: the Library would otherwise fetch a hundred and
        thirty manifests to draw its filter pills.
        """
        chosen = await resolve_project(project)
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
    async def animation_info(path: str = Query(...)) -> dict[str, Any]:
        """Duration, and the checkpoints this animation can be stepped through."""
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
    ) -> FileResponse:
        """The animation at a chosen speed, optionally just one checkpoint."""
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
    async def raw_asset(path: str = Query(...)) -> FileResponse:
        """Serves generated GIFs and other binaries to the viewer."""
        target = _resolve(config, path)
        if not target.is_file():
            raise HTTPException(404, "not a file")
        media_type, _ = mimetypes.guess_type(target.name)
        return FileResponse(target, media_type=media_type or "application/octet-stream")

    # A follow-up started by a worker (from a queued note) is announced the
    # same way one started from a button is.
    tasks.announce = _announce

    # ------------------------------------------------------------------- ui

    # In production the API and the built UI share an origin, so a phone only
    # needs the one URL the server prints. Mounted last so /api wins.
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
