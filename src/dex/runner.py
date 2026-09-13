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
from pathlib import Path
from typing import Any

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
from .bus import EventBus
from .config import Config
from .store import SettingsStore, TaskStore
from .diffs import preview_change
from .models import Event, Task, TaskState
from . import widgets
from .permissions import PermissionPolicy
from .prompts import DESIGN_SYSTEM, GENERATION_SYSTEM, design_prompt, generation_prompt


log = logging.getLogger("dex.runner")


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
        self.task_dir = task.output_dir(config.assets_dir, config.default_project)
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
        asyncio.create_task(self._persist_state(state, extra))

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
            options = [str(o) for o in (args.get("options") or [])]
            self.emit(
                "question",
                id=question_id,
                question=str(args.get("question", "")),
                options=options,
            )
            # A utility question has one answer while sharing is on, and the
            # operator said so by leaving the setting on. Still asked, so the
            # decision is recorded where they can see it, but answered here
            # rather than parking the task on a doorbell nobody needs to hear.
            auto = await self._auto_answer(str(args.get("kind", "")), options)
            if auto is not None:
                self.emit("question_answered", id=question_id, answer=auto, auto=True)
                return {"content": [{"type": "text", "text": auto}]}
            answer = await self._park(question_id, "waiting on a clarifying question")
            self.emit("question_answered", id=question_id, answer=answer)
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

        return create_sdk_mcp_server(
            name="dex", version="0.1.0", tools=[ask_user, utility_proposals_enabled]
        )

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

        policy = PermissionPolicy(
            workspace=self.config.workspace,
            task_dir=self.task_dir,
            # A design turn also authors widgets, which are shared across
            # projects and therefore live at the top level rather than inside
            # the project it is editing.
            extra_writable=(
                (widgets.widgets_dir(self.config.workspace),) if self.task.is_design else ()
            ),
            escalate=self._escalate,
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
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": DESIGN_SYSTEM if self.task.is_design else GENERATION_SYSTEM,
            },
            permission_mode="default",
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
            mcp_servers={"dex": self._ask_user_server()},
            max_turns=self.config.max_turns,
            # Read at task start, so a change applies to new work while runs
            # already going keep the effort they were planned with.
            effort=effort,  # type: ignore[arg-type]
            setting_sources=[],  # ignore local .claude config; the brief is the spec
            # Continues the earlier run's conversation when there is one, so the
            # agent keeps what it already worked out instead of starting over.
            resume=resume_session,
            stderr=lambda line: self.emit("error", message=line[:500], fatal=False),
        )

        self._refresh_utils_index()
        if self.task.is_design:
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
        else:
            prompt = generation_prompt(
                problem=self.task.problem,
                task_dir=self.task_dir,
                python=Path(sys.executable),
                manim_available=manim_available(),
                project_wide=self.task.project_wide,
            )

        try:
            async with asyncio.timeout(self.config.task_timeout_s):
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(prompt)
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
            self._fail(f"{type(exc).__name__}: {exc}")
        else:
            self.task.finished_at = time.time()
            self.task.activity = None
            if self.task.state is not TaskState.FAILED:
                self.set_state(TaskState.SUCCEEDED)
        finally:
            for future in list(self.task.pending.values()):
                if not future.done():
                    future.cancel()
            self.task.pending.clear()

    def _accrue(self, usage: Any) -> None:
        """Add one message's usage to the running estimate and report it.

        Guarded separately from `_observe` so a pricing problem costs only the
        estimate, not the tool and text events in the same message.
        """
        if not usage:
            return
        try:
            as_dict = usage if isinstance(usage, dict) else getattr(usage, "__dict__", None)
            added = pricing.estimate(self.task.model or self.config.model, as_dict)
        except Exception:
            log.exception("could not price usage for task %s", self.task.id)
            return
        if added <= 0:
            return
        self._estimated_cost += added
        self.task.cost_usd = self._estimated_cost
        self.task.cost_is_estimate = True
        self.emit("cost", costUsd=self._estimated_cost, estimate=True)
        asyncio.create_task(self._store_cost(self._estimated_cost, estimate=True))

    async def _record_limit(self, info: dict[str, Any]) -> None:
        """Keep the reading. Never let it interfere with the run."""
        try:
            await self.settings.record_limit(info)
        except Exception:
            log.exception("could not record the rate limit reading")

    async def _store_cost(self, cost: float, *, estimate: bool) -> None:
        try:
            await self.store.set_cost(self.task.id, cost, estimate=estimate)
        except Exception:
            log.exception("could not record cost for %s", self.task.id)

    async def _post_design_reply(self, thread_id: str, summary: str) -> None:
        """Append a design turn's answer to its thread.

        Written through the same store the API uses, so a reload shows it; the
        event is what puts it on screen without one.
        """
        try:
            from .models import ThreadMessage
            from .store import ThreadStore

            message = ThreadMessage(
                role="dex", kind="text", text=summary.strip()[:4000],
                data={"project": self.task.project, "taskId": self.task.id},
            )
            await ThreadStore(self.store.db).append(thread_id, message)
            self.bus.publish(
                Event(
                    type="thread_message",
                    data={"threadId": thread_id, "message": message.to_json()},
                    project=self.task.project,
                )
            )
        except Exception:
            # The run itself succeeded; failing to echo it must not undo that.
            log.exception("could not post the design reply for %s", self.task.id)

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
            self._accrue(getattr(message, "usage", None))
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
            if self.task.is_design and self.task.thread_id and message.result:
                # The design thread is a conversation, so it needs the reply in
                # line. The task panel still holds the whole run; this is the
                # part somebody reading the thread should not have to dig for.
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
                self._fail(message.result or "agent reported an error")


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
