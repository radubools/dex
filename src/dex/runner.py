"""Runs a single generation task through the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import contextlib
import logging

from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from . import pricing
from .artifacts import is_artifact
from .bus import EventBus
from .config import Config
from .store import SettingsStore, TaskMessageStore, TaskStore, ThreadStore
from .diffs import preview_change
from .models import Event, Task, TaskState
from . import local_sources, skills, surveyor, widgets
from .threads import ThreadMessage
from .permissions import PermissionPolicy
from .prompts import (
    DESIGN_SYSTEM, GENERATION_SYSTEM, SURVEY_SYSTEM, TODOS_SYSTEM, power_prompt, power_system,
    todos_prompt,
    design_prompt, generation_prompt, survey_prompt,
)
from .usage import is_usage_limit_error, limit_pause_note

#: Largest single message the agent SDK will read from the CLI (default 1 MB).
AGENT_MAX_BUFFER_BYTES = 32 * 1024 * 1024


log = logging.getLogger("dex.runner")

#: Why a run that raised nothing can still have failed. A package task that
#: ends its turn with an empty directory has not done its job, however calmly
#: it said so — and the usual way to get here is an agent that decided to wait
#: for something and ended its turn to do the waiting. Nothing re-invokes a
#: finished task, so that wait never ends and the run is lost in silence.
#: Failing it says so out loud and keeps the session, so picking it back up is
#: one press rather than a fresh start.
EMPTY_PACKAGE = (
    "ended its turn without writing anything into its package. If it stopped "
    "to wait for something, nothing would have woken it up again — resume the "
    "task to carry on in the same session."
)


def manim_available() -> bool:
    """Whether animations can be rendered.

    Checked by import, not by looking for a `manim` on PATH: dex runs from a
    virtualenv that is not activated, so PATH may hold a different manim (a
    pyenv shim, say) or none at all, while the one that matters is the one this
    interpreter can import.
    """
    return importlib.util.find_spec("manim") is not None


AUTH_HINT = (
    "The Claude Agent SDK has no credentials. Either export an API key:\n"
    "  export ANTHROPIC_API_KEY=sk-ant-...\n"
    "or use a Claude subscription — for a long-running server, mint a one-year\n"
    "token so it does not expire mid-task:\n"
    "  claude setup-token   →   export CLAUDE_CODE_OAUTH_TOKEN=...\n"
    "(see https://code.claude.com/docs/en/authentication)"
)

#: Checked in the CLI's own precedence order, so what dex reports is what the
#: agent will actually authenticate with when more than one is set.
AUTH_ENV_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

_auth_cache: tuple[float, str | None] = (0.0, None)
_AUTH_CACHE_TTL = 15.0


def bundled_cli() -> Path | None:
    """The Claude Code binary the SDK will spawn, if it ships one."""
    import claude_agent_sdk

    candidate = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if candidate.exists():
        return candidate
    found = shutil.which("claude")
    return Path(found) if found else None


def credential_source() -> str | None:
    """How the agent will authenticate, or None if it cannot.

    An env var is not the only way in: the SDK spawns the Claude Code CLI, so a
    subscription sign-in stored by `claude auth login` works too and shows up
    nowhere in the environment. Asking the CLI is the only reliable check.
    """
    global _auth_cache
    for var in AUTH_ENV_VARS:
        if os.environ.get(var):
            return var

    cached_at, cached = _auth_cache
    if time.time() - cached_at < _AUTH_CACHE_TTL:
        return cached

    source = None
    cli = bundled_cli()
    if cli is not None:
        try:
            # Spawning the CLI costs ~1s, hence the cache.
            result = subprocess.run(
                [str(cli), "auth", "status"], capture_output=True, text=True, timeout=20
            )
            status = json.loads(result.stdout or "{}")
            if status.get("loggedIn"):
                source = f"claude auth login ({status.get('authMethod', 'unknown')})"
        except (OSError, subprocess.SubprocessError, ValueError):
            source = None
    _auth_cache = (time.time(), source)
    return source


def has_credentials() -> bool:
    return credential_source() is not None


def is_auth_error(message: str) -> bool:
    lowered = message.lower()
    return "not logged in" in lowered or "authentication_failed" in lowered or "please run /login" in lowered


def chat_interjection(text: str) -> str:
    """A message the player typed while a chat task was working, as its next turn."""
    return (
        "The player just sent this message from the game while you were working "
        "(your previous step was interrupted to hand it to you). Take it into "
        "account, then carry on with whatever still makes sense:\n\n" + text.strip()
    )



#: Sent to a resumed session that answered with an empty turn.
#: Appended to the brief of a task whose end-to-end tests are off.
E2E_OFF = (
    "\n\n## End-to-end tests are off for this task\n\n"
    "Whoever asked for this task turned end-to-end tests off (quicker, untested), and the "
    "tasks you start or continue inherit that. Do not run end-to-end tests in the game "
    "(game-story's `story.e2e`, the engine's story-e2e tool) or write their reports, and "
    "ignore what `story.done` says about them. Every other check still applies: validate "
    "the package, read the outline, look at the previews. The game installs it untested "
    "(`--allow-untested`).\n"
)

RESUME_NUDGE = (
    "You were interrupted (the dex server restarted or paused this task) and the "
    "session has just been resumed. Carry on from where you stopped: look at what "
    "is already in your task directory, then continue the work your brief asks for."
)
#: Put in front of the brief when a resumed session could not run at all.
FRESH_AFTER_LOST_SESSION = (
    "Note: this task ran before and was interrupted; its earlier agent session "
    "could not be resumed. Work it already did is in your task directory: read "
    "it first and continue from there rather than starting over.\n\n"
)

class TaskRunner:
    """Owns one task's agent session and translates it into bus events."""

    def __init__(
        self,
        task: Task,
        config: Config,
        bus: EventBus,
        store: TaskStore,
        settings: SettingsStore,
    ) -> None:
        self.task = task
        self.config = config
        self.bus = bus
        self.store = store
        self.settings = settings
        #: Set by the queue after construction: re-decide the usage hold now,
        #: so the rest of the fleet stops being dispatched the moment the CLI
        #: rejects work. None on a runner built directly (a test, say).
        self.on_limit_reading: Any = None
        self.task_dir = task.output_dir(config.assets_dir, config.default_project)
        #: Set by wait_for_tasks: the ids this run ends blocked on (deps.py).
        self._park_on: list[str] | None = None
        #: The task graph (deps.Dependencies), set by the queue.
        self.graph = None
        #: The enabled skills as they were before this task ran, so a
        #: promotion can publish a new version without losing the old one.
        self._skill_snapshot: dict[str, Path] = {}
        self._skill_snapshot_dir: Path | None = None
        #: Set by the queue for a survey run, so its result reaches the planner.
        #: Declared here so a runner built directly — in a test, say — has the
        #: attribute rather than failing on the one line that reads it.
        self.surveyed: Any = None
        #: Streaming block index -> the id the UI accumulates deltas under.
        self._blocks: dict[int, str] = {}
        self._block_kinds: dict[int, str] = {}
        #: Whether this transport streams thinking at all. Set by the first
        #: thinking delta and never cleared: one delta proves the deltas are
        #: coming, so no completed block ever needs emitting whole.
        self._thinking_streams = False
        #: Running list-price estimate, so a task shows spend while it works
        #: instead of nothing until it finishes.
        self._estimated_cost = 0.0
        #: Each message's usage so far. The SDK hands a message over once per
        #: content block, each repeating its usage, so counting them as they
        #: come would bill most messages about three times.
        self._message_usage: dict[str, dict[str, float]] = {}
        #: This run's cost per rollup row, to correct to the agent's own total.
        self._run_costs: dict[tuple[datetime, str], float] = {}
        #: The skill spend goes to now. '' until the task touches one.
        self._skill = "power" if task.is_power else ""
        self._skill_named = task.is_power
        self._attribution: skills.Attribution | None = None
        #: Rollup writes keep their order: naming a skill must follow what it moves.
        self._usage_lock = asyncio.Lock()
        self._added_todos = 0
        #: State writes still in flight (`set_state` persists in the background).
        self._writes: set[asyncio.Task[None]] = set()

    async def settled(self) -> None:
        """Wait until every state change so far is in the database.

        The queue delivers notes typed during a run once it ends, and reads the
        task's state from the database to see that it has; without this wait
        it read the row before the final write landed, saw it still running,
        and left the notes undelivered for good.
        """
        while self._writes:
            await asyncio.gather(*list(self._writes), return_exceptions=True)

    # ---------------------------------------------------------------- events

    def emit(self, type_: str, **data: Any) -> None:
        self.bus.publish(
            # Tagged with the project so a scoped subscriber's stream can be
            # filtered without a database round trip per event.
            Event(type=type_, data=data, task_id=self.task.id, project=self.task.project)  # type: ignore[arg-type]
        )

    def set_state(self, state: TaskState, **extra: Any) -> None:
        self.task.state = state
        self.emit("task_state", state=state.value, activity=self.task.activity, **extra)
        # Persisted in the background: a state change must not block the agent
        # loop, but it must survive the process.
        write = asyncio.create_task(self._persist_state(state, extra))
        self._writes.add(write)
        write.add_done_callback(self._writes.discard)

    async def _correct_state(self) -> None:
        """Tell the UI what the database actually holds.

        `set_state` emits before it writes, because a state change must not
        block the agent loop. When the write is then refused -- archiving is the
        case that exists -- the screen is left showing something that was never
        stored, and only a reload fixes it. So the row is read back and the real
        state re-emitted.
        """
        try:
            actual = await self.store.get(self.task.id)
            if actual is None or actual.state is self.task.state:
                return
            self.task.state = actual.state
            self.task.activity = None
            self.emit("task_state", state=actual.state.value, activity=None)
        except Exception:
            log.exception("could not re-read the state of %s", self.task.id)

    async def _persist_state(self, state: TaskState, extra: dict[str, Any]) -> None:
        fields: dict[str, Any] = {"activity": self.task.activity}
        if state is TaskState.RUNNING and self.task.started_at:
            fields["started_at"] = self.task.started_at
        if state.terminal:
            fields["finished_at"] = self.task.finished_at or time.time()
            fields["error"] = self.task.error
            fields["cost_usd"] = self.task.cost_usd
            fields["turns"] = self.task.turns
        try:
            applied = await self.store.set_state(self.task.id, state, **fields)
            if not applied:
                # The row would not take this state -- it has been archived out
                # from under the run. The UI was already told otherwise, so put
                # it right rather than leaving a stale screen until a reload.
                await self._correct_state()
        except Exception:  # a reporting failure must not kill the run
            log.exception("could not persist state %s for %s", state.value, self.task.id)

    # ------------------------------------------------------- operator prompts

    async def _park(self, key: str, state_note: str, *, approval: bool = False) -> Any:
        """Suspend the agent until the operator answers `key`."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self.task.pending[key] = future
        if approval:
            self.task.pending_approvals.add(key)
        previous_activity = self.task.activity
        self.task.activity = state_note
        self.set_state(TaskState.AWAITING_INPUT)
        try:
            return await future
        finally:
            self.task.pending.pop(key, None)
            self.task.pending_approvals.discard(key)
            self.task.activity = previous_activity
            if self.task.state is TaskState.AWAITING_INPUT:
                self.set_state(TaskState.RUNNING)

    async def _auto_answer(self, kind: str, options: list[str]) -> str | None:
        """The answer dex gives on the operator's behalf, or None to ask them.

        Only the shared-utility question, and only while the setting is on. The
        first option is the affirmative one — the project guides fix that order,
        and an empty list means the agent offered nothing to pick.
        """
        if kind.strip().lower() != "utility" or not options:
            return None
        # Read now rather than at task start, so turning sharing off stops the
        # next question in a run already going.
        if not await self.settings.utility_proposals():
            return None
        return options[0]

    async def _reused_answer(self, question: str, options: list[str]) -> str | None:
        """The answer this same question already got, if one applies now.

        Per project and within the last half day; the store holds the details.
        """
        try:
            return await self.settings.prior_answer(
                question, options, project=self.task.project
            )
        except Exception:
            # A reading from the reuse cache must never cost the run: the
            # question stays askable on any failure.
            log.exception("could not read the recorded answers")
            return None

    async def _remember_answer(
        self, question: str, options: list[str], answer: str
    ) -> None:
        """Keep an operator's answer for the next task that asks the same."""
        try:
            await self.settings.remember_answer(
                question, options, answer, project=self.task.project
            )
        except Exception:
            log.exception("could not record the answer for reuse")

    def _ask_user_server(self) -> Any:
        import uuid

        @tool(
            "ask_user",
            "Ask the operator one clarifying question about the problem statement. "
            "Use it only when the answer changes the algorithm, and offer concrete options. "
            "Pass kind='utility' for the end-of-task question about promoting a helper "
            "into the project's utils/, which dex may answer for the operator.",
            {"question": str, "options": list, "kind": str},
        )
        async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
            question_id = uuid.uuid4().hex[:12]
            question = str(args.get("question", ""))
            options = [str(o) for o in (args.get("options") or [])]
            self.emit(
                "question",
                id=question_id,
                question=question,
                options=options,
            )
            # A utility question has one answer while sharing is on, and the
            # operator said so by leaving the setting on. Still asked, so the
            # decision is recorded where they can see it, but answered here
            # rather than parking the task on a doorbell nobody needs to hear.
            auto = await self._auto_answer(str(args.get("kind", "")), options)
            # The same question, answered in this project in the last half
            # day: answered from what was said then, so a batch whose briefs
            # all hit the same snag does not park thirty-one times for one
            # decision the operator has already made.
            if auto is None:
                reused = await self._reused_answer(question, options)
                if reused is not None:
                    self.emit(
                        "question_answered",
                        id=question_id, answer=reused, auto=True, reuse=True,
                    )
                    return {"content": [{"type": "text", "text": reused}]}
            if auto is not None:
                self.emit("question_answered", id=question_id, answer=auto, auto=True)
                return {"content": [{"type": "text", "text": auto}]}
            answer = await self._park(question_id, "waiting on a clarifying question")
            self.emit("question_answered", id=question_id, answer=answer)
            asyncio.create_task(
                self._remember_answer(question, options, str(answer))
            )
            return {"content": [{"type": "text", "text": str(answer)}]}

        @tool(
            "utility_proposals_enabled",
            "Check whether this dex has shared-utility proposals turned on. Call it at "
            "the end of a task, before asking the operator about promoting a helper "
            "into the project's utils/. Returns 'enabled' or 'disabled'.",
            {},
        )
        async def utility_proposals_enabled(_args: dict[str, Any]) -> dict[str, Any]:
            # Read now, not at task start: turning the loop off silences runs
            # already in flight, which is the whole point of a global switch.
            on = await self.settings.utility_proposals()
            return {"content": [{"type": "text", "text": "enabled" if on else "disabled"}]}

        @tool(
            "link_local_source",
            "Give this project read access to a directory elsewhere on this "
            "machine, by linking it into the project's data directory. Use it "
            "only when the operator has named a path and asked for access to "
            "it. Pass the absolute path. Returns the link's name and what it "
            "found there, or why the path was refused. The link is read-only "
            "and every task in this project can read it from then on.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the directory, as the operator gave it.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional name for the link. Defaults to the directory's own.",
                    },
                },
                "required": ["path"],
            },
        )
        async def link_local_source(args: dict[str, Any]) -> dict[str, Any]:
            datasets = self.config.project_datasets(self.task.project)
            try:
                made = local_sources.link(
                    datasets, str(args.get("path", "")), self.config.workspace,
                    str(args.get("name", "")),
                )
            except local_sources.Blocked as exc:
                # Returned rather than raised: the design turn has to tell the
                # operator why their path was refused, and an exception would
                # end the turn with nothing said.
                return {"content": [{"type": "text", "text": f"Refused. {exc}"}]}
            self.emit("text", text=f"Linked a local source — {made.describe()}")
            return {"content": [{"type": "text", "text": (
                f"Linked. {made.describe()}\n"
                f"Tasks in this project read it at `{made.path}`. "
                "It is read-only."
            )}]}

        return create_sdk_mcp_server(
            name="dex", version="0.1.0",
            tools=[ask_user, utility_proposals_enabled]
            # Work in other threads: look it up, start or continue it, wait for
            # it (design/18_task_dependencies.md). Not for surveys.
            + (self._task_tools() if not self.task.is_survey and self.graph is not None else [])
            # Only a design turn may widen what a project can read. A
            # generation task uses what is already linked; granting access is
            # the operator's decision, and the designer is where they make it.
            + ([link_local_source] if self.task.is_design else []),
        )

    async def _woken_wrote(self) -> bool:
        """Whether the run before this task blocked wrote into its package.

        A woken run may only review what its dependencies made and write
        nothing new; the work it is the second half of was written before it
        blocked, and that counts.
        """
        try:
            wrote = await self.store.db.pool.fetchval(
                """SELECT (data->>'wrote')::boolean FROM events
                   WHERE task_id = $1 AND type = 'task_state' AND data->>'state' = 'blocked'
                   ORDER BY seq DESC LIMIT 1""", self.task.id)
        except Exception:
            log.exception("could not read how %s blocked", self.task.id)
            return False
        return bool(wrote)

    async def _answered_notes(self) -> bool:
        """Whether this run answered the operator's notes on a finished package.

        A note typed into a finished task starts the next attempt
        (`deliver_messages`), and a note is as often a question about the
        package ("how many flat sides does this character have?") as a change
        to it. Answered without touching a file, that run did what it was
        asked; failing it as an empty package read as if the question broke
        something. Two things have to hold: the attempt came from notes (its
        parent has delivered ones; a plain Resume of a failed run has none, and
        stays checked), and the package already holds work, so a follow-up
        cannot stand in for a package nobody ever made.
        """
        if not self.task.parent_id or not self._artifacts():
            return False
        try:
            return bool(await self.store.db.pool.fetchval(
                """SELECT EXISTS (SELECT 1 FROM task_messages
                    WHERE task_id = $1 AND delivered_at IS NOT NULL)""", self.task.parent_id))
        except Exception:
            log.exception("could not read the notes behind %s", self.task.id)
            return False

    async def _unfinished_dependencies(self) -> list[str]:
        """The tasks this one depends on that are not done, plus any it asked to wait on."""
        if self.graph is None:
            return list(self._park_on or [])
        try:
            ids = list(dict.fromkeys([*(self._park_on or []), *await self.graph.store.of(self.task.id)]))
            return [s["id"] for s, i in zip(await self.graph.statuses(ids), ids) if not s.get("done", True)
                    and s.get("id")] if ids else []
        except Exception:
            log.exception("could not read the dependencies of %s", self.task.id)
            return list(self._park_on or [])

    def _task_tools(self) -> list[Any]:
        """Tools for work that belongs to other tasks and threads of this project."""
        import json as _json

        from .deps import Refused

        graph = self.graph
        project = self.task.project or self.config.default_project
        # A conversation (a game chat, a design turn) blocks too, but its last
        # message is what the person reads: it says what it is waiting for.
        conversation = self.task.is_chat or self.task.is_design

        def reply(payload: Any) -> dict[str, Any]:
            text = payload if isinstance(payload, str) else _json.dumps(payload, indent=2, default=str)
            return {"content": [{"type": "text", "text": text}]}

        def ids_of(raw: Any) -> list[str]:
            items = raw if isinstance(raw, list) else [raw] if raw else []
            return [str(i).strip() for i in items if str(i).strip()]

        async def resolve_all(refs: list[str]) -> list[str]:
            return [(await graph.resolve_task(project, r)).id for r in refs]

        @tool(
            "project_threads",
            "List this project's threads (id, title, how many tasks). Asset work has dedicated "
            "threads (one per kind of asset): start a task in the one it belongs to.",
            {},
        )
        async def project_threads(_args: dict[str, Any]) -> dict[str, Any]:
            return reply(await graph.project_threads(project))

        @tool(
            "find_tasks",
            "Look up existing tasks in this project before starting one: by words in their title, "
            "slug, package name or brief, optionally in one thread. Returns one entry per package "
            "(its latest attempt): state, whether it is done, its package directory and summary. "
            "If the work exists and only needs changes, use continue_task rather than start_task.",
            {"type": "object", "properties": {
                "query": {"type": "string", "description": "Words to match, e.g. 'loss bowl' or 'player builder'."},
                "thread": {"type": "string", "description": "Optional thread title or id to search in."},
                "limit": {"type": "integer", "description": "At most this many (default 10)."},
            }},
        )
        async def find_tasks(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return reply(await graph.find(project, str(args.get("query") or ""),
                                              args.get("thread") or None, int(args.get("limit") or 10)))
            except Refused as exc:
                return reply(f"Refused. {exc}")

        @tool(
            "start_task",
            "Start a new task in a thread of this project, for work this task needs but should not do "
            "itself (an asset in its own asset thread, say). Use it sparingly: only when what exists "
            "cannot do, after find_tasks found nothing to continue. The brief must stand on its own: "
            "what to make, which skill, the package name, the project's conventions (tags), and what "
            "done means. Every task started spends the operator's usage. This task then depends on it: "
            "when this turn ends it is blocked until that task is done, then resumed to review the "
            "result and finish (wait_for_tasks parks it sooner).",
            {"type": "object", "properties": {
                "thread": {"type": "string", "description": "The thread's title or id (see project_threads)."},
                "title": {"type": "string", "description": "A short title for the task."},
                "brief": {"type": "string", "description": "The full brief, as the task will read it."},
                "slug": {"type": "string", "description": "The package directory name, prefixed by its type "
                         "(dataviz-, background-, story-, …): dex checks the thread is the one that type is made in."},
                "create_thread": {"type": "boolean", "description": "Make the thread if none matches (default false)."},
            }, "required": ["thread", "title", "brief", "slug"]},
        )
        async def start_task(args: dict[str, Any]) -> dict[str, Any]:
            if not str(args.get("slug") or "").strip():
                return reply("Refused. Give the package a slug with its type prefix (e.g. `dataviz-ravine-bowl`).")
            try:
                child = await graph.spawn(
                    self.task, thread=str(args.get("thread") or ""), title=str(args.get("title") or ""),
                    brief=str(args.get("brief") or ""), slug=args.get("slug") or None,
                    create_thread=bool(args.get("create_thread")),
                )
            except Refused as exc:
                return reply(f"Refused. {exc}")
            self.emit("text", text=f"Started the task “{child.title}” ({child.slug}); this task now depends on it.")
            return reply({"started": graph.describe(await graph.store.status(child.id))})

        @tool(
            "add_todos",
            "Add the todos this request splits into, in the order they should run. Each one is a "
            "power task run later, alone, by an agent that sees only its brief.",
            {"type": "object", "properties": {"todos": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "A short title, as a to-do."},
                    "brief": {"type": "string", "description": "The full brief, as the task will read it."},
                },
                "required": ["title", "brief"],
            }}}, "required": ["todos"]},
        )
        async def add_todos(args: dict[str, Any]) -> dict[str, Any]:
            try:
                made = await graph.m.todos.add(self.task, list(args.get("todos") or []))
            except Refused as exc:
                return reply(f"Refused. {exc}")
            self._added_todos += len(made)
            return reply({"added": [t.title for t in made]})

        @tool(
            "start_power_task",
            "Start a power task: a code change this task cannot make itself, to dex (its server, UI "
            "or skills) or to any directory outside this project (a game's or engine's code, tools, "
            "tests). It runs in the project's Power thread and may change code anywhere, and it makes "
            "no content: no packages, stories, models or sounds. Use it only when the work needs "
            "such a change, and say exactly what must change, where if you know, why, and how to "
            "tell it works. Every task spends the operator's usage. This task then depends on it, "
            "as with start_task.",
            {"type": "object", "properties": {
                "title": {"type": "string", "description": "A short title for the change."},
                "brief": {"type": "string", "description": "The full brief, as the task will read it."},
            }, "required": ["title", "brief"]},
        )
        async def start_power_task(args: dict[str, Any]) -> dict[str, Any]:
            try:
                child = await graph.spawn_power(self.task, title=str(args.get("title") or ""),
                                                brief=str(args.get("brief") or ""))
            except Refused as exc:
                return reply(f"Refused. {exc}")
            self.emit("text", text=f"Started the power task “{child.title}” ({child.slug}); this task now depends on it.")
            return reply({"started": graph.describe(await graph.store.status(child.id))})

        @tool(
            "continue_task",
            "Ask an existing task of this project for a change: a follow-up message to its latest "
            "attempt, which reruns it in the same package with the message added (once it finishes, "
            "if it is running). Name it by id, slug or package. This task then depends on it.",
            {"type": "object", "properties": {
                "task": {"type": "string", "description": "The task's id, slug or package name."},
                "message": {"type": "string", "description": "What to change, and why."},
            }, "required": ["task", "message"]},
        )
        async def continue_task(args: dict[str, Any]) -> dict[str, Any]:
            try:
                out = await graph.follow_up(self.task, str(args.get("task") or ""), str(args.get("message") or ""))
            except Refused as exc:
                return reply(f"Refused. {exc}")
            t = out["task"]
            self.emit("text", text=f"Asked “{t.title}” ({t.slug}) for a change; this task now depends on it.")
            return reply({"continued": graph.describe(await graph.store.status(t.id)),
                          "note": "queued until its current run ends" if out["running"] else "a new attempt is starting"})

        @tool(
            "task_status",
            "Where tasks stand now (by id, slug or package; default: every task this one depends on). "
            "Returns state, done, package directory, summary or error. Does not wait.",
            {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "string"}}}},
        )
        async def task_status(args: dict[str, Any]) -> dict[str, Any]:
            try:
                ids = await resolve_all(ids_of(args.get("tasks"))) or await graph.store.of(self.task.id)
            except Refused as exc:
                return reply(f"Refused. {exc}")
            return reply(await graph.statuses(ids))

        @tool(
            "wait_for_tasks",
            "Wait until tasks are done (by id, slug or package; default: every task this one depends "
            "on). If they finish within `wait_s` seconds (at most 300; default 0) you get their "
            "statuses back. Otherwise this task is parked as blocked: END YOUR TURN right after this "
            "call, with no further work. A blocked task holds no slot and no time limit; dex resumes "
            "this same session, ahead of other queued work, when they are all done, and tells you how "
            "each went. A task that started or continued work is blocked at the end of its turn anyway, "
            "until that work is done: it then resumes to review the results and finish.",
            {"type": "object", "properties": {
                "tasks": {"type": "array", "items": {"type": "string"}},
                "wait_s": {"type": "number", "description": "Seconds to wait in this turn first (0-300)."},
            }},
        )
        async def wait_for_tasks(args: dict[str, Any]) -> dict[str, Any]:
            try:
                ids = await resolve_all(ids_of(args.get("tasks"))) or await graph.store.of(self.task.id)
            except Refused as exc:
                return reply(f"Refused. {exc}")
            if not ids:
                return reply("This task depends on no tasks: nothing to wait for.")
            # Waiting on a task is depending on it: never on one that waits for this one.
            above = await graph.store.packages([self.task.id, *await graph.store.waiters(self.task.id)])
            for st in await graph.statuses(ids):
                if st.get("package") in above:
                    return reply(f"Refused. {st.get('title')} waits for this task (directly or through "
                                 "others): waiting for it would never end.")
            for i in ids:
                await graph.store.add(self.task.id, i, "waited")
            seconds = max(0.0, min(300.0, float(args.get("wait_s") or 0)))
            if seconds:
                self.task.activity = f"waiting up to {seconds:.0f}s on {len(ids)} task(s)"
            statuses = await graph.wait(ids, seconds)
            if all(s.get("done", True) for s in statuses):
                return reply({"done": True, "tasks": statuses})
            self._park_on = ids
            self.emit("text", text=f"Waiting on {len(ids)} task(s); this task will be blocked until they are done.")
            return reply({"done": False, "tasks": statuses, "parked": True, "note": (
                ("Parked. Tell the person in one or two sentences what you are waiting for, then end "
                 "your turn. " if conversation else "Parked. End your turn now, without further work. ")
                + "dex resumes this session when they are done, with their results.")})

        if self.task.is_todos:
            return [add_todos]
        if self.task.is_power and not self.task.todo:
            # It looks things up; it starts nothing, or it could have content made.
            return [project_threads, find_tasks, task_status]
        return [project_threads, find_tasks, start_task, start_power_task, continue_task, task_status, wait_for_tasks]

    async def _escalate(
        self, approval_id: str, tool_name: str, input_data: dict[str, Any], title: str | None
    ) -> str:
        # Read the toggle now rather than at task start, so turning it on takes
        # effect on runs already in flight.
        if await self.settings.auto_approve():
            self.emit(
                "approval",
                id=approval_id,
                tool=tool_name,
                title=title or tool_name,
                input=_trim_input(input_data),
                auto=True,
            )
            self.emit("approval_resolved", id=approval_id, decision="allow", auto=True)
            return "allow"

        self.emit(
            "approval",
            id=approval_id,
            tool=tool_name,
            title=title or tool_name,
            input=_trim_input(input_data),
        )
        decision = await self._park(
            approval_id, f"waiting on approval for {tool_name}", approval=True
        )
        self.emit("approval_resolved", id=approval_id, decision=decision)
        return decision

    def _with_diffs(self, decide: Any) -> Any:
        """Emit a `diff` event for any tool that is about to change a file.

        The permission callback runs before the tool does, which is the only
        moment both the old contents and the intended new contents are
        available — afterwards the original is gone.
        """

        async def wrapped(tool_name: str, input_data: dict[str, Any], context: Any) -> Any:
            try:
                change = preview_change(tool_name, input_data, self.config.workspace)
            except Exception:
                # A diff is a courtesy; failing to build one must not block the
                # edit it was describing.
                log.exception("could not build a diff for %s", tool_name)
                change = None
            if change is not None:
                self.emit(
                    "diff",
                    id=context.tool_use_id or change.path,
                    tool=tool_name,
                    **change.to_json(),
                )
            return await decide(tool_name, input_data, context)

        return wrapped

    # ------------------------------------------------------------------- run

    async def run(self) -> None:
        self.task.started_at = time.time()
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.task.activity = "starting"
        self.set_state(TaskState.RUNNING)

        # Before the policy and the prompt, which both name the copies.
        self._prepare_skill_work()
        policy = PermissionPolicy(
            workspace=self.config.workspace,
            task_dir=self.task_dir,
            # Its neighbours are readable and not writable. A project-wide task
            # has the project as its own directory, so this names the same path
            # twice and nothing beside it counts as somebody else's.
            project_dir=self.task.project_dir(
                self.config.assets_dir, self.config.default_project
            ),
            # A design turn also authors widgets, which are shared across
            # projects and therefore live at the top level rather than inside
            # the project it is editing.
            extra_writable=(
                # A project's source data. Every task may read and write here:
                # a corpus to index, a PDF to translate, a scratch database
                # built beside it — none of that belongs in the package the
                # task produces, and a task that had to ask before touching it
                # would stop on its first real step.
                (self.config.project_datasets(self.task.project),)
                # The `utils/` of every skill this project has enabled. A
                # promoted helper belongs in the skill: the project's own
                # `utils/` is materialised from these, so a module written
                # there is gone at the next enable and the skill it should
                # have joined never learns it exists.
                # A design turn authors skills: it forks one to a draft,
                # edits the draft, and dex publishes it. That means creating
                # directories under `skills/`, so the root is writable for
                # this scope only — a generation task may write into the
                # `utils/` of a skill it was given, and nothing more.
                + ((skills.skills_dir(self.config.workspace),)
                   if self.task.is_design else ())
                # A generation task promotes into its own working copy of each
                # skill (see `_prepare_skill_work`), never the shared version.
                + (skills.enabled_utils_dirs(
                    self.config.workspace,
                    self.task.project_dir(
                        self.config.assets_dir, self.config.default_project
                    ),
                ) if self.task.is_design or self._skill_work is None
                   else tuple(work / "utils" for _, work in self._skill_work.values()))
                # A design turn also authors widgets, which are shared across
                # projects and therefore live at the top level rather than
                # inside the project it is editing.
                + ((widgets.widgets_dir(self.config.workspace),) if self.task.is_design else ())
                # A chat from inside a game works on that game: its assets
                # folder and its code (dex's game-chat settings name the root).
                + self._chat_game_roots()
            ),
            # Linked directories are readable — that is why they were linked
            # — and never writable. The link lives inside the data directory,
            # which *is* writable, so the policy needs the targets to tell the
            # two apart.
            read_only=local_sources.targets(
                self.config.project_datasets(self.task.project)
            ),
            escalate=self._escalate,
            # A chat asks the asset's own task for every asset change, and the
            # game's pull installs it (design/18): it builds no packages itself.
            delegate_assets=self.task.is_chat,
            asset_dirs=tuple(root / "public" / "assets" for root in self._chat_game_roots()),
            power=self.task.is_power,
            assets_dir=self.config.assets_dir,
        )
        decide = self._with_diffs(policy.build())
        effort = await self.settings.effort() or self.config.effort
        resume_session = self.task.resumed_from
        if resume_session:
            self.emit("text", text=f"Resuming the previous agent session ({resume_session[:8]}…).")

        options = ClaudeAgentOptions(
            # The task's own directory, so a relative path lands in the package
            # rather than in the repository. The agent used to start at the repo
            # root — the render helper was thought to need it, but it is invoked
            # as `-m dex.tools.render_manim` and resolves from the venv wherever
            # it runs. Meanwhile anything the agent ran directly wrote where it
            # stood: a stray `manim` left a 15 MB `media/` beside dex's own
            # source. Created just above, so it exists to start in.
            cwd=str(self.task_dir),
            model=self.task.model or self.config.model,
            # A split only reads its brief and calls add_todos.
            tools=[] if self.task.is_todos else None,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": (
                    SURVEY_SYSTEM if self.task.is_survey
                    else DESIGN_SYSTEM if self.task.is_design
                    else power_system(self.config.workspace) if self.task.is_power
                    else TODOS_SYSTEM if self.task.is_todos
                    else GENERATION_SYSTEM
                ),
            },
            permission_mode="default",
            # The SDK stops reading the CLI's output at 1 MB per message, and
            # one Read of a ~500 KB PNG (base64 inside a JSON message) crossed
            # it and ended a landmark task. Agents review rendered previews all
            # the time, so allow messages far larger than any image they open.
            max_buffer_size=AGENT_MAX_BUFFER_BYTES,
            # Stream token deltas so the UI can render text as it arrives
            # instead of a block at a time.
            include_partial_messages=True,
            # On a high-effort run thinking is most of the wall clock — one
            # goddess-pose task spent 122s in a single block before its first
            # visible token, and the panel showed nothing for it. The CLI omits
            # thinking from the stream unless a display is asked for, which is
            # why not one `thinking` event exists in 157k rows. Ask for it, and
            # the panel's collapsed thinking step fills in as it arrives.
            thinking={"type": "adaptive", "display": "summarized"},
            can_use_tool=decide,
            mcp_servers=(
                {
                    "survey": surveyor.survey_server(
                        self.config.project_datasets(self.task.project),
                        # Only what this message brought. The project's data
                        # directory keeps every upload ever made to it, and a
                        # survey shown the lot spends its first turns working
                        # out which of them the operator actually meant.
                        self.task.survey_payload()["attachments"],
                    ),
                    # A survey asks too. Pre-planning is where a question
                    # belongs: it is the one step that has read the guide and
                    # looked at the material, and asking here parks a task with
                    # an activity pane rather than leaving a sentence in the
                    # chat that the next planning call will not remember.
                    "dex": self._ask_user_server(),
                }
                if self.task.is_survey
                else {"dex": self._ask_user_server()}
            ),
            # A survey reads structure and writes nothing, so it is given the
            # four survey tools and denied every built-in one. This is the
            # guarantee that a thousand-page document never enters the context:
            # not a line in the prompt asking nicely, but the absence of any
            # tool that could do it. `allowed_tools` is left unset for every
            # other scope, where the permission callback is what decides.
            **(
                {"allowed_tools": surveyor.TOOL_NAMES + ["mcp__dex__ask_user"]}
                if self.task.is_survey
                else {}
            ),
            max_turns=(
                surveyor.MAX_TURNS if self.task.is_survey else self.config.max_turns
            ),
            # Read at task start, so a change applies to new work while runs
            # already going keep the effort they were planned with.
            effort=effort,  # type: ignore[arg-type]
            setting_sources=[],  # ignore local .claude config; the brief is the spec
            # Continues the earlier run's conversation when there is one, so the
            # agent keeps what it already worked out instead of starting over.
            resume=resume_session,
            stderr=lambda line: self.emit("error", message=line[:500], fatal=False),
            # Read by the skills' test helpers (game-story's `story.e2e`).
            **({"env": {"DEX_SKIP_E2E": "1"}} if not self.task.e2e else {}),
        )

        self._refresh_utils_index()
        if self.task.is_survey:
            payload = self.task.survey_payload()
            prompt = survey_prompt(
                message=payload["message"],
                attachments=payload["attachments"],
                urls=payload["urls"],
                project=self.task.project or self.config.default_project,
                guide=payload["guide"],
            )
        elif self.task.is_design:
            # A design turn carries its conversation in `problem`, packed by the
            # API, because a task has one prompt field and the chat has a
            # history the model needs.
            payload = self.task.design_payload()
            prompt = design_prompt(
                message=payload["message"],
                project=self.task.project or self.config.default_project,
                project_dir=self.task_dir,
                python=Path(sys.executable),
                workspace=self.config.workspace,
                guide=payload["guide"],
                history=payload["history"],
            )
        elif self.task.is_power:
            prompt = power_prompt(self.task.problem, self.task_dir, self.config.workspace)
        elif self.task.is_todos:
            prompt = todos_prompt(self.task.problem)
        else:
            prompt = generation_prompt(
                problem=self.task.problem,
                task_dir=self.task_dir,
                python=Path(sys.executable),
                manim_available=manim_available(),
                project_wide=self.task.project_wide,
                workspace=self.config.workspace,
                datasets_dir=self.config.project_datasets(self.task.project),
                skills=self._enabled_skills(),
                linked=self._linked_sources(),
            )

        # Tests switched off by whoever started it (a game chat's switch),
        # inherited from the task that asked for this one (design/18).
        if not self.task.e2e and not self.task.is_survey:
            prompt += E2E_OFF
            self.emit("text", text="End-to-end tests are off for this task.")

        # Woken after waiting on other tasks (deps.py): the session already has
        # the brief, so it is told only how they went. A fresh session (the old
        # one lost) gets the brief and the news together.
        brief_prompt = prompt
        woken = bool(self.task.wake_note)
        if self.task.wake_note:
            prompt = self.task.wake_note if resume_session else f"{prompt}\n\n{self.task.wake_note}"
            self.emit("text", text="Resumed: the tasks it waited on are done.")
            note, self.task.wake_note = self.task.wake_note, None
            try:
                await self.store.db.pool.execute("UPDATE tasks SET wake_note = NULL WHERE id = $1", self.task.id)
            except Exception:
                log.exception("could not clear the wake note of %s", self.task.id)
            brief_prompt = f"{brief_prompt}\n\n{note}"

        # Taken before the agent runs, so what it wrote can be told apart from
        # what it inherited: a resumed attempt continues its parent's package,
        # and siblings sharing one open onto a directory that already has files
        # in it. Against emptiness alone, both would pass without lifting a
        # finger.
        before = self._artifacts() if self.task.builds_package else {}

        try:
            async with asyncio.timeout(self.config.task_timeout_s):
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(prompt)
                    if self.task.is_chat:
                        await self._converse(client)
                    else:
                        async for message in client.receive_response():
                            self._observe(message)
                        # A session resumed after it was cut off mid-turn (a
                        # server restart, a pause for capacity) can answer with
                        # an empty turn: nothing ran. Nudge it once to carry on.
                        if resume_session and self.task.turns == 0:
                            self.emit("text", text="The resumed session did not run; asking it to carry on.")
                            await client.query(RESUME_NUDGE)
                            async for message in client.receive_response():
                                self._observe(message)
                # Still nothing: that session cannot go on. Start a fresh one on
                # the same package, told that earlier work is already there.
                if (resume_session and self.task.turns == 0 and not self.task.is_chat
                        and self.task.state not in (TaskState.FAILED, TaskState.PAUSED)):
                    self.emit("text", text="The resumed session still did not run; starting a fresh session on the same package.")
                    import dataclasses
                    async with ClaudeSDKClient(options=dataclasses.replace(options, resume=None)) as client:
                        await client.query(FRESH_AFTER_LOST_SESSION + brief_prompt)
                        async for message in client.receive_response():
                            self._observe(message)
        except asyncio.CancelledError:
            self.task.finished_at = time.time()
            self.task.activity = None
            # Told apart by who cancelled it: dex making room is a pause it will
            # undo, a person pressing Stop is a cancellation it will not.
            if self.task.archived:
                self.set_state(TaskState.ARCHIVED)
            else:
                self.set_state(
                    TaskState.PAUSED if self.task.preempted else TaskState.CANCELLED
                )
            raise
        except TimeoutError:
            self._fail(f"timed out after {self.config.task_timeout_s:.0f}s")
        except Exception as exc:  # surfaced to the operator, never swallowed
            text = f"{type(exc).__name__}: {exc}"
            # A refusal from the CLI's raw path arrives raised rather than
            # yielded; same spent limit, same pause.
            if is_usage_limit_error(text):
                self._pause_for_limit(text)
            else:
                self._fail(text)
        else:
            # Failed or paused mid-run (a limit the CLI refused to spend
            # against): the run said so already and its state is what shows.
            # Neither ends the task, so neither gets a finished time.
            if self.task.state not in (TaskState.FAILED, TaskState.PAUSED):
                self.task.finished_at = time.time()
                self.task.activity = None
            if self.task.state in (TaskState.FAILED, TaskState.PAUSED):
                pass
            # Work it started or continued is not done yet (or it asked to wait):
            # blocked, holding nothing, until dex wakes it to review and finish
            # (deps.py). Not judged yet -- its own work is not done either. The
            # same for a chat: it is not done before what it asked for is.
            elif waiting := await self._unfinished_dependencies():
                self.task.state = TaskState.BLOCKED
                self.task.activity = f"waiting on {len(waiting)} task{'s' if len(waiting) != 1 else ''}"
                await self.graph.park(self.task, waiting)
                # Whether this half of the work wrote anything: the run that
                # resumes it is judged on both halves (see `_woken_wrote`).
                self.emit("task_state", state=TaskState.BLOCKED.value, activity=self.task.activity,
                          waitingOn=list(waiting),
                          wrote=bool(self.task.builds_package and self._wrote_anything(before)))
            # A clean exit is not the same thing as a result. Checked here and
            # not left to the operator's eye, because an empty package looks
            # exactly like a full one in a list of green rows.
            elif self.task.is_todos and not self._added_todos:
                self._fail("The request was not split: no todos were added.")
            elif (self.task.builds_package and not self.task.is_chat and not self.task.is_power
                  and not self.task.is_todos and not self._wrote_anything(before)
                  and not (woken and await self._woken_wrote())
                  and not (self.task.resumed_from and self._wrote_since_created())
                  and not await self._answered_notes()):
                # A chat turn that only answers wrote nothing, and is still done.
                self._fail(EMPTY_PACKAGE)
            else:
                self.set_state(TaskState.SUCCEEDED)
            # After the verdict, so a run that failed its own checks does not
            # publish a helper it wrote on the way. Only a task that finished
            # clean gets its promotion materialised for everybody.
            if self.task.state is TaskState.SUCCEEDED:
                if self.task.is_design:
                    self._adopt_drafts()
                else:
                    self._resync_skills()
        finally:
            for future in list(self.task.pending.values()):
                if not future.done():
                    future.cancel()
            self.task.pending.clear()
            # A run that did not finish clean promotes nothing: drop its copies.
            leftover = getattr(self, "_skill_work_dir", None)
            if leftover is not None and self.task.state not in (TaskState.SUCCEEDED, TaskState.BLOCKED):
                import shutil

                shutil.rmtree(leftover, ignore_errors=True)
                self._skill_work_dir = None

    def _accrue(self, usage: Any, message_id: str | None = None) -> None:
        """Add one message's usage to the rollup and the running estimate.

        Guarded separately from `_observe` so a pricing problem costs only the
        estimate, not the tool and text events in the same message.
        """
        if not usage:
            return
        try:
            as_dict = usage if isinstance(usage, dict) else getattr(usage, "__dict__", None)
            seen = self._message_usage.get(message_id, {}) if message_id else {}
            now = {
                "cost": pricing.estimate(self.task.model or self.config.model, as_dict),
                **pricing.tokens(as_dict),
            }
            added = {k: max(0, v - seen.get(k, 0)) for k, v in now.items()}
            if message_id:
                self._message_usage[message_id] = {k: max(v, seen.get(k, 0)) for k, v in now.items()}
        except Exception:
            log.exception("could not price usage for task %s", self.task.id)
            return
        cost = added.pop("cost")
        if not any(added.values()) and cost <= 0:
            return
        hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        key = (hour, self._skill)
        self._run_costs[key] = self._run_costs.get(key, 0.0) + cost
        self._write_usage(self.store.add_usage, self.task.id, hour, self._skill, cost,
                          {k: int(v) for k, v in added.items()})
        if cost <= 0:
            return
        self._estimated_cost += cost
        self.task.cost_usd = self._estimated_cost
        self.task.cost_is_estimate = True
        self.emit("cost", costUsd=self._estimated_cost, estimate=True)
        asyncio.create_task(self._store_cost(self._estimated_cost, estimate=True))

    def _note_skill(self, tool: str, tool_input: dict[str, Any] | None) -> None:
        """Follow which skill the task is working with, so spend lands on it."""
        if self.task.is_power:
            return
        if self._attribution is None:
            self._attribution = skills.attribution(self.config.workspace)
        found = self._attribution.find(tool, tool_input)
        if not found or self._skill in found:
            return
        self._skill = found[0]
        if not self._skill_named:
            self._skill_named = True
            for (hour, skill), cost in list(self._run_costs.items()):
                if skill == "":
                    del self._run_costs[(hour, skill)]
                    key = (hour, self._skill)
                    self._run_costs[key] = self._run_costs.get(key, 0.0) + cost
            self._write_usage(self.store.name_usage_skill, self.task.id, self._skill)

    def _settle_usage(self, total: float) -> None:
        """Correct this run's rollup rows to the agent's own total.

        The estimate is list price and misses what only the agent knows, so
        the rows keep its shape and take the real figure. A resumed session's
        total includes everything before it, which the rollup already holds.
        """
        if total <= 0:
            return

        async def settle() -> None:
            earlier = await self.store.session_usage(self.task.id, self.task.session_id)
            own = max(0.0, total - max(0.0, earlier - self._estimated_cost))
            if self._estimated_cost <= 0:
                hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                self._run_costs[(hour, self._skill)] = own
                await self.store.add_usage(self.task.id, hour, self._skill, own, {})
            elif own > 0:
                factor = own / self._estimated_cost
                for (hour, skill), cost in self._run_costs.items():
                    self._run_costs[(hour, skill)] = cost * factor
                    await self.store.add_usage(self.task.id, hour, skill, cost * (factor - 1), {})
            self._estimated_cost = own or self._estimated_cost

        self._write_usage(settle)

    def _write_usage(self, write: Any, *args: Any) -> None:
        async def run() -> None:
            async with self._usage_lock:
                try:
                    await write(*args)
                except Exception:
                    log.exception("could not record usage for %s", self.task.id)

        task = asyncio.create_task(run())
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def _record_limit(self, info: dict[str, Any]) -> None:
        """Keep the reading. Never let it interfere with the run."""
        try:
            await self.settings.record_limit(info)
        except Exception:
            log.exception("could not record the rate limit reading")
            return
        # A rejection is the CLI saying it refused this call. The watcher
        # decides what to hold every five minutes; a rejection can strand a
        # whole queue in those five, so it is asked to re-decide now.
        if str(info.get("status")) == "rejected" and self.on_limit_reading is not None:
            try:
                await self.on_limit_reading()
            except Exception:
                log.exception("could not re-decide the usage hold")

    async def _store_cost(self, cost: float, *, estimate: bool) -> None:
        try:
            await self.store.set_cost(self.task.id, cost, estimate=estimate)
        except Exception:
            log.exception("could not record cost for %s", self.task.id)

    async def _hand_survey_on(self, result: str) -> None:
        """Give a finished survey to whoever plans from it."""
        if self.surveyed is None:
            log.warning("survey %s finished with nobody to plan from it", self.task.id)
            return
        try:
            await self.surveyed(self.task, result)
        except Exception:
            # The survey itself succeeded and its activity is on record; a
            # failure to plan from it must not mark the run as failed.
            log.exception("could not plan from survey %s", self.task.id)

    def _chat_game_roots(self) -> tuple[Path, ...]:
        """The game folder a chat task may write into, from .dex/game-chat.json."""
        if not self.task.is_chat:
            return ()
        from .game_chat import game_root  # noqa: PLC0415 -- the API module, imported lazily

        root = game_root(self.config, self.task.project)
        return (root,) if root else ()

    async def _converse(self, client: ClaudeSDKClient) -> None:
        """Run a chat task: its turns, plus whatever the player types meanwhile.

        Each message from the player (`task.inbox`, filled by the game-chat
        API) interrupts the turn under way and goes to the same session as the
        next turn, so the agent takes it into account at once rather than
        after finishing what it was doing. The run ends when a turn finishes
        with nothing left in the inbox; anything typed after that point is
        held as a follow-up note and starts the next attempt.
        """
        inbox: asyncio.Queue[str] = asyncio.Queue()
        self.task.inbox = inbox
        due = 1  # turns still to finish: the opening prompt
        interrupted: asyncio.Event | None = None

        async def feed() -> None:
            nonlocal due, interrupted
            while True:
                text = await inbox.get()
                # Stop the turn under way and wait for it to wind down, so its
                # result is not taken for the new turn's.
                interrupted = asyncio.Event()
                with contextlib.suppress(Exception):
                    await client.interrupt()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(interrupted.wait(), timeout=20)
                interrupted = None
                due += 1
                self.emit("text", text="The player sent a message: taking it into account.")
                await client.query(chat_interjection(text))

        feeder = asyncio.create_task(feed(), name=f"dex-chat-inbox-{self.task.slug}")
        try:
            async for message in client.receive_messages():
                if isinstance(message, ResultMessage) and interrupted is not None and not interrupted.is_set():
                    # The turn the player's message cut short ends as an error;
                    # that is the interrupt, not a failure, so it is not reported.
                    interrupted.set()
                    due = max(0, due - 1)
                    continue
                self._observe(message)
                if not isinstance(message, ResultMessage):
                    continue
                due = max(0, due - 1)
                if due == 0 and inbox.empty() and interrupted is None:
                    break
        finally:
            feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await feeder
            self.task.inbox = None
            # Typed after the last turn ended: kept for the follow-up attempt.
            while not inbox.empty():
                text = inbox.get_nowait()
                with contextlib.suppress(Exception):
                    await TaskMessageStore(self.store.db).add(self.task.id, text)

    async def _post_design_reply(
        self, thread_id: str, summary: str, failed: bool = False
    ) -> None:
        """Append a design turn's answer to its thread.

        Written through the same store the API uses, so a reload shows it; the
        event is what puts it on screen without one.
        """
        try:
            message = ThreadMessage(
                role="dex", kind="text", text=summary.strip()[:4000],
                # `taskId` is what lets the reader open the run behind the
                # reply; without it a finished turn's activity is unreachable.
                data={
                    "project": self.task.project,
                    "taskId": self.task.id,
                    "failed": failed,
                },
            )
            await ThreadStore(self.store.db).append(thread_id, message)
            self.bus.publish(
                Event(
                    type="thread_message",
                    data={"threadId": thread_id, "message": message.to_json()},
                    project=self.task.project,
                )
            )
        except Exception as exc:
            # The run itself succeeded; failing to echo it must not undo that.
            # But it must not be silent either: this swallowed an ImportError
            # for every design turn ever run — `ThreadMessage` was imported
            # from the wrong module — and the only trace was a line in a log
            # nobody reads, while the thread quietly lost every reply.
            log.exception("could not post the design reply for %s", self.task.id)
            self.emit(
                "error",
                message=f"The turn finished but its reply could not be posted: {exc}",
                fatal=False,
            )

    async def _store_tokens(self, counts: dict[str, int]) -> None:
        try:
            await self.store.set_tokens(self.task.id, counts)
        except Exception:
            log.exception("could not record tokens for %s", self.task.id)

    async def _store_session(self, session_id: str) -> None:
        try:
            await self.store.set_session(self.task.id, session_id)
        except Exception:
            log.exception("could not record session for %s", self.task.id)

    def _enabled_skills(self) -> list[tuple[str, str, str, bool]]:
        """Every enabled skill: name, description, directory, and whether it
        has `utils/` a helper could be promoted into.

        The path is the versioned one, because that is the only path the
        permission policy allows. A widget-only skill is still listed — its
        `SKILL.md` is worth reading — but without the promote line, since there
        is nowhere in it to put a module.
        """
        try:
            project_dir = self.task.project_dir(
                self.config.assets_dir, self.config.default_project
            )
            on = skills.read_enabled(project_dir).skills
            # Matched on name *and* version. Two versions of a skill can sit
            # side by side, and matching the name alone listed both — which
            # tells a task to read two sets of instructions and leaves it to
            # guess which directory it may write to.
            work = getattr(self, "_skill_work", None) or {}
            return [
                # The task's own working copy when it has one: that is where it
                # reads the instructions and where a promotion goes.
                (s.name, s.description, str(work[s.name][1]) if s.name in work else str(s.path), s.utils_dir.is_dir())
                for s in skills.all_skills(self.config.workspace)
                if on.get(s.name) == s.version
            ]
        except Exception:
            log.exception("could not read the enabled skills for %s", self.task.id)
            return []

    _skill_work: dict | None = None

    def _prepare_skill_work(self) -> None:
        """Give this task its own working copy of each enabled skill.

        A promotion is written there, not into the published version other
        tasks share, and applied at the end under the skill's lock onto
        whatever version the project is on by then (`skills.promote`). A design
        turn edits drafts instead and gets none.
        """
        self._skill_work = None
        if self.task.is_design:
            return
        try:
            root = self.config.state_dir / "skill-work" / self.task.id
            self._skill_work_dir = root
            self._skill_work = skills.work_copies(
                self.config.workspace,
                self.task.project_dir(self.config.assets_dir, self.config.default_project),
                root,
            )
        except Exception:
            # Without copies the task simply cannot promote: the shared
            # versions stay out of its writable roots either way.
            log.exception("could not copy skills for %s", self.task.id)
            self._skill_work = {}

    def _adopt_drafts(self) -> None:
        """Publish what a design turn authored, and move every project onto it.

        Different from a task's promotion on purpose. A task writing a helper
        is doing it in passing, so only its own project moves. A design turn is
        an authoring act — deliberate, and tested before it lands — so
        everything on that skill moves, or the thing just designed is running
        nowhere.
        """
        try:
            done = skills.adopt(self.config.workspace, self.config.assets_dir)
            for name, version, moved in done:
                log.info("published %s@%s onto %s", name, version, moved or "nobody")
                where = ", ".join(moved) if moved else "no project yet"
                self.emit(
                    "text",
                    text=f"Published `{name}@{version}` — {where} now on it.",
                )
        except Exception:
            log.exception("could not adopt drafts after %s", self.task.id)

    def _resync_skills(self) -> None:
        """Publish what this task promoted: per skill, locked, rebased onto the project's version now.

        After the run, not during it: a task that promoted a helper and then
        failed its own checks should not leave a published version behind.
        Never fails a task: the work is done and on disk either way. A
        promotion that collides with another (both changed the same lines) is
        not published; the task's copy is kept in its package for a person.
        """
        work = getattr(self, "_skill_work", None)
        try:
            if work:
                project_dir = self.task.project_dir(self.config.assets_dir, self.config.default_project)
                out = skills.promote(self.config.workspace, project_dir, work,
                                     state_dir=self.config.state_dir, owner=self.task.slug)
                if out["changed"]:
                    names = ", ".join(f"{n}@{v}" for n, v in out["changed"].items())
                    log.info("task %s promoted into %s", self.task.id, names)
                    self.emit("text", text=f"Published {names} with this task's helpers, on top of "
                              "whatever other tasks promoted meanwhile. This project is on it.")
                for name, files in out["conflicts"].items():
                    keep = self.task_dir / "promotion-conflicts" / name
                    import shutil
                    shutil.rmtree(keep, ignore_errors=True)
                    shutil.copytree(work[name][1], keep)
                    self.emit("text", text=f"Not promoted into {name}: another task changed the same "
                              f"lines of {', '.join(files)} meanwhile. This task's copy is in "
                              f"promotion-conflicts/{name}/ for a person to merge.")
        except Exception:
            log.exception("could not promote skills after %s", self.task.id)
        finally:
            cleanup = getattr(self, "_skill_work_dir", None)
            if cleanup is not None:
                import shutil

                shutil.rmtree(cleanup, ignore_errors=True)
                self._skill_work_dir = None

    def _linked_sources(self) -> list[tuple[str, str]]:
        """Directories linked into this project from elsewhere on the machine.

        Read now rather than at task start for the same reason the rest is: a
        link made while a batch is queued should reach the tasks that have not
        started yet.
        """
        found = []
        for link in local_sources.links(
            self.config.project_datasets(self.task.project)
        ):
            if not link.readable:
                # Listed as broken rather than dropped. A task told nothing is
                # there reports a missing file; one told the link is broken
                # says something the operator can act on.
                found.append((link.name, f"**unreadable** — `{link.target}` is not listable"))
                continue
            count = f"{link.entries:,}{'+' if link.partial else ''}"
            found.append((link.name, f"{count} entries, on `{link.target}`"))
        return found

    def _refresh_utils_index(self) -> None:
        """Rewrite the project's `utils/API.md` before the agent reads it.

        The guides send a task to that one file instead of the modules — 13% of
        the bytes — but a generated index is only worth reading if it is true.
        Doing it here means it cannot drift behind a module a previous task
        changed and forgot to regenerate. Writes only on a real difference, so
        in the steady state it touches nothing and the watcher stays quiet.
        """
        project_dir = self.task_dir if self.task.project_wide else self.task_dir.parent
        try:
            from .tools.utils_api import write

            write(project_dir)
        except Exception:
            # An index is a convenience. Never fail a task over one.
            log.exception("could not refresh the utils index for %s", project_dir)

    def _translate_delta(self, message: Any) -> None:
        """One Anthropic stream event -> an append to a text or thinking block."""
        event = getattr(message, "event", None) or {}
        kind = event.get("type")
        index = event.get("index")

        if kind == "content_block_start":
            block = event.get("content_block") or {}
            block_type = block.get("type")
            if block_type in ("text", "thinking"):
                self._blocks[index] = f"{message.uuid}:{index}"
                self._block_kinds[index] = block_type
            return

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            block_id = self._blocks.get(index)
            if block_id is None:
                return
            if delta.get("type") == "text_delta" and delta.get("text"):
                self.emit("text_delta", id=block_id, delta=delta["text"])
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                self._thinking_streams = True
                self.emit("thinking_delta", id=block_id, delta=delta["thinking"])
            return

        if kind == "content_block_stop":
            block_id = self._blocks.pop(index, None)
            self._block_kinds.pop(index, None)
            if block_id is not None:
                self.emit("block_end", id=block_id)

    def _artifacts(self) -> dict[Path, int]:
        """Every artifact in the package, and when it was last written.

        The watcher's own test, so what it already declines to announce —
        `__pycache__`, `.cues`, a half-written `.part` — is not counted here
        either. Relative paths, because the ignore list is matched against path
        parts and the absolute prefix is none of its business.
        """
        found: dict[Path, int] = {}
        if not self.task_dir.is_dir():
            return found
        for path in self.task_dir.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.task_dir)
            if not is_artifact(relative):
                continue
            try:
                found[relative] = path.stat().st_mtime_ns
            except OSError:  # vanished mid-walk; it is not output either way
                continue
        return found

    def _wrote_since_created(self) -> bool:
        """Whether an earlier run of this very task wrote into its package.

        A paused task (preempted, or the server stopped under it) resumes its
        own session in the same row; a resumed run that finds the work done
        and only confirms it is the second half of a run that wrote it. A new
        attempt is a new row, created after its parent's files, so it still
        cannot coast on them.
        """
        since = int(self.task.created_at * 1e9)
        return any(written >= since for written in self._artifacts().values())

    def _wrote_anything(self, before: dict[Path, int]) -> bool:
        """Whether this run put a file worth showing into its package.

        A file nobody had before, or one whose contents were rewritten. Asking
        only whether the directory is non-empty would let a run that inherited
        a full package coast on somebody else's work.
        """
        return any(
            before.get(relative) != written
            for relative, written in self._artifacts().items()
        )

    def _pause_for_limit(self, result: str) -> None:
        """The CLI refused work because a Claude limit is spent.

        Failing here throws away a task somebody is waiting on, and the queue
        dispatches the next one into the same refusal — one failure per task,
        and a batch of them fails itself to death inside a minute (fifty-five
        tasks in seventy seconds on 27 Oct). So this is the shape of the other
        limit paths instead: the run stops in place, keeps its agent session
        on the row, and dex holds the fleet until the window the CLI named has
        rolled over, whereupon rebalance puts the paused work back.
        """
        self.task.activity = limit_pause_note(result)
        # Not failed, so no terminal fields: the task has not ended, it has
        # stopped where the window caught it.
        self.task.finished_at = None
        self.set_state(TaskState.PAUSED)
        asyncio.create_task(self._store_limit_pause())

    async def _store_limit_pause(self) -> None:
        """Persist the pause, with the session to resume, and hold the fleet."""
        try:
            await self.store.set_state(
                self.task.id,
                TaskState.PAUSED,
                resumed_from=self.task.session_id,
                activity=self.task.activity,
                finished_at=None,
            )
        except Exception:
            log.exception("could not persist the usage pause for %s", self.task.id)
        # A rejected call does not always carry a reading: some refusals come
        # back as the error text alone. The watcher needs one to hold on, or
        # the fleet keeps dispatching into the same refusal; a conservative
        # shared-weekly reading is corrected by the next probe (or the next
        # run's own RateLimitEvent) a few minutes later.
        try:
            snapshot = await self.settings.limit_snapshot()
            fresh = [
                w for w, r in snapshot.items()
                if isinstance(r, dict) and r.get("status") == "rejected"
                and time.time() - r.get("seenAt", 0) < 60
            ]
            if not fresh:
                await self.settings.record_limit(
                    {"rateLimitType": "seven_day", "status": "rejected"})
        except Exception:
            log.exception("could not record a synthetic reading for %s", self.task.id)
        if self.on_limit_reading is not None:
            try:
                await self.on_limit_reading()
            except Exception:
                log.exception("could not re-decide the usage hold")

    def _fail(self, message: str) -> None:
        if self.task.archived:
            # Archived out from under the run. The SDK reports the vanished
            # subprocess as an error; there is nothing wrong and nothing to
            # resume.
            self.task.activity = None
            self.set_state(TaskState.ARCHIVED)
            return
        if self.task.preempted:
            # dex stopped this itself — a restart, or making room. The agent
            # reports that as an error because its subprocess vanished, but
            # nothing is wrong with the work and it should go again.
            self.task.finished_at = None
            self.task.activity = None
            self.set_state(TaskState.PAUSED)
            return
        # Missing credentials is by far the most common first-run failure, and
        # the SDK's own wording ("Please run /login") points at the wrong fix
        # for a server, so say what to actually do.
        if is_auth_error(message):
            message = f"{message}\n\n{AUTH_HINT}"
        self.task.error = message
        self.task.finished_at = time.time()
        self.task.activity = None
        self.emit("error", message=message, fatal=True)
        self.set_state(TaskState.FAILED, error=message)
        if self.task.is_design and self.task.thread_id:
            # A design turn that succeeds posts its answer into the thread, and
            # that message is the only thing linking back to the run. One that
            # failed posted nothing, so the turn vanished from the conversation
            # the moment it stopped being live — no reply, no chip, no way back
            # to the activity that would say what went wrong.
            asyncio.create_task(
                self._post_design_reply(
                    self.task.thread_id, f"That turn failed.\n\n{message}", failed=True
                )
            )

    # ------------------------------------------------------------- translate

    def _observe(self, message: Any) -> None:
        """Translate one message, never letting that failure end the run.

        Reporting is not the work. A bad cost estimate, an unexpected message
        shape, or a formatting slip should cost a line in the log, not the task
        the agent is part-way through. Errors from *receiving* messages are a
        different matter and still fail the run — those mean the agent itself
        has stopped.
        """
        try:
            self._translate(message)
        except Exception:
            kind = type(message).__name__
            log.exception("could not translate a %s for task %s", kind, self.task.id)
            self.emit(
                "error",
                message=f"Could not render a {kind} from the agent; the run continues.",
                fatal=False,
            )

    def _translate(self, message: Any) -> None:
        if isinstance(message, StreamEvent):
            self._translate_delta(message)
            return

        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                session_id = message.data.get("session_id")
                if session_id and session_id != self.task.session_id:
                    self.task.session_id = session_id
                    # Recorded immediately: if the server dies mid-run, this is
                    # the only handle on the work the agent has already done.
                    asyncio.create_task(self._store_session(session_id))
            return

        if isinstance(message, AssistantMessage):
            self._accrue(getattr(message, "usage", None), getattr(message, "message_id", None))
            for block in message.content:
                # Text already arrived as deltas; re-emitting the completed
                # block here would duplicate every word.
                if isinstance(block, TextBlock):
                    continue
                if isinstance(block, ThinkingBlock):
                    # Thinking usually arrives as deltas too — but when the CLI
                    # hands it over only as a finished block, skipping it here
                    # dropped it entirely and the task looked idle for minutes.
                    #
                    # Matching this text against what streamed is what the first
                    # version did, and it duplicated every block in the panel:
                    # the SDK yields this message before the block's trailing
                    # `content_block_stop`, so the comparison ran against a set
                    # that did not have the text yet. One delta anywhere in the
                    # run is proof enough that the deltas are coming.
                    if block.thinking and not self._thinking_streams:
                        self.emit("thinking", text=block.thinking)
                    continue
                if isinstance(block, ToolUseBlock):
                    self._note_skill(block.name, block.input)
                    title = _tool_title(block.name, block.input)
                    self.task.activity = title
                    self.emit(
                        "tool",
                        id=block.id,
                        name=block.name,
                        title=title,
                        input=_trim_input(block.input),
                        # Carried on the tool event so the UI's status line
                        # updates the moment a tool starts. Without this the
                        # activity only refreshed on a state transition, which
                        # is what made the status look stale.
                        activity=title,
                    )
            return

        if isinstance(message, UserMessage):
            content = message.content if isinstance(message.content, list) else []
            for block in content:
                if isinstance(block, ToolResultBlock):
                    self.emit(
                        "tool_result",
                        id=block.tool_use_id,
                        ok=not block.is_error,
                        output=_flatten(block.content)[:4000],
                    )
            return

        if isinstance(message, RateLimitEvent):
            # How much of a Claude limit is gone. The CLI only says so while a
            # run is in flight, so recording it here is the only way dex learns
            # it at all — the usage watcher decides what to do about it.
            info = message.rate_limit_info
            asyncio.create_task(self._record_limit(dict(info.raw)))
            return

        if isinstance(message, ResultMessage):
            # The agent's own total supersedes the running estimate.
            if message.total_cost_usd is not None:
                self._settle_usage(message.total_cost_usd)
                self.task.cost_usd = message.total_cost_usd
                self.task.cost_is_estimate = False
                asyncio.create_task(
                    self._store_cost(message.total_cost_usd, estimate=False)
                )
                self.emit("cost", costUsd=message.total_cost_usd, estimate=False)
            # Tokens come from the result rather than the streamed messages:
            # only this payload carries `output_tokens_details`, which is the
            # one place thinking is reported apart from the output it is part of.
            counts = pricing.tokens(getattr(message, "usage", None))
            if any(counts.values()):
                self.task.tokens = counts
                asyncio.create_task(self._store_tokens(counts))
            self.task.turns = message.num_turns
            if self.task.is_survey and self.task.thread_id and message.result:
                # A survey is a means, not an end: nothing it says is useful
                # until the planner has turned it into tasks the operator can
                # choose from. Handing it straight on is what makes the two
                # runs read as one step in the thread.
                asyncio.create_task(self._hand_survey_on(message.result))
            elif (self.task.is_design or self.task.is_chat) and self.task.thread_id and message.result:
                # The design thread is a conversation, so it needs the reply in
                # line. The task panel still holds the whole run; this is the
                # part somebody reading the thread should not have to dig for.
                # A game chat's Chats thread reads the same way in dex.
                asyncio.create_task(
                    self._post_design_reply(self.task.thread_id, message.result)
                )
            self.emit(
                "result",
                ok=not message.is_error,
                durationMs=message.duration_ms,
                turns=message.num_turns,
                costUsd=message.total_cost_usd,
                summary=(message.result or "")[:4000],
            )
            if message.is_error:
                result = message.result or "agent reported an error"
                if is_usage_limit_error(result):
                    self._pause_for_limit(result)
                else:
                    self._fail(result)


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _trim_input(data: dict[str, Any], limit: int = 600) -> dict[str, Any]:
    """Tool inputs can hold whole files; the UI only needs a preview."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        text = value if isinstance(value, str) else repr(value)
        out[key] = text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"
    return out


def _tool_title(name: str, data: dict[str, Any]) -> str:
    path = data.get("file_path") or data.get("path")
    stem = Path(str(path)).name if path else ""
    match name:
        case "Read":
            return f"Read {stem}"
        case "Write":
            return f"Write {stem}"
        case "Edit" | "MultiEdit":
            return f"Edit {stem}"
        case "Bash":
            return str(data.get("description") or data.get("command", "run command"))[:120]
        case "Glob" | "Grep":
            return f"Search {data.get('pattern', '')}"
        case _:
            return name.split("__")[-1] if name.startswith("mcp__") else name
